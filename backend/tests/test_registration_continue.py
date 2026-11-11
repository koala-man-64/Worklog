"""A chat often ends before its work does. The identity it registered then continues in the next
chat: it keeps everything that names it, and the new chat takes over the old one's place on the
work and its steps, with what the person decided about the old one."""

import secrets
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog import registration
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
OLD_CHAT = "3f0c6a1e-8d2b-4c5a-9e7f-1a2b3c4d5e6f"
NEW_CHAT = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
LABEL = "Parser fix (Claude)"
REGISTRATION = ("id", "agentId", "label", "avatar", "chatId", "registeredBy", "directory")


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


def bearer(token):
    return {"Authorization": "Bearer " + token}


def write(c, method, path, payload=None, token=None):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **(bearer(token) if token else {})}
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def refused(response, status, code):
    assert (response.status_code, response.json()["error"]["code"]) == (status, code), response.text
    return response.json()["error"]


def enroll(client, agent_http, chat=OLD_CHAT, **extra):
    """A shared credential the person issued, and an identity an agent registered from it for its chat."""
    sponsor = ok(write(client, "POST", "/credentials", {"label": "Shared agents"}))
    token = secrets.token_urlsafe(32)
    identity = ok(write(agent_http, "POST", "/agent-registrations", {"label": LABEL, "token": token, "chatId": chat, **extra},
                        sponsor["token"]))
    return sponsor, identity, token


def carry_on(agent_http, token, chat=NEW_CHAT, new=None, **extra):
    """The identity behind `token` continues in `chat` with a new token, as `register --continue` sends it."""
    new = new or secrets.token_urlsafe(32)
    return agent_http.post("/api/v1/agent-registrations/continue", json={"token": new, "chatId": chat, **extra},
                           headers=bearer(token)), new


def track(agent_http, token, **payload):
    return write(agent_http, "POST", "/chats/track", payload, token)


def plan(client, titles=("Parse", "Tokenize", "Report")):
    steps = [{"id": str(uuid.uuid4()), "title": title} for title in titles]
    return ok(write(client, "POST", "/roadmaps", {"title": "Parser", "steps": steps})), [s["id"] for s in steps]


def chats_on(client, owner_type, owner_id):
    path = f"/api/v1/work-items/{owner_id}" if owner_type == "item" else f"/api/v1/roadmaps/{owner_id}"
    return ok(client.get(path))["chats"]


def step_chat_events(client, step_id):
    return [(e["kind"], e["actorId"], (e["before"] or e["after"])["chatId"])
            for e in ok(client.get("/api/v1/history", params={"entityId": step_id}))["items"] if e["kind"].startswith("chat.")]


