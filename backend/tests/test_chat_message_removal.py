"""Schema v30 removes messaging agents from a chat (decision R1 in docs/ROADMAPS-RETHINK.md, step
0.7): the person's send box, the confirmations of who works in a chat, their routes and the agent's
`inbox` command are gone. The upgrade drops the chat_messages, chat_message_deliveries and
chat_confirmations tables, every row with them, and changes no other record: the tracked chats,
the agents and their runs stay, and so does the history messages and confirmations wrote, which an
agent still never reads. The pre-migration backup keeps the tables, and the documented way back
gives v29's exact shape."""

import io
import json
import sqlite3
import uuid
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
import sealed
from test_chat_label_links import call, session
from test_sealed_schemas import contents
from worklog import cli, migrations, recovery
from worklog.db import Database
from worklog.doctor import doctor_database

# docs/UPGRADING.md: from version 30 to version 29, with Worklog stopped and after a manual backup.
# The tables and their indexes as version 29 had them.
WAY_BACK = """\
CREATE TABLE chat_messages(id TEXT PRIMARY KEY,chat_id TEXT NOT NULL REFERENCES agent_chats(id),text TEXT NOT NULL,author_id TEXT NOT NULL,author_label TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX chat_messages_chat ON chat_messages(chat_id,created_at,id);
CREATE TABLE chat_message_deliveries(message_id TEXT NOT NULL REFERENCES chat_messages(id),run_id TEXT NOT NULL REFERENCES agent_runs(id),status TEXT NOT NULL CHECK(status IN('pending','delivered','expired')),expires_at TEXT NOT NULL,delivered_at TEXT,expired_at TEXT,expire_reason TEXT,PRIMARY KEY(message_id,run_id));
CREATE INDEX chat_message_deliveries_run ON chat_message_deliveries(run_id,status);
CREATE TABLE chat_confirmations(chat_id TEXT NOT NULL,agent_id TEXT NOT NULL REFERENCES communication_agents(id),confirmed_by TEXT NOT NULL,confirmed_by_label TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(chat_id,agent_id));
PRAGMA user_version=29;
"""
TABLES = ("chat_messages", "chat_message_deliveries", "chat_confirmations")
KINDS = ("chat.message", "chat.message.deliver", "chat.confirm")
CHAT = "session-1"
STAMP = "2026-11-01T00:00:00+00:00"


def to_v29(path):
    """Take a current database back to v29 the documented way."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def worked(data):
    """A current database with a tracked chat and an agent whose open run reports it, and their IDs."""
    client = session(data)
    try:
        agent = call(client, "POST", "/credentials", {"label": "Builder"})
        item = call(client, "POST", "/work-items", {"title": "Fix the parser"})
        chat = call(client, "POST", "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": CHAT, "title": "Parser"})
        run = call(client, "POST", "/agent-runs", {"clientRunKey": "parser", "displayName": "Parser", "externalChatId": CHAT},
                   headers={"Authorization": "Bearer " + agent["token"]})
    finally:
        client.__exit__(None, None, None)
    ids = {"agent": agent["agentId"], "token": agent["token"], "chat": chat["id"], "run": run["id"]}
    return data / "worklog.sqlite3", ids


def messaging(conn, ids):
    """A message the person wrote into the chat, its delivery to the agent's run, the person's
    confirmation of the agent there, and the history they wrote, which stays. Returns the message's ID."""
    message = str(uuid.uuid4())
    conn.execute("INSERT INTO chat_messages(id,chat_id,text,author_id,author_label,created_at) "
                 "VALUES(?,?,'Rebase first','local-user','You',?)", (message, ids["chat"], STAMP))
    conn.execute("INSERT INTO chat_message_deliveries(message_id,run_id,status,expires_at,delivered_at) "
                 "VALUES(?,?,'delivered',?,?)", (message, ids["run"], STAMP, STAMP))
    conn.execute("INSERT INTO chat_confirmations(chat_id,agent_id,confirmed_by,confirmed_by_label,created_at) "
                 "VALUES(?,?,'local-user','You',?)", (CHAT, ids["agent"], STAMP))
    for kind, entity, after in (("chat.message", message, {"id": message, "text": "Rebase first"}),
                                ("chat.message.deliver", message, {"runIds": [ids["run"]], "deliveredAt": STAMP}),
                                ("chat.confirm", ids["chat"], {"chatId": CHAT, "agentId": ids["agent"]})):
        conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,before_json,"
                     "after_json) VALUES(?,?,?,'local-user','You','human',?,'2026-11-01','null',?)",
                     (str(uuid.uuid4()), entity, kind, STAMP, json.dumps(after)))
    return message


