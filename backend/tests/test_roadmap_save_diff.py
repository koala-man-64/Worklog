"""A roadmap save writes only what it changes (consolidation step 4.1, K4, findings B-02 and REM-13).

A step the save leaves as it is keeps its revision and gets no event, whatever position number it
takes. A save that changes nothing records no revision, version or event. The roadmap's event names
its version and the steps changed, where it held two copies of the tree. Progress raises only the
step's revision, and a proposal that revises a roadmap and then sets a step's progress expects one
more only for a step the revise changes."""

import random
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.common import AppError, read_events, uid
from worklog.db import Database
from worklog.plans import PlanService

HUMAN = {"id": "human", "label": "Rudy", "kind": "human"}
ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def plans(tmp_path):
    db = Database(tmp_path / "worklog.db")
    db.initialize()
    return PlanService(db)


def apply(plans, action, payload):
    with plans.db.transaction() as conn:
        return plans.apply(conn, HUMAN, action, payload)


def events(plans, identifier):
    with plans.db.read() as conn:
        return read_events(conn, identifier)


def kinds(plans, identifier):
    """The kinds of a record's events, sorted: events in one instant have no order between them."""
    return sorted(event["kind"] for event in events(plans, identifier))


def three(plans):
    a, b, c = uid(), uid(), uid()
    created = apply(plans, "roadmap.create", {"title": "Plan", "steps": [
        {"id": a, "title": "A"}, {"id": b, "title": "B"}, {"id": c, "title": "C"}]})
    return created, a, b, c


def steps_of(roadmap, **titles):
    """The roadmap's active steps as a save sends them, with some titles changed."""
    return [{"id": s["id"], "title": titles.get(s["id"], s["title"]), "description": s["description"],
             "parentId": s["parentId"], "position": s["position"]} for s in roadmap["steps"] if not s["retired"]]


def test_a_save_touches_only_the_steps_it_changes(plans):
    created, a, b, c = three(plans)
    revised = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Plan",
                                              "steps": steps_of(created, **{b: "B, renamed"})})
    assert {s["id"]: s["revision"] for s in revised["steps"]} == {a: 1, b: 2, c: 1}
    assert (kinds(plans, a), kinds(plans, b), kinds(plans, c)) == (["step.create"], ["step.create", "step.revise"], ["step.create"])
    assert revised["revision"] == 2 and [v["number"] for v in revised["versions"]] == [1, 2]
    event = next(event for event in events(plans, created["id"]) if event["kind"] == "roadmap.revise")
    assert event["before"] is None
    assert event["after"] == {"versionNumber": 2, "changedStepIds": [b]}


def test_a_save_that_changes_nothing_records_nothing(plans):
    created, *_ = three(plans)
    same = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Plan",
                                           "steps": steps_of(created), "note": "No change"})
    assert same["revision"] == 1 and [v["number"] for v in same["versions"]] == [1]
    assert kinds(plans, created["id"]) == ["roadmap.create"]
    # Restoring the version the plan already matches changes nothing either.
    restored = apply(plans, "roadmap.restore", {"id": created["id"], "revision": 1, "number": 1})
    assert restored["revision"] == 1 and kinds(plans, created["id"]) == ["roadmap.create"]


def test_retiring_restoring_and_moving_a_step_are_changes(plans):
    created, a, b, c = three(plans)
    retired = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Plan",
                                              "steps": [s for s in steps_of(created) if s["id"] != c]})
    assert {s["id"]: (s["revision"], s["retired"]) for s in retired["steps"]} == {a: (1, False), b: (1, False), c: (2, True)}
    back = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 2, "title": "Plan", "steps": steps_of(created)})
    assert {s["id"]: s["revision"] for s in back["steps"]} == {a: 1, b: 1, c: 3}
    assert kinds(plans, c) == ["step.create", "step.restore", "step.retire"]
    moved = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 3, "title": "Plan", "steps": [
        {**s, "parentId": a} if s["id"] == b else s for s in steps_of(back)]})
    assert {s["id"]: s["revision"] for s in moved["steps"]} == {a: 1, b: 2, c: 3}