def test_the_identity_continues_in_a_new_chat_with_everything_it_holds(client, agent_http):
    sponsor, identity, old = enroll(client, agent_http)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    roadmap, (parse, _, _) = plan(client)
    # What the identity holds: a role on a step, a tie, a ticket assigned to it, a draft, and an open
    # run whose participation reports the work item.
    role = ok(write(agent_http, "POST", "/discussions/roles", {"contextType": "step", "contextId": parse, "label": "Implementation"}, old))
    ok(write(agent_http, "POST", f"/discussions/roles/{role['id']}/claim", {"revision": role["revision"]}, old))
    tie = ok(write(agent_http, "POST", "/agent-links", {"targetType": "roadmap", "targetId": roadmap["id"], "relationship": "Maintainer"}, old))
    ticket = ok(write(agent_http, "POST", "/tickets", {"title": "Lexer drops a token", "assigneeId": identity["agentId"]}, old))
    draft = ok(write(agent_http, "POST", "/proposals", {"title": "Parser notes", "rationale": "Found while fixing it.", "operations": [
        {"action": "note.create", "payload": {"ownerType": "item", "ownerId": item["id"], "title": "Notes", "text": "The lexer."}}]}, old))
    run = ok(write(agent_http, "POST", "/agent-runs", {"clientRunKey": "run-1", "displayName": "Parser fix"}, old))
    participation = ok(write(agent_http, "POST", "/participations", {"runId": run["id"], "itemId": item["id"]}, old))

    response, new = carry_on(agent_http, old)
    continued = ok(response)
    # The same identity under a new credential, registered with the new chat by the same sponsor.
    assert continued["id"] != identity["id"]
    assert {k: continued[k] for k in REGISTRATION if k not in {"id", "chatId"}} == {
        k: identity[k] for k in REGISTRATION if k not in {"id", "chatId"}}
    assert (continued["chatId"], continued["registeredBy"]) == (NEW_CHAT, {"id": sponsor["agentId"], "label": "Shared agents"})
    assert {k: continued["continued"][k] for k in ("credentialId", "chatId", "runsEnded")} == {
        "credentialId": identity["id"], "chatId": OLD_CHAT, "runsEnded": 1}
    assert continued["continued"]["roles"] == [{"id": role["id"], "label": "Implementation", "contextType": "step",
                                                "contextId": parse, "contextTitle": "Parse"}]
    # The old token stops working; the new one speaks for the identity.
    assert agent_http.get("/api/v1/context", headers=bearer(old)).status_code == 401
    assert ok(agent_http.get("/api/v1/agent-registrations/self", headers=bearer(new))) == {k: continued[k] for k in REGISTRATION}
    assert ok(agent_http.get("/api/v1/discussions/participants", headers=bearer(new)))["self"]["id"] == identity["agentId"]
    # The old chat's run ended, releasing what it reported.
    assert ok(agent_http.get(f"/api/v1/agent-runs/{run['id']}", headers=bearer(new)))["endReason"] == "credential_continued"
    assert ok(agent_http.get(f"/api/v1/participations/{participation['id']}", headers=bearer(new)))["releaseReason"] == "credential_continued"
    # What names the identity stays its own.
    [owned] = ok(agent_http.get("/api/v1/discussions/roles", params={"contextType": "step", "contextId": parse}, headers=bearer(new)))["items"]
    assert (owned["ownerId"], owned["revision"]) == (identity["agentId"], role["revision"] + 1)
    assert [t["id"] for t in ok(agent_http.get("/api/v1/agent-links", headers=bearer(new)))["items"]] == [tie["id"]]
    assert ok(agent_http.get(f"/api/v1/tickets/{ticket['id']}", headers=bearer(new)))["assignee"]["id"] == identity["agentId"]
    submitted = ok(write(agent_http, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, new))
    assert submitted["status"] == "submitted"
    # Its runs report the new chat now.
    assert ok(write(agent_http, "POST", "/agent-runs", {"clientRunKey": "run-2", "displayName": "Parser fix"}, new))["externalChatId"] == NEW_CHAT

    # The person sees one identity with both credentials, each with its chat, and what happened.
    settings = {c["id"]: c for c in ok(client.get("/api/v1/settings"))["credentials"]}
    assert (settings[identity["id"]]["revoked"], settings[identity["id"]]["chatId"]) == (True, OLD_CHAT)
    assert (settings[continued["id"]]["revoked"], settings[continued["id"]]["chatId"], settings[continued["id"]]["agentId"]) == (
        False, NEW_CHAT, identity["agentId"])
    assert settings[continued["id"]]["registeredBy"] == {"id": sponsor["agentId"], "label": "Shared agents"}
    detail = ok(client.get(f"/api/v1/agents/{identity['agentId']}"))
    assert detail["active"] is True
    assert {(c["chatId"], c["revoked"]) for c in detail["credentials"]} == {(OLD_CHAT, True), (NEW_CHAT, False)}
    history = ok(client.get("/api/v1/history", params={"entityId": continued["id"]}))["items"]
    assert [(e["kind"], e["actorId"], e["before"]["chatId"], e["after"]["chatId"]) for e in history] == [
        ("credential.continue", identity["id"], OLD_CHAT, NEW_CHAT)]
    with client.app.state.db.read() as conn:
        assert new not in "\n".join(conn.iterdump())


