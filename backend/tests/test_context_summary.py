"""Agent context is a summary by default (audit finding P3, roadmap step 3.5; Rudy's answer of
2026-11-24: yes, with --full for today's shape).

The read every agent starts with returned 326,030 characters, roughly 80,000 tokens: status updates
39% (the latest 100, each whole), roadmap overviews 27%, work item descriptions 16%. Now GET /context
answers a summary by default: work items by id, title, status and rank; roadmaps by id, title and
open questions; people by id and name; the latest ten updates cut to one line. detail=full answers
today's shape."""

import json
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog import cli
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
LONG = "Long text that an agent's first read does not need. " * 40  # about 2 KB


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """The audit's scale: 31 work items, 22 roadmaps, 100 updates, each with long text."""
    with TestClient(create_app(tmp_path_factory.mktemp("context"), testing=True), base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]

        def call(method, path, body=None, agent=None):
            headers = {"Idempotency-Key": str(uuid.uuid4())}
            if agent:
                headers["Authorization"] = "Bearer " + agent["token"]
            response = c.request(method, "/api/v1" + path, json=body, headers=headers)
            assert response.status_code == 200, response.text
            return response.json()

        for n in range(31):
            call("POST", "/work-items", {"title": f"Work {n}", "description": LONG})
        for n in range(22):
            roadmap = call("POST", "/roadmaps", {"title": f"Plan {n}", "overview": LONG, "steps": []})
        call("POST", "/questions", {"roadmapId": roadmap["id"], "title": "Which parser?"})
        call("POST", "/people", {"name": "Alice", "notes": LONG})
        agent = call("POST", "/credentials", {"label": "Reporter"})
        repository = call("POST", "/repositories", {"name": "Worklog"})
        run = call("POST", "/agent-runs", {"clientRunKey": "context", "displayName": "Reporting"}, agent)
        participation = call("POST", "/participations", {"runId": run["id"], "contexts": [{"repositoryId": repository["id"]}]},
                             agent)
        for n in range(100):
            call("POST", f"/participations/{participation['id']}/updates",
                 {"kind": "update", "text": f"Update {n}\nsecond line\n" + LONG}, agent)
        c.agent = agent
        c.roadmap = roadmap
        yield c


def read(c, query=""):
    response = c.get("/api/v1/context" + query, headers={"Authorization": "Bearer " + c.agent["token"]})
    assert response.status_code == 200, response.text
    return response.json(), len(response.content)


def test_the_default_context_is_a_summary_under_40_kb(world):
    summary, size = read(world)
    assert size < 40_000, size
    assert {key for item in summary["workItems"]["items"] for key in item} == {"id", "title", "status", "rank"}
    assert summary["workItems"]["total"] == 31
    roadmaps = summary["roadmaps"]["items"]
    assert len(roadmaps) == 22 and {key for r in roadmaps for key in r} == {"id", "title", "openQuestions"}
    assert next(r["openQuestions"] for r in roadmaps if r["id"] == world.roadmap["id"]) == 1
    assert summary["people"]["items"][0].keys() == {"id", "name"}
    updates = summary["updates"]
    assert updates["total"] == 100 and len(updates["items"]) == 10
    latest = updates["items"][0]
    assert latest.keys() == {"id", "kind", "text", "authorLabel", "participationId", "createdAt"}
    # One line, cut short.
    assert latest["text"].startswith("Update 99") and "\n" not in latest["text"] and len(latest["text"]) <= 200
    # The rest of the context keeps its shape.
    for key in ("day", "repositories", "runs", "participations", "tickets", "resources", "proposals"):
        assert key in summary


def test_detail_full_answers_the_whole_context(world):
    full, size = read(world, "?detail=full")
    assert size > 200_000
    assert full["workItems"]["items"][0]["description"].strip() == LONG.strip()
    assert {r["overview"].strip() for r in full["roadmaps"]["items"]} == {LONG.strip()}
    assert len(full["updates"]["items"]) == 100 and full["updates"]["items"][0]["text"].endswith(LONG.strip())


def test_summary_skips_detail_columns_and_preserves_the_work_page(world, monkeypatch):
    # This fixture has no day entries. A planned day's entry includes its own full work item.
    detail_columns = {("work_items", "description"), ("roadmaps", "overview"), ("people", "notes")}
    reads = set()
    db = world.app.state.db
    connect = db.connect

    def observe(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ and (table, column) in detail_columns:
            reads.add((table, column))
        return sqlite3.SQLITE_OK

    def observed_connect():
        connection = connect()
        connection.set_authorizer(observe)
        return connection

    monkeypatch.setattr(db, "connect", observed_connect)
    query = "?limit=2&offset=1"
    full, _ = read(world, query + "&detail=full")
    assert reads == detail_columns
    reads.clear()
    summary, _ = read(world, query)
    assert not reads
    assert summary["workItems"] == {
        "items": [{key: item[key] for key in ("id", "title", "status", "rank")} for item in full["workItems"]["items"]],
        "total": full["workItems"]["total"],
    }


def test_an_unknown_detail_is_refused(world):
    response = world.get("/api/v1/context?detail=everything", headers={"Authorization": "Bearer " + world.agent["token"]})
    assert response.status_code == 422


def test_the_cli_asks_for_the_full_context_with_full(monkeypatch, capsys):
    asked = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, *args, **kwargs):
            asked.append(path)
            return {}

    monkeypatch.setattr(cli, "AgentClient", Client)
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", "test-token")
    for arguments in (["context"], ["context", "--full"]):
        cli.main(["agent", *arguments])
        json.loads(capsys.readouterr().out or "{}")
    assert asked == ["/context", "/context?detail=full"]
