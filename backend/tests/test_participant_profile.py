"""One participant as any caller sees it (`GET /discussions/participants/{identity}`): who it is, whether
its credential is live, what it reports working on and the roles it owns, so an agent can look up the
one whose work overlaps its own before it asks it about it. It counts the agent's runs; their records,
its credentials and its chats stay on the person's Agents page."""

import uuid

import pytest
from test_discussion_tags import credential, error, ok, person, read, write
from test_repository_discussions import repository

SUMMARY = {"id", "label", "kind", "avatar", "active", "createdAt", "lastActivityAt", "providers", "openRuns",
           "runCount", "messageCount", "working", "roles"}


@pytest.fixture
def client(tmp_path):
    with person(tmp_path) as c:
        yield c


def profile(c, identity, agent=None):
    return c.get(f"/api/v1/discussions/participants/{identity}",
                 headers={"Authorization": "Bearer " + agent["token"]} if agent else {})


def test_an_agent_looks_up_another_and_sees_its_work_but_not_its_credentials_or_chats(client):
    repo = repository(client)
    work = ok(write(client, "POST", "/work-items", {"title": "Fix the parser"}))
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    running = ok(write(client, "POST", "/agent-runs", {"clientRunKey": str(uuid.uuid4()), "displayName": "Implementation",
                                                       "provider": "claude", "externalChatId": "chat-1"}, builder))
    reported = ok(write(client, "POST", "/participations", {
        "runId": running["id"], "itemId": work["id"], "description": "Fixing the parser",
        "contexts": [{"repositoryId": repo["id"], "branch": "claude/parser"}]}, builder))
    role = ok(write(client, "POST", "/discussions/roles", {"contextType": "item", "contextId": work["id"], "label": "Implementation"}, builder))
    ok(write(client, "POST", f"/discussions/roles/{role['id']}/claim", {"revision": role["revision"]}, builder))

    seen = ok(profile(client, builder["agentId"], reviewer))
    assert set(seen) == SUMMARY
    listed = {p["id"]: p for p in read(client, "/discussions/participants", reviewer)["items"]}[builder["agentId"]]
    assert {key: seen[key] for key in listed} == listed
    assert (seen["kind"], seen["label"], seen["active"], seen["openRuns"], seen["providers"]) == ("agent", "Builder", True, 1, ["claude"])
    [working] = seen["working"]
    assert (working["participationId"], working["runId"], working["description"], working["item"]["id"]) == (
        reported["id"], running["id"], "Fixing the parser", work["id"])
    assert [(ctx["repositoryId"], ctx["branch"]) for ctx in working["contexts"]] == [(repo["id"], "claude/parser")]
    assert [(r["id"], r["label"], r["contextId"]) for r in seen["roles"]] == [(role["id"], "Implementation", work["id"])]
    # The person's record of the agent says more, and stays the person's.
    detail = read(client, f"/agents/{builder['agentId']}")
    assert {"credentials", "runs", "chats"} <= set(detail) and not {"credentials", "runs", "chats"} & set(seen)
    assert {key: detail[key] for key in SUMMARY - {"kind", "avatar"}} == {key: seen[key] for key in SUMMARY - {"kind", "avatar"}}
    error(client.get(f"/api/v1/agents/{builder['agentId']}", headers={"Authorization": "Bearer " + reviewer["token"]}), 403, "agent_scope")
    # The person reads it too, and an agent reads its own.
    assert ok(profile(client, builder["agentId"])) == seen == ok(profile(client, builder["agentId"], builder))


def test_the_person_a_revoked_agent_and_an_unknown_identity(client):
    reviewer = credential(client, "Reviewer")
    you = ok(profile(client, "local-user", reviewer))
    assert you == {"id": "local-user", "label": "You", "kind": "human", "avatar": None, "active": True}
    assert you == read(client, "/discussions/participants", reviewer)["items"][0]
    gone = credential(client, "Gone")
    ok(write(client, "POST", f"/credentials/{gone['id']}/revoke"))
    assert ok(profile(client, gone["agentId"], reviewer))["active"] is False
    # A revoked identity looks nobody up.
    error(profile(client, reviewer["agentId"], gone), 401, "authentication")
    error(profile(client, str(uuid.uuid4()), reviewer), 404, "not_found")
    error(profile(client, "not-an-identity", reviewer), 404, "not_found")