def test_the_new_chat_takes_over_where_the_old_one_was_tracked(client, agent_http):
    _, identity, old = enroll(client, agent_http)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    roadmap, (parse, tokenize, report) = plan(client)
    repository = ok(write(client, "POST", "/repositories", {"name": "parser", "url": "https://github.com/team/parser"}))
    retired_repository = ok(write(client, "POST", "/repositories", {"name": "lexer", "url": "https://github.com/team/lexer"}))
    ok(write(agent_http, "POST", "/agent-runs", {"clientRunKey": "run-1", "displayName": "Parser fix"}, old))
    chat = ok(track(agent_http, old, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse, tokenize, report], provider="claude",
                    model="claude-opus-5-5"))
    # The person links the chat to two repositories and archives one; the plan later drops a step the chat had.
    chat = ok(write(client, "PATCH", f"/chats/{chat['id']}", {"revision": chat["revision"],
                                                               "repositoryIds": [repository["id"], retired_repository["id"]]}))
    ok(write(client, "POST", f"/repositories/{retired_repository['id']}/archive", {"revision": retired_repository["revision"]}))
    current = ok(client.get(f"/api/v1/roadmaps/{roadmap['id']}"))
    ok(write(client, "POST", f"/roadmaps/{roadmap['id']}/revisions", {"revision": current["revision"], "title": "Parser", "steps": [
        {"id": parse, "title": "Parse"}, {"id": tokenize, "title": "Tokenize"}]}))
    on_item = ok(track(agent_http, old, ownerType="item", ownerId=item["id"]))
    elsewhere, (other,) = plan(client, ("Elsewhere",))

    response, new = carry_on(agent_http, old)
    continued = ok(response)["continued"]
    on_roadmap = {c["chatId"]: c for c in chats_on(client, "roadmap", roadmap["id"])}
    taken = on_roadmap[NEW_CHAT]
    # The old chat stays tracked, with the step the plan dropped; the new one takes the rest, under
    # the old one's title, provider, model and repositories.
    assert (on_roadmap[OLD_CHAT]["id"], on_roadmap[OLD_CHAT]["archived"], on_roadmap[OLD_CHAT]["stepIds"]) == (chat["id"], False, [report])
    # Steps assigned at one moment come in the order of their IDs, as a chat's steps always have.
    assert (taken["title"], taken["provider"], taken["model"], taken["repositoryIds"], taken["stepIds"]) == (
        LABEL, "claude", "claude-opus-5-5", [repository["id"]], sorted([parse, tokenize]))
    assert {c["chatId"]: c["id"] == on_item["id"] for c in chats_on(client, "item", item["id"])} == {OLD_CHAT: True, NEW_CHAT: False}
    assert chats_on(client, "roadmap", elsewhere["id"]) == []
    new_on_item = next(c for c in chats_on(client, "item", item["id"]) if c["chatId"] == NEW_CHAT)
    titles = {parse: "Parse", tokenize: "Tokenize"}
    assert continued["chats"] == [
        {"id": taken["id"], "ownerType": "roadmap", "ownerId": roadmap["id"], "ownerTitle": "Parser", "title": LABEL,
         "steps": [{"id": s, "title": titles[s]} for s in sorted(titles)], "created": True},
        {"id": new_on_item["id"], "ownerType": "item", "ownerId": item["id"], "ownerTitle": "Parser fix", "title": LABEL,
         "steps": [], "created": True}]
    # Each step's history says the identity moved it from the old chat to the new one, naming the new
    # one where the old one let it go.
    assert sorted(step_chat_events(client, parse)) == sorted([("chat.assign", identity["id"], OLD_CHAT),
                                                              ("chat.unassign", identity["id"], OLD_CHAT),
                                                              ("chat.assign", identity["id"], NEW_CHAT)])
    [unassigned] = [e for e in ok(client.get("/api/v1/history", params={"entityId": parse}))["items"] if e["kind"] == "chat.unassign"]
    assert (unassigned["before"]["id"], unassigned["after"]["id"]) == (chat["id"], taken["id"])
    history = ok(client.get("/api/v1/history", params={"entityId": taken["id"]}))["items"]
    assert sorted((e["kind"], (e["before"] or {}).get("id") or "") for e in history) == [("chat.continue", chat["id"]), ("chat.create", "")]

    # Once the new chat's run starts, the roadmap shows the identity on those steps through it, and
    # the old chat, whose run ended, no longer counts.
    ok(write(agent_http, "POST", "/agent-runs", {"clientRunKey": "run-2", "displayName": "Parser fix"}, new))
    workers = ok(client.get(f"/api/v1/roadmaps/{roadmap['id']}/agents"))["items"]
    assert [(w["kind"], w["id"]) for w in workers] == [("agent", identity["agentId"])]
    assert sorted((t["stepId"], t["chat"]["id"]) for t in workers[0]["ties"]) == sorted([(parse, taken["id"]), (tokenize, taken["id"])])
    # The new chat takes on more steps as any registered chat does.
    assert ok(track(agent_http, new, ownerType="roadmap", ownerId=elsewhere["id"], stepIds=[other]))["created"] is True


