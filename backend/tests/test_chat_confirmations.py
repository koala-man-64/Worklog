"""Chats bound to identities (schema v13, decision K3). An identity is bound to the chat it
registered with, and to each chat where the person confirmed it on the chat page. What the person
writes into a chat reaches bound identities only: any credential's run can report any chat ID, so
a run of an identity bound to neither shows as unconfirmed and receives nothing. Upgrade, the
documented way back and restore validation are here too."""

import secrets
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
CHAT = "3f6b8d0a-2c4e-4a61-9b7d-5e8f0a1b2c3d"
# README: from version 13 to version 12, with Worklog stopped and after a manual backup.
WAY_BACK = """\
BEGIN;
UPDATE chat_message_deliveries SET expire_reason=NULL WHERE expire_reason='unconfirmed';
DROP TABLE chat_confirmations;
PRAGMA user_version=12;
COMMIT;
"""


def to_v12(path):
    """Take a current database back to the v12 shape the documented way: to version 13 first."""
    from test_task_order import to_v13

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


def confirm(c, chat, credential):
    return post(c, f"/chats/{chat['id']}/confirmations", {"agentId": credential["agentId"]})


def unconfirm(c, chat, credential):
    return post(c, f"/chats/{chat['id']}/confirmations/remove", {"agentId": credential["agentId"]})


def outbox(c, chat):
    """Who a message sent now reaches, each with how it is bound, and who only claims the chat."""
    view = ok(request(c, "GET", f"/chats/{chat['id']}/messages"))
    return [(r["agentLabel"], r["binding"]) for r in view["recipients"]], [r["agentLabel"] for r in view["unconfirmed"]]


def send(c, chat, text):
    return post(c, f"/chats/{chat['id']}/messages", {"text": text})


def receive(c, credential):
    return [m["text"] for m in post(c, "/inbox/receive", {}, credential)["items"]]


def statuses(c, chat):
    """Each delivery of the chat's messages, oldest message first."""
    view = ok(request(c, "GET", f"/chats/{chat['id']}/messages"))
    return [(m["text"], d["agentLabel"], d["status"], d["reason"]) for m in reversed(view["items"]) for d in m["deliveries"]]


def history(c, chat):
    return [e["kind"] for e in ok(request(c, "GET", f"/history?entityId={chat['id']}"))["items"]]


def count(directory, sql):
    with closing(sqlite3.connect(directory / "worklog.sqlite3")) as conn:
        return conn.execute(sql).fetchone()[0]


def test_a_registered_identity_is_reached_and_a_claim_receives_nothing(client):
    sponsor = issue(client, "Shared agents")
    child = register(client, sponsor, "Planner (Claude)")
    claimant = issue(client, "Other agents")
    chat = tracked_chat(client)
    child_run = run(client, child)
    run(client, claimant, CHAT)
    assert outbox(client, chat) == ([("Planner (Claude)", "registered")], ["Other agents"])

    message = send(client, chat, "For the planner")
    assert [d["runId"] for d in message["deliveries"]] == [child_run["id"]]
    assert receive(client, claimant) == []
    assert receive(client, child) == ["For the planner"]
    # With only the claim left, a message would reach no one, and the person is told why.
    post(client, f"/agent-runs/{child_run['id']}/end", {"revision": child_run["revision"]}, child)
    refused = error(request(client, "POST", f"/chats/{chat['id']}/messages", {"text": "Anyone?"}), 409, "no_recipients")
    assert "confirm" in refused["message"]


def test_the_chat_page_says_where_each_identity_comes_from(client):
    # Labels are each agent's own choice; who registered an identity is not.
    sponsor = issue(client, "Shared agents")
    child = register(client, sponsor, "Planner (Claude)")
    elsewhere = register(client, sponsor, "Planner (Claude)", chat="another-chat")
    claimant = issue(client, "Other agents")
    chat = tracked_chat(client)
    run(client, child)
    run(client, elsewhere, CHAT)
    run(client, claimant, CHAT)
    view = ok(request(client, "GET", f"/chats/{chat['id']}/messages"))
    by_sponsor = {"id": sponsor["agentId"], "label": "Shared agents"}
    assert [(r["agentId"], r["registeredBy"]) for r in view["recipients"]] == [(child["agentId"], by_sponsor)]
    assert [(r["agentId"], r["registeredBy"]) for r in view["unconfirmed"]] == [
        (elsewhere["agentId"], by_sponsor), (claimant["agentId"], None)]
    # Rotating a registered identity's credential in Settings keeps where it came from.
    rotated = post(client, f"/credentials/{child['id']}/rotate")
    run(client, rotated)
    view = ok(request(client, "GET", f"/chats/{chat['id']}/messages"))
    assert [(r["agentId"], r["registeredBy"]) for r in view["recipients"]] == [(child["agentId"], by_sponsor)]