def test_the_upgrade_drops_every_message_delivery_and_confirmation_and_changes_no_other_record(tmp_path):
    database, ids = worked(tmp_path / "data")
    to_v29(database)
    with closing(sqlite3.connect(database)) as conn:
        messaging(conn, ids)
        conn.commit()
    recovery.validate_database(database)
    _, _, before = contents(database)
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    assert [len(before[table]) for table in TABLES] == [1, 1, 1]
    assert not set(TABLES) & set(after)
    # Every other table keeps every row: the tracked chat, the agent, its run and the history.
    assert after == {table: rows for table, rows in before.items() if table not in TABLES}
    assert sorted(row[2] for row in after["events"] if row[2] in KINDS) == sorted(KINDS)
    # Nothing is left for the doctor to find, and an export holds no messages.
    assert doctor_database(database)["ok"] is True
    names = zipfile.ZipFile(io.BytesIO(recovery.export_database(database))).namelist()
    assert not [name for name in names if any(table in name for table in TABLES)]
    snapshot, = (tmp_path / "data" / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 29
        assert [conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES] == [1, 1, 1]


def test_the_way_back_gives_v29s_exact_shape_and_upgrades_again(tmp_path):
    database, _ = worked(tmp_path / "data")
    to_v29(database)
    recovery.validate_database(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 29
        assert recovery._schema_signature(conn) == sealed.shape(29)
    Database(database).initialize()
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    # The SQL tested here is the SQL docs/UPGRADING.md gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "docs" / "UPGRADING.md").read_text(encoding="utf-8")


def test_the_routes_are_gone_and_a_chat_names_its_agents_by_their_runs(tmp_path):
    _, ids = worked(tmp_path / "data")
    client = session(tmp_path / "data")
    try:
        agent = {"Authorization": "Bearer " + ids["token"]}
        chat = f"/chats/{ids['chat']}"
        tie = {"chatId": CHAT, "agentId": ids["agent"]}
        for method, path, body in (("GET", chat + "/messages", None), ("POST", chat + "/messages", {"text": "Rebase first"}),
                                   ("POST", chat + "/confirmations", {"agentId": ids["agent"]}),
                                   ("POST", chat + "/confirmations/remove", {"agentId": ids["agent"]}),
                                   ("POST", "/chat-confirmations", tie), ("POST", "/chat-confirmations/remove", tie),
                                   ("POST", "/inbox/receive", {})):
            # No route serves them. An agent's read finds nothing either, and its write is outside what an
            # agent may call, as for any path no route serves.
            for headers, status in (({}, 404), (agent, 404 if method == "GET" else 403)):
                response = client.request(method, "/api/v1" + path, json=body,
                                          headers={**headers, "Idempotency-Key": str(uuid.uuid4())})
                assert response.status_code == status, (method, path, headers, response.text)
                if status == 403:
                    assert response.json()["error"]["code"] == "agent_scope"
        # Nothing confirms an agent in a chat any more: the chat names the one whose run reports it.
        listed = {c["id"]: c for c in call(client, "GET", "/chats")["items"]}
        assert [(a["id"], a["binding"]) for a in listed[ids["chat"]]["agents"]] == [(ids["agent"], "run")]
    finally:
        client.__exit__(None, None, None)


def test_an_agent_still_cannot_read_an_old_messages_history(tmp_path):
    database, ids = worked(tmp_path / "data")
    to_v29(database)
    with closing(sqlite3.connect(database)) as conn:
        message = messaging(conn, ids)
        conn.commit()
    Database(database).initialize()
    client = session(tmp_path / "data")
    try:
        # The message's text stays with the person, as it did while the table held it.
        response = client.get(f"/api/v1/history?entityId={message}", headers={"Authorization": "Bearer " + ids["token"]})
        assert response.status_code == 403 and response.json()["error"]["code"] == "agent_scope", response.text
        assert sorted(e["kind"] for e in call(client, "GET", f"/history?entityId={message}")["items"]) == [
            "chat.message", "chat.message.deliver"]
    finally:
        client.__exit__(None, None, None)


def test_the_agent_command_line_has_no_inbox(capsys):
    with pytest.raises(SystemExit):
        cli.main(["agent", "inbox"])
    assert "invalid choice: 'inbox'" in capsys.readouterr().err
    client = cli.AgentClient("http://127.0.0.1:8893", "token")
    with pytest.raises(ValueError, match="outside proposal/read scope"):
        client.request("POST", "/inbox/receive", {})