def test_what_the_person_decided_about_the_old_chat_stays(client, agent_http):
    _, _, old = enroll(client, agent_http)
    roadmap, (parse, tokenize, report) = plan(client)
    chat = ok(track(agent_http, old, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse, tokenize]))
    # The step pane's "Unassign" takes the old chat off one step.
    ok(write(client, "PATCH", f"/chats/{chat['id']}", {"revision": chat["revision"], "stepIds": [tokenize]}))
    # A chat the person archived, and work they archived, take no new chat.
    archived_chat = ok(write(client, "POST", "/work-items", {"title": "Archived chat"}))
    record = ok(track(agent_http, old, ownerType="item", ownerId=archived_chat["id"]))
    ok(write(client, "POST", f"/chats/{record['id']}/archive", {"revision": record["revision"]}))
    archived_work = ok(write(client, "POST", "/work-items", {"title": "Archived work"}))
    ok(track(agent_http, old, ownerType="item", ownerId=archived_work["id"]))
    current = ok(client.get(f"/api/v1/work-items/{archived_work['id']}"))
    ok(write(client, "POST", f"/work-items/{archived_work['id']}/archive", {"revision": current["revision"]}))

    response, new = carry_on(agent_http, old)
    chats = ok(response)["continued"]["chats"]
    assert [(c["ownerId"], [s["id"] for s in c["steps"]]) for c in chats] == [(roadmap["id"], [tokenize])]
    assert [c["chatId"] for c in chats_on(client, "item", archived_chat["id"])] == [OLD_CHAT]
    assert [c["chatId"] for c in chats_on(client, "item", archived_work["id"])] == [OLD_CHAT]
    # The step the person took the old chat off stays off the new one, and off the next one too.
    error = refused(track(agent_http, new, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[report, parse]), 403, "forbidden")
    assert error["fields"] == {"stepIds": [parse]}
    response, newer = carry_on(agent_http, new, chat="b2d4f6a8-1c3e-4a5b-9d7f-0e2c4a6b8d1f")
    assert [s["id"] for s in ok(response)["continued"]["chats"][0]["steps"]] == [tokenize]
    refused(track(agent_http, newer, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse]), 403, "forbidden")
    assert [s for s in ok(track(agent_http, newer, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[report]))["stepIds"]] == [tokenize, report]
    # The person can still put it there.
    latest = next(c for c in chats_on(client, "roadmap", roadmap["id"]) if c["chatId"] == "b2d4f6a8-1c3e-4a5b-9d7f-0e2c4a6b8d1f")
    assert ok(write(client, "PATCH", f"/chats/{latest['id']}", {"revision": latest["revision"],
                                                                 "stepIds": [*latest["stepIds"], parse]}))["stepIds"] == [tokenize, report, parse]


def test_a_new_chat_tracked_already_keeps_its_record_and_takes_the_steps(client, agent_http):
    _, _, old = enroll(client, agent_http)
    roadmap, (parse, tokenize, report) = plan(client)
    chat = ok(track(agent_http, old, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse, tokenize]))
    # The person tracked the new chat there already, on one of those steps and on another.
    there = ok(write(client, "POST", "/chats", {"ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": NEW_CHAT, "title": "Next chat",
                                                "stepIds": [tokenize, report]}))
    response, _ = carry_on(agent_http, old)
    [continued] = ok(response)["continued"]["chats"]
    assert (continued["id"], continued["title"], continued["created"]) == (there["id"], "Next chat", False)
    # The steps the person assigned at one moment come in the order of their IDs, then the one it took on.
    steps = [*sorted([tokenize, report]), parse]
    assert [s["id"] for s in continued["steps"]] == steps
    assert {c["id"]: c["stepIds"] for c in chats_on(client, "roadmap", roadmap["id"])} == {chat["id"]: [], there["id"]: steps}