def test_confirming_a_claim_reaches_that_identity_from_then_on(client, tmp_path):
    sponsor = issue(client, "Shared agents")
    child = register(client, sponsor, "Planner (Claude)")
    claimant = issue(client, "Other agents")
    chat = tracked_chat(client)
    run(client, child)
    run(client, claimant, CHAT)
    send(client, chat, "Before")

    confirmed = {"chat": {"id": chat["id"], "chatId": CHAT}, "agentId": claimant["agentId"], "binding": "confirmed"}
    assert confirm(client, chat, claimant) == confirmed
    assert confirm(client, chat, claimant) == confirmed
    assert outbox(client, chat) == ([("Planner (Claude)", "registered"), ("Other agents", "confirmed")], [])
    send(client, chat, "After")
    assert receive(client, claimant) == ["After"]
    assert receive(client, child) == ["Before", "After"]
    # An identity bound by registration needs no confirmation, and gets none.
    assert confirm(client, chat, child)["binding"] == "registered"
    assert count(tmp_path, "SELECT count(*) FROM chat_confirmations") == 1
    assert history(client, chat).count("chat.confirm") == 1


def test_a_confirmation_holds_for_the_chat_on_every_record_and_through_rotation(client):
    claimant = issue(client, "Other agents")
    first, second = tracked_chat(client, CHAT, "On the import"), tracked_chat(client, CHAT, "On the parser")
    elsewhere = tracked_chat(client, "another-chat", "Elsewhere")
    run(client, claimant, CHAT)
    run(client, claimant, "another-chat")
    confirm(client, first, claimant)
    # The confirmation is for the chat itself, not for one record that tracks it.
    assert outbox(client, second) == ([("Other agents", "confirmed")], [])
    assert outbox(client, elsewhere) == ([], ["Other agents"])

    # Rotating the credential in Settings keeps the identity, and so what it is bound to.
    rotated = post(client, f"/credentials/{claimant['id']}/rotate")
    assert rotated["agentId"] == claimant["agentId"]
    assert outbox(client, first) == ([], [])
    run(client, rotated, CHAT)
    send(client, second, "After rotating")
    assert receive(client, rotated) == ["After rotating"]


def test_removing_a_confirmation_ends_what_waits_and_reaches_nothing_more(client):
    sponsor = issue(client, "Shared agents")
    child = register(client, sponsor, "Planner (Claude)")
    claimant = issue(client, "Other agents")
    chat = tracked_chat(client)
    run(client, child)
    run(client, claimant, CHAT)
    confirm(client, chat, claimant)
    send(client, chat, "Waiting")

    assert unconfirm(client, chat, claimant)["binding"] is None
    assert statuses(client, chat) == [("Waiting", "Planner (Claude)", "pending", None),
                                      ("Waiting", "Other agents", "expired", "unconfirmed")]
    assert receive(client, claimant) == []
    assert outbox(client, chat) == ([("Planner (Claude)", "registered")], ["Other agents"])
    send(client, chat, "Later")
    assert receive(client, claimant) == []
    # Confirming again reaches it with what is sent from then on; what ended stays ended.
    confirm(client, chat, claimant)
    send(client, chat, "Again")
    assert receive(client, claimant) == ["Again"]
    # The chat an identity registered with stays bound: there is no confirmation of it to remove.
    assert unconfirm(client, chat, child)["binding"] == "registered"
    assert receive(client, child) == ["Waiting", "Later", "Again"]
    assert history(client, chat).count("chat.unconfirm") == 1


