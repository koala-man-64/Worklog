"""An agent tracks its own chat on the work it does, without a proposal: the chat it registered
with, on a work item or on a roadmap and its steps. What the person decided about that chat
stays: one they archived there stays archived, and a step they took it off is not assigned again."""

import io
import json
import secrets
import uuid
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient
from worklog import cli
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
CHAT = "5e2a9c1d-7b3f-4e8a-9d6c-2f1b0a3c4d5e"
LABEL = "Parser fix (Claude)"


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


def write(c, method, path, payload=None, token=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4()), **({"Authorization": "Bearer " + token} if token else {})}
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def refused(response, status, code):
    assert (response.status_code, response.json()["error"]["code"]) == (status, code), response.text
    return response.json()["error"]


def enroll(client, agent_http, chat=CHAT):
    """A shared credential the person issued, and an identity an agent registered from it for its chat."""
    sponsor = ok(write(client, "POST", "/credentials", {"label": "Shared agents"}))
    token = secrets.token_urlsafe(32)
    identity = ok(write(agent_http, "POST", "/agent-registrations", {"label": LABEL, "token": token, "chatId": chat},
                        sponsor["token"]))
    return sponsor, identity, token


def track(c, token, key=None, **payload):
    return write(c, "POST", "/chats/track", payload, token, key)


def plan(client, titles=("Parse", "Tokenize", "Report")):
    steps = [{"id": str(uuid.uuid4()), "title": title} for title in titles]
    return ok(write(client, "POST", "/roadmaps", {"title": "Parser", "steps": steps})), [s["id"] for s in steps]


def chats_on(client, owner_type, owner_id):
    path = f"/api/v1/work-items/{owner_id}" if owner_type == "item" else f"/api/v1/roadmaps/{owner_id}"
    return ok(client.get(path))["chats"]


def test_an_agent_tracks_its_registered_chat_on_a_work_item_without_a_proposal(client, agent_http):
    _, identity, token = enroll(client, agent_http)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    chat = ok(track(agent_http, token, ownerType="item", ownerId=item["id"], provider="claude", model="claude-opus-5-5"))
    assert chat["created"] is True
    assert (chat["ownerType"], chat["ownerId"], chat["chatId"], chat["title"], chat["provider"], chat["model"], chat["stepIds"],
            chat["archived"]) == ("item", item["id"], CHAT, LABEL, "claude", "claude-opus-5-5", [], False)
    assert [c["id"] for c in chats_on(client, "item", item["id"])] == [chat["id"]]
    # No proposal: the chat's history says the agent's own credential tracked it.
    assert ok(client.get("/api/v1/proposals"))["items"] == []
    history = ok(client.get("/api/v1/history", params={"entityId": chat["id"]}))["items"]
    assert [(e["kind"], e["actorKind"], e["actorId"]) for e in history] == [("chat.create", "agent", identity["id"])]
    # Chats lists it with the work, and names the agent registered with it.
    listed = [c for c in ok(client.get("/api/v1/chats"))["items"] if c["id"] == chat["id"]]
    assert listed and listed[0]["owner"] == {"type": "item", "id": item["id"], "title": "Parser fix", "archived": False}
    assert [(a["id"], a["binding"]) for a in listed[0]["agents"]] == [(identity["agentId"], "registered")]

    # Tracking it again finds the same chat, changed in nothing.
    again = ok(track(agent_http, token, ownerType="item", ownerId=item["id"], title="Another title", provider="codex"))
    assert again == {**chat, "created": False}
    assert len(ok(client.get("/api/v1/history", params={"entityId": chat["id"]}))["items"]) == 1


def test_the_chat_its_reported_work_tracked_is_the_one_it_finds(client, agent_http):
    _, _, token = enroll(client, agent_http)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    run = ok(write(agent_http, "POST", "/agent-runs", {"clientRunKey": "run-1", "displayName": "Parser run"}, token))
    ok(write(agent_http, "POST", "/participations", {"runId": run["id"], "itemId": item["id"]}, token))
    [reported] = chats_on(client, "item", item["id"])
    found = ok(track(agent_http, token, ownerType="item", ownerId=item["id"]))
    assert (found["id"], found["title"], found["created"]) == (reported["id"], "Parser run", False)


