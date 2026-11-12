"""What a roadmap's status board reads besides the roadmap (step 1.3 of docs/ROADMAPS-RETHINK.md): the
submitted proposals waiting on it and its steps lately done, and each listed roadmap's open questions,
which the sidebar counts."""

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
PARSE, LEX, WRITE, PARENT, GONE = (str(uuid.uuid4()) for _ in range(5))


@contextmanager
def person(directory):
    app = create_app(directory, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


@pytest.fixture
def client(tmp_path):
    with person(tmp_path) as c:
        yield c


def send(c, method, path, payload=None, token=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if token:
        headers["Authorization"] = "Bearer " + token
    response = c.request(method, "/api/v1" + path, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def proposal(c, token, title, operations, submit=True):
    draft = send(c, "POST", "/proposals", {"title": title, "rationale": "", "operations": operations}, token)
    return send(c, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, token) if submit else draft


def progress(c, step, status):
    current = send(c, "GET", f"/steps/{step}")
    send(c, "PATCH", f"/steps/{step}/progress", {"revision": current["revision"], "status": status})


def test_the_board_names_the_proposals_waiting_on_the_roadmap(client, tmp_path):
    roadmap = send(client, "POST", "/roadmaps", {"title": "Exports", "steps": [{"id": PARSE, "title": "Parser"}]})
    other = send(client, "POST", "/roadmaps", {"title": "Imports"})
    loose = send(client, "POST", "/roadmaps", {"title": "Loose ends"})
    token = send(client, "POST", "/credentials", {"label": "Planner"})["token"]
    rid = roadmap["id"]
    # Each would change the roadmap or what it holds: a work item on it or on its step, a question
    # for it, its step's progress or text, a sub-roadmap under it, and a roadmap moved under it.
    expected = [proposal(client, token, title, [operation]) for title, operation in (
        ("Track the writer", {"action": "item.create", "payload": {"title": "Writer", "roadmapId": rid}}),
        ("Ask about CSV", {"action": "question.create", "payload": {"roadmapId": rid, "title": "CSV?"}}),
        ("Start the parser", {"action": "progress.set", "payload": {"id": PARSE, "revision": 1, "status": "doing"}}),
        ("Rename the parser", {"action": "step.update", "payload": {"id": PARSE, "revision": 1, "title": "Parse"}}),
        ("Track the lexer", {"action": "item.create", "payload": {"title": "Lexer", "stepId": PARSE}}),
        ("Plan the CLI", {"action": "roadmap.create", "payload": {"title": "CLI", "parentId": rid}}),
        ("Adopt loose ends", {"action": "roadmap.move", "payload": {"id": loose["id"], "revision": loose["revision"], "parentId": rid}}),
    )]
    # A change to another roadmap, a draft, and an approved or withdrawn proposal don't wait on it.
    proposal(client, token, "Elsewhere", [{"action": "question.create", "payload": {"roadmapId": other["id"], "title": "Why?"}}])
    proposal(client, token, "Not yet", [{"action": "item.create", "payload": {"title": "Later", "roadmapId": rid}}], submit=False)
    approved = proposal(client, token, "Done with", [{"action": "item.create", "payload": {"title": "Old", "roadmapId": rid}}])
    send(client, "POST", f"/proposals/{approved['id']}/approve", {"revision": approved["revision"]})
    withdrawn = proposal(client, token, "Changed my mind", [{"action": "item.create", "payload": {"title": "Gone", "roadmapId": rid}}])
    send(client, "POST", f"/proposals/{withdrawn['id']}/withdraw", {"revision": withdrawn["revision"]}, token)
    # Changes that can't be read count toward nothing, and the board still answers.
    broken = [proposal(client, token, f"Broken {n}", [{"action": "item.create", "payload": {"title": "X", "roadmapId": rid}}])
              for n in (1, 2)]
    unreadable = [1, {"action": 5}, {"action": "item.create", "payload": "x"}, {"action": "item.create", "payload": {"roadmapId": [rid]}}]
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        conn.execute("UPDATE proposals SET operations=? WHERE id=?", ("not json", broken[0]["id"]))
        conn.execute("UPDATE proposals SET operations=? WHERE id=?", (json.dumps(unreadable), broken[1]["id"]))
        conn.commit()
    board = send(client, "GET", f"/roadmaps/{rid}/board")
    assert [(p["id"], p["title"], p["status"], p["actorLabel"], p["operationCount"]) for p in board["waiting"]] == [
        (p["id"], p["title"], "submitted", "Planner", 1) for p in expected]
    assert all(p["submittedAt"] for p in board["waiting"])
    assert send(client, "GET", f"/roadmaps/{other['id']}/board")["waiting"][0]["title"] == "Elsewhere"
    # The board is the person's page: agents read the outline, a step and the context.
    refused = client.get(f"/api/v1/roadmaps/{roadmap['id']}/board", headers={"Authorization": "Bearer " + token})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "agent_scope", refused.text


def test_the_board_lists_the_leaves_lately_done_the_latest_first(client, tmp_path):
    steps = [{"id": PARENT, "title": "Read"}, {"id": PARSE, "title": "Parser", "parentId": PARENT},
             {"id": LEX, "title": "Lexer", "parentId": PARENT}, {"id": WRITE, "title": "Writer"}, {"id": GONE, "title": "Old"}]
    roadmap = send(client, "POST", "/roadmaps", {"title": "Exports", "steps": steps})
    for step in (LEX, PARSE, WRITE, GONE):
        progress(client, step, "done")
    progress(client, WRITE, "doing")  # reopened: no longer done
    roadmap = send(client, "GET", f"/roadmaps/{roadmap['id']}")
    send(client, "POST", f"/roadmaps/{roadmap['id']}/revisions", {"revision": roadmap["revision"], "title": "Exports", "steps": steps[:-1]})
    # The events' times decide the order: give the lexer the latest.
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        conn.execute("UPDATE events SET created_at='2999-01-01T00:00:00+00:00' WHERE entity_id=? AND kind='progress.set'", (LEX,))
        conn.commit()
    done = send(client, "GET", f"/roadmaps/{roadmap['id']}/board")["recentlyDone"]
    # The parent, done because its leaves are, the reopened step and the retired one are left out.
    assert [(s["id"], s["title"]) for s in done] == [(LEX, "Lexer"), (PARSE, "Parser")]
    assert done[0]["doneAt"] == "2999-01-01T00:00:00+00:00"


def test_recently_done_keeps_ten_and_counts_from_the_change_to_done(client, tmp_path):
    steps = [{"id": str(uuid.uuid4()), "title": f"Task {n}"} for n in range(12)]
    roadmap = send(client, "POST", "/roadmaps", {"title": "Exports", "steps": steps})
    for s in steps:
        progress(client, s["id"], "done")
    # Done again: a change that leaves a done step done doesn't make it lately done.
    progress(client, steps[0]["id"], "done")
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        for n, s in enumerate(steps):
            conn.execute("UPDATE events SET created_at=? WHERE entity_id=? AND kind='progress.set'",
                         (f"2026-11-{n + 1:02d}T00:00:00+00:00", s["id"]))
        conn.execute("UPDATE events SET created_at='2999-01-01T00:00:00+00:00' WHERE entity_id=? AND kind='progress.set'"
                     " AND json_extract(before_json,'$.status')='done'", (steps[0]["id"],))
        conn.commit()
    done = send(client, "GET", f"/roadmaps/{roadmap['id']}/board")["recentlyDone"]
    assert [s["title"] for s in done] == [f"Task {n}" for n in range(11, 1, -1)]


def test_each_listed_roadmap_counts_its_open_questions(client):
    roadmap = send(client, "POST", "/roadmaps", {"title": "Exports"})
    quiet = send(client, "POST", "/roadmaps", {"title": "Imports"})
    asked = [send(client, "POST", "/questions", {"roadmapId": roadmap["id"], "title": title}) for title in ("CSV?", "JSON?", "XML?", "YAML?")]
    send(client, "PATCH", f"/questions/{asked[2]['id']}", {"revision": asked[2]["revision"], "status": "answered", "answer": "No"})
    send(client, "POST", f"/questions/{asked[3]['id']}/archive", {"revision": asked[3]["revision"]})
    counts = {r["id"]: r["openQuestions"] for r in send(client, "GET", "/roadmaps")["items"]}
    assert counts == {roadmap["id"]: 2, quiet["id"]: 0}


def test_an_unknown_roadmap_has_no_board(client):
    response = client.get(f"/api/v1/roadmaps/{uuid.uuid4()}/board")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found", response.text
