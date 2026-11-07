"""One step's text, without saving the whole roadmap (step.update, decision K15).

The person changes it at PATCH /steps/{id}, and an agent proposes it. Either way it is checked against
the step's own revision and writes that step alone: no other step's row, revision, event or search
row changes, and no version is recorded. The roadmap's revision rises with it, so a structure save
drafted before it is refused rather than writing the old text back, while other agents' changes to
other steps stay current."""

import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
FIELDS = ("id", "title", "description", "parentId", "position")


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert boot.status_code == 200, boot.text
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        c.db_path = tmp_path / "worklog.sqlite3"
        yield c


def write(c, method, path, payload=None, agent=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def read(c, path, agent=None):
    return c.get("/api/v1" + path, headers={"Authorization": "Bearer " + agent["token"]} if agent else {})


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def refused(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text
    return response.json()["error"]


def submit(c, agent, title, operations):
    drafted = ok(write(c, "POST", "/proposals", {"title": title, "rationale": "", "operations": operations}, agent))
    submitted = ok(write(c, "POST", f"/proposals/{drafted['id']}/submit", {"revision": drafted["revision"]}, agent))
    assert submitted["status"] == "submitted"
    return submitted


def approve(c, proposal):
    current = ok(read(c, f"/proposals/{proposal['id']}"))
    return write(c, "POST", f"/proposals/{proposal['id']}/approve", {"revision": current["revision"]})


def change(action, **payload):
    return {"action": action, "payload": payload}


def snapshot(c, roadmap_id):
    """Every stored step of the roadmap, its events and search row, the roadmap, its versions, and
    the work items and notes beside them."""
    with closing(sqlite3.connect(c.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        steps = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM steps WHERE roadmap_id=?", (roadmap_id,))}
        return {
            "steps": steps,
            "events": {sid: [tuple(e) for e in conn.execute(
                "SELECT id,kind,actor_kind FROM events WHERE entity_id=? ORDER BY created_at,id", (sid,))] for sid in steps},
            "search": {r["entity_id"]: tuple(r) for r in conn.execute(
                "SELECT entity_id,title,body,owner_id,archived FROM search_index WHERE type='step' AND owner_id=?", (roadmap_id,))},
            "roadmap": dict(conn.execute("SELECT * FROM roadmaps WHERE id=?", (roadmap_id,)).fetchone()),
            "versions": conn.execute("SELECT count(*) FROM roadmap_versions WHERE roadmap_id=?", (roadmap_id,)).fetchone()[0],
            "items": [tuple(r) for r in conn.execute("SELECT * FROM work_items ORDER BY id")],
            "notes": [tuple(r) for r in conn.execute("SELECT * FROM notes ORDER BY id")],
        }


def written(before, after):
    """The steps whose stored row, events or search row differ."""
    return {sid for sid in after["steps"]
            if before["steps"].get(sid) != after["steps"][sid]
            or before["events"].get(sid, []) != after["events"][sid]
            or before["search"].get(sid) != after["search"].get(sid)}


def new_events(before, after, sid):
    return [e[1:] for e in after["events"][sid] if e not in before["events"].get(sid, [])]


@pytest.fixture
def plan(client):
    """A        (0)
         A1     (1)  doing
         A2     (2)            note
       B        (3)
         B1     (4)  done      work item
       C        (5)  blocked
    """
    ids = {name: str(uuid.uuid4()) for name in ("A", "A1", "A2", "B", "B1", "C")}
    parent = {"A1": "A", "A2": "A", "B1": "B"}
    roadmap = ok(write(client, "POST", "/roadmaps", {"title": "Plan", "overview": "Why", "steps": [
        {"id": ids[n], "title": n, "description": f"About {n}", "parentId": ids.get(parent.get(n, ""))}
        for n in ("A", "A1", "A2", "B", "B1", "C")]}))
    for name, status in (("A1", "doing"), ("B1", "done"), ("C", "blocked")):
        ok(write(client, "PATCH", f"/steps/{ids[name]}/progress", {"revision": 1, "status": status}))
    ok(write(client, "POST", "/work-items", {"title": "Ship B1", "stepId": ids["B1"]}))
    ok(write(client, "POST", "/notes", {"ownerType": "step", "ownerId": ids["A2"], "text": "Context for A2"}))
    agents = {label: ok(write(client, "POST", "/credentials", {"label": label})) for label in ("Planner", "Tracker", "Syncer")}
    return {"id": roadmap["id"], "ids": ids, **agents}


def step(client, sid):
    return ok(read(client, f"/steps/{sid}"))


def roadmap(client, plan):
    return ok(read(client, f"/roadmaps/{plan['id']}"))


def test_the_person_changes_one_steps_text_and_nothing_else(client, plan):
    ids = plan["ids"]
    before = snapshot(client, plan["id"])
    result = ok(write(client, "PATCH", f"/steps/{ids['A2']}", {"revision": 1, "description": "AB#1559 is the predecessor"}))
    after = snapshot(client, plan["id"])
    assert (result["id"], result["roadmapId"], result["title"], result["revision"]) == (ids["A2"], plan["id"], "A2", 2)
    assert result["description"] == "AB#1559 is the predecessor"
    assert written(before, after) == {ids["A2"]}
    old, new = before["steps"][ids["A2"]], after["steps"][ids["A2"]]
    assert {k for k in new if new[k] != old[k]} == {"description", "revision"}
    assert new_events(before, after, ids["A2"]) == [("step.update", "human")]
    assert after["search"][ids["A2"]][2] == "AB#1559 is the predecessor"
    # The roadmap's revision rises; its title, overview and versions stay as they were.
    assert after["roadmap"]["revision"] == before["roadmap"]["revision"] + 1
    assert (after["roadmap"]["title"], after["roadmap"]["overview"]) == ("Plan", "Why")
    assert after["versions"] == before["versions"]
    assert (after["items"], after["notes"]) == (before["items"], before["notes"])
    found = ok(read(client, "/search?q=AB%231559"))["items"]
    assert [(r["id"], r["type"], r["routeId"]) for r in found] == [(ids["A2"], "step", plan["id"])]


def test_a_title_alone_keeps_the_description_and_is_trimmed(client, plan):
    ids = plan["ids"]
    result = ok(write(client, "PATCH", f"/steps/{ids['C']}", {"revision": 2, "title": "  C, renamed  "}))
    assert (result["title"], result["description"], result["status"]) == ("C, renamed", "About C", "blocked")


def test_text_the_step_already_has_records_nothing(client, plan):
    ids = plan["ids"]
    before = snapshot(client, plan["id"])
    result = ok(write(client, "PATCH", f"/steps/{ids['A']}", {"revision": 1, "title": " A ", "description": "About A"}))
    assert result["revision"] == 1
    after = snapshot(client, plan["id"])
    assert written(before, after) == set() and after["roadmap"] == before["roadmap"]


@pytest.mark.parametrize("body, status, code", [
    ({"revision": 1}, 422, "validation"),
    ({"revision": 1, "title": None, "description": None}, 422, "validation"),
    ({"revision": 1, "title": "   "}, 422, "validation"),
    ({"revision": 1, "title": "x" * 1001}, 422, "validation"),
    ({"revision": 1, "title": "A", "position": 3}, 422, "validation"),
    ({"revision": 7, "title": "A, renamed"}, 409, "conflict"),
])
def test_refusals_change_nothing(client, plan, body, status, code):
    before = snapshot(client, plan["id"])
    refused(write(client, "PATCH", f"/steps/{plan['ids']['A']}", body), status, code)
    assert snapshot(client, plan["id"]) == before


def test_a_retired_step_or_one_on_an_archived_roadmap_cannot_change(client, plan):
    ids, current = plan["ids"], roadmap(client, plan)
    kept = [{k: s[k] for k in FIELDS} for s in current["steps"] if s["id"] != ids["C"]]
    revised = ok(write(client, "POST", f"/roadmaps/{plan['id']}/revisions",
                       {"revision": current["revision"], "title": "Plan", "overview": "Why", "steps": kept}))
    refused(write(client, "PATCH", f"/steps/{ids['C']}", {"revision": step(client, ids["C"])["revision"], "title": "C2"}), 409, "conflict")
    ok(write(client, "POST", f"/roadmaps/{plan['id']}/archive", {"revision": revised["revision"]}))
    refused(write(client, "PATCH", f"/steps/{ids['A']}", {"revision": 1, "title": "A2"}), 409, "conflict")
    refused(write(client, "PATCH", f"/steps/{uuid.uuid4()}", {"revision": 1, "title": "A2"}), 404, "not_found")


def test_an_agent_proposes_it_and_approval_writes_only_that_step(client, plan):
    ids = plan["ids"]
    refused(write(client, "PATCH", f"/steps/{ids['A2']}", {"revision": 1, "title": "Mine"}, plan["Planner"]), 403, "agent_scope")
    proposal = submit(client, plan["Planner"], "Record the predecessor",
                      [change("step.update", id=ids["A2"], revision=1, description="AB#1559, for #3072 only")])
    before = snapshot(client, plan["id"])
    assert ok(approve(client, proposal))["status"] == "approved"
    after = snapshot(client, plan["id"])
    assert written(before, after) == {ids["A2"]}
    assert new_events(before, after, ids["A2"]) == [("step.update", "agent")]
    assert after["versions"] == before["versions"]
    with closing(sqlite3.connect(client.db_path)) as conn:
        assert conn.execute("SELECT target_type,target_id FROM proposal_targets WHERE proposal_id=?",
                            (proposal["id"],)).fetchall() == [("roadmap", plan["id"])]
    applied = ok(read(client, f"/roadmaps/{plan['id']}/proposals"))["items"]
    assert [(p["id"], p["changes"]) for p in applied] == [(proposal["id"], [{"number": 1, "action": "step.update"}])]


def test_other_agents_changes_to_other_steps_stay_current(client, plan):
    ids = plan["ids"]
    progress = submit(client, plan["Tracker"], "Unblock C", [change("progress.set", id=ids["C"], revision=2, status="doing")])
    other = submit(client, plan["Syncer"], "Sync B", [change("step.update", id=ids["B"], revision=1, description="ADO rev 7")])
    mine = submit(client, plan["Planner"], "Note the predecessor", [change("step.update", id=ids["A2"], revision=1, description="AB#1559")])
    assert ok(approve(client, mine))["status"] == "approved"
    assert ok(approve(client, other))["status"] == "approved"
    assert ok(approve(client, progress))["status"] == "approved"
    current = {s["id"]: s for s in roadmap(client, plan)["steps"]}
    assert (current[ids["A2"]]["description"], current[ids["B"]]["description"], current[ids["C"]]["status"]) == (
        "AB#1559", "ADO rev 7", "doing")


def test_a_revise_drafted_before_it_is_refused_rather_than_writing_the_old_text_back(client, plan):
    ids, current = plan["ids"], roadmap(client, plan)
    steps = [{k: s[k] for k in FIELDS} for s in current["steps"]]
    steps = [{**s, "title": "B, renamed"} if s["id"] == ids["B"] else s for s in steps]
    revise = submit(client, plan["Syncer"], "Rename B", [change(
        "roadmap.revise", id=plan["id"], revision=current["revision"], title="Plan", overview="Why", steps=steps)])
    edit = submit(client, plan["Planner"], "Note the predecessor", [change("step.update", id=ids["A2"], revision=1, description="AB#1559")])
    assert ok(approve(client, edit))["status"] == "approved"
    before = snapshot(client, plan["id"])
    refused(approve(client, revise), 409, "conflict")
    assert snapshot(client, plan["id"]) == before
    assert step(client, ids["A2"])["description"] == "AB#1559"
    # Its agent is told which change went stale.
    with closing(sqlite3.connect(client.db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM proposal_stale_notices WHERE proposal_id=?", (revise["id"],)).fetchone()[0] == 1


def test_progress_set_on_the_same_step_since_makes_it_stale(client, plan):
    """A step's progress and text share its revision, as every record's fields share its own."""
    ids = plan["ids"]
    edit = submit(client, plan["Planner"], "Rename A2", [change("step.update", id=ids["A2"], revision=1, title="A2, renamed")])
    ok(write(client, "PATCH", f"/steps/{ids['A2']}/progress", {"revision": 1, "status": "doing"}))
    refused(approve(client, edit), 409, "conflict")
    assert step(client, ids["A2"])["title"] == "A2"


def test_later_changes_in_one_proposal_expect_the_revisions_earlier_ones_leave(client, plan):
    ids, current = plan["ids"], roadmap(client, plan)
    steps = [{k: s[k] for k in FIELDS} for s in current["steps"]]
    steps = [{**s, "title": "B, renamed"} if s["id"] == ids["B"] else s for s in steps]
    proposal = submit(client, plan["Planner"], "Rename, start, then rename B", [
        change("step.update", id=ids["A2"], revision=1, title="A2, renamed"),
        change("progress.set", id=ids["A2"], revision=2, status="doing"),
        # The step.update raised the roadmap's revision, so the revise carries one more.
        change("roadmap.revise", id=plan["id"], revision=current["revision"] + 1, title="Plan", overview="Why",
               steps=[{**s, "title": "A2, renamed"} if s["id"] == ids["A2"] else s for s in steps])])
    with client.app.state.db.read() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal["id"],)).fetchone()
        assert client.app.state.proposals._stale_operations(conn, row) == []
    assert ok(approve(client, proposal))["status"] == "approved"
    after = {s["id"]: s for s in roadmap(client, plan)["steps"]}
    assert (after[ids["A2"]]["title"], after[ids["A2"]]["status"], after[ids["B"]]["title"]) == ("A2, renamed", "doing", "B, renamed")


def test_a_step_an_earlier_revise_adds_can_take_its_text_in_the_same_proposal(client, plan):
    current = roadmap(client, plan)
    new = str(uuid.uuid4())
    steps = [{k: s[k] for k in FIELDS} for s in current["steps"]] + [{"id": new, "title": "D", "position": 6}]
    proposal = submit(client, plan["Planner"], "Add D and describe it", [
        change("roadmap.revise", id=plan["id"], revision=current["revision"], title="Plan", overview="Why", steps=steps),
        change("step.update", id=new, revision=1, description="Why D")])
    with client.app.state.db.read() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal["id"],)).fetchone()
        assert client.app.state.proposals._stale_operations(conn, row) == []
    assert ok(approve(client, proposal))["status"] == "approved"
    assert (step(client, new)["description"], step(client, new)["revision"]) == ("Why D", 2)


def test_submit_refuses_a_value_the_operation_never_takes(client, plan):
    drafted = ok(write(client, "POST", "/proposals", {"title": "Bad", "rationale": "", "operations": [
        change("step.update", id=plan["ids"]["A"], revision=1, title=7)]}, plan["Planner"]))
    refused(write(client, "POST", f"/proposals/{drafted['id']}/submit", {"revision": drafted["revision"]}, plan["Planner"]), 422, "validation")
    unknown = write(client, "POST", "/proposals", {"title": "Bad", "rationale": "", "operations": [
        change("step.update", id=plan["ids"]["A"], revision=1, parentId=None)]}, plan["Planner"])
    assert refused(unknown, 422, "validation")["fields"]["unknown"] == ["parentId"]
