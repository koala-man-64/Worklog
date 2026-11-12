"""Schema v13 bound chats to identities (decision K3): an identity was bound to the chat it
registered with, and to each chat where the person confirmed it, and what the person wrote into a
chat reached bound identities only. v30 drops messaging and confirmations (step 0.7 of
docs/ROADMAPS-RETHINK.md). What stays: the upgrade from v12, the way back to it, which older
upgrade tests build their databases with, and the shapes of v12 and v13, which can't claim each
other's version. The helpers here are shared with other tests of agents and their chats."""

import secrets
import sqlite3
import uuid
from contextlib import closing, contextmanager

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
CHAT = "3f6b8d0a-2c4e-4a61-9b7d-5e8f0a1b2c3d"
STAMP = "2026-01-01T00:00:00+00:00"
FAR = "2999-01-01T00:00:00+00:00"
# From version 13 to version 12, with Worklog stopped and after a manual backup. The README gave this way back
# while version 13 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
BEGIN;
UPDATE chat_message_deliveries SET expire_reason=NULL WHERE expire_reason='unconfirmed';
DROP TABLE chat_confirmations;
PRAGMA user_version=12;
COMMIT;
"""


def to_v13(path):
    """Take a current database back to the v13 shape the documented way."""
    from test_task_order import to_v13 as from_v14

    from_v14(path)


def to_v12(path):
    """Take a current database back to the v12 shape the documented way: to version 13 first."""
    to_v13(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


@contextmanager
def person(directory):
    """The person's browser session on an app over this data directory; agents add their token."""
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


def request(c, method, path, payload=None, agent=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def post(c, path, payload=None, agent=None):
    return ok(request(c, "POST", path, payload or {}, agent))


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]


def issue(c, label):
    """A credential the person issues in Settings."""
    return post(c, "/credentials", {"label": label})


def register(c, sponsor, label, chat=CHAT):
    """An identity an agent registers for itself, with the chat it works in."""
    token = secrets.token_urlsafe(32)
    return {**post(c, "/agent-registrations", {"label": label, "token": token, "chatId": chat}, sponsor), "token": token}


def tracked_chat(c, chat_id=CHAT, title="Parser work"):
    item = post(c, "/work-items", {"title": "Import format"})
    return post(c, "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": chat_id, "title": title})


def run(c, credential, chat_id=None, name="Implementation"):
    """A run that reports the chat given, or by default the chat its identity registered with."""
    payload = {"clientRunKey": str(uuid.uuid4()), "displayName": name}
    if chat_id is not None:
        payload["externalChatId"] = chat_id
    return post(c, "/agent-runs", payload, credential)


def message(conn, chat_id, text, deliveries):
    """A message as v4 to v29 kept it, with each run's delivery: (run ID, status, expire reason)."""
    identity = str(uuid.uuid4())
    conn.execute("INSERT INTO chat_messages(id,chat_id,text,author_id,author_label,created_at) VALUES(?,?,?,'local-user','You',?)",
                 (identity, chat_id, text, STAMP))
    conn.executemany("INSERT INTO chat_message_deliveries(message_id,run_id,status,expires_at,expire_reason) VALUES(?,?,?,?,?)",
                     [(identity, run_id, status, FAR, reason) for run_id, status, reason in deliveries])


def test_the_upgrade_from_v12_ends_with_no_messages_and_keeps_every_other_record(tmp_path):
    # v13 ended a delivery waiting for an identity bound to no chat it was sent to; v30 drops them all.
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        sponsor = issue(c, "Shared agents")
        child = register(c, sponsor, "Planner (Claude)")
        claimant = issue(c, "Other agents")
        chat = tracked_chat(c)
        bound, claim = run(c, child), run(c, claimant, CHAT)
    to_v12(path)
    with closing(sqlite3.connect(path)) as conn:
        message(conn, chat["id"], "Waiting", [(bound["id"], "pending", None), (claim["id"], "pending", None)])
        conn.commit()
        kept = conn.execute("SELECT * FROM agent_runs ORDER BY id").fetchall()
    recovery.validate_database(path)
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'chat_message%' OR name='chat_confirmations'").fetchone()[0] == 0
        assert conn.execute("SELECT * FROM agent_runs ORDER BY id").fetchall() == kept
    snapshot, = (tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
        assert conn.execute("SELECT status FROM chat_message_deliveries").fetchall() == [("pending",), ("pending",)]


def test_the_way_back_to_v12_gives_its_exact_shape_and_upgrades_again(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        claimant = issue(c, "Other agents")
        chat = tracked_chat(c)
        claim = run(c, claimant, CHAT)
    to_v13(path)
    # A delivery ended by removing a confirmation, and a confirmation, as v13 kept them.
    with closing(sqlite3.connect(path)) as conn:
        message(conn, chat["id"], "Ended by removing the confirmation", [(claim["id"], "expired", "unconfirmed")])
        conn.execute("INSERT INTO chat_confirmations(chat_id,agent_id,confirmed_by,confirmed_by_label,created_at) "
                     "VALUES(?,?,'local-user','You',?)", (CHAT, claimant["agentId"], STAMP))
        conn.commit()
        conn.executescript(WAY_BACK)
    recovery.validate_database(path)
    with Database(path).read() as conn:
        assert recovery._schema_signature(conn) == sealed.shape(12)
        # Version 12 has no 'unconfirmed' reason, so that delivery reads as plainly expired there.
        assert [tuple(r) for r in conn.execute("SELECT status,expire_reason FROM chat_message_deliveries")] == [("expired", None)]
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()


def test_a_v12_shape_cannot_claim_version_13_and_a_v13_shape_cannot_claim_12(tmp_path):
    older, current = tmp_path / "older.sqlite3", tmp_path / "current.sqlite3"
    Database(older).initialize()
    to_v12(older)
    recovery.validate_database(older)
    Database(current).initialize()
    to_v13(current)
    recovery.validate_database(current)
    for path, version in ((older, 13), (current, 12)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
