"""Events record what changed, not whole records (audit finding B3, roadmap step 2.3, decision K6 as
Rudy revised it on 2026-11-24).

Every proposal transition used to write the whole proposal before and after, operations included, so
an approved proposal was stored about six times: 149 MB of the 524 MB file. A transition now records
a summary on both sides, proposal.update its after-state whole and its before-state as the summary,
and a roadmap's archive, recovery or move the fields that changed. proposal.create keeps the
content, and proposal.stale stays whole."""

import json
import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
SUMMARY = {"id", "title", "status", "revision", "submittedAt", "autoApproved", "operationCount"}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        c.db_path = tmp_path / "worklog.sqlite3"
        yield c


def write(c, method, path, payload=None, agent=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    response = c.request(method, "/api/v1" + path, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def events(c, entity_id, kind=None):
    with closing(sqlite3.connect(c.db_path)) as conn:
        rows = conn.execute("SELECT kind,before_json,after_json FROM events WHERE entity_id=? ORDER BY rowid",
                            (entity_id,)).fetchall()
    found = [(k, json.loads(b) if b else None, json.loads(a) if a else None) for k, b, a in rows]
    return [e for e in found if kind is None or e[0] == kind]


def stored_bytes(c, entity_id):
    with closing(sqlite3.connect(c.db_path)) as conn:
        return conn.execute("SELECT coalesce(sum(length(before_json)+length(after_json)),0) FROM events WHERE entity_id=?",
                            (entity_id,)).fetchone()[0]


def items(count, size):
    return [{"action": "item.create", "payload": {"title": f"Item {n}", "description": "d" * size}} for n in range(count)]


def drafted(c, agent, operations, title="Plan the parser"):
    return write(c, "POST", "/proposals", {"title": title, "rationale": "Why", "operations": operations}, agent)


def advance(c, proposal, verb, agent=None):
    return write(c, "POST", f"/proposals/{proposal['id']}/{verb}", {"revision": proposal["revision"]}, agent)


@pytest.fixture
def agent(client):
    return write(client, "POST", "/credentials", {"label": "Planner"})


def test_each_transition_records_a_summary_on_both_sides(client, agent):
    draft = drafted(client, agent, items(3, 10))
    submitted = advance(client, draft, "submit", agent)
    reopened = advance(client, submitted, "reopen")
    resubmitted = advance(client, reopened, "submit", agent)
    advance(client, resubmitted, "approve")
    other = advance(client, drafted(client, agent, items(1, 10), "Other"), "submit", agent)
    advance(client, other, "reject")
    withdrawn = drafted(client, agent, items(1, 10), "Third")
    advance(client, withdrawn, "withdraw", agent)
    for proposal in (draft, other, withdrawn):
        for kind, before, after in events(client, proposal["id"]):
            if kind in {"proposal.submit", "proposal.approve", "proposal.reject", "proposal.withdraw", "proposal.reopen"}:
                assert set(before) == SUMMARY and set(after) == SUMMARY, kind
    approve = events(client, draft["id"], "proposal.approve")[0]
    assert approve[1]["status"] == "submitted" and approve[2]["status"] == "approved"
    assert approve[2]["operationCount"] == 3 and approve[2]["title"] == "Plan the parser"


def test_an_edit_keeps_its_new_content_and_summarises_the_old(client, agent):
    draft = drafted(client, agent, items(2, 10))
    edited = write(client, "PATCH", f"/proposals/{draft['id']}", {"revision": draft["revision"], "operations": items(4, 10)},
                   agent)
    [(_, before, after)] = events(client, draft["id"], "proposal.update")
    assert set(before) == SUMMARY and before["operationCount"] == 2
    assert after["operations"] == edited["operations"] and len(after["operations"]) == 4


def test_creating_a_proposal_keeps_its_content(client, agent):
    draft = drafted(client, agent, items(2, 10))
    [(_, before, after)] = events(client, draft["id"], "proposal.create")
    assert before is None and after["operations"] == draft["operations"]


def test_submitting_and_approving_a_megabyte_proposal_adds_under_2_kb(client, agent):
    draft = drafted(client, agent, items(100, 10_000))
    created = stored_bytes(client, draft["id"])
    assert created > 1_000_000
    advance(client, advance(client, draft, "submit", agent), "approve")
    assert stored_bytes(client, draft["id"]) - created < 2048


def test_archiving_recovering_and_moving_a_roadmap_record_what_changed(client):
    steps = [{"id": str(uuid.uuid4()), "title": f"Step {n}", "description": "x" * 1000} for n in range(20)]
    parent = write(client, "POST", "/roadmaps", {"title": "Parent"})
    roadmap = write(client, "POST", "/roadmaps", {"title": "Plan", "steps": steps})
    moved = write(client, "POST", f"/roadmaps/{roadmap['id']}/move", {"revision": roadmap["revision"], "parentId": parent["id"]})
    archived = write(client, "POST", f"/roadmaps/{roadmap['id']}/archive", {"revision": moved["revision"]})
    write(client, "POST", f"/roadmaps/{roadmap['id']}/recover", {"revision": archived["revision"]})
    [(_, before, after)] = events(client, roadmap["id"], "roadmap.move")
    assert before == {"id": roadmap["id"], "title": "Plan", "parentId": None, "revision": roadmap["revision"]}
    assert after == {"id": roadmap["id"], "title": "Plan", "parentId": parent["id"], "revision": moved["revision"]}
    [(_, before, after)] = events(client, roadmap["id"], "roadmap.archive")
    assert before == {"id": roadmap["id"], "title": "Plan", "archived": False, "revision": moved["revision"]}
    assert after == {"id": roadmap["id"], "title": "Plan", "archived": True, "revision": archived["revision"]}
    [(_, before, after)] = events(client, roadmap["id"], "roadmap.recover")
    assert (before["archived"], after["archived"]) == (True, False) and "steps" not in after
