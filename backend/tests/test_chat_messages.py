"""Messages to a chat's agents: who receives them, once, and what never revives them.

A credential issued in Settings reaches a chat once the person confirms its identity there,
so these tests confirm each one; test_chat_confirmations.py covers who is bound to a chat."""

import io
import json
import sqlite3
import uuid
import zipfile
from contextlib import closing
from datetime import timedelta

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import chat_messages, migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
STAMP = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def request(c, method, path, payload=None, agent=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def post(c, path, payload=None, agent=None, key=None):
    return ok(request(c, "POST", path, payload or {}, agent, key))


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def tracked_chat(c, chat_id="session-1", title="Parser work"):
    item = post(c, "/work-items", {"title": "Import format"})
    return post(c, "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": chat_id, "title": title})


def agent(c, label="Implementer"):
    return post(c, "/credentials", {"label": label})


def run(c, credential, chat_id, name="Implementation"):
    return post(c, "/agent-runs", {"clientRunKey": str(uuid.uuid4()), "displayName": name, "externalChatId": chat_id}, credential)


def confirm(c, chat, credential):
    """The person confirms on the chat page that the credential's identity works in the chat."""
    return post(c, f"/chats/{chat['id']}/confirmations", {"agentId": credential["agentId"]})


def outbox(c, chat):
    return ok(request(c, "GET", f"/chats/{chat['id']}/messages"))


def send(c, chat, text="Please also handle the revision field."):
    return post(c, f"/chats/{chat['id']}/messages", {"text": text})


def receive(c, credential, key=None, **payload):
    return post(c, "/inbox/receive", payload, credential, key)


def statuses(c, chat):
    return [(d["agentLabel"], d["status"], d["reason"]) for m in outbox(c, chat)["items"] for d in m["deliveries"]]


def test_a_message_reaches_each_open_run_that_reported_the_chat_once(client):
    chat = tracked_chat(client)
    tied, ended, elsewhere, other_case = agent(client, "Tied"), agent(client, "Ended"), agent(client, "Elsewhere"), agent(client, "Case")
    tied_run = run(client, tied, "session-1")
    finished = run(client, ended, "session-1")
    post(client, f"/agent-runs/{finished['id']}/end", {"revision": finished["revision"]}, ended)
    run(client, elsewhere, "session-2")
    run(client, other_case, "SESSION-1")
    # Bound to this chat, all four: only the open run that reported its exact ID is reached.
    for credential in (tied, ended, elsewhere, other_case):
        confirm(client, chat, credential)
    assert [r["agentLabel"] for r in outbox(client, chat)["recipients"]] == ["Tied"]

    message = send(client, chat, "  Use the **revised** import format.  ")
    assert message["text"] == "Use the **revised** import format."
    assert [(d["runId"], d["status"]) for d in message["deliveries"]] == [(tied_run["id"], "pending")]
    for credential in (ended, elsewhere, other_case):
        assert receive(client, credential)["items"] == []

    key = str(uuid.uuid4())
    received = receive(client, tied, key)
    assert received["total"] == 1
    item = received["items"][0]
    assert item["text"] == "Use the **revised** import format."
    assert item["authorKind"] == "human" and item["runIds"] == [tied_run["id"]]
    assert item["chat"] == {"id": chat["id"], "chatId": "session-1", "title": "Parser work"}
    # An uncertain result can be retried with the same key; a new request finds nothing more.
    assert receive(client, tied, key) == received
    assert receive(client, tied)["items"] == []
    delivery = outbox(client, chat)["items"][0]["deliveries"][0]
    assert delivery["status"] == "delivered" and delivery["deliveredAt"]


def test_two_open_runs_on_one_chat_receive_one_copy(client):
    chat = tracked_chat(client)
    credential = agent(client)
    first, second = run(client, credential, "session-1", "First"), run(client, credential, "session-1", "Second")
    confirm(client, chat, credential)
    send(client, chat)
    received = receive(client, credential)
    assert received["total"] == 1
    assert sorted(received["items"][0]["runIds"]) == sorted([first["id"], second["id"]])
    assert [d["status"] for d in outbox(client, chat)["items"][0]["deliveries"]] == ["delivered", "delivered"]