def test_the_roadmap_event_names_the_change_instead_of_holding_the_tree(plans):
    steps = [{"id": uid(), "title": f"Step {n}", "description": "x" * 2000} for n in range(300)]
    created = apply(plans, "roadmap.create", {"title": "Large", "steps": steps})
    apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Large",
                                    "steps": steps_of(created, **{steps[0]["id"]: "Renamed"})})
    with plans.db.read() as conn:
        sizes = dict(conn.execute("SELECT kind,length(before_json)+length(after_json) FROM events WHERE entity_id=?",
                                  (created["id"],)).fetchall())
    # Each held the whole tree twice, some 1.2 MB here. The create's now names its 300 new steps,
    # and the revise its one.
    assert set(sizes) == {"roadmap.create", "roadmap.revise"}
    assert sizes["roadmap.create"] < 300 * 45 and sizes["roadmap.revise"] < 200


ORIGIN_HEADERS = {"Origin": ORIGIN}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def call(c, method, path, body=None):
    response = c.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_proposal_that_revises_then_sets_progress_expects_one_more_only_where_the_revise_changes(client):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    roadmap = call(client, "POST", "/roadmaps", {"title": "Plan", "steps": [{"id": a, "title": "A"}, {"id": b, "title": "B"}]})
    steps = [{"id": s["id"], "title": "A, renamed" if s["id"] == a else s["title"], "description": s["description"],
              "parentId": s["parentId"], "position": s["position"]} for s in roadmap["steps"]]
    proposal = call(client, "POST", "/proposals", {"title": "Rename and start", "rationale": "", "operations": [
        {"action": "roadmap.revise", "payload": {"id": roadmap["id"], "revision": 1, "title": "Plan", "steps": steps}},
        # A changes in the revise, so its revision will be 2; B stays at 1.
        {"action": "progress.set", "payload": {"id": a, "revision": 2, "status": "doing"}},
        {"action": "progress.set", "payload": {"id": b, "revision": 1, "status": "doing"}}]})
    with client.app.state.db.read() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal["id"],)).fetchone()
        assert client.app.state.proposals._stale_operations(conn, row) == []
    submitted = call(client, "POST", f"/proposals/{proposal['id']}/submit", {"revision": proposal["revision"]})
    approved = call(client, "POST", f"/proposals/{proposal['id']}/approve", {"revision": submitted["revision"]})
    assert approved["status"] == "approved"
    statuses = {s["id"]: (s["status"], s["revision"]) for s in call(client, "GET", f"/roadmaps/{roadmap['id']}")["steps"]}
    assert statuses == {a: ("doing", 3), b: ("doing", 2)}
    # Progress left the roadmap's revision where the revise put it.
    assert call(client, "GET", f"/roadmaps/{roadmap['id']}")["revision"] == 2


@pytest.mark.parametrize("change", ["title", "overview", "description", "parent", "position"])
def test_each_kind_of_change_raises_the_roadmap_revision_once_and_only_its_steps(plans, change):
    created, a, b, c = three(plans)
    steps = steps_of(created)
    title, overview = "Plan", ""
    if change == "title":
        title = "Renamed"
    elif change == "overview":
        overview = "Why this plan"
    elif change == "description":
        steps[1]["description"] = "More about B"
    elif change == "parent":
        steps[1]["parentId"] = a
    else:
        steps[1]["position"], steps[2]["position"] = steps[2]["position"], steps[1]["position"]
    revised = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": title, "overview": overview, "steps": steps})
    raised = {s["id"] for s in revised["steps"] if s["revision"] > 1}
    assert revised["revision"] == 2 and len(revised["versions"]) == 2
    # Swapping B and C moves one of them past the other: C, which now comes first, is the one moved.
    assert raised == {"title": set(), "overview": set(), "description": {b}, "parent": {b}, "position": {c}}[change]


def test_a_revise_that_changes_nothing_leaves_a_later_one_in_the_proposal_its_revision(client):
    """From the review of #126: a revise that changes nothing keeps the roadmap's revision, so a
    later roadmap change in the same proposal carries the same revision, not one more."""
    roadmap = call(client, "POST", "/roadmaps", {"title": "Plan", "steps": [{"title": "A"}]})
    steps = [{"id": s["id"], "title": s["title"], "description": s["description"], "parentId": s["parentId"],
              "position": s["position"]} for s in roadmap["steps"]]
    proposal = call(client, "POST", "/proposals", {"title": "Twice", "rationale": "", "operations": [
        {"action": "roadmap.revise", "payload": {"id": roadmap["id"], "revision": 1, "title": "Plan", "steps": steps}},
        {"action": "roadmap.revise", "payload": {"id": roadmap["id"], "revision": 1, "title": "Renamed", "steps": steps}}]})
    submitted = call(client, "POST", f"/proposals/{proposal['id']}/submit", {"revision": proposal["revision"]})
    assert call(client, "POST", f"/proposals/{proposal['id']}/approve", {"revision": submitted["revision"]})["status"] == "approved"
    assert call(client, "GET", f"/roadmaps/{roadmap['id']}")["title"] == "Renamed"


