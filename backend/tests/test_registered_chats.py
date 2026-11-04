"""An agent registers the chat it works in: its runs report that chat, and the work they
report tracks it there, so the person sees each agent's chat with its work."""

import secrets
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
CHAT = "7d3c1f2a-5b6e-4f10-9a8b-1c2d3e4f5a6b"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert response.status_code == 200
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


@pytest.fixture
def agent_http(client):
    """What the CLI sends: no browser origin and no session cookie."""
    with TestClient(client.app, base_url=ORIGIN) as anonymous:
        yield anonymous


def write(c, method, path, payload=None, token=None):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **({"Authorization": "Bearer " + token} if token else {})}
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def issue(c, label="Shared agents"):
    return ok(write(c, "POST", "/credentials", {"label": label}))


def register(c, sponsor_token, chat=CHAT, label="Parser fix (Claude)", token=None):
    token = token or secrets.token_urlsafe(32)
    return write(c, "POST", "/agent-registrations", {"label": label, "token": token, "chatId": chat}, sponsor_token), token


def work_item(c, title="Parser fix"):
    return ok(write(c, "POST", "/work-items", {"title": title}))


def start_run(c, token, key="run-1", name="Parser fix", **extra):
    return ok(write(c, "POST", "/agent-runs", {"clientRunKey": key, "displayName": name, "provider": "claude", **extra}, token))


def tracked(c, item_id):
    return [chat for chat in ok(c.get(f"/api/v1/work-items/{item_id}"))["chats"]]


def test_registration_needs_the_chat_and_records_it(client, agent_http):
    sponsor = issue(client)
    for chat in (None, "", "   ", "two words", "tab\there", "x" * 501):
        payload = {"label": "Parser fix", "token": secrets.token_urlsafe(32), **({} if chat is None else {"chatId": chat})}
        assert write(agent_http, "POST", "/agent-registrations", payload, sponsor["token"]).status_code == 422, chat
    response, token = register(agent_http, sponsor["token"], chat=" " + CHAT + " ")
    registered = ok(response)
    assert registered["chatId"] == CHAT
    # The person sees each identity's chat where its credential is listed.
    settings = {c["id"]: c for c in ok(client.get("/api/v1/settings"))["credentials"]}
    assert settings[registered["id"]]["chatId"] == CHAT
    assert settings[sponsor["id"]]["chatId"] is None
    detail = ok(client.get(f"/api/v1/agents/{registered['agentId']}"))
    assert [c["chatId"] for c in detail["credentials"]] == [CHAT]
    history = ok(client.get("/api/v1/history", params={"entityId": registered["id"]}))["items"]
    assert history[0]["after"]["chatId"] == CHAT


def test_an_identity_keeps_the_chat_it_registered_with(client, agent_http):
    sponsor = issue(client)
    first, token = register(agent_http, sponsor["token"])
    # Retrying with the same token and chat returns the same identity.
    assert ok(register(agent_http, sponsor["token"], token=token)[0]) == ok(first)
    other, _ = register(agent_http, sponsor["token"], chat="another-chat", token=token)
    assert other.status_code == 409 and other.json()["error"]["code"] == "chat_mismatch"
    assert "another chat" in other.json()["error"]["message"]
    with client.app.state.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE kind='credential.register'").fetchone()[0] == 1


def test_an_identity_registered_before_chats_were_recorded_registers_anew(client, agent_http):
    sponsor = issue(client)
    first, token = register(agent_http, sponsor["token"])
    legacy = ok(first)
    with client.app.state.db.transaction() as conn:
        # Registrations before this change recorded no chat, so v12 copied none into their row.
        conn.execute("UPDATE events SET after_json=json_remove(after_json,'$.chatId') WHERE kind='credential.register'")
        conn.execute("UPDATE communication_credentials SET chat_id=NULL WHERE credential_id=?", (legacy["id"],))
    assert {c["id"]: c["chatId"] for c in ok(client.get("/api/v1/settings"))["credentials"]}[legacy["id"]] is None
    again, _ = register(agent_http, sponsor["token"], token=token)
    assert again.status_code == 409 and "registered before chats were recorded" in again.json()["error"]["message"]
    # Its runs report a chat only when they name one, and the identity keeps working.
    assert start_run(agent_http, token)["externalChatId"] is None
    assert ok(client.get(f"/api/v1/agents/{legacy['agentId']}"))["credentials"][0]["chatId"] is None


def test_runs_report_the_registered_chat_unless_they_name_another(client, agent_http):
    sponsor = issue(client)
    response, token = register(agent_http, sponsor["token"])
    ok(response)
    assert start_run(agent_http, token)["externalChatId"] == CHAT
    assert start_run(agent_http, token, key="run-2", externalChatId="named-chat")["externalChatId"] == "named-chat"
    # A credential issued in Settings registered no chat; its runs report one only when they name it.
    assert start_run(agent_http, sponsor["token"], key="run-3")["externalChatId"] is None


def test_rotating_the_credential_keeps_the_identitys_chat(client, agent_http):
    sponsor = issue(client)
    response, token = register(agent_http, sponsor["token"])
    registered = ok(response)
    rotated = ok(write(client, "POST", f"/credentials/{registered['id']}/rotate", {}))
    assert rotated["agentId"] == registered["agentId"]
    item = work_item(client)
    run = start_run(agent_http, rotated["token"])
    assert run["externalChatId"] == CHAT
    ok(write(agent_http, "POST", "/participations", {"runId": run["id"], "itemId": item["id"]}, rotated["token"]))
    assert [c["chatId"] for c in tracked(client, item["id"])] == [CHAT]


