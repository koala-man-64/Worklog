"""Agents register separate identities with tokens they generate, then act as them."""

import hashlib
import io
import secrets
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from worklog import registration
from worklog.app import create_app
from worklog.avatars import AVATARS

ORIGIN = "http://127.0.0.1:8877"


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


def write(c, method, path, payload=None, **kwargs):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **kwargs.pop("headers", {})}
    if payload is not None:
        kwargs["json"] = payload
    return c.request(method, "/api/v1" + path, headers=headers, **kwargs)


def bearer(token):
    return {"Authorization": "Bearer " + token}


def issue(c, label="Shared agents"):
    response = write(c, "POST", "/credentials", {"label": label})
    assert response.status_code == 200, response.text
    return response.json()


def chat_of(token):
    """A chat ID per token, so retrying a registration names the same chat."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, token))


def register(c, sponsor_token, label="Parser fix", token=None, **extra):
    token = token or secrets.token_urlsafe(32)
    payload = {"label": label, "token": token, "chatId": chat_of(token), **extra}
    return c.post("/api/v1/agent-registrations", json=payload, headers=bearer(sponsor_token)), token


def credential_count(c):
    with c.app.state.db.read() as conn:
        return conn.execute("SELECT count(*) FROM credentials").fetchone()[0]


def test_agent_registers_a_separate_identity_and_acts_as_it(client, agent_http):
    sponsor = issue(client)
    with client.app.state.db.read() as conn:
        receipts = conn.execute("SELECT count(*) FROM receipts").fetchone()[0]
    response, token = register(agent_http, sponsor["token"])
    assert response.status_code == 200, response.text
    registered = response.json()
    # Nothing derived from the token lands in a receipt.
    with client.app.state.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM receipts").fetchone()[0] == receipts
    assert registered["label"] == "Parser fix" and registered["avatar"] in AVATARS
    assert registered["agentId"] != sponsor["agentId"]
    assert registered["registeredBy"] == {"id": sponsor["agentId"], "label": "Shared agents"}
    assert "token" not in registered

    # The new token authenticates as the new identity, never as the sponsor.
    me = agent_http.get("/api/v1/discussions/participants", headers=bearer(token)).json()["self"]
    assert me == {"id": registered["agentId"], "label": "Parser fix", "kind": "agent"}
    posted = write(agent_http, "POST", "/discussions", {"title": "Plan", "text": "Starting the parser"}, headers=bearer(token))
    assert posted.status_code == 200, posted.text
    message = posted.json()["message"]
    assert (message["authorId"], message["authorLabel"]) == (registered["agentId"], "Parser fix")
    reply = write(agent_http, "POST", f"/discussions/{posted.json()['discussion']['id']}/messages", {"text": "Shared reply"},
                  headers=bearer(sponsor["token"]))
    assert reply.json()["authorId"] == sponsor["agentId"]
    run = write(agent_http, "POST", "/agent-runs", {"clientRunKey": "chat-a", "displayName": "Parser"}, headers=bearer(token))
    assert run.status_code == 200, run.text
    assert run.json()["agentId"] == registered["agentId"]

    # Only the hash is kept; neither the database nor an export holds the token.
    with client.app.state.db.read() as conn:
        assert conn.execute("SELECT token_hash FROM credentials WHERE id=?", (registered["id"],)).fetchone()[0] == hashlib.sha256(token.encode()).hexdigest()
        assert token not in "\n".join(conn.iterdump())
    archive = zipfile.ZipFile(io.BytesIO(client.get("/api/v1/export").content))
    assert all(token.encode() not in archive.read(name) for name in archive.namelist())

    # The person sees who registered it, beside credentials issued in Settings.
    settings = {c["id"]: c for c in client.get("/api/v1/settings").json()["credentials"]}
    assert settings[registered["id"]]["registeredBy"] == {"id": sponsor["agentId"], "label": "Shared agents"}
    assert settings[registered["id"]]["agentId"] == registered["agentId"]
    assert settings[sponsor["id"]]["registeredBy"] is None
    detail = client.get(f"/api/v1/agents/{registered['agentId']}").json()
    assert [c["registeredBy"] for c in detail["credentials"]] == [{"id": sponsor["agentId"], "label": "Shared agents"}]
    history = client.get("/api/v1/history", params={"entityId": registered["id"]}).json()["items"]
    assert [(e["kind"], e["actorId"]) for e in history] == [("credential.register", sponsor["id"])]


def test_registered_identity_keeps_agent_scope(client, agent_http):
    sponsor = issue(client)
    _, token = register(agent_http, sponsor["token"])
    headers = bearer(token)
    assert agent_http.get("/api/v1/settings", headers=headers).status_code == 403
    assert agent_http.get("/api/v1/agents", headers=headers).status_code == 403
    assert write(agent_http, "POST", "/credentials", {"label": "Escalate"}, headers=headers).status_code == 403
    assert write(agent_http, "POST", "/work-items", {"title": "Direct write"}, headers=headers).status_code == 403
    # A registered agent may register another identity in turn, still under the same scope.
    nested, nested_token = register(agent_http, token, "Parser subagent")
    assert nested.status_code == 200, nested.text
    assert nested.json()["registeredBy"]["label"] == "Parser fix"
    assert agent_http.get("/api/v1/settings", headers=bearer(nested_token)).status_code == 403


def test_registration_is_idempotent_by_token_and_refuses_reused_tokens(client, agent_http):
    sponsor = issue(client)
    first, token = register(agent_http, sponsor["token"])
    count = credential_count(client)
    # Retrying an uncertain registration with the same token returns it unchanged.
    again, _ = register(agent_http, sponsor["token"], "Another label", token=token)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert credential_count(client) == count
    reused, _ = register(agent_http, sponsor["token"], token=sponsor["token"])
    assert reused.status_code == 409 and reused.json()["error"]["code"] == "token_in_use"
    assert write(client, "POST", f"/credentials/{first.json()['id']}/revoke", {}).status_code == 200
    revoked, _ = register(agent_http, sponsor["token"], token=token)
    assert revoked.status_code == 409 and revoked.json()["error"]["code"] == "token_revoked"
    assert credential_count(client) == count


def test_registration_needs_an_active_agent_credential_and_a_valid_request(client, agent_http):
    sponsor = issue(client)
    count = credential_count(client)
    token = secrets.token_urlsafe(32)
    chat = chat_of(token)
    assert agent_http.post("/api/v1/agent-registrations", json={"label": "Nobody", "token": token, "chatId": chat}).status_code == 401
    human = write(client, "POST", "/agent-registrations", {"label": "Person", "token": token, "chatId": chat})
    assert human.status_code == 403 and human.json()["error"]["code"] == "agent_scope"
    assert register(agent_http, secrets.token_urlsafe(32))[0].status_code == 401
    assert agent_http.post("/api/v1/agent-registrations", json={"label": "Browser", "token": token, "chatId": chat},
                           headers={**bearer(sponsor["token"]), "Origin": "http://evil.example"}).status_code == 403
    for invalid in ({"token": "short"}, {"token": "a" * 42 + "!"}, {"token": "a" * 43 + "\n"}, {"token": "a" * 201}, {"label": "   "},
                    {"token": "a" * 43}, {"token": "abcdefghi" * 5}, {"label": " you "}, {"avatar": "not-an-avatar"}, {"extra": True}):
        response = agent_http.post("/api/v1/agent-registrations", json={"label": "Invalid", "token": token, "chatId": chat_of(token), **invalid},
                                   headers=bearer(sponsor["token"]))
        assert response.status_code == 422, (invalid, response.text)
        assert token not in response.text
    taken = client.get("/api/v1/settings").json()["agents"][0]["avatar"]
    clash, _ = register(agent_http, sponsor["token"], avatar=taken)
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "avatar_in_use"
    assert credential_count(client) == count
    chosen = next(name for name in AVATARS if name != taken)
    assert register(agent_http, sponsor["token"], avatar=chosen)[0].json()["avatar"] == chosen
    # Any well-formed random token passes, hexadecimal included.
    assert register(agent_http, sponsor["token"], token=secrets.token_hex(32))[0].status_code == 200
    assert write(client, "POST", f"/credentials/{sponsor['id']}/revoke", {}).status_code == 200
    assert register(agent_http, sponsor["token"])[0].status_code == 401


def test_active_registration_limit_stops_registration_loops(client, agent_http, monkeypatch):
    monkeypatch.setattr(registration, "ACTIVE_LIMIT", 2)
    # Credentials issued in Settings never count against agents.
    sponsor = issue(client)
    issue(client, "Another issued credential")
    first, token = register(agent_http, sponsor["token"])
    assert first.status_code == 200, first.text
    assert register(agent_http, sponsor["token"])[0].status_code == 200
    count = credential_count(client)
    refused, _ = register(agent_http, sponsor["token"])
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "agent_limit"
    assert credential_count(client) == count
    # The person is never refused, and a retired agent frees its place.
    issue(client, "Issued anyway")
    assert agent_http.post("/api/v1/agent-registrations/retire", headers=bearer(token)).status_code == 200
    assert register(agent_http, sponsor["token"])[0].status_code == 200


def test_registered_agent_retires_itself_but_settings_credentials_cannot(client, agent_http):
    sponsor = issue(client)
    registered, token = register(agent_http, sponsor["token"])
    agent = registered.json()
    repository = write(client, "POST", "/repositories", {"name": "Parser"}).json()
    run = write(agent_http, "POST", "/agent-runs", {"clientRunKey": "chat-a", "displayName": "Parser"}, headers=bearer(token)).json()
    participation = write(agent_http, "POST", "/participations", {"runId": run["id"], "contexts": [{"repositoryId": repository["id"]}]},
                          headers=bearer(token)).json()

    assert agent_http.post("/api/v1/agent-registrations/retire", headers=bearer(sponsor["token"])).status_code == 403
    assert agent_http.get("/api/v1/context", headers=bearer(sponsor["token"])).status_code == 200
    assert write(client, "POST", "/agent-registrations/retire", {}).status_code == 403
    retired = agent_http.post("/api/v1/agent-registrations/retire", json={}, headers=bearer(token))
    assert retired.status_code == 200, retired.text
    assert retired.json() == {"id": agent["id"], "agentId": agent["agentId"], "retired": True, "registeredRevoked": 0}
    assert agent_http.get("/api/v1/context", headers=bearer(token)).status_code == 401
    assert agent_http.post("/api/v1/agent-registrations/retire", headers=bearer(token)).status_code == 401

    ended = client.get(f"/api/v1/agent-runs/{run['id']}").json()
    assert ended["endReason"] == "credential_retired"
    assert client.get(f"/api/v1/participations/{participation['id']}").json()["releaseReason"] == "credential_retired"
    assert client.get(f"/api/v1/agents/{agent['agentId']}").json()["active"] is False
    # A retired agent's avatar returns to the pool, as after a revoke.
    catalog = agent_http.get("/api/v1/discussions/avatars", headers=bearer(sponsor["token"])).json()["items"]
    assert {item["name"]: item["available"] for item in catalog}[agent["avatar"]] is True
    history = client.get("/api/v1/history", params={"entityId": agent["id"]}).json()["items"]
    assert [e["kind"] for e in history] == ["credential.retire", "credential.register"]


def test_revoking_a_credential_revokes_what_it_registered_but_rotation_keeps_it(client, agent_http):
    sponsor = issue(client)
    other = issue(client, "Independent agent")
    child, child_token = register(agent_http, sponsor["token"], "Parser fix")
    grandchild, grandchild_token = register(agent_http, child_token, "Parser subagent")
    sibling, sibling_token = register(agent_http, sponsor["token"], "Docs")
    unrelated, unrelated_token = register(agent_http, other["token"], "Unrelated")
    run = write(agent_http, "POST", "/agent-runs", {"clientRunKey": "chat-a", "displayName": "Subagent"}, headers=bearer(grandchild_token)).json()
    counts = {c["id"]: c["registeredActive"] for c in client.get("/api/v1/settings").json()["credentials"]}
    assert (counts[sponsor["id"]], counts[child.json()["id"]], counts[other["id"]], counts[unrelated.json()["id"]]) == (3, 1, 1, 0)

    # Rotation keeps the identity, so what it registered keeps working.
    rotated = write(client, "POST", f"/credentials/{sponsor['id']}/rotate", {}).json()
    assert agent_http.get("/api/v1/context", headers=bearer(child_token)).status_code == 200

    revoked = write(client, "POST", f"/credentials/{rotated['id']}/revoke", {})
    assert revoked.json() == {"ok": True, "registeredRevoked": 3}
    for token in (rotated["token"], child_token, grandchild_token, sibling_token):
        assert agent_http.get("/api/v1/context", headers=bearer(token)).status_code == 401
    assert agent_http.get("/api/v1/context", headers=bearer(unrelated_token)).status_code == 200
    assert agent_http.get("/api/v1/context", headers=bearer(other["token"])).status_code == 200
    assert client.get(f"/api/v1/agent-runs/{run['id']}").json()["endReason"] == "credential_revoked"
    history = client.get("/api/v1/history", params={"entityId": grandchild.json()["id"]}).json()["items"]
    assert history[0]["kind"] == "credential.revoke" and history[0]["after"]["via"] == rotated["id"]


def test_retiring_also_retires_what_the_agent_registered(client, agent_http):
    sponsor = issue(client)
    child, child_token = register(agent_http, sponsor["token"], "Parser fix")
    _, grandchild_token = register(agent_http, child_token, "Parser subagent")
    _, sibling_token = register(agent_http, sponsor["token"], "Docs")
    retired = agent_http.post("/api/v1/agent-registrations/retire", headers=bearer(child_token))
    assert retired.json()["registeredRevoked"] == 1
    assert agent_http.get("/api/v1/context", headers=bearer(grandchild_token)).status_code == 401
    assert agent_http.get("/api/v1/context", headers=bearer(sibling_token)).status_code == 200
    assert agent_http.get("/api/v1/context", headers=bearer(sponsor["token"])).status_code == 200


def test_daily_registration_limit_bounds_register_and_retire_loops(client, agent_http, monkeypatch):
    monkeypatch.setattr(registration, "DAILY_LIMIT", 3)
    sponsor = issue(client)
    for _ in range(3):
        response, token = register(agent_http, sponsor["token"])
        assert response.status_code == 200, response.text
        assert agent_http.post("/api/v1/agent-registrations/retire", headers=bearer(token)).status_code == 200
    refused, _ = register(agent_http, sponsor["token"])
    assert refused.status_code == 429 and "today" in refused.json()["error"]["message"]
