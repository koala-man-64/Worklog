"""The agent registry gathers what agents already recorded and never records anything itself."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def write(c, path, payload=None, agent=None, method="POST"):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json={} if payload is None else payload)


def get(c, path, agent=None):
    return c.get("/api/v1" + path, headers={"Authorization": "Bearer " + agent["token"]} if agent else {})


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def enroll(c, label):
    return ok(write(c, "/credentials", {"label": label}))


def run(c, credential, **extra):
    return ok(write(c, "/agent-runs", {"clientRunKey": str(uuid.uuid4()), "displayName": "Implementation", **extra}, credential))


def ties(detail, kind):
    return {tie["id"]: (tie["via"], tie["current"]) for tie in detail[kind]["items"]}


def test_every_identity_is_listed_with_active_agents_first(client):
    idle = enroll(client, "Idle reviewer")
    builder = enroll(client, "Builder")
    retired = enroll(client, "Retired")
    ok(write(client, f"/credentials/{retired['id']}/revoke"))
    running = run(client, builder, provider="claude")

    listing = ok(get(client, "/agents"))
    assert [a["label"] for a in listing["items"]] == ["Builder", "Idle reviewer", "Retired"]
    busy, quiet, gone = listing["items"]
    assert (busy["openRuns"], busy["runCount"], busy["providers"]) == (1, 1, ["claude"])
    assert busy["lastActivityAt"] == running["createdAt"]
    assert quiet == {"id": idle["agentId"], "label": "Idle reviewer", "active": True, "createdAt": quiet["createdAt"],
                     "lastActivityAt": None, "providers": [], "openRuns": 0, "runCount": 0, "messageCount": 0,
                     "working": [], "roles": []}
    assert not gone["active"]
    page = ok(get(client, "/agents?limit=1&offset=1"))
    assert (page["total"], [a["label"] for a in page["items"]]) == (3, ["Idle reviewer"])
    assert get(client, "/agents?limit=201").status_code == 422


def test_reported_work_ties_the_item_its_roadmap_and_repositories(client):
    roadmap = ok(write(client, "/roadmaps", {"title": "Platform", "steps": [{"title": "API"}]}))
    work = ok(write(client, "/work-items", {"title": "Build the API", "roadmapId": roadmap["id"], "stepId": roadmap["steps"][0]["id"]}))
    repository = ok(write(client, "/repositories", {"name": "Worklog"}))
    builder = enroll(client, "Builder")
    running = run(client, builder)
    reported = ok(write(client, "/participations", {"runId": running["id"], "itemId": work["id"], "description": "Writing routes",
                                                      "contexts": [{"repositoryId": repository["id"], "branch": "claude/api"}]}, builder))

    [current] = ok(get(client, "/agents"))["items"][0]["working"]
    assert (current["item"]["title"], current["roadmap"]["title"], current["runName"]) == ("Build the API", "Platform", "Implementation")
    assert (current["description"], current["contexts"][0]["branch"], current["stale"]) == ("Writing routes", "claude/api", False)
    detail = ok(get(client, f"/agents/{builder['agentId']}"))
    assert ties(detail, "workItems") == {work["id"]: (["participation"], True)}
    assert detail["workItems"]["items"][0]["roadmap"]["title"] == "Platform"
    assert ties(detail, "roadmaps") == {roadmap["id"]: (["participation"], True)}
    assert ties(detail, "repositories") == {repository["id"]: (["participation"], True)}

    with client.app.state.db.transaction() as c:
        c.execute("UPDATE participations SET last_seen_at=?", ((datetime.now(UTC) - timedelta(hours=3)).isoformat(),))
    assert ok(get(client, "/agents"))["items"][0]["working"][0]["stale"]
    ok(write(client, f"/participations/{reported['id']}/release", {"revision": reported["revision"]}, builder))
    detail = ok(get(client, f"/agents/{builder['agentId']}"))
    # Released work stays in the record, no longer current.
    assert ties(detail, "workItems") == {work["id"]: (["participation"], False)}
    assert detail["working"] == []


def test_messages_roles_runs_and_tracked_chats_tie_through_their_contexts(client):
    roadmap = ok(write(client, "/roadmaps", {"title": "Platform", "steps": [{"title": "API"}, {"title": "Web"}]}))
    web = roadmap["steps"][1]["id"]
    work = ok(write(client, "/work-items", {"title": "Unplanned fix"}))
    repository = ok(write(client, "/repositories", {"name": "Worklog"}))
    chat = ok(write(client, "/chats", {"ownerType": "item", "ownerId": work["id"], "chatId": "chat-1", "title": "Fix session",
                                        "provider": "claude", "model": "opus", "repositoryIds": [repository["id"]]}))
    builder, other = enroll(client, "Builder"), enroll(client, "Other")
    ok(write(client, "/discussions", {"text": "Starting on the plan", "title": "Plan", "chatId": "chat-1",
                                       "contexts": [{"type": "roadmap", "id": roadmap["id"]}]}, builder))
    ok(write(client, "/discussions", {"text": "Unrelated", "contexts": [{"type": "item", "id": work["id"]}]}, other))
    role = ok(write(client, "/discussions/roles", {"contextType": "step", "contextId": web, "label": "Review"}))
    ok(write(client, f"/discussions/roles/{role['id']}/claim", {"revision": role["revision"]}, builder))

    detail = ok(get(client, f"/agents/{builder['agentId']}"))
    assert detail["messageCount"] == 1
    assert [(r["label"], r["contextTitle"], r["roadmap"]["title"]) for r in detail["roles"]] == [("Review", "Web", "Platform")]
    assert ties(detail, "roadmaps") == {roadmap["id"]: (["role", "message"], True)}
    assert ties(detail, "chats") == {chat["id"]: (["message"], False)}
    assert detail["chats"]["items"][0] | {"via": None, "lastAt": None, "current": None} == {
        "id": chat["id"], "chatId": "chat-1", "title": "Fix session", "provider": "claude", "model": "opus", "archived": False,
        "ownerType": "item", "ownerId": work["id"], "ownerTitle": "Unplanned fix", "via": None, "lastAt": None, "current": None}
    assert ties(detail, "workItems") == {work["id"]: (["chat"], False)}
    assert ties(detail, "repositories") == {repository["id"]: (["chat"], False)}

    # An open run reporting the tracked chat makes its work current.
    run(client, builder, externalChatId="chat-1")
    detail = ok(get(client, f"/agents/{builder['agentId']}"))
    assert ties(detail, "chats") == {chat["id"]: (["message", "run"], True)}
    assert ties(detail, "workItems") == {work["id"]: (["chat"], True)}
    assert [c["title"] for c in detail["runs"]["items"][0]["chats"]] == ["Fix session"]
    # The other agent's message context never leaks into this record.
    assert ties(ok(get(client, f"/agents/{other['agentId']}")), "workItems") == {work["id"]: (["message"], False)}


def placed(worker):
    """Each tie as (step, reason, item, chat, role, stale), by title."""
    return {(t["stepId"], t["via"], (t["item"] or {}).get("title"), (t["chat"] or {}).get("title"),
             (t["role"] or {}).get("label"), t["stale"]) for t in worker["ties"]}


def test_a_roadmap_shows_who_works_on_each_of_its_steps_now(client):
    delivery, api, web, old = (str(uuid.uuid4()) for _ in range(4))
    steps = [{"id": delivery, "title": "Delivery"}, {"id": api, "title": "API", "parentId": delivery}, {"id": web, "title": "Web"}]
    roadmap = ok(write(client, "/roadmaps", {"title": "Platform", "steps": [*steps, {"id": old, "title": "Old"}]}))
    elsewhere = ok(write(client, "/roadmaps", {"title": "Elsewhere"}))
    build, chores, legacy = (ok(write(client, "/work-items", {"title": title, "roadmapId": roadmap["id"], "stepId": step}))
                             for title, step in (("Build the API", api), ("Chores", None), ("Legacy", old)))
    unrelated = ok(write(client, "/work-items", {"title": "Unrelated", "roadmapId": elsewhere["id"]}))
    builder, reviewer, sleepy, other = (enroll(client, label) for label in ("Builder", "Reviewer", "Sleepy", "Other"))

    def report(agent, work, **extra):
        return ok(write(client, "/participations", {"runId": run(client, agent, **extra)["id"], "itemId": work["id"]}, agent))
    report(builder, build, externalChatId="chat-1")
    report(builder, build)  # A second run on the same item is the same tie.
    stale = report(builder, chores)
    report(builder, legacy)
    report(other, unrelated)
    asleep = report(sleepy, build)
    released = report(reviewer, chores)
    ok(write(client, f"/participations/{released['id']}/release", {"revision": released["revision"]}, reviewer))
    role = ok(write(client, "/discussions/roles", {"contextType": "step", "contextId": delivery, "label": "Review"}))
    ok(write(client, f"/discussions/roles/{role['id']}/claim", {"revision": role["revision"]}, reviewer))

    def track(chat_id, title, owner=roadmap, owner_type="roadmap", **extra):
        return ok(write(client, "/chats", {"ownerType": owner_type, "ownerId": owner["id"], "chatId": chat_id, "title": title, **extra}))
    track("chat-1", "Builder session", stepIds=[web])
    track("chat-2", "Docs session", stepIds=[api], provider="OpenAI", model="gpt-5.5")
    track("chat-3", "Planning")
    track("chat-4", "Fix session", owner=build, owner_type="item")
    gone = track("chat-5", "Finished", stepIds=[api])
    ok(write(client, f"/chats/{gone['id']}/archive", {"revision": gone["revision"]}))
    # Retiring a step moves its work to the roadmap itself.
    current = ok(get(client, f"/roadmaps/{roadmap['id']}"))
    ok(write(client, f"/roadmaps/{roadmap['id']}/revisions", {"revision": current["revision"], "title": "Platform", "steps": steps}))
    hours_ago = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    with client.app.state.db.transaction() as c:
        c.executemany("UPDATE participations SET last_seen_at=? WHERE id=?", [(hours_ago, stale["id"]), (hours_ago, asleep["id"])])

    workers = ok(get(client, f"/roadmaps/{roadmap['id']}/agents"))["items"]
    by_label = {w["label"]: w for w in workers}
    assert set(by_label) == {"Builder", "Reviewer", "Sleepy", "Docs session", "Planning", "Fix session"}
    assert placed(by_label["Builder"]) == {(api, "participation", "Build the API", None, None, False),
                                           (None, "participation", "Chores", None, None, True),
                                           (None, "participation", "Legacy", None, None, False),
                                           (web, "chat", None, "Builder session", None, False)}
    assert placed(by_label["Reviewer"]) == {(delivery, "role", None, None, "Review", False)}
    assert placed(by_label["Docs session"]) == {(api, "chat", None, "Docs session", None, False)}
    assert placed(by_label["Planning"]) == {(None, "chat", None, "Planning", None, False)}
    assert placed(by_label["Fix session"]) == {(api, "chat", "Build the API", "Fix session", None, False)}
    builder_view, docs = by_label["Builder"], by_label["Docs session"]
    assert (builder_view["kind"], builder_view["id"], builder_view["avatar"] is not None, builder_view["stale"]) == ("agent", builder["agentId"], True, False)
    assert (docs["kind"], docs["avatar"], docs["provider"], docs["model"]) == ("chat", None, "OpenAI", "gpt-5.5")
    # Agents whose only work went quiet come last.
    assert ([w["label"] for w in workers][-1], workers[-1]["stale"]) == ("Sleepy", True)
    assert not any(w["stale"] for w in workers[:-1])
    assert ok(get(client, f"/roadmaps/{elsewhere['id']}/agents"))["items"][0]["label"] == "Other"


def test_agents_that_finished_and_the_chats_they_reported_leave_the_roadmap(client):
    roadmap = ok(write(client, "/roadmaps", {"title": "Platform", "steps": [{"title": "API"}]}))
    api = roadmap["steps"][0]["id"]
    build = ok(write(client, "/work-items", {"title": "Build the API", "roadmapId": roadmap["id"], "stepId": api}))
    builder, finisher, retired, reviewer = (enroll(client, label) for label in ("Builder", "Finisher", "Retired", "Reviewer"))

    def owns(agent, label):
        role = ok(write(client, "/discussions/roles", {"contextType": "step", "contextId": api, "label": label}))
        ok(write(client, f"/discussions/roles/{role['id']}/claim", {"revision": role["revision"]}, agent))

    def end(agent, running):
        ok(write(client, f"/agent-runs/{running['id']}/end", {"revision": running["revision"]}, agent))

    # Builder is at work in one chat; its run in another ended.
    end(builder, run(client, builder, externalChatId="chat-old"))
    run(client, builder, externalChatId="chat-now")
    # Finisher ended its only run, which releases its work; Retired's revoked credential ends its runs.
    done = run(client, finisher, externalChatId="chat-done")
    ok(write(client, "/participations", {"runId": done["id"], "itemId": build["id"]}, finisher))
    end(finisher, done)
    run(client, retired, externalChatId="chat-retired")
    owns(retired, "Release")
    ok(write(client, f"/credentials/{retired['id']}/revoke"))
    # Reviewer owns a role but has no run open.
    owns(reviewer, "Review")
    titles = {"chat-old": "Old session", "chat-now": "Builder session", "chat-done": "Finished session",
              "chat-retired": "Retired session", "chat-unreported": "Planning"}
    for chat_id, title in titles.items():
        ok(write(client, "/chats", {"ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": chat_id, "title": title, "stepIds": [api]}))

    def workers():
        return {w["label"]: w for w in ok(get(client, f"/roadmaps/{roadmap['id']}/agents"))["items"]}
    # A chat whose runs all ended is finished too; one no run reported still stands for itself.
    assert set(workers()) == {"Builder", "Planning"}
    assert placed(workers()["Builder"]) == {(api, "chat", None, "Builder session", None, False)}
    # An owned role counts again once its owner is at work.
    run(client, reviewer)
    assert placed(workers()["Reviewer"]) == {(api, "role", None, None, "Review", False)}


def test_activity_pages_this_agents_messages_and_updates_newest_first(client):
    repository = ok(write(client, "/repositories", {"name": "Worklog"}))
    builder, other = enroll(client, "Builder"), enroll(client, "Other")
    running = run(client, builder)
    reported = ok(write(client, "/participations", {"runId": running["id"], "contexts": [{"repositoryId": repository["id"]}]}, builder))
    ok(write(client, f"/participations/{reported['id']}/updates", {"kind": "blocker", "text": "x" * 1500}, builder))
    started = ok(write(client, "/discussions", {"text": "Question about the parser", "title": "Parser", "chatId": "chat-9"}, builder))
    ok(write(client, f"/discussions/{started['discussion']['id']}/messages", {"text": "Human reply"}))
    ok(write(client, f"/discussions/{started['discussion']['id']}/messages", {"text": "Thanks"}, builder))
    ok(write(client, "/discussions", {"text": "Not mine"}, other))

    activity = ok(get(client, f"/agents/{builder['agentId']}/activity"))
    assert activity["total"] == 3
    assert [(a["kind"], a["text"][:8]) for a in activity["items"]] == [("message", "Thanks"), ("message", "Question"), ("update", "x" * 8)]
    message = activity["items"][1]
    assert (message["discussionTitle"], message["chatId"], message["chats"], message["truncated"]) == ("Parser", "chat-9", [], False)
    update = ok(get(client, f"/agents/{builder['agentId']}/activity?limit=1&offset=2"))["items"][0]
    assert (update["updateKind"], update["runName"], update["truncated"], len(update["text"])) == ("blocker", "Implementation", True, 1000)
    assert update["contexts"][0]["repositoryName"] == "Worklog"


def test_registry_is_for_the_person_only(client):
    builder = enroll(client, "Builder")
    roadmap = ok(write(client, "/roadmaps", {"title": "Platform"}))
    for path in ("/agents", "/agents/", f"/agents/{builder['agentId']}", f"/agents/{builder['agentId']}/activity",
                 f"/roadmaps/{roadmap['id']}/agents", f"/roadmaps/{roadmap['id']}/agents/"):
        assert get(client, path, builder).status_code == 403, path
    # Agents keep their own coordination reads, and the roadmap itself.
    assert get(client, "/agent-runs", builder).status_code == 200
    assert get(client, f"/roadmaps/{roadmap['id']}", builder).status_code == 200
    assert get(client, "/agents/missing").status_code == 404
    assert get(client, "/agents/missing/activity").status_code == 404
    assert get(client, "/roadmaps/missing/agents").status_code == 404


def test_rotation_keeps_one_identity_and_closing_runs_is_not_agent_activity(client):
    builder = enroll(client, "Builder")
    running = run(client, builder)
    rotated = ok(write(client, f"/credentials/{builder['id']}/rotate", {}))

    [agent] = ok(get(client, "/agents"))["items"]
    assert (agent["active"], agent["openRuns"], agent["runCount"]) == (True, 0, 1)
    assert agent["lastActivityAt"] == running["createdAt"]
    detail = ok(get(client, f"/agents/{agent['id']}"))
    assert [(c["id"], c["revoked"]) for c in detail["credentials"]] == [(rotated["id"], False), (builder["id"], True)]
    assert detail["runs"]["items"][0]["endReason"] == "credential_rotated"
    run(client, rotated)
    assert ok(get(client, "/agents"))["items"][0]["runCount"] == 2