def test_an_identity_that_comes_back_to_an_earlier_chat_gets_its_steps_back(client, agent_http):
    _, _, old = enroll(client, agent_http)
    roadmap, (parse, tokenize, report) = plan(client)
    first = ok(track(agent_http, old, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse, tokenize, report]))
    # The person takes the chat off one step.
    ok(write(client, "PATCH", f"/chats/{first['id']}", {"revision": first["revision"], "stepIds": [parse, tokenize]}))
    response, new = carry_on(agent_http, old)
    [moved] = ok(response)["continued"]["chats"]
    # The earlier chat comes back, as a resumed session does under its ID, and the identity continues there.
    response, back = carry_on(agent_http, new, chat=OLD_CHAT)
    [returned] = ok(response)["continued"]["chats"]
    both = sorted([parse, tokenize])
    assert (returned["id"], returned["created"], sorted(s["id"] for s in returned["steps"])) == (first["id"], False, both)
    assert {c["id"]: sorted(c["stepIds"]) for c in chats_on(client, "roadmap", roadmap["id"])} == {first["id"]: both, moved["id"]: []}
    # Moving a step never counts as the person taking a chat off it; what they took off stays off.
    refused(track(agent_http, back, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[report]), 403, "forbidden")
    # Each chat continues the other now, and the steps move on again with the next chat.
    response, _ = carry_on(agent_http, back)
    assert sorted(s["id"] for s in ok(response)["continued"]["chats"][0]["steps"]) == both


def test_the_old_chats_records_move_whoever_else_registered_with_it(client, agent_http):
    sponsor, _, old = enroll(client, agent_http)
    # Another identity registered with the same chat, as for a subagent: a chat's records belong to the chat.
    other = secrets.token_urlsafe(32)
    ok(write(agent_http, "POST", "/agent-registrations", {"label": "Lexer (Claude)", "token": other, "chatId": OLD_CHAT}, sponsor["token"]))
    roadmap, (parse, _, _) = plan(client)
    ok(track(agent_http, other, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse]))
    response, _ = carry_on(agent_http, old)
    assert [s["id"] for s in ok(response)["continued"]["chats"][0]["steps"]] == [parse]
    assert {c["chatId"]: c["stepIds"] for c in chats_on(client, "roadmap", roadmap["id"])} == {OLD_CHAT: [], NEW_CHAT: [parse]}
    # The other identity still works in the old chat.
    assert ok(agent_http.get("/api/v1/agent-registrations/self", headers=bearer(other)))["chatId"] == OLD_CHAT


def test_a_message_waiting_in_the_old_chat_ends_with_its_run(client, agent_http):
    _, _, old = enroll(client, agent_http)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    ok(write(agent_http, "POST", "/agent-runs", {"clientRunKey": "run-1", "displayName": "Parser fix"}, old))
    chat = ok(track(agent_http, old, ownerType="item", ownerId=item["id"]))
    ok(write(client, "POST", f"/chats/{chat['id']}/messages", {"text": "Please also handle the revision field."}))
    ok(carry_on(agent_http, old)[0])
    [delivery] = ok(client.get(f"/api/v1/chats/{chat['id']}/messages"))["items"][0]["deliveries"]
    assert (delivery["status"], delivery["reason"]) == ("expired", "credential_continued")


def test_a_failure_partway_changes_nothing(client, agent_http, monkeypatch):
    from worklog import support
    from worklog.common import AppError
    _, identity, old = enroll(client, agent_http)
    roadmap, (parse, _, _) = plan(client)
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))
    run = ok(write(agent_http, "POST", "/agent-runs", {"clientRunKey": "run-1", "displayName": "Parser fix"}, old))
    ok(track(agent_http, old, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse]))
    ok(track(agent_http, old, ownerType="item", ownerId=item["id"]))
    before = {owner: chats_on(client, *owner) for owner in (("roadmap", roadmap["id"]), ("item", item["id"]))}
    real, calls = support.apply, []

    def fail_on_the_third(*args, **kwargs):
        # The roadmap's two records change, then the work item's new record fails.
        calls.append(args[2])
        if len(calls) == 3:
            raise AppError(409, "conflict", "This record changed. Reload and review your draft.")
        return real(*args, **kwargs)

    monkeypatch.setattr(support, "apply", fail_on_the_third)
    response, new = carry_on(agent_http, old)
    refused(response, 409, "conflict")
    assert calls == ["chat.create", "chat.update", "chat.create"]
    # Nothing changed: the old token still speaks for the identity, its run is open, its chats are as they were.
    assert ok(agent_http.get("/api/v1/agent-registrations/self", headers=bearer(old)))["chatId"] == OLD_CHAT
    assert agent_http.get("/api/v1/agent-registrations/self", headers=bearer(new)).status_code == 401
    assert ok(agent_http.get(f"/api/v1/agent-runs/{run['id']}", headers=bearer(old)))["endedAt"] is None
    assert {owner: chats_on(client, *owner) for owner in before} == before
    with client.app.state.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM communication_credentials WHERE agent_id=?", (identity["agentId"],)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM events WHERE kind IN ('credential.continue','chat.continue')").fetchone()[0] == 0


