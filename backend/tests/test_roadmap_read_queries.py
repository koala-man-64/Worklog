"""Roadmap and step reads skip what they don't return (audit finding P5, roadmap step 3.3).

The roadmap read listed its versions with SELECT *, reading every saved snapshot (28.9 MB on the
largest roadmap) to return each version's number, date and note, and read each step's notes and
files in two queries a step. The step read built the whole roadmap, every step's text included, to
return one step. Now the versions list selects its three columns, the steps' notes and files come in
two queries, and the step read takes the one step in full and only the others' places and states."""

import uuid

import pytest
from fastapi.testclient import TestClient
from worklog import db as database
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
STEPS = 500


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, testing=True), base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def call(c, method, path, body=None):
    response = c.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def traced(monkeypatch):
    """Every SQL statement the app's connections run while the list is being kept."""
    statements: list[str] = []
    keeping = {"on": False}
    connect = database.Database.connect

    def traced_connect(self):
        conn = connect(self)
        conn.set_trace_callback(lambda sql: keeping["on"] and statements.append(sql))
        return conn

    monkeypatch.setattr(database.Database, "connect", traced_connect)

    def run(read):
        statements.clear()
        keeping["on"] = True
        try:
            result = read()
        finally:
            keeping["on"] = False
        return result, list(statements)

    return run


@pytest.fixture
def plan(client):
    """A roadmap of 500 steps, 250 parents with a sub-step each, every step with long text, saved
    three times; a note and a file on a few steps."""
    steps = []
    for n in range(STEPS // 2):
        parent = str(uuid.uuid4())
        steps.append({"id": parent, "title": f"Parent {n}", "description": "p" * 2_000, "position": n})
        steps.append({"id": str(uuid.uuid4()), "title": f"Child {n}", "description": "c" * 2_000,
                      "parentId": parent, "position": 0})
    roadmap = call(client, "POST", "/roadmaps", {"title": "Large", "steps": steps})
    for number in (2, 3):
        roadmap = call(client, "POST", f"/roadmaps/{roadmap['id']}/revisions",
                       {"revision": roadmap["revision"], "title": f"Large {number}", "steps": steps})
    for step in steps[:3]:
        call(client, "POST", "/notes", {"ownerType": "step", "ownerId": step["id"], "title": "Note", "text": "n"})
    return roadmap, steps


def test_the_roadmap_read_runs_a_bounded_number_of_queries_and_reads_no_snapshot(client, plan, traced):
    roadmap, steps = plan
    read, statements = traced(lambda: call(client, "GET", f"/roadmaps/{roadmap['id']}"))
    assert len(read["steps"]) == STEPS and [v["number"] for v in read["versions"]] == [1, 2, 3]
    assert [n["title"] for s in read["steps"] for n in s["notes"]] == ["Note"] * 3
    # Two queries a step was over a thousand.
    assert len(statements) < 60, len(statements)
    assert not [sql for sql in statements if "roadmap_versions" in sql and "*" in sql]


def test_the_step_read_runs_a_bounded_number_of_queries_and_reads_one_steps_text(client, plan, traced):
    roadmap, steps = plan
    child = steps[1]
    read, statements = traced(lambda: call(client, "GET", f"/steps/{child['id']}"))
    assert (read["description"], read["roadmapId"], [a["id"] for a in read["ancestors"]]) == (
        "c" * 2_000, roadmap["id"], [steps[0]["id"]])
    assert len(statements) < 30, len(statements)
    assert not [sql for sql in statements if "roadmap_versions" in sql]
    # No query reads every step's text: the steps query selects the text of this step alone. (The
    # trace shows each statement with its values bound; one step read by its ID is fine.)
    whole = [sql for sql in statements if "FROM steps" in sql and ("SELECT *" in sql or "SELECT s.*" in sql)
             and "WHERE id='" not in sql]
    assert not whole, whole