def test_the_chat_filter_takes_only_that_chats_messages(client):
    one, two = tracked_chat(client, "session-1", "One"), tracked_chat(client, "session-2", "Two")
    credential = agent(client)
    run(client, credential, "session-1")
    run(client, credential, "session-2")
    confirm(client, one, credential)
    confirm(client, two, credential)
    send(client, one, "For one")
    send(client, two, "For two")
    assert [m["text"] for m in receive(client, credential, chatId="session-2")["items"]] == ["For two"]
    assert [m["text"] for m in receive(client, credential)["items"]] == ["For one"]


def test_sending_refuses_archived_chats_and_empty_audiences_and_stores_nothing(client, tmp_path):
    chat = tracked_chat(client)
    error(request(client, "POST", f"/chats/{chat['id']}/messages", {"text": "Anyone?"}), 409, "no_recipients")
    credential = agent(client)
    run(client, credential, "session-1")
    archived = post(client, f"/chats/{chat['id']}/archive", {"revision": chat["revision"]})
    error(request(client, "POST", f"/chats/{chat['id']}/messages", {"text": "Still there?"}), 409, "chat_archived")
    view = outbox(client, chat)
    assert view["chat"]["archived"] is True and view["recipients"] == view["unconfirmed"] == [] and view["total"] == 0
    error(request(client, "POST", f"/chats/{chat['id']}/messages", {"text": "   "}), 409, "chat_archived")
    error(request(client, "POST", "/chats/missing/messages", {"text": "Hello"}), 404, "not_found")
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        assert conn.execute("SELECT archived,revision FROM agent_chats WHERE id=?", (chat["id"],)).fetchone() == (1, archived["revision"])
        assert conn.execute("SELECT count(*) FROM events WHERE kind='chat.recover'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 0


@pytest.mark.parametrize("ending,reason", [
    ("agent_end", "run_ended"), ("human_end", "human_closed"), ("revoke", "credential_revoked"),
    ("rotate", "credential_rotated"), ("archive", "chat_archived"),
])
def test_a_pending_delivery_ends_with_its_run_credential_or_chat_and_never_returns(client, ending, reason):
    chat = tracked_chat(client)
    credential = agent(client)
    running = run(client, credential, "session-1")
    confirm(client, chat, credential)
    send(client, chat)
    if ending == "agent_end":
        post(client, f"/agent-runs/{running['id']}/end", {"revision": running["revision"]}, credential)
    elif ending == "human_end":
        post(client, f"/agent-runs/{running['id']}/end", {"revision": running["revision"]})
    elif ending == "revoke":
        post(client, f"/credentials/{credential['id']}/revoke")
        error(request(client, "POST", "/inbox/receive", {}, credential), 401, "authentication")
    elif ending == "rotate":
        credential = post(client, f"/credentials/{credential['id']}/rotate")
    else:
        current = post(client, f"/chats/{chat['id']}/archive", {"revision": chat["revision"]})
        # Recovering the chat is the person's own act, and it does not revive the delivery.
        post(client, f"/chats/{chat['id']}/recover", {"revision": current["revision"]})
    assert statuses(client, chat) == [("Implementer", "expired", reason)]
    if ending in {"agent_end", "human_end", "rotate"}:
        # A new run on the same chat does not inherit messages sent before it started.
        run(client, credential, "session-1")
    if ending != "revoke":
        assert receive(client, credential)["items"] == []
    assert statuses(client, chat) == [("Implementer", "expired", reason)]


def test_a_delivery_nobody_picks_up_expires_and_keeps_that_reason(client, monkeypatch):
    chat = tracked_chat(client)
    credential = agent(client)
    running = run(client, credential, "session-1")
    confirm(client, chat, credential)
    monkeypatch.setattr(chat_messages, "PICKUP_WINDOW", timedelta(0))
    send(client, chat)
    assert receive(client, credential)["items"] == []
    delivery = outbox(client, chat)["items"][0]["deliveries"][0]
    assert (delivery["status"], delivery["reason"]) == ("expired", "not_picked_up")
    assert delivery["expiredAt"] == delivery["expiresAt"]
    post(client, f"/agent-runs/{running['id']}/end", {"revision": running["revision"]}, credential)
    assert statuses(client, chat) == [("Implementer", "expired", "not_picked_up")]


def test_agents_cannot_read_or_write_a_chats_messages_and_people_cannot_receive(client):
    chat = tracked_chat(client)
    credential = agent(client)
    run(client, credential, "session-1")
    confirm(client, chat, credential)
    send(client, chat)
    for method, path in (("GET", f"/chats/{chat['id']}/messages"), ("POST", f"/chats/{chat['id']}/messages"),
                         ("GET", f"/chats/{chat['id']}/messages/")):
        error(request(client, method, path, {"text": "Agent write"} if method == "POST" else None, credential), 403, "agent_scope")
    error(request(client, "POST", "/inbox/receive", {}), 403, "agent_scope")
    assert request(client, "POST", "/inbox/receive", {"chatId": "session-1", "extra": True}, credential).status_code == 422
    assert request(client, "POST", "/inbox/receive", {}, credential, key="short").status_code == 422
    assert client.post("/api/v1/inbox/receive", headers={"Authorization": "Bearer " + credential["token"]}, json={}).status_code == 428
    assert receive(client, credential)["total"] == 1


def test_messages_list_newest_first_with_paging(client):
    chat = tracked_chat(client)
    credential = agent(client)
    run(client, credential, "session-1")
    confirm(client, chat, credential)
    for text in ("First", "Second", "Third"):
        send(client, chat, text)
    page = ok(request(client, "GET", f"/chats/{chat['id']}/messages?limit=2"))
    assert [m["text"] for m in page["items"]] == ["Third", "Second"] and page["total"] == 3
    rest = ok(request(client, "GET", f"/chats/{chat['id']}/messages?limit=2&offset=2"))
    assert [m["text"] for m in rest["items"]] == ["First"]


def v3_database(path):
    """The shape main shipped as v3: avatars, but no messages to chats."""
    sealed.build(path, 3)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO credentials VALUES('credential','Agent','hash-agent',?,0)", (STAMP,))
        conn.execute("INSERT INTO communication_agents(id,label,created_at,avatar) VALUES('credential','Agent',?,'teal-dots-antenna')", (STAMP,))
        conn.execute("INSERT INTO communication_credentials VALUES('credential','credential')")
        conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES('item','Work',0,?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO agent_chats(id,owner_type,owner_id,chat_id,title,created_date,created_at,updated_at) VALUES('chat','item','item','session-1','Chat','2026-01-01',?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO agent_runs(id,agent_id,credential_id,client_run_key,display_name,external_chat_id,created_at,updated_at) VALUES('run','credential','credential','key','Run','session-1',?,?)", (STAMP, STAMP))
        conn.commit()
    recovery.validate_database(path)


def test_v3_upgrade_adds_messages_after_a_snapshot_and_restores(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    v3_database(path)
    before = {t: conn_rows(path, t) for t in ("credentials", "communication_agents", "agent_chats", "agent_runs")}
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 0
    assert {t: conn_rows(path, t) for t in before} == before
    snapshots = list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert recovery._schema_signature(conn) == sealed.shape(3)
    restored = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(restored, snapshots[0], tmp_path / "restore-backups")
    with Database(restored).read() as conn:
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("SELECT end_reason FROM agent_runs").fetchone()[0] == "restore"


def conn_rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def test_failed_message_schema_rolls_back_the_v3_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    v3_database(path)
    execute = migrations.execute_schema

    def fail_after_messages(conn, schema):
        execute(conn, schema)
        if schema == migrations.CHAT_MESSAGE_SCHEMA:
            raise RuntimeError("Injected message schema failure")

    monkeypatch.setattr(migrations, "execute_schema", fail_after_messages)
    with pytest.raises(RuntimeError, match="Injected"):
        Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert recovery._schema_signature(conn) == sealed.shape(3)
    recovery.validate_database(path)


def test_a_v3_shape_cannot_claim_version_4_and_a_v4_shape_cannot_claim_3(tmp_path):
    older = tmp_path / "older.sqlite3"
    v3_database(older)
    current = tmp_path / "current.sqlite3"
    Database(current).initialize()
    for path, version in ((older, 4), (current, 3)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)


def test_restore_ends_pending_deliveries_and_export_keeps_messages(client, tmp_path):
    chat = tracked_chat(client)
    credential = agent(client)
    run(client, credential, "session-1")
    confirm(client, chat, credential)
    delivered = send(client, chat, "Already received")
    receive(client, credential)
    waiting = send(client, chat, "Still waiting")
    live = tmp_path / "worklog.sqlite3"
    with zipfile.ZipFile(io.BytesIO(recovery.export_database(live))) as archive:
        texts = {m["text"] for m in json.loads(archive.read("records/chat_messages.json"))}
        assert texts == {"Already received", "Still waiting"}
        assert len(json.loads(archive.read("records/chat_message_deliveries.json"))) == 2
    backup = recovery.backup_database(live, tmp_path / "manual")
    target = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(target, tmp_path / "manual" / backup["name"], tmp_path / "restore-backups")
    with closing(sqlite3.connect(target)) as conn:
        rows = dict(conn.execute("SELECT message_id,status||':'||coalesce(expire_reason,'') FROM chat_message_deliveries"))
    assert rows == {delivered["id"]: "delivered:", waiting["id"]: "expired:restore"}


def test_changing_a_chats_id_ends_its_waiting_deliveries_for_good(client):
    chat = tracked_chat(client)
    credential = agent(client)
    run(client, credential, "session-1")
    confirm(client, chat, credential)
    send(client, chat)
    moved = ok(request(client, "PATCH", f"/chats/{chat['id']}", {"revision": chat["revision"], "chatId": "other-chat"}))
    assert statuses(client, chat) == [("Implementer", "expired", "chat_changed")]
    assert receive(client, credential)["items"] == []
    # Changing it back ties the run again for new messages, never for the old one.
    back = ok(request(client, "PATCH", f"/chats/{chat['id']}", {"revision": moved["revision"], "chatId": "session-1"}))
    assert receive(client, credential)["items"] == []
    send(client, chat, "After the move")
    ok(request(client, "PATCH", f"/chats/{chat['id']}", {"revision": back["revision"], "title": "Renamed"}))
    assert [m["text"] for m in receive(client, credential)["items"]] == ["After the move"]


def test_a_run_no_longer_tied_to_the_chat_receives_nothing_even_without_an_expiry(client, tmp_path):
    chat = tracked_chat(client)
    credential = agent(client)
    run(client, credential, "session-1")
    confirm(client, chat, credential)
    send(client, chat)
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        conn.execute("UPDATE agent_chats SET chat_id='elsewhere' WHERE id=?", (chat["id"],))
        conn.commit()
    assert receive(client, credential)["items"] == []


def test_agents_cannot_read_a_messages_history(client):
    chat = tracked_chat(client)
    credential = agent(client)
    run(client, credential, "session-1")
    confirm(client, chat, credential)
    message = send(client, chat)
    receive(client, credential)
    error(request(client, "GET", f"/history?entityId={message['id']}", agent=credential), 403, "agent_scope")
    kinds = [e["kind"] for e in ok(request(client, "GET", f"/history?entityId={message['id']}"))["items"]]
    assert sorted(kinds) == ["chat.message", "chat.message.deliver"]


def test_an_empty_receive_leaves_no_receipt(client, tmp_path):
    chat = tracked_chat(client)
    credential = agent(client)
    run(client, credential, "session-1")
    confirm(client, chat, credential)

    def receipts():
        with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
            return conn.execute("SELECT count(*) FROM receipts WHERE actor_id=?", (credential["id"],)).fetchone()[0]

    before = receipts()
    for _ in range(3):
        assert receive(client, credential)["items"] == []
    assert receipts() == before
    send(client, chat)
    assert receive(client, credential)["total"] == 1
    assert receipts() == before + 1