def test_an_archived_roadmap_refuses_a_save_even_one_that_changes_nothing(plans):
    created, *_ = three(plans)
    archived = apply(plans, "roadmap.archive", {"id": created["id"], "revision": 1})
    with pytest.raises(AppError) as error:
        apply(plans, "roadmap.revise", {"id": created["id"], "revision": archived["revision"], "title": "Plan",
                                        "steps": steps_of(created)})
    assert error.value.status == 409


# A step's place is its parent and its order among its siblings, which the roadmap's revision guards.
# A save that only gives a step another position number, as numbering the list afresh does when a
# step is added or retired before it, leaves it its revision and gives it no event.


def stored(plans, roadmap_id):
    with plans.db.read() as conn:
        return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM steps WHERE roadmap_id=?", (roadmap_id,))}


def numbered(steps):
    """The list numbered afresh in its order, as the structure editor numbers every save."""
    return [{**s, "position": index} for index, s in enumerate(steps)]


def revised_and_kinds(plans, created, steps):
    revised = apply(plans, "roadmap.revise", {"id": created["id"], "revision": created["revision"], "title": "Plan", "steps": steps})
    # Events in one instant have no order between them, so find this save's by its version.
    event = next(e for e in events(plans, created["id"])
                 if e["kind"] == "roadmap.revise" and e["after"]["versionNumber"] == revised["versions"][-1]["number"])
    return revised, set(event["after"]["changedStepIds"])


def test_adding_a_step_before_others_numbered_afresh_moves_none_of_them(plans):
    created, a, b, c = three(plans)
    new = uid()
    revised, changed = revised_and_kinds(plans, created, numbered([{"id": new, "title": "First"}, *steps_of(created)]))
    assert changed == {new}
    assert {s["id"]: s["revision"] for s in revised["steps"]} == {new: 1, a: 1, b: 1, c: 1}
    assert (kinds(plans, a), kinds(plans, b), kinds(plans, c)) == (["step.create"],) * 3
    # The new numbers are stored, so the plan shows in the order sent.
    assert [s["title"] for s in revised["steps"]] == ["First", "A", "B", "C"]
    assert {sid: row["position"] for sid, row in stored(plans, created["id"]).items()} == {new: 0, a: 1, b: 2, c: 3}


def test_retiring_a_step_with_the_rest_numbered_afresh_changes_only_it(plans):
    created, a, b, c = three(plans)
    revised, changed = revised_and_kinds(plans, created, numbered([s for s in steps_of(created) if s["id"] != a]))
    assert changed == {a}
    assert {s["id"]: (s["revision"], s["retired"]) for s in revised["steps"]} == {a: (2, True), b: (1, False), c: (1, False)}
    assert (kinds(plans, b), kinds(plans, c)) == (["step.create"], ["step.create"])


def test_a_child_added_in_tree_order_moves_no_step_in_another_branch(plans):
    """Rudy's case: listing the new step where it shows renumbers every step after it."""
    a, a1, b, b1, c = uid(), uid(), uid(), uid(), uid()
    created = apply(plans, "roadmap.create", {"title": "Plan", "steps": [
        {"id": a, "title": "A"}, {"id": a1, "title": "A1", "parentId": a}, {"id": b, "title": "B"},
        {"id": b1, "title": "B1", "parentId": b}, {"id": c, "title": "C"}]})
    steps = steps_of(created)
    new = uid()
    steps.insert(2, {"id": new, "title": "A2", "parentId": a})
    revised, changed = revised_and_kinds(plans, created, numbered(steps))
    assert changed == {new}
    assert all(s["revision"] == 1 for s in revised["steps"])
    assert [s["title"] for s in revised["steps"] if s["parentId"] == a] == ["A1", "A2"]