def test_an_old_chat_tracked_twice_in_one_place_becomes_one_record(client, agent_http):
    _, _, old = enroll(client, agent_http)
    roadmap, (parse, tokenize, _) = plan(client)
    first = ok(track(agent_http, old, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse]))
    # The person tracked the same chat there again, on another step.
    again = ok(write(client, "POST", "/chats", {"ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": OLD_CHAT, "title": "Again",
                                                "stepIds": [tokenize]}))
    [continued] = ok(carry_on(agent_http, old)[0])["continued"]["chats"]
    assert (continued["created"], sorted(s["id"] for s in continued["steps"])) == (True, sorted([parse, tokenize]))
    assert {c["id"]: sorted(c["stepIds"]) for c in chats_on(client, "roadmap", roadmap["id"])} == {
        first["id"]: [], again["id"]: [], continued["id"]: sorted([parse, tokenize])}


def test_only_a_live_identity_an_agent_registered_continues_and_only_into_another_chat(client, agent_http):
    sponsor, identity, old = enroll(client, agent_http)
    # A credential the person issued in Settings may be shared; it registers new identities instead.
    refused(carry_on(agent_http, sponsor["token"])[0], 403, "agent_scope")
    refused(write(client, "POST", "/agent-registrations/continue", {"token": secrets.token_urlsafe(32), "chatId": NEW_CHAT}), 403, "agent_scope")
    refused(carry_on(agent_http, old, chat=OLD_CHAT)[0], 409, "same_chat")
    refused(carry_on(agent_http, old, new=old)[0], 409, "token_in_use")
    for extra in ({"chatId": "two words"}, {"directory": "relative/path"}, {"label": "Another label"}, {"avatar": "teal-visor-antenna"}):
        assert carry_on(agent_http, old, **extra)[0].status_code == 422, extra
    refused(carry_on(agent_http, old, new="ab" * 22)[0], 422, "validation")
    # None of that changed anything.
    assert ok(agent_http.get("/api/v1/agent-registrations/self", headers=bearer(old)))["chatId"] == OLD_CHAT
    with client.app.state.db.read() as conn:
        assert conn.execute("SELECT count(*) FROM communication_credentials WHERE agent_id=?", (identity["agentId"],)).fetchone()[0] == 1
    # Once it continued, its old token can't continue again; a revoked identity can't at all.
    ok(carry_on(agent_http, old)[0])
    assert carry_on(agent_http, old, chat="another-chat")[0].status_code == 401
    _, revoked, revoked_token = enroll(client, agent_http, chat="revoked-chat")
    ok(write(client, "POST", f"/credentials/{revoked['id']}/revoke", {}))
    assert carry_on(agent_http, revoked_token)[0].status_code == 401


def test_the_identities_it_registered_stay_and_it_retires_as_before(client, agent_http):
    _, identity, old = enroll(client, agent_http)
    child_token = secrets.token_urlsafe(32)
    child = ok(write(agent_http, "POST", "/agent-registrations", {"label": "Lexer (Claude)", "token": child_token, "chatId": "child-chat"}, old))
    response, new = carry_on(agent_http, old)
    continued = ok(response)
    assert ok(agent_http.get("/api/v1/agent-registrations/self", headers=bearer(child_token)))["registeredBy"]["id"] == identity["agentId"]
    settings = {c["id"]: c for c in ok(client.get("/api/v1/settings"))["credentials"]}
    assert settings[continued["id"]]["registeredActive"] == 1
    # Retiring the continued identity takes what it registered with it, as retiring always did.
    retired = ok(write(agent_http, "POST", "/agent-registrations/retire", {}, new))
    assert (retired["agentId"], retired["registeredRevoked"]) == (identity["agentId"], 1)
    assert agent_http.get("/api/v1/agent-registrations/self", headers=bearer(child_token)).status_code == 401
    assert child["agentId"] != identity["agentId"]


