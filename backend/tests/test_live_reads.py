"""Live and the registry read less, less often (audit finding P7, roadmap step 3.7).

/live loaded every step, work item, run, participation, update and discussion message whole on
each call, and the page polls it: a pulse carried a message's whole text. Now a Live read selects
the columns it shows, with no step description or outcome, work item description or roadmap
overview, and a pulse or an agent's latest note is cut to about 300 characters."""

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog import db as database
from worklog import live
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
LONG = "word " * 80_000  # 400 KB


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, testing=True), base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def call(c, method, path, body=None, token=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if token:
        headers["Authorization"] = "Bearer " + token
    response = c.request(method, "/api/v1" + path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def world(client):
    """A roadmap step and work item with long text, an agent at work on the item that posted a long
    update, and a 400 KB discussion message."""
    step = str(uuid.uuid4())
    roadmap = call(client, "POST", "/roadmaps", {"title": "Plan", "overview": LONG,
                                                 "steps": [{"id": step, "title": "Step", "description": LONG}]})
    item = call(client, "POST", "/work-items", {"title": "Work", "description": LONG, "roadmapId": roadmap["id"],
                                                "stepId": step})
    agent = call(client, "POST", "/credentials", {"label": "Talker"})
    repository = call(client, "POST", "/repositories", {"name": "Worklog"})
    run = call(client, "POST", "/agent-runs", {"clientRunKey": "live", "displayName": "Talking"}, agent["token"])
    participation = call(client, "POST", "/participations", {"runId": run["id"], "itemId": item["id"],
                                                             "contexts": [{"repositoryId": repository["id"]}]},
                         agent["token"])
    call(client, "POST", f"/participations/{participation['id']}/updates", {"kind": "update", "text": "note " * 800},
         agent["token"])
    call(client, "POST", "/discussions", {"title": "Long", "text": LONG, "contexts": [{"type": "item", "id": item["id"]}]},
         agent["token"])
    return roadmap, item


def test_a_live_read_stays_small_with_a_400_kb_message_present(client, world):
    response = client.get("/api/v1/live")
    assert response.status_code == 200, response.text
    assert len(response.content) < 50_000, len(response.content)
    answer = response.json()
    assert answer["pulses"] and all(len(pulse["summary"]) <= live.BRIEF for pulse in answer["pulses"])
    notes = [agent["latestNote"] for agent in answer["agents"] if agent["latestNote"]]
    assert notes and all(len(note) <= live.BRIEF for note in notes)


def test_a_live_read_reads_no_step_work_or_roadmap_text(client, world, monkeypatch):
    statements: list[str] = []
    connect = database.Database.connect

    def traced(self):
        conn = connect(self)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(database.Database, "connect", traced)
    assert client.get("/api/v1/live").status_code == 200
    whole = [sql for sql in statements if re.search(r"SELECT (rowid AS _rowid,)?\* FROM (steps|work_items|roadmaps)\b", sql)]
    assert not whole, whole
    text = [sql for sql in statements if re.search(r"FROM (steps|work_items|roadmaps)\b", sql)
            and re.search(r"\b(description|outcome|overview)\b", sql.split(" FROM ")[0])]
    assert not text, text
