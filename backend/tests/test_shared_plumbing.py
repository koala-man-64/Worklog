"""Wire behaviour the shared plumbing of consolidation step 2.1 must keep: archive and recover of
every supporting record, each search box's query rules, and reads taken from one snapshot."""

import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from worklog import search
from worklog.app import create_app
from worklog.common import rows_in
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
SUPPORTING = ["notes", "tasks", "references", "attachments", "links", "chats", "github", "questions", "decisions"]


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def write(c, method, path, payload=None, **kwargs):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **kwargs.pop("headers", {})}
    if payload is not None:
        kwargs["json"] = payload
    return c.request(method, "/api/v1" + path, headers=headers, **kwargs)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def supporting_record(c, kind):
    item = ok(write(c, "POST", "/work-items", {"title": "Plumbing"}))
    roadmap = ok(write(c, "POST", "/roadmaps", {"title": "Plan", "overview": "", "steps": [{"title": "One"}]}))
    owner = {"ownerType": "item", "ownerId": item["id"]}
    if kind == "attachments":
        return ok(write(c, "POST", f"/attachments?ownerType=item&ownerId={item['id']}&name=notes.txt",
                        content=b"hello", headers={"Content-Type": "text/plain"}))
    payload = {
        "notes": {**owner, "title": "Note"},
        "tasks": {"itemId": item["id"], "text": "Task"},
        "references": {"itemId": item["id"], "path": "src/app.py"},
        "links": {**owner, "url": "https://example.com/spec"},
        "chats": {**owner, "chatId": str(uuid.uuid4()), "title": "Chat"},
        "github": {**owner, "kind": "pr", "url": "https://github.com/example/repo/pull/7"},
        "questions": {"roadmapId": roadmap["id"], "title": "Open question"},
        "decisions": {"roadmapId": roadmap["id"], "title": "Decision"},
    }[kind]
    return ok(write(c, "POST", "/" + kind, payload))


@pytest.mark.parametrize("kind", SUPPORTING)
def test_every_supporting_record_archives_and_recovers_over_http(client, kind):
    record = supporting_record(client, kind)
    archived = ok(write(client, "POST", f"/{kind}/{record['id']}/archive", {"revision": record["revision"]}))
    assert archived["archived"] is True
    recovered = ok(write(client, "POST", f"/{kind}/{record['id']}/recover", {"revision": archived["revision"]}))
    assert recovered["archived"] is False
    # A stale revision changes nothing.
    stale = write(client, "POST", f"/{kind}/{record['id']}/archive", {"revision": record["revision"]})
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize("path", ["/tickets", "/resources", "/discussions"])
def test_these_search_boxes_refuse_a_query_over_2000_bytes(client, path):
    refused = client.get("/api/v1" + path, params={"q": "x" * 2001})
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "validation"
    assert client.get("/api/v1" + path, params={"q": "x" * 2000}).status_code == 200


@pytest.mark.parametrize("path", ["/work-items", "/people", "/search"])
def test_these_search_boxes_take_a_query_of_any_length(client, path):
    assert client.get("/api/v1" + path, params={"q": "word " * 1000}).status_code == 200


@pytest.mark.parametrize("path", ["/work-items", "/people", "/search", "/tickets", "/resources", "/discussions"])
def test_a_query_matches_words_by_prefix_and_ignores_punctuation(client, path):
    # Every search box takes up to 30 words and matches each as a prefix; quotes and operators are words' edges.
    assert client.get("/api/v1" + path, params={"q": 'pars* "fix" OR -import AND (x)'}).status_code == 200
    assert client.get("/api/v1" + path, params={"q": "!!! ..."}).status_code == 200


def test_unknown_commands_on_supporting_records_find_no_route(client):
    # Before step 2.1 one generic route caught these and answered with Worklog's error envelope;
    # now each record has literal archive and recover routes, and anything else finds no route.
    note = supporting_record(client, "notes")
    assert write(client, "POST", f"/notes/{note['id']}/delete", {"revision": note["revision"]}).status_code == 404
    assert write(client, "POST", "/nothing/here/archive", {"revision": 1}).status_code == 404
    # A path another method serves answers that the method is wrong.
    roadmap = ok(write(client, "POST", "/roadmaps", {"title": "Plan", "overview": "", "steps": [{"title": "One"}]}))
    step = roadmap["steps"][0]["id"]
    assert write(client, "POST", f"/steps/{step}/progress", {"revision": 1}).status_code == 405


def test_a_search_box_matches_each_word_as_a_prefix(client):
    item = ok(write(client, "POST", "/work-items", {"title": "Parser revision handling"}))
    ok(write(client, "POST", "/work-items", {"title": "Unrelated chores"}))
    found = ok(client.get("/api/v1/work-items", params={"q": "pars REVIS", "view": "all"}))
    assert [w["id"] for w in found["items"]] == [item["id"]]
    assert ok(client.get("/api/v1/search", params={"q": "pars revis"}))["items"][0]["id"] == item["id"]
    assert ok(client.get("/api/v1/search", params={"q": "pars nothing"}))["items"] == []