@pytest.mark.parametrize("order, moved", [
    ("CAB", "C"),  # C to the top: A and B keep their order between them
    ("BCA", "A"),  # A to the end
    ("ACB", "C"),  # neighbours swapped: the one that now comes first
    ("CBA", "BC"),  # reversed: no fewer than two can account for it
])
def test_reordering_siblings_moves_the_fewest_steps_that_account_for_the_order(plans, order, moved):
    created, a, b, c = three(plans)
    by_title = {s["title"]: s for s in steps_of(created)}
    revised, changed = revised_and_kinds(plans, created, numbered([by_title[t] for t in order]))
    assert changed == {by_title[t]["id"] for t in moved}
    assert [s["title"] for s in revised["steps"]] == list(order)
    assert {s["id"] for s in revised["steps"] if s["revision"] > 1} == changed


def test_a_step_sent_back_marked_retired_stays_retired(plans):
    created, a, b, c = three(plans)
    retired = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Plan",
                                              "steps": [s for s in steps_of(created) if s["id"] != c]})
    # The list as the plan reads it, retired step and all, with one title changed.
    listed = [{"id": s["id"], "title": "A, renamed" if s["id"] == a else s["title"], "description": s["description"],
               "parentId": s["parentId"], "position": s["position"], "retired": s["retired"]} for s in retired["steps"]]
    assert any(s["retired"] for s in listed)
    revised, changed = revised_and_kinds(plans, retired, listed)
    assert changed == {a}
    assert {s["id"]: (s["revision"], s["retired"]) for s in revised["steps"]} == {a: (2, False), b: (1, False), c: (2, True)}
    # Marking an active step retired retires it, as leaving it out does.
    again = apply(plans, "roadmap.revise", {"id": created["id"], "revision": revised["revision"], "title": "Plan",
                                            "steps": [{**s, "retired": s["id"] == b} for s in steps_of(revised)]})
    assert {s["id"]: s["retired"] for s in again["steps"]} == {a: False, b: True, c: True}


def test_a_retired_flag_must_be_true_or_false(plans):
    created, *_ = three(plans)
    with pytest.raises(AppError) as error:
        apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Plan",
                                        "steps": [{**s, "retired": "no"} for s in steps_of(created)]})
    assert error.value.status == 422


def test_another_agents_progress_stays_current_when_a_revise_numbers_the_plan_afresh(client):
    """A step a revise only renumbers keeps its revision, so a pending progress.set on it still applies,
    and the stale check, which predicts the same, finds nothing to report."""
    agents = [call(client, "POST", "/credentials", {"label": label}) for label in ("Tracker", "Planner")]

    def as_agent(agent, method, path, body):
        response = client.request(method, "/api/v1" + path, json=body, headers={
            "Idempotency-Key": str(uuid.uuid4()), "Authorization": "Bearer " + agent["token"]})
        assert response.status_code == 200, response.text
        return response.json()

    def submitted(agent, operations):
        drafted = as_agent(agent, "POST", "/proposals", {"title": "Change", "rationale": "", "operations": operations})
        return as_agent(agent, "POST", f"/proposals/{drafted['id']}/submit", {"revision": drafted["revision"]})

    roadmap = call(client, "POST", "/roadmaps", {"title": "Plan", "steps": [{"title": "A"}, {"title": "B"}, {"title": "C"}]})
    c = roadmap["steps"][2]
    progress = submitted(agents[0], [{"action": "progress.set", "payload": {"id": c["id"], "revision": 1, "status": "doing"}}])
    steps = numbered([{"title": "First"}, *[{k: s[k] for k in ("id", "title", "description", "parentId")} for s in roadmap["steps"]]])
    revise = submitted(agents[1], [{"action": "roadmap.revise", "payload": {"id": roadmap["id"], "revision": 1, "title": "Plan", "steps": steps}}])
    assert call(client, "POST", f"/proposals/{revise['id']}/approve", {"revision": revise["revision"]})["status"] == "approved"
    with client.app.state.db.read() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id=?", (progress["id"],)).fetchone()
        assert client.app.state.proposals._stale_operations(conn, row) == []
    assert call(client, "POST", f"/proposals/{progress['id']}/approve", {"revision": progress["revision"]})["status"] == "approved"


# From the review of K15: the cases a step's place can turn on, and the rule checked over many saves.

TREE = ("id", "title", "description", "parentId", "position")