def test_an_identity_registered_before_chats_were_recorded_continues_into_one(client, agent_http):
    _, identity, old = enroll(client, agent_http)
    with client.app.state.db.transaction() as conn:
        conn.execute("UPDATE communication_credentials SET chat_id=NULL WHERE credential_id=?", (identity["id"],))
    response, new = carry_on(agent_http, old)
    continued = ok(response)
    assert (continued["chatId"], continued["continued"]["chatId"], continued["continued"]["chats"]) == (NEW_CHAT, None, [])
    assert ok(write(agent_http, "POST", "/agent-runs", {"clientRunKey": "run-1", "displayName": "Parser"}, new))["externalChatId"] == NEW_CHAT


def test_a_rotated_identity_continues_from_the_chat_it_registered_with(client, agent_http):
    sponsor, identity, old = enroll(client, agent_http)
    roadmap, (parse, _, _) = plan(client)
    ok(track(agent_http, old, ownerType="roadmap", ownerId=roadmap["id"], stepIds=[parse]))
    # The person rotated its token in Settings: the new credential names no chat and no sponsor.
    rotated = ok(write(client, "POST", f"/credentials/{identity['id']}/rotate", {}))
    continued = ok(carry_on(agent_http, rotated["token"])[0])
    assert (continued["agentId"], continued["registeredBy"]["id"], continued["continued"]["chatId"]) == (
        identity["agentId"], sponsor["agentId"], OLD_CHAT)
    assert [s["id"] for s in continued["continued"]["chats"][0]["steps"]] == [parse]
    assert agent_http.get("/api/v1/context", headers=bearer(rotated["token"])).status_code == 401


def test_review_names_the_directory_of_the_chat_that_made_each_proposal(client, agent_http):
    _, _, old = enroll(client, agent_http, directory="C:\\Projects\\parser")
    item = ok(write(client, "POST", "/work-items", {"title": "Parser fix"}))

    def propose(token, title):
        draft = ok(write(agent_http, "POST", "/proposals", {"title": title, "rationale": "r", "operations": [
            {"action": "item.update", "payload": {"id": item["id"], "revision": 1, "description": title}}]}, token))
        return ok(write(agent_http, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, token))["id"]

    before = propose(old, "From the first chat")
    response, new = carry_on(agent_http, old, directory="C:\\Projects\\parser-2")
    assert ok(response)["directory"] == "C:\\Projects\\parser-2"
    after = propose(new, "From the next chat")
    shown = {p["id"]: p["actorDirectory"] for p in ok(client.get("/api/v1/proposals", params={"status": "submitted"}))["items"]}
    assert shown == {before: "C:\\Projects\\parser", after: "C:\\Projects\\parser-2"}


def test_continuing_counts_toward_the_daily_limit(client, agent_http, monkeypatch):
    _, _, old = enroll(client, agent_http)
    monkeypatch.setattr(registration, "DAILY_LIMIT", 2)
    response, new = carry_on(agent_http, old)
    ok(response)
    error = refused(carry_on(agent_http, new, chat="third-chat")[0], 429, "agent_limit")
    assert "continuing resumes tomorrow" in error["message"]
    assert ok(agent_http.get("/api/v1/agent-registrations/self", headers=bearer(new)))["chatId"] == NEW_CHAT


def test_an_agent_reads_its_own_registration(client, agent_http):
    sponsor, identity, token = enroll(client, agent_http, directory="C:\\Projects\\parser")
    assert ok(agent_http.get("/api/v1/agent-registrations/self", headers=bearer(token))) == identity
    # A credential issued in Settings has no sponsor and no chat.
    issued = ok(agent_http.get("/api/v1/agent-registrations/self", headers=bearer(sponsor["token"])))
    assert (issued["id"], issued["registeredBy"], issued["chatId"], issued["directory"]) == (sponsor["id"], None, None, None)
    refused(client.get("/api/v1/agent-registrations/self"), 403, "agent_scope")
    ok(write(client, "POST", f"/credentials/{identity['id']}/revoke", {}))
    assert agent_http.get("/api/v1/agent-registrations/self", headers=bearer(token)).status_code == 401