def test_prefix_match_keeps_words_and_drops_everything_else():
    assert search.prefix_match("Parser fix") == '"Parser"* AND "fix"*'
    assert search.prefix_match('pars* "fix" OR -import (x)') == '"pars"* AND "fix"* AND "OR"* AND "import"* AND "x"*'
    assert search.prefix_match("Größe café") == '"Größe"* AND "café"*'
    assert search.prefix_match("!!! ... ''") is None
    assert search.prefix_match("") is None
    many = " ".join(f"w{n}" for n in range(40))
    assert search.prefix_match(many).count("*") == search.MAX_TERMS


def test_rows_in_batches_dedupes_and_keeps_each_ids_rows_in_order(tmp_path):
    with closing(sqlite3.connect(tmp_path / "rows.sqlite3")) as conn:
        conn.execute("CREATE TABLE t(owner TEXT, value INTEGER)")
        conn.executemany("INSERT INTO t VALUES(?,?)", [(f"o{n}", v) for n in range(450) for v in (2, 1)])
        ids = [f"o{n}" for n in range(450)] + ["o0", "o1"]
        rows = rows_in(conn, "SELECT owner,value FROM t WHERE owner IN ({}) ORDER BY owner,value", ids, size=100)
        assert len(rows) == 900
        by_owner: dict[str, list[int]] = {}
        for owner, value in rows:
            by_owner.setdefault(owner, []).append(value)
        assert all(values == [1, 2] for values in by_owner.values()) and len(by_owner) == 450
        assert rows_in(conn, "SELECT owner FROM t WHERE owner IN ({})", []) == []


def test_a_snapshot_read_holds_one_transaction_and_keeps_nothing_it_writes(tmp_path):
    db = Database(tmp_path / "worklog.sqlite3")
    db.initialize()

    def read_then_write(conn):
        assert conn.in_transaction
        conn.execute("INSERT INTO settings VALUES('snapshotProbe','1')")
        return conn.execute("SELECT count(*) FROM settings WHERE key='snapshotProbe'").fetchone()[0]

    assert db.snapshot(read_then_write) == 1
    with db.read() as conn:
        assert conn.execute("SELECT count(*) FROM settings WHERE key='snapshotProbe'").fetchone()[0] == 0


def test_no_route_is_shadowed_by_one_registered_before_it(tmp_path):
    # Literal routes replace a catch-all that had to come last; nothing now checks registration order but this.
    import re

    from fastapi.routing import APIRoute
    from starlette.routing import Match

    app = create_app(tmp_path, testing=True)
    for route in [r for r in app.router.routes if isinstance(r, APIRoute)]:
        path = re.sub(r"\{[^}]+\}", "placeholder", route.path)
        for method in route.methods:
            scope = {"type": "http", "path": path, "root_path": "", "method": method}
            first = next(r for r in app.router.routes if r.matches(scope)[0] == Match.FULL)
            assert first is route, f"{method} {route.path} is reached by {getattr(first, 'path', first)} first"


def test_an_archive_route_needs_its_revision_replays_under_its_key_and_refuses_agents(client):
    note = supporting_record(client, "notes")
    assert write(client, "POST", f"/notes/{note['id']}/archive", {}).status_code == 422
    key = str(uuid.uuid4())
    first = ok(write(client, "POST", f"/notes/{note['id']}/archive", {"revision": note["revision"]}, headers={"Idempotency-Key": key}))
    assert ok(write(client, "POST", f"/notes/{note['id']}/archive", {"revision": note["revision"]}, headers={"Idempotency-Key": key})) == first
    agent = ok(write(client, "POST", "/credentials", {"label": "Agent"}))
    refused = write(client, "POST", f"/notes/{note['id']}/recover", {"revision": first["revision"]},
                    headers={"Authorization": "Bearer " + agent["token"]})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "agent_scope"


def test_a_discussion_read_refuses_a_credential_revoked_after_the_request_was_let_in(client, monkeypatch):
    agent = ok(write(client, "POST", "/credentials", {"label": "Agent"}))
    snapshot = Database.snapshot

    def revoke_first(self, fn):
        # The middleware has already accepted the credential; it goes before the read begins.
        with self.transaction() as conn:
            conn.execute("UPDATE credentials SET revoked=1 WHERE id=?", (agent["id"],))
        return snapshot(self, fn)

    monkeypatch.setattr(Database, "snapshot", revoke_first)
    refused = client.get("/api/v1/discussions", headers={"Authorization": "Bearer " + agent["token"]})
    assert refused.status_code == 401, refused.text
    assert refused.json()["error"]["code"] == "authentication"
