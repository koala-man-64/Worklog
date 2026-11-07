"""A roadmap save writes only what it changes (consolidation step 4.1, K4, findings B-02 and REM-13).

A step the save leaves as it is keeps its revision and gets no event. A save that changes nothing
records no revision, version or event. The roadmap's event names its version and the steps changed,
where it held two copies of the tree. Progress raises only the step's revision, and a proposal that
revises a roadmap and then sets a step's progress expects one more only for a step the revise changes."""

import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.common import read_events, uid
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