def test_on_a_roadmap_the_chat_takes_on_steps_and_keeps_the_ones_it_has(client, agent_http):
    _, identity, token = enroll(client, agent_http)
    roadmap, (parse, tokenize, report) = plan(client)
    first = ok(track(agent_http, token, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse, parse]))
    assert (first["created"], first["stepIds"], first["title"]) == (True, [parse], LABEL)
    more = ok(track(agent_http, token, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[tokenize, parse], title="Ignored"))
    assert (more["id"], more["created"], more["stepIds"], more["title"]) == (first["id"], False, [parse, tokenize], LABEL)
    assert more["revision"] == first["revision"] + 1
    # Naming no new step changes nothing.
    assert ok(track(agent_http, token, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse])) == {**more, "created": False}
    assert ok(track(agent_http, token, ownerType="roadmap", ownerId=roadmap["id"]))["revision"] == more["revision"]
    [shown] = chats_on(client, "roadmap", roadmap["id"])
    assert (shown["id"], shown["stepIds"]) == (first["id"], [parse, tokenize])
    # Each step's history says which agent took it on.
    def chat_events(step_id):
        return [(e["kind"], e["actorKind"], e["actorId"])
                for e in ok(client.get("/api/v1/history", params={"entityId": step_id}))["items"] if e["kind"].startswith("chat.")]
    assert chat_events(tokenize) == [("chat.assign", "agent", identity["id"])]
    assert chat_events(report) == []


def test_with_an_archived_and_an_active_record_there_it_finds_the_active_one(client, agent_http):
    _, _, token = enroll(client, agent_http)
    roadmap, (parse, _, _) = plan(client)
    # The person tracked the chat there, archived that record, then tracked it again.
    earlier = ok(write(client, "POST", "/chats", {"ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": CHAT, "title": "Earlier"}))
    ok(write(client, "POST", f"/chats/{earlier['id']}/archive", {"revision": earlier["revision"]}))
    later = ok(write(client, "POST", "/chats", {"ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": CHAT, "title": "Later"}))
    found = ok(track(agent_http, token, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse]))
    assert (found["id"], found["title"], found["created"], found["stepIds"]) == (later["id"], "Later", False, [parse])
    assert {c["id"]: (c["archived"], c["stepIds"]) for c in chats_on(client, "roadmap", roadmap["id"])} == {
        earlier["id"]: (True, []), later["id"]: (False, [parse])}


def test_a_chat_the_person_archived_there_stays_archived(client, agent_http):
    _, _, token = enroll(client, agent_http)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    chat = ok(track(agent_http, token, ownerType="item", ownerId=item["id"]))
    ok(write(client, "POST", f"/chats/{chat['id']}/archive", {"revision": chat["revision"]}))
    error = refused(track(agent_http, token, ownerType="item", ownerId=item["id"]), 409, "archived")
    assert error["fields"] == {"id": chat["id"]}
    assert [(c["id"], c["archived"]) for c in chats_on(client, "item", item["id"])] == [(chat["id"], True)]
    # Once the person recovers it, the agent finds it again.
    archived = chats_on(client, "item", item["id"])[0]
    ok(write(client, "POST", f"/chats/{chat['id']}/recover", {"revision": archived["revision"]}))
    assert ok(track(agent_http, token, ownerType="item", ownerId=item["id"]))["id"] == chat["id"]