def test_reported_work_tracks_the_runs_chat_on_that_item_once(client, agent_http):
    sponsor = issue(client)
    response, token = register(agent_http, sponsor["token"])
    registered = ok(response)
    item, other = work_item(client), work_item(client, "Tokenizer")
    run = start_run(agent_http, token)
    participation = ok(write(agent_http, "POST", "/participations", {"runId": run["id"], "itemId": item["id"]}, token))
    [chat] = tracked(client, item["id"])
    assert (chat["chatId"], chat["title"], chat["provider"], chat["archived"]) == (CHAT, "Parser fix", "claude", False)
    # The chat's history says which agent's report tracked it.
    history = ok(client.get("/api/v1/history", params={"entityId": chat["id"]}))["items"]
    assert [(e["kind"], e["actorKind"], e["actorId"]) for e in history] == [("chat.create", "agent", registered["id"])]
    # Chats lists it with the work, and a message written there reaches the agent's run.
    listed = [c for c in ok(client.get("/api/v1/chats"))["items"] if c["id"] == chat["id"]]
    assert listed and listed[0]["owner"] == {"type": "item", "id": item["id"], "title": "Parser fix", "archived": False}
    assert [r["runId"] for r in ok(client.get(f"/api/v1/chats/{chat['id']}/messages"))["recipients"]] == [run["id"]]
    # The Agents page ties the agent to its tracked chat and that chat's work.
    agent = ok(client.get(f"/api/v1/agents/{registered['agentId']}"))
    assert [c["id"] for c in agent["chats"]["items"]] == [chat["id"]]

    # Reporting the same work again tracks nothing new; moving to other work tracks it there too.
    ok(write(agent_http, "POST", f"/participations/{participation['id']}/check-in", {"revision": participation["revision"]}, token))
    second = ok(write(agent_http, "POST", "/participations", {"runId": run["id"], "itemId": item["id"]}, token))
    assert len(tracked(client, item["id"])) == 1
    moved = ok(write(agent_http, "PATCH", f"/participations/{second['id']}", {"revision": second["revision"], "itemId": other["id"]}, token))
    assert moved["itemId"] == other["id"]
    assert [c["chatId"] for c in tracked(client, other["id"])] == [CHAT]


def test_a_chat_the_person_archived_on_that_work_stays_archived(client, agent_http):
    sponsor = issue(client)
    response, token = register(agent_http, sponsor["token"])
    ok(response)
    item = work_item(client)
    run = start_run(agent_http, token)
    ok(write(agent_http, "POST", "/participations", {"runId": run["id"], "itemId": item["id"]}, token))
    [chat] = tracked(client, item["id"])
    ok(write(client, "POST", f"/chats/{chat['id']}/archive", {"revision": chat["revision"]}))
    ok(write(agent_http, "POST", "/participations", {"runId": run["id"], "itemId": item["id"]}, token))
    assert [(c["id"], c["archived"]) for c in tracked(client, item["id"])] == [(chat["id"], True)]


def test_work_without_an_item_or_a_chat_tracks_nothing(client, agent_http):
    sponsor = issue(client)
    repository = ok(write(client, "POST", "/repositories", {"name": "Parser", "url": "https://github.com/team/parser"}))
    response, token = register(agent_http, sponsor["token"])
    ok(response)
    item = work_item(client)
    # Reporting only a repository names no work to track the chat on.
    run = start_run(agent_http, token)
    ok(write(agent_http, "POST", "/participations", {"runId": run["id"], "contexts": [{"repositoryId": repository["id"]}]}, token))
    # A run that reports no chat has none to track.
    unnamed = start_run(agent_http, sponsor["token"], key="run-2")
    ok(write(agent_http, "POST", "/participations", {"runId": unnamed["id"], "itemId": item["id"]}, sponsor["token"]))
    # Only an identity's registered chat tracks itself: a chat a run merely names stays provenance,
    # whether the credential was issued in Settings or registered for another chat.
    named = start_run(agent_http, sponsor["token"], key="run-3", externalChatId="codex-thread-1")
    ok(write(agent_http, "POST", "/participations", {"runId": named["id"], "itemId": item["id"]}, sponsor["token"]))
    elsewhere = start_run(agent_http, token, key="run-4", externalChatId="another-chat")
    ok(write(agent_http, "POST", "/participations", {"runId": elsewhere["id"], "itemId": item["id"]}, token))
    assert tracked(client, item["id"]) == []


def test_a_repeated_report_under_its_key_tracks_the_chat_once(client, agent_http):
    sponsor = issue(client)
    response, token = register(agent_http, sponsor["token"])
    ok(response)
    item = work_item(client)
    run = start_run(agent_http, token)
    key = str(uuid.uuid4())
    payload = {"runId": run["id"], "itemId": item["id"]}
    headers = {"Idempotency-Key": key, "Authorization": "Bearer " + token}
    first = ok(agent_http.post("/api/v1/participations", json=payload, headers=headers))
    assert ok(agent_http.post("/api/v1/participations", json=payload, headers=headers)) == first
    assert len(tracked(client, item["id"])) == 1
