"""Step-linked participations, chat auto-tracking, Live cap badges, and roadmap agent attribution (schema v35)."""

from __future__ import annotations

import uuid

from test_step_update import client, ok, read, refused, write

__all__ = ["client"]


def _repo(c, name, **extra):
    return ok(write(c, "POST", "/repositories", {"name": name, "url": f"https://github.com/org/{name}", **extra}))


def _roadmap(c, title="Platform", steps=None):
    return ok(write(c, "POST", "/roadmaps", {"title": title, "overview": "", "steps": steps or []}))


def test_participation_step_links_auto_track_chat_and_surface_in_live_and_agents(client):
    r1 = _repo(client, "contracts", tier=1, wipCap=1)
    r2 = _repo(client, "service", tier=2, wipCap=1, upstreamIds=[r1["id"]])
    s1 = str(uuid.uuid4())
    rm = _roadmap(client, steps=[
        {"id": s1, "title": "Stabilize API schema", "position": 0, "repositoryIds": [r1["id"]]},
    ])
    # Freeze r1 in active wave so Live reflects frozenWaveName + pendingRepinsCount.
    w = ok(write(client, "POST", f"/roadmaps/{rm['id']}/waves", {
        "name": "Wave Alpha", "stepIds": [s1],
        "repositories": [{"repositoryId": r1["id"], "tier": 1}, {"repositoryId": r2["id"], "tier": 2}],
    }))
    w = ok(write(client, "POST", f"/waves/{w['id']}/status", {"revision": w["revision"], "status": "active"}))
    ok(write(client, "POST", f"/waves/{w['id']}/freeze", {
        "revision": w["revision"], "repositoryId": r1["id"], "freezeStatus": "frozen", "frozenRef": "v1",
    }))

    # Sponsor issues credential; two agents register with chatIds and report participation on s1 in r1 (2 writers > cap 1).
    sponsor = ok(write(client, "POST", "/credentials", {"label": "Sponsor"}))
    tok1 = "tok-" + uuid.uuid4().hex + uuid.uuid4().hex
    tok2 = "tok-" + uuid.uuid4().hex + uuid.uuid4().hex
    reg1 = {
        **ok(write(client, "POST", "/agent-registrations", {"label": "Writer One", "chatId": "chat-w1", "token": tok1}, sponsor)),
        "token": tok1,
    }
    reg2 = {
        **ok(write(client, "POST", "/agent-registrations", {"label": "Writer Two", "chatId": "chat-w2", "token": tok2}, sponsor)),
        "token": tok2,
    }

    run1 = ok(write(client, "POST", "/agent-runs", {
        "clientRunKey": "k1", "displayName": "Run 1", "externalChatId": "chat-w1",
    }, reg1))
    run2 = ok(write(client, "POST", "/agent-runs", {
        "clientRunKey": "k2", "displayName": "Run 2", "externalChatId": "chat-w2",
    }, reg2))

    # Unknown stepId is rejected with 404.
    refused(write(client, "POST", "/participations", {
        "runId": run1["id"],
        "stepId": str(uuid.uuid4()),
        "description": "Bad step",
        "contexts": [{"repositoryId": r1["id"], "branch": "feat/bad"}],
    }, reg1), 404, "not_found")

    p1 = ok(write(client, "POST", "/participations", {
        "runId": run1["id"],
        "stepId": s1,
        "description": "Implementing schema",
        "contexts": [{"repositoryId": r1["id"], "branch": "feat/schema-1"}],
    }, reg1))
    assert p1["stepId"] == s1

    p2 = ok(write(client, "POST", "/participations", {
        "runId": run2["id"],
        "stepId": s1,
        "description": "Also touching contracts",
        "contexts": [{"repositoryId": r1["id"], "branch": "feat/schema-2"}],
    }, reg2))
    assert p2["stepId"] == s1

    # Filtering GET /participations?stepId=... returns both.
    filtered = ok(read(client, f"/participations?stepId={s1}"))
    assert {p["id"] for p in filtered["items"]} == {p1["id"], p2["id"]}

    # Auto-tracking: registered chats were tracked on the roadmap and linked to s1 in chat_steps.
    rm_detail = ok(read(client, f"/roadmaps/{rm['id']}"))
    chat_ids = {c["chatId"] for c in rm_detail["chats"]}
    assert {"chat-w1", "chat-w2"} <= chat_ids

    # Roadmap agents roster includes both agents on s1.
    roster = ok(read(client, f"/roadmaps/{rm['id']}/agents"))
    worker_labels = {w["label"] for w in roster["items"]}
    assert {"Writer One", "Writer Two"} <= worker_labels

    # Live snapshot reflects step-linked work node, cap-aware overWipCap, frozenWaveName, and pendingRepinsCount.
    live = ok(read(client, f"/live?roadmapId={rm['id']}"))
    assert live["summary"]["overCapRepos"] == 1
    repo_node = next(r for r in live["repositories"] if r["id"] == r1["id"])
    assert repo_node["wipCap"] == 1
    assert repo_node["effectiveWipCap"] == 1
    assert repo_node["overWipCap"] is True
    assert repo_node["frozenWaveName"] == "Wave Alpha"
    assert repo_node["pendingRepinsCount"] == 1
    assert any(node["kind"] == "step" and node["id"] == s1 for node in live["work"])

    # Dispatch shows activeAgents on s1 card and 2 activeWriters on r1 lane.
    disp = ok(read(client, f"/roadmaps/{rm['id']}/dispatch"))
    r1_lane = next(lane for lane in disp["repoLanes"] if lane["repositoryId"] == r1["id"])
    assert r1_lane["activeWriters"] == 2
    assert r1_lane["overWipCap"] is True
    step_card = next(c for c in r1_lane["queued"] if c["id"] == s1)
    assert {a["label"] for a in step_card["activeAgents"]} == {"Writer One", "Writer Two"}

    # Updating participation stepId to None clears it cleanly.
    p2_updated = ok(write(client, "PATCH", f"/participations/{p2['id']}", {
        "revision": p2["revision"], "stepId": None,
    }, reg2))
    assert p2_updated["stepId"] is None
