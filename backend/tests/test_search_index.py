"""Keeping search current costs one scan of the index per roadmap save, not one per step.

The index's entity_id and owner_id columns are FTS5 UNINDEXED, so each DELETE that
filters on them reads the whole index. A structure save of a large roadmap used to run
one such scan per step, inside the write lock.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
STEPS = 40


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def write(c, method, path, body):
    response = c.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def index_deletes(monkeypatch):
    """Every DELETE on the search index from any connection opened from here on."""
    statements: list[str] = []
    connect = Database.connect

    def traced(self):
        conn = connect(self)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(Database, "connect", traced)
    return lambda: [s for s in statements if s.lstrip().upper().startswith("DELETE FROM SEARCH_INDEX")]


def found(c, archived=False):
    result = c.get("/api/v1/search", params={"q": "quokka", "archived": archived, "limit": 200}).json()
    return result["total"], sorted(r["id"] for r in result["items"])


def test_a_structure_save_scans_the_index_once_and_keeps_one_row_per_step(client, monkeypatch):
    steps = [{"id": str(uuid.uuid4()), "title": f"Quokka step {n}", "description": "", "parentId": None, "position": n}
             for n in range(STEPS)]
    roadmap = write(client, "POST", "/roadmaps", {"title": "Large plan", "steps": steps})
    deletes = index_deletes(monkeypatch)
    steps[0]["title"] = "Quokka step 0, renamed"
    write(client, "POST", f"/roadmaps/{roadmap['id']}/revisions",
          {"title": "Large plan", "revision": roadmap["revision"], "steps": steps})
    # One bulk delete of the roadmap's rows; before, one more full scan per roadmap and step.
    assert len(deletes()) == 1
    total, ids = found(client)
    assert total == STEPS and ids == sorted(s["id"] for s in steps)
    renamed = client.get("/api/v1/search", params={"q": "renamed"}).json()["items"]
    assert [r["id"] for r in renamed] == [steps[0]["id"]]


def test_archive_and_recover_keep_one_row_per_step(client):
    steps = [{"id": str(uuid.uuid4()), "title": f"Quokka step {n}", "description": "", "parentId": None, "position": n}
             for n in range(3)]
    roadmap = write(client, "POST", "/roadmaps", {"title": "Small plan", "steps": steps})
    archived = write(client, "POST", f"/roadmaps/{roadmap['id']}/archive", {"revision": roadmap["revision"]})
    assert found(client)[0] == 0
    assert found(client, archived=True)[0] == 3
    write(client, "POST", f"/roadmaps/{roadmap['id']}/recover", {"revision": archived["revision"]})
    assert found(client) == (3, sorted(s["id"] for s in steps))