def as_listed(roadmap):
    return [{k: s[k] for k in TREE} for s in sorted(roadmap["steps"], key=lambda s: (s["position"], s["id"])) if not s["retired"]]


def test_a_step_restored_under_another_parent_changes_once(plans):
    created, a, b, c = three(plans)
    retired = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Plan",
                                              "steps": [s for s in steps_of(created) if s["id"] != c]})
    back = [*steps_of(retired), {"id": c, "title": "C", "description": "", "parentId": a, "position": 3}]
    revised, changed = revised_and_kinds(plans, retired, back)
    assert changed == {c}
    assert {s["id"]: (s["revision"], s["parentId"], s["retired"]) for s in revised["steps"]} == {
        a: (1, None, False), b: (1, None, False), c: (3, a, False)}
    assert kinds(plans, c) == ["step.create", "step.restore", "step.retire"]


def test_a_step_moved_to_another_branch_with_the_plan_numbered_afresh_moves_only_it(plans):
    a, a1, b, b1, c = uid(), uid(), uid(), uid(), uid()
    created = apply(plans, "roadmap.create", {"title": "Plan", "steps": [
        {"id": a, "title": "A"}, {"id": a1, "title": "A1", "parentId": a}, {"id": b, "title": "B"},
        {"id": b1, "title": "B1", "parentId": b}, {"id": c, "title": "C"}]})
    steps = {s["id"]: s for s in as_listed(created)}
    moved = [steps[a], steps[a1], {**steps[b1], "parentId": a}, steps[b], steps[c]]
    revised, changed = revised_and_kinds(plans, created, numbered(moved))
    assert changed == {b1}
    assert [s["title"] for s in revised["steps"] if s["parentId"] == a] == ["A1", "B1"]


def test_siblings_on_the_same_number_show_in_id_order_and_move_only_when_that_order_changes(plans):
    first, second = "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"
    created = apply(plans, "roadmap.create", {"title": "Plan", "steps": [
        {"id": first, "title": "First"}, {"id": second, "title": "Second"}]})
    # Both on 0: still First, then Second, so neither moved and the save records nothing.
    tied = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Plan",
                                           "steps": [{**s, "position": 0} for s in as_listed(created)]})
    assert tied["revision"] == 1 and [s["title"] for s in tied["steps"]] == ["First", "Second"]
    assert {s["position"] for s in stored(plans, created["id"]).values()} == {0}
    # Second now first: one of the two moved, the one now first.
    swapped = [{**s, "position": 1 if s["id"] == first else 0} for s in as_listed(tied)]
    revised, changed = revised_and_kinds(plans, tied, swapped)
    assert changed == {second} and [s["title"] for s in revised["steps"]] == ["Second", "First"]


def test_positions_left_out_count_the_retired_steps_listed_too(plans):
    created, a, b, c = three(plans)
    retired = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Plan",
                                              "steps": [s for s in steps_of(created) if s["id"] != a]})
    # As `roadmap show` lists them, retired A first, with no positions: B is 1 and C is 2.
    listed = [{"id": s["id"], "title": s["title"], "description": s["description"], "parentId": s["parentId"],
               "retired": s["retired"]} for s in sorted(retired["steps"], key=lambda s: s["title"])]
    revised = apply(plans, "roadmap.revise", {"id": created["id"], "revision": retired["revision"], "title": "Plan", "steps": listed})
    assert revised["revision"] == retired["revision"] and len(revised["versions"]) == len(retired["versions"])
    assert {sid: (row["position"], row["revision"], row["retired"]) for sid, row in stored(plans, created["id"]).items()} == {
        a: (0, 2, 1), b: (1, 1, 0), c: (2, 1, 0)}


def test_numbering_afresh_alone_stores_the_numbers_and_records_nothing(plans):
    created, a, b, c = three(plans)
    revised = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Plan",
                                              "steps": [{**s, "position": 10 * (s["position"] + 1)} for s in steps_of(created)]})
    assert revised["revision"] == 1 and len(revised["versions"]) == 1
    assert kinds(plans, created["id"]) == ["roadmap.create"]
    assert {sid: (row["position"], row["revision"]) for sid, row in stored(plans, created["id"]).items()} == {
        a: (10, 1), b: (20, 1), c: (30, 1)}


