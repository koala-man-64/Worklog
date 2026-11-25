"""The proposal list returns counts, not operations (audit finding P2, roadmap step 3.4).

GET /proposals returned every proposal whole: 12.6 MB for the default page of 50 and 25.8 MB for the
136 approved ones, where Review's rows read only the number of changes and `worklog agent proposals`
printed it all. Now each listed proposal carries its title, status, dates, author and
operationCount; its rationale, operations and evidence come from GET /proposals/{id}, and the write
routes still answer the whole proposal."""

import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
LISTED = {"id", "title", "status", "revision", "autoApproved", "supersedesId", "actorId", "actorLabel",
          "actorDirectory", "createdAt", "submittedAt", "updatedAt", "operationCount"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, testing=True), base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def call(c, method, path, body=None, agent=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    response = c.request(method, "/api/v1" + path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_listed_proposal_carries_its_count_and_not_its_operations(client):
    agent = call(client, "POST", "/credentials", {"label": "Planner"})
    operations = [{"action": "item.create", "payload": {"title": f"New {n}", "description": "d" * 5_000}}
                  for n in range(3)]
    written = call(client, "POST", "/proposals", {"title": "Plan", "rationale": "r" * 5_000, "operations": operations},
                   agent=agent)
    # The write still answers the whole proposal.
    assert len(written["operations"]) == 3 and written["rationale"] == "r" * 5_000
    [listed] = call(client, "GET", "/proposals?status=draft", agent=agent)["items"]
    assert set(listed) == LISTED
    assert (listed["id"], listed["title"], listed["operationCount"], listed["status"]) == (written["id"], "Plan", 3, "draft")
    assert (listed["actorId"], listed["revision"], listed["submittedAt"]) == (written["actorId"], written["revision"], None)
    # One proposal's read has the rest.
    shown = call(client, "GET", f"/proposals/{written['id']}", agent=agent)
    assert len(shown["operations"]) == 3 and shown["rationale"] == "r" * 5_000


def test_a_proposal_whose_stored_operations_are_not_a_list_counts_none(client, tmp_path):
    # The count is read in SQL; operations that are not valid JSON must not fail the list.
    agent = call(client, "POST", "/credentials", {"label": "Planner"})
    written = call(client, "POST", "/proposals", {"title": "Plan", "rationale": "", "operations": [
        {"action": "item.create", "payload": {"title": "New"}}]}, agent=agent)
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        conn.execute("UPDATE proposals SET operations='not json' WHERE id=?", (written["id"],))
        conn.commit()
    [listed] = call(client, "GET", "/proposals?status=draft", agent=agent)["items"]
    assert listed["operationCount"] == 0