def test_a_step_the_person_took_the_chat_off_is_not_assigned_again(client, agent_http):
    _, _, token = enroll(client, agent_http)
    roadmap, (parse, tokenize, report) = plan(client)
    chat = ok(track(agent_http, token, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse, tokenize]))
    # The step pane's "Unassign": the person's update to the chat.
    unassigned = ok(write(client, "PATCH", f"/chats/{chat['id']}", {"revision": chat["revision"], "stepIds": [tokenize]}))
    error = refused(track(agent_http, token, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[report, parse]), 403, "forbidden")
    assert error["fields"] == {"stepIds": [parse]}
    assert chats_on(client, "roadmap", roadmap["id"])[0]["revision"] == unassigned["revision"]
    # Other steps it can still take on, and the person can assign the step again.
    assert ok(track(agent_http, token, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[report]))["stepIds"] == [tokenize, report]
    current = chats_on(client, "roadmap", roadmap["id"])[0]
    assert ok(write(client, "PATCH", f"/chats/{chat['id']}", {"revision": current["revision"],
                                                               "stepIds": [*current["stepIds"], parse]}))["stepIds"] == [tokenize, report, parse]


def test_only_an_identity_registered_with_its_chat_tracks_it(client, agent_http):
    sponsor, _, _ = enroll(client, agent_http)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    # A credential issued in Settings registered no chat, and no request names one.
    refused(track(agent_http, sponsor["token"], ownerType="item", ownerId=item["id"]), 403, "agent_scope")
    refused(track(agent_http, sponsor["token"], ownerType="item", ownerId=item["id"], chatId=CHAT), 422, "validation")
    # The person adds chats under "Agent chats", with POST /chats.
    refused(track(client, None, ownerType="item", ownerId=item["id"]), 403, "forbidden")
    assert chats_on(client, "item", item["id"]) == []


def test_the_work_must_exist_be_active_and_hold_the_steps(client, agent_http):
    _, _, token = enroll(client, agent_http)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    roadmap, (parse, tokenize, _) = plan(client)
    elsewhere, (other_step, *_) = plan(client, ("Elsewhere",))
    refused(track(agent_http, token, ownerType="item", ownerId=str(uuid.uuid4())), 404, "not_found")
    refused(track(agent_http, token, ownerType="step", ownerId=parse), 422, "validation")
    # A work item's chat takes no steps, whether new or tracked already.
    refused(track(agent_http, token, ownerType="item", ownerId=item["id"], stepIds=[parse]), 422, "validation")
    on_item = ok(track(agent_http, token, ownerType="item", ownerId=item["id"]))
    refused(track(agent_http, token, ownerType="item", ownerId=item["id"], stepIds=[parse]), 422, "validation")
    assert [(c["id"], c["revision"], c["stepIds"]) for c in chats_on(client, "item", item["id"])] == [
        (on_item["id"], on_item["revision"], [])]
    refused(track(agent_http, token, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[other_step]), 422, "validation")
    # A retired step takes on no chat.
    ok(write(client, "POST", f"/roadmaps/{roadmap['id']}/revisions",
             {"revision": roadmap["revision"], "title": "Parser", "steps": [{"id": parse, "title": "Parse"}]}))
    refused(track(agent_http, token, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[tokenize]), 409, "conflict")
    assert chats_on(client, "roadmap", roadmap["id"]) == []
    # Archived work takes no new chats.
    current = ok(client.get(f"/api/v1/work-items/{item['id']}"))
    ok(write(client, "POST", f"/work-items/{item['id']}/archive", {"revision": current["revision"]}))
    refused(track(agent_http, token, ownerType="item", ownerId=item["id"]), 409, "archived")
    ok(write(client, "POST", f"/roadmaps/{elsewhere['id']}/archive", {"revision": elsewhere["revision"]}))
    refused(track(agent_http, token, ownerType="roadmap", ownerId=elsewhere["id"]), 409, "archived")


def test_a_retry_under_its_key_returns_the_first_result_and_a_revoked_identity_is_refused(client, agent_http):
    _, identity, token = enroll(client, agent_http)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    first = ok(track(agent_http, token, key="track-parser-fix", ownerType="item", ownerId=item["id"]))
    assert ok(track(agent_http, token, key="track-parser-fix", ownerType="item", ownerId=item["id"])) == first
    refused(track(agent_http, token, key="track-parser-fix", ownerType="item", ownerId=item["id"], title="Other"),
            409, "idempotency_conflict")
    refused(write(agent_http, "POST", "/chats/track", {"ownerType": "item", "ownerId": item["id"]}, token, key="short"),
            422, "idempotency")
    missing = agent_http.post("/api/v1/chats/track", json={"ownerType": "item", "ownerId": item["id"]},
                              headers={"Authorization": "Bearer " + token})
    refused(missing, 428, "idempotency")
    ok(write(client, "POST", f"/credentials/{identity['id']}/revoke", {}))
    assert track(agent_http, token, ownerType="item", ownerId=item["id"]).status_code == 401
    assert len(chats_on(client, "item", item["id"])) == 1