def test_a_delivery_left_waiting_for_an_unbound_identity_is_never_handed_out(client, tmp_path):
    claimant = issue(client, "Other agents")
    chat = tracked_chat(client)
    run(client, claimant, CHAT)
    confirm(client, chat, claimant)
    send(client, chat, "Waiting")
    # As if the binding went without its deliveries ending: handing out checks the binding itself.
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        conn.execute("DELETE FROM chat_confirmations")
        conn.commit()
    assert receive(client, claimant) == []
    assert statuses(client, chat) == [("Waiting", "Other agents", "pending", None)]


def test_replaying_a_receive_after_its_confirmation_went_returns_only_what_it_received(client):
    claimant = issue(client, "Other agents")
    chat = tracked_chat(client)
    run(client, claimant, CHAT)
    confirm(client, chat, claimant)
    send(client, chat, "First")
    key = str(uuid.uuid4())
    headers = {"Idempotency-Key": key, "Authorization": "Bearer " + claimant["token"]}
    first = ok(client.post("/api/v1/inbox/receive", json={}, headers=headers))
    assert [m["text"] for m in first["items"]] == ["First"]
    send(client, chat, "Second")
    unconfirm(client, chat, claimant)
    # An uncertain receive may be retried under its key: that returns what it already handed out, nothing more.
    assert ok(client.post("/api/v1/inbox/receive", json={}, headers=headers)) == first
    assert receive(client, claimant) == []


def test_only_the_person_confirms_and_only_for_a_tracked_chat_and_a_known_identity(client, tmp_path):
    sponsor = issue(client, "Shared agents")
    child = register(client, sponsor, "Planner (Claude)")
    claimant = issue(client, "Other agents")
    chat = tracked_chat(client)
    run(client, claimant, CHAT)
    for agent in (claimant, child, sponsor):
        for route in ("confirmations", "confirmations/remove"):
            error(request(client, "POST", f"/chats/{chat['id']}/{route}", {"agentId": claimant["agentId"]}, agent),
                  403, "agent_scope")
    error(request(client, "POST", "/chats/missing/confirmations", {"agentId": claimant["agentId"]}), 404, "not_found")
    error(request(client, "POST", f"/chats/{chat['id']}/confirmations", {"agentId": "no-such-agent"}), 404, "not_found")
    assert request(client, "POST", f"/chats/{chat['id']}/confirmations",
                   {"agentId": claimant["agentId"], "chatId": CHAT}).status_code == 422
    assert client.post(f"/api/v1/chats/{chat['id']}/confirmations", json={"agentId": claimant["agentId"]}).status_code == 428
    # Another site's page, or one without this session's CSRF token, changes nothing.
    for headers, code in (({"Origin": "http://elsewhere.example"}, "origin"), ({"X-CSRF-Token": "not-this-one"}, "csrf")):
        for route in ("confirmations", "confirmations/remove"):
            error(client.post(f"/api/v1/chats/{chat['id']}/{route}", json={"agentId": claimant["agentId"]},
                              headers={"Idempotency-Key": str(uuid.uuid4()), **headers}), 403, code)
    post(client, f"/chats/{chat['id']}/archive", {"revision": chat["revision"]})
    for route in ("confirmations", "confirmations/remove"):
        error(request(client, "POST", f"/chats/{chat['id']}/{route}", {"agentId": claimant["agentId"]}), 409, "chat_archived")
    assert count(tmp_path, "SELECT count(*) FROM chat_confirmations") == 0


def test_a_proposal_notice_goes_only_to_chats_its_agent_is_bound_to(client):
    sponsor = issue(client, "Shared agents")
    child = register(client, sponsor, "Planner (Claude)")
    claimant = issue(client, "Other agents")
    chat = tracked_chat(client)
    claimed = tracked_chat(client, "claimed-chat", "Claimed")
    run(client, child)
    run(client, child, "claimed-chat")
    run(client, claimant, CHAT)
    for author in (child, claimant):
        proposal = post(client, "/proposals", {"title": "Reviewed by " + author["label"], "rationale": "",
                                               "operations": [{"action": "item.create", "payload": {"title": "Reviewed"}}]}, author)
        submitted = post(client, f"/proposals/{proposal['id']}/submit", {"revision": proposal["revision"]}, author)
        # Returning it to draft notifies its agent, and a chat its run only claims is not that agent's chat.
        assert post(client, f"/proposals/{submitted['id']}/reopen", {"revision": submitted["revision"]})["status"] == "draft"
    notices = ok(request(client, "GET", f"/chats/{chat['id']}/messages"))["items"]
    assert len(notices) == 1 and "Reviewed by Planner (Claude)" in notices[0]["text"]
    assert [d["agentLabel"] for d in notices[0]["deliveries"]] == ["Planner (Claude)"]
    assert ok(request(client, "GET", f"/chats/{claimed['id']}/messages"))["items"] == []
    assert receive(client, claimant) == []