def random_save(rng, roadmap):
    """A save as a person or an agent might send it: steps retired with what is under them, one
    restored, new ones, new titles, one moved to another branch, the order changed or not, and the
    positions numbered afresh, with gaps, or loosely with ties."""
    steps = as_listed(roadmap)

    def under(root):
        found = {root}
        while True:
            more = {s["id"] for s in steps if s["parentId"] in found} - found
            if not more:
                return found
            found |= more

    if steps and rng.random() < 0.3:
        gone = under(rng.choice(steps)["id"])
        steps = [s for s in steps if s["id"] not in gone]
    retired = [s for s in roadmap["steps"] if s["retired"]]
    if retired and rng.random() < 0.3:
        back = rng.choice(retired)
        steps.append({**{k: back[k] for k in TREE}, "parentId": rng.choice([None, *(s["id"] for s in steps)])})
    for _ in range(rng.randint(0, 2)):
        steps.insert(rng.randint(0, len(steps)), {"id": uid(), "title": "New", "description": "",
                                                  "parentId": rng.choice([None, *(s["id"] for s in steps)]), "position": 0})
    for s in rng.sample(steps, k=min(len(steps), rng.randint(0, 2))):
        s["title"] += "!"
    if steps and rng.random() < 0.3:
        s = rng.choice(steps)
        s["parentId"] = rng.choice([None, *(o["id"] for o in steps if o["id"] not in under(s["id"]))])
    order = rng.random()
    if order < 0.3 and len(steps) > 1:
        steps.insert(rng.randrange(len(steps)), steps.pop(rng.randrange(len(steps))))
    elif order < 0.45:
        rng.shuffle(steps)
    numbering = rng.random()
    for index, s in enumerate(steps):
        s["position"] = (index if numbering < 0.5 else index * rng.randint(1, 3) if numbering < 0.8
                         else rng.randint(0, len(steps)))
    return steps


def test_over_many_random_saves_a_step_keeps_its_revision_exactly_when_the_save_keeps_it(plans):
    """Seeded, so a failure repeats. A step whose revision a save leaves alone was active and stays
    so, with its parent, its text and its order among the siblings that stay; every other stored step
    it touched takes one more revision; the roadmap's event names exactly the steps raised or added;
    and the plan shows in the order sent, ties going to the lower ID."""
    rng = random.Random(4115)
    for _ in range(40):
        ids = [uid() for _ in range(rng.randint(2, 9))]
        current = apply(plans, "roadmap.create", {"title": "Random", "steps": [
            {"id": sid, "title": f"S{n}", "parentId": rng.choice([None, *ids[:n]]) if n else None,
             "position": rng.randint(0, 9)} for n, sid in enumerate(ids)]})
        for _ in range(4):
            sent = random_save(rng, current)
            before = stored(plans, current["id"])
            revised = apply(plans, "roadmap.revise", {"id": current["id"], "revision": current["revision"],
                                                      "title": "Random", "steps": sent})
            after = stored(plans, current["id"])
            raised = {sid for sid in before if after[sid]["revision"] != before[sid]["revision"]}
            assert all(after[sid]["revision"] == before[sid]["revision"] + 1 for sid in raised)
            kept = [sid for sid in before if sid not in raised and not before[sid]["retired"]]
            for sid in kept:
                fields = ("retired", "parent_id", "title", "description")
                assert [after[sid][f] for f in fields] == [before[sid][f] for f in fields], sent
            for parent in {before[sid]["parent_id"] for sid in kept}:
                group = [sid for sid in kept if before[sid]["parent_id"] == parent]
                assert (sorted(group, key=lambda sid: (before[sid]["position"], sid))
                        == sorted(group, key=lambda sid: (after[sid]["position"], sid))), sent
            for parent in {s["parentId"] for s in sent}:
                sent_order = [s["id"] for s in sorted((s for s in sent if s["parentId"] == parent),
                                                       key=lambda s: (s["position"], s["id"]))]
                shown = [s["id"] for s in revised["steps"] if not s["retired"] and s["parentId"] == parent]
                assert shown == sent_order, sent
            added = set(after) - set(before)
            if len(revised["versions"]) > len(current["versions"]):
                event = next(e for e in events(plans, current["id"]) if e["kind"] == "roadmap.revise"
                             and e["after"]["versionNumber"] == revised["versions"][-1]["number"])
                assert set(event["after"]["changedStepIds"]) == raised | added
            else:
                assert raised | added == set()
            current = revised
