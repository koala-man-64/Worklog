"""Receipts stay small (audit finding B4, roadmap step 2.5).

mutate stored the whole answer of every keyed write: 2,780 receipts held 45.5 MB, the largest 1.2 MB
each, the answers to approvals and roadmap saves. The routes of proposals, roadmaps and steps now pass
a reader: an answer over RECEIPT_LIMIT is stored as what identifies its record, and a replay reads
that record as it now stands. A replay still applies nothing twice, and a receipt stored whole, as
every one before this change was, answers as before."""

import json
import logging
import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from worklog import app as app_module
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        c.db_path = tmp_path / "worklog.sqlite3"
        yield c


def call(c, method, path, body=None, key=None, agent=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    response = c.request(method, "/api/v1" + path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def scalar(c, sql, *parameters):
    with closing(sqlite3.connect(c.db_path)) as conn:
        return conn.execute(sql, parameters).fetchone()[0]


def receipt(c, key):
    return json.loads(scalar(c, "SELECT response_json FROM receipts WHERE key=?", key))


def big_items(count=60, size=2000):
    return [{"action": "item.create", "payload": {"title": f"Item {n}", "description": "d" * size}} for n in range(count)]


@pytest.fixture
def agent(client):
    return call(client, "POST", "/credentials", {"label": "Planner"})


def test_replaying_an_approval_answers_the_proposal_and_applies_nothing_twice(client, agent):
    draft = call(client, "POST", "/proposals", {"title": "Big", "rationale": "", "operations": big_items()}, agent=agent)
    submitted = call(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, agent=agent)
    key = str(uuid.uuid4())
    approved = call(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]}, key=key)
    assert len(json.dumps(approved)) > app_module.RECEIPT_LIMIT
    stored = receipt(client, key)
    assert set(stored) == {app_module.RECEIPT_MARK}
    assert len(json.dumps(stored)) < 1024
    items, events = scalar(client, "SELECT count(*) FROM work_items"), scalar(client, "SELECT count(*) FROM events")
    replayed = call(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]}, key=key)
    assert replayed == approved
    assert (scalar(client, "SELECT count(*) FROM work_items"), scalar(client, "SELECT count(*) FROM events")) == (items, events)


def test_replaying_a_roadmap_save_answers_the_roadmap_as_it_stands(client):
    steps = [{"id": str(uuid.uuid4()), "title": f"Step {n}", "description": "s" * 800} for n in range(120)]
    roadmap = call(client, "POST", "/roadmaps", {"title": "Plan", "steps": steps})
    key = str(uuid.uuid4())
    body = {"revision": roadmap["revision"], "title": "Plan 2", "steps": steps}
    saved = call(client, "POST", f"/roadmaps/{roadmap['id']}/revisions", body, key=key)
    assert set(receipt(client, key)) == {app_module.RECEIPT_MARK}
    versions = scalar(client, "SELECT count(*) FROM roadmap_versions WHERE roadmap_id=?", roadmap["id"])
    assert call(client, "POST", f"/roadmaps/{roadmap['id']}/revisions", body, key=key) == saved
    assert scalar(client, "SELECT count(*) FROM roadmap_versions WHERE roadmap_id=?", roadmap["id"]) == versions


def test_a_receipt_stored_whole_answers_as_before(client, agent):
    draft = call(client, "POST", "/proposals", {"title": "Small", "rationale": "", "operations": big_items(1, 10)},
                 agent=agent)
    submitted = call(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, agent=agent)
    key = str(uuid.uuid4())
    approved = call(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]}, key=key)
    assert receipt(client, key) == approved
    assert call(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]}, key=key) == approved


def test_no_receipt_of_the_three_families_passes_the_limit(client, agent):
    steps = [{"id": str(uuid.uuid4()), "title": f"Step {n}", "description": "s" * 800} for n in range(120)]
    parent = call(client, "POST", "/roadmaps", {"title": "Parent"})
    roadmap = call(client, "POST", "/roadmaps", {"title": "Plan", "steps": steps})
    roadmap = call(client, "POST", f"/roadmaps/{roadmap['id']}/revisions",
                   {"revision": roadmap["revision"], "title": "Plan 2", "steps": steps})
    roadmap = call(client, "POST", f"/roadmaps/{roadmap['id']}/move", {"revision": roadmap["revision"], "parentId": parent["id"]})
    roadmap = call(client, "POST", f"/roadmaps/{roadmap['id']}/archive", {"revision": roadmap["revision"]})
    roadmap = call(client, "POST", f"/roadmaps/{roadmap['id']}/recover", {"revision": roadmap["revision"]})
    roadmap = call(client, "POST", f"/roadmaps/{roadmap['id']}/restore", {"revision": roadmap["revision"], "number": 1})
    step = steps[0]["id"]
    current = call(client, "GET", f"/steps/{step}")
    current = call(client, "PATCH", f"/steps/{step}", {"revision": current["revision"], "description": "t" * 200_000})
    call(client, "PATCH", f"/steps/{step}/progress", {"revision": current["revision"], "status": "doing"})
    draft = call(client, "POST", "/proposals", {"title": "Big", "rationale": "", "operations": big_items()}, agent=agent)
    draft = call(client, "PATCH", f"/proposals/{draft['id']}",
                 {"revision": draft["revision"], "operations": big_items(70)}, agent=agent)
    submitted = call(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, agent=agent)
    call(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]})
    assert scalar(client, "SELECT max(length(response_json)) FROM receipts") <= app_module.RECEIPT_LIMIT


def test_a_route_without_a_reader_stores_its_answer_whole_and_says_so(client, caplog):
    item = call(client, "POST", "/work-items", {"title": "Notes"})
    key = str(uuid.uuid4())
    with caplog.at_level(logging.WARNING, logger="worklog"):
        note = call(client, "POST", "/notes", {"ownerType": "item", "ownerId": item["id"], "title": "Long",
                                               "text": "n" * (app_module.RECEIPT_LIMIT + 10)}, key=key)
    assert receipt(client, key) == note
    assert "/api/v1/notes" in caplog.text