def test_the_v13_upgrade_ends_what_waits_for_an_unbound_identity_and_keeps_the_rest(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        sponsor = issue(c, "Shared agents")
        child = register(c, sponsor, "Planner (Claude)")
        claimant = issue(c, "Other agents")
        chat = tracked_chat(c)
        run(c, child)
        run(c, claimant, CHAT)
        # Version 12 delivered to every run that reported the chat's ID. A confirmation stands in
        # for that here, and the way back drops it.
        confirm(c, chat, claimant)
        send(c, chat, "Read")
        assert receive(c, child) == receive(c, claimant) == ["Read"]
        send(c, chat, "Waiting")
    to_v12(path)
    recovery.validate_database(path)
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == recovery._expected_schema()
    snapshots = list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
    with person(tmp_path) as c:
        assert statuses(c, chat) == [("Read", "Planner (Claude)", "delivered", None),
                                     ("Read", "Other agents", "delivered", None),
                                     ("Waiting", "Planner (Claude)", "pending", None),
                                     ("Waiting", "Other agents", "expired", "unconfirmed")]
        assert outbox(c, chat) == ([("Planner (Claude)", "registered")], ["Other agents"])
        assert receive(c, claimant) == []
        assert receive(c, child) == ["Waiting"]


def test_an_upgrade_from_v11_binds_registered_identities_before_ending_unbound_deliveries(tmp_path):
    from test_decision_state_recovery import to_v11

    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        sponsor = issue(c, "Shared agents")
        child = register(c, sponsor, "Planner (Claude)")
        claimant = issue(c, "Other agents")
        chat = tracked_chat(c)
        run(c, child)
        run(c, claimant, CHAT)
        confirm(c, chat, claimant)
        send(c, chat, "Waiting")
    # Version 11 keeps the registered chat only in its event; the upgrade reads it from there first.
    to_v11(path)
    Database(path).initialize()
    with person(tmp_path) as c:
        assert statuses(c, chat) == [("Waiting", "Planner (Claude)", "pending", None),
                                     ("Waiting", "Other agents", "expired", "unconfirmed")]
        assert receive(c, child) == ["Waiting"]
        assert receive(c, claimant) == []


def test_the_documented_way_back_to_v12_gives_its_exact_shape_and_upgrades_again(tmp_path):
    # The SQL tested here is the SQL the README gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    with person(tmp_path) as c:
        claimant = issue(c, "Other agents")
        chat = tracked_chat(c)
        run(c, claimant, CHAT)
        confirm(c, chat, claimant)
        send(c, chat, "Ended by removing the confirmation")
        unconfirm(c, chat, claimant)
        confirm(c, chat, claimant)
    path = tmp_path / "worklog.sqlite3"
    to_v12(path)
    recovery.validate_database(path)
    with Database(path).read() as conn:
        assert recovery._schema_signature(conn) == recovery._expected_schema(chat_confirmations=False)
        # Version 12 has no 'unconfirmed' reason, so that delivery reads as plainly expired there.
        assert [tuple(r) for r in conn.execute("SELECT status,expire_reason FROM chat_message_deliveries")] == [("expired", None)]
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == recovery._expected_schema()
        # The way back drops the person's confirmations; they confirm those agents again.
        assert conn.execute("SELECT count(*) FROM chat_confirmations").fetchone()[0] == 0


def test_a_v12_shape_cannot_claim_version_13_and_a_v13_shape_cannot_claim_12(tmp_path):
    from test_task_order import to_v13

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