def test_a_credential_revoked_while_the_call_waits_gets_nothing_not_even_a_replay(client, agent_http, monkeypatch):
    from worklog import guard
    _, identity, token = enroll(client, agent_http)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    ok(track(agent_http, token, key="track-before-revoke", ownerType="item", ownerId=item["id"]))
    read_body = guard.read_body

    async def revoke_after_authentication(receive, headers, maximum):
        # The person's revoke commits after the guard accepted the token.
        with client.app.state.db.transaction() as conn:
            conn.execute("UPDATE credentials SET revoked=1 WHERE id=?", (identity["id"],))
        return await read_body(receive, headers, maximum)

    # Only the recheck inside the write lock stops a replay: the receipt would answer before any
    # tracking code runs.
    monkeypatch.setattr(guard, "read_body", revoke_after_authentication)
    replay = track(agent_http, token, key="track-before-revoke", ownerType="item", ownerId=item["id"])
    refused(replay, 401, "authentication")
    assert len(chats_on(client, "item", item["id"])) == 1


def test_the_cli_tracks_the_registered_chat_on_a_roadmap_step(tmp_path, monkeypatch, capsys):
    """The whole path an agent takes: register for its chat, then track it with `chats track`."""
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "agent-data"))
    app = create_app(tmp_path / "server", testing=True)
    with TestClient(app, base_url=ORIGIN) as person, TestClient(app, base_url=ORIGIN) as wire:
        person.headers["Origin"] = ORIGIN
        person.headers["X-CSRF-Token"] = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"},
                                                     json={}).json()["csrfToken"]
        sponsor = ok(write(person, "POST", "/credentials", {"label": "Shared agents"}))
        roadmap, (parse, tokenize, _) = plan(person)
        monkeypatch.setenv("WORKLOG_AGENT_TOKEN", sponsor["token"])

        def bridge(request, **kwargs):
            # The CLI's request as sent: no browser origin, no session cookie.
            response = wire.request(request.get_method(), request.full_url, content=request.data, headers=dict(request.header_items()))
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, "rejected", {}, io.BytesIO(response.content))
            return io.BytesIO(response.content)

        monkeypatch.setattr(cli, "_open", bridge)

        def run(*args):
            code = cli.main(["agent", *args])
            out, err = capsys.readouterr()
            assert code == 0, err
            return json.loads(out)

        run("register", "--chat", CHAT, "--name", "parser-fix", "--label", LABEL)
        tracked = run("--as", "parser-fix", "chats", "track", "--roadmap", roadmap["id"], "--step", parse,
                      "--provider", "claude", "--request-id", "track-parser-1")
        assert (tracked["created"], tracked["chatId"], tracked["title"], tracked["stepIds"]) == (True, CHAT, LABEL, [parse])
        more = run("--as", "parser-fix", "chats", "track", "--roadmap", roadmap["id"], "--step", tokenize,
                   "--request-id", "track-parser-2")
        assert (more["id"], more["created"], more["stepIds"]) == (tracked["id"], False, [parse, tokenize])
        [shown] = chats_on(person, "roadmap", roadmap["id"])
        assert (shown["chatId"], shown["provider"], shown["stepIds"]) == (CHAT, "claude", [parse, tokenize])
        # The credential issued in Settings tracks nothing, and says what to do instead.
        assert cli.main(["agent", "chats", "track", "--roadmap", roadmap["id"], "--request-id", "track-shared-1"]) == 1
        assert "HTTP 403 agent_scope" in capsys.readouterr().err
