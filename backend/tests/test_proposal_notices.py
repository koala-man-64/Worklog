"""A notice to a proposal's agent never fails the person's write (audit findings C1 and C2, roadmap
step 1.4).

A plan change, an approval and the person's reopening each tell the agents whose proposals they
leave stale, in a discussion. That notice is posted inside the person's own write, so a notice that
cannot be posted must not refuse the write: its title is the proposal's cut by bytes behind a
prefix, it carries at most TAG_LIMIT tags, and one that still fails is rolled back to its own
savepoint, logged and recorded, so it is not tried again on every later write. A database error is
not swallowed: a busy or full database is never reported as saved."""

import logging
import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.common import AppError
from worklog.discussions import TAG_LIMIT, DiscussionService

ORIGIN = "http://127.0.0.1:8877"


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


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def submit(c, agent, title, operations):
    drafted = ok(write(c, "POST", "/proposals", {"title": title, "rationale": "", "operations": operations}, agent))
    return ok(write(c, "POST", f"/proposals/{drafted['id']}/submit", {"revision": drafted["revision"]}, agent))


def progress(sid, status="doing", revision=1):
    return {"action": "progress.set", "payload": {"id": sid, "revision": revision, "status": status}}


def plan(c, steps=2):
    ids = [str(uuid.uuid4()) for _ in range(steps)]
    roadmap = ok(write(c, "POST", "/roadmaps", {"title": "Plan", "steps": [
        {"id": sid, "title": f"Step {n}"} for n, sid in enumerate(ids)]}))
    agent = ok(write(c, "POST", "/credentials", {"label": "Planner"}))
    return roadmap, ids, agent


def notices(c):
    """The notices posted to agents, oldest first: title, tags and the proposal each names."""
    with closing(sqlite3.connect(c.db_path)) as conn:
        return [(title, conn.execute("SELECT count(*) FROM discussion_contexts WHERE discussion_id=?", (did,)).fetchone()[0])
                for did, title in conn.execute(
                    "SELECT id,title FROM discussions WHERE title LIKE 'Plan changed%' OR title LIKE 'Proposal returned%' "
                    "ORDER BY created_at,rowid")]


def recorded(c, proposal_id):
    with closing(sqlite3.connect(c.db_path)) as conn:
        rows = conn.execute("SELECT count(*) FROM proposal_stale_notices WHERE proposal_id=?", (proposal_id,)).fetchone()[0]
        events = conn.execute("SELECT count(*) FROM events WHERE kind='proposal.stale' AND entity_id=?",
                              (proposal_id,)).fetchone()[0]
        return rows, events


def step_status(c, sid):
    return ok(c.get(f"/api/v1/steps/{sid}"))["status"]


# A thousand bytes each: plain, two-byte, and three-byte characters that a cut by characters or in
# the middle of one would break.
TITLES = {"plain": "p" * 1000, "two-byte": "é" * 500, "three-byte": "—" * 333 + "x"}


@pytest.mark.parametrize("title", TITLES.values(), ids=TITLES.keys())
def test_the_person_changes_a_step_a_proposal_with_a_1000_byte_title_names(client, title):
    _, ids, agent = plan(client)
    waiting = submit(client, agent, title, [progress(ids[0], "done")])
    ok(write(client, "PATCH", f"/steps/{ids[0]}/progress", {"revision": 1, "status": "blocked"}))
    assert step_status(client, ids[0]) == "blocked"
    [(notice, _)] = notices(client)
    assert notice.startswith("Plan changed — re-review proposal: ")
    assert len(notice.encode("utf-8")) <= 1000
    assert title.startswith(notice.removeprefix("Plan changed — re-review proposal: "))
    assert recorded(client, waiting["id"]) == (1, 1)


def test_the_person_reopens_a_proposal_that_names_31_steps(client):
    _, ids, agent = plan(client, steps=TAG_LIMIT + 1)
    proposal = submit(client, agent, "Start every step", [progress(sid) for sid in ids])
    reopened = ok(write(client, "POST", f"/proposals/{proposal['id']}/reopen", {"revision": proposal["revision"]}))
    assert reopened["status"] == "draft"
    assert notices(client) == [("Proposal returned to draft: Start every step", TAG_LIMIT)]


def test_the_person_approves_while_another_waiting_proposal_has_a_990_byte_title(client):
    _, ids, agent = plan(client)
    approved = submit(client, agent, "Start step 0", [progress(ids[0])])
    waiting = submit(client, agent, "w" * 990, [progress(ids[0], "done")])
    current = ok(client.get(f"/api/v1/proposals/{approved['id']}"))
    assert ok(write(client, "POST", f"/proposals/{approved['id']}/approve", {"revision": current["revision"]}))["status"] == "approved"
    assert step_status(client, ids[0]) == "doing"
    [(notice, _)] = notices(client)
    assert len(notice.encode("utf-8")) == 1000
    assert recorded(client, waiting["id"]) == (1, 1)


def test_a_notice_that_cannot_be_posted_leaves_the_write_applied_and_is_recorded(client, monkeypatch, caplog):
    _, ids, agent = plan(client)
    waiting = submit(client, agent, "Finish step 0", [progress(ids[0], "done")])

    def refuse(*args, **kwargs):
        raise AppError(422, "validation", "Invalid or oversized title", {"title": "invalid"})

    monkeypatch.setattr(DiscussionService, "create", refuse)
    with caplog.at_level(logging.WARNING, logger="worklog"):
        ok(write(client, "PATCH", f"/steps/{ids[0]}/progress", {"revision": 1, "status": "blocked"}))
    assert step_status(client, ids[0]) == "blocked"
    assert notices(client) == []
    # Recorded, so the next write does not try it again; no proposal.stale event, which says a
    # notice was posted.
    assert recorded(client, waiting["id"]) == (1, 0)
    assert waiting["id"] in caplog.text
    ok(write(client, "PATCH", f"/steps/{ids[1]}/progress", {"revision": 1, "status": "doing"}))
    assert recorded(client, waiting["id"]) == (1, 0)


def test_a_reopening_whose_notice_cannot_be_posted_still_returns_the_proposal_to_draft(client, monkeypatch):
    _, ids, agent = plan(client)
    proposal = submit(client, agent, "Start step 0", [progress(ids[0])])

    def refuse(*args, **kwargs):
        raise AppError(422, "validation", "A discussion holds at most 30 tags", {"contexts": "limit"})

    monkeypatch.setattr(DiscussionService, "create", refuse)
    reopened = ok(write(client, "POST", f"/proposals/{proposal['id']}/reopen", {"revision": proposal["revision"]}))
    assert reopened["status"] == "draft"
    assert notices(client) == []


def test_a_database_error_in_a_notice_still_fails_the_write(client, monkeypatch):
    _, ids, agent = plan(client)
    submit(client, agent, "Finish step 0", [progress(ids[0], "done")])

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(DiscussionService, "create", busy)
    response = write(client, "PATCH", f"/steps/{ids[0]}/progress", {"revision": 1, "status": "blocked"})
    assert response.status_code >= 500, response.text
    assert step_status(client, ids[0]) == "open"
