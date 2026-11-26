"""The live workflow is one deterministic, person-only read across the existing v31 tables."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from worklog import live_api, proposal_targets
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
INSERT_MESSAGE = (
    "INSERT INTO discussion_messages(id,discussion_id,text,author_id,author_label,author_kind,created_at,chat_id,"
    "on_behalf_of_role_id,ownership_generation) VALUES(?,?,?,?,?,?,?,?,?,?)"
)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as found:
        found.headers["Origin"] = ORIGIN
        bootstrap = found.post(
            "/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={}
        )
        found.headers["X-CSRF-Token"] = bootstrap.json()["csrfToken"]
        yield found


def post(client, path, payload, token=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if token:
        headers["Authorization"] = "Bearer " + token
    response = client.post("/api/v1" + path, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def agent(client, label):
    return post(client, "/credentials", {"label": label})


def stamp():
    return datetime.now(UTC).isoformat()


def days_ago(days):
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def reports(c, issued, key, item, at, *, repository=None, released=False):
    """A run and its participation on `item`, both last touched `at`: open, or, with `released`,
    ended and released then."""
    done = at if released else None
    c.execute(
        "INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"{key}-run",
            issued["agentId"],
            issued["id"],
            f"{key}-run",
            key,
            "codex",
            None,
            1,
            at,
            at,
            done,
            "finished" if released else None,
        ),
    )
    c.execute(
        "INSERT INTO participations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"{key}-part",
            f"{key}-run",
            item,
            "",
            1,
            at,
            at,
            at,
            done,
            "finished" if released else None,
            None,
        ),
    )
    if repository:
        c.execute(
            "INSERT INTO participation_contexts VALUES(?,?,?,?,?,?)",
            (f"{key}-part", 0, repository, "", "", "main"),
        )


def asks(c, message, discussion, author, targets, at):
    """A pending acknowledgement request. `author` is None for the person, else an issued
    credential; each target is ("human", "local-user") or ("agent", agent ID)."""
    c.execute(
        INSERT_MESSAGE,
        (
            message,
            discussion,
            "Please acknowledge",
            author["agentId"] if author else "local-user",
            (author.get("label") or "Agent") if author else "You",
            "agent" if author else "human",
            at,
            None,
            None,
            None,
        ),
    )
    c.execute(
        "INSERT INTO discussion_requests(message_id,mode,state) VALUES(?,?,?)",
        (message, "all", "pending"),
    )
    for number, (kind, recipient) in enumerate(targets):
        c.execute(
            "INSERT INTO discussion_targets VALUES(?,?,?,?)",
            (f"{message}-target-{number}", message, kind, recipient),
        )


def base_graph(client):
    at = stamp()
    first, second = agent(client, "Agent A"), agent(client, "Agent B")
    with client.app.state.db.transaction() as c:
        c.execute(
            "INSERT INTO roadmaps VALUES(?,?,?,?,?,?,?,?)",
            ("road", "Delivery", "", 1, 0, at, at, None),
        )
        c.execute(
            "INSERT INTO steps VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("step", "road", "Build", "", None, 0, "doing", 1, 0, "", ""),
        )
        c.execute(
            "INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("item", "Implement live view", "", "doing", None, "step", 1, 0, 1, at, at, "road"),
        )
        c.execute(
            "INSERT INTO repositories(id,name,url,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("repo", "Worklog", "https://example.test/worklog", at, at),
        )
        c.execute("INSERT INTO item_repositories VALUES(?,?,?)", ("item", "repo", at))
        for number, issued in enumerate((first, second), 1):
            run_id, participation_id = f"run-{number}", f"part-{number}"
            c.execute(
                "INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    issued["agentId"],
                    issued["id"],
                    run_id,
                    f"Run {number}",
                    "codex",
                    None,
                    1,
                    at,
                    at,
                    None,
                    None,
                ),
            )
            c.execute(
                "INSERT INTO participations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    participation_id,
                    run_id,
                    "item",
                    f"Agent {number} implementation",
                    1,
                    at,
                    at,
                    at,
                    None,
                    None,
                    None,
                ),
            )
            c.execute(
                "INSERT INTO participation_contexts VALUES(?,?,?,?,?,?)",
                (
                    participation_id,
                    0,
                    "repo",
                    "C:/worklog",
                    f"C:/worktree/{number}",
                    f"agent-{number}",
                ),
            )
    return first, second


def test_live_graph_wires_work_agents_repositories_edges_lanes_and_summary(client):
    first, second = base_graph(client)
    answer = client.get("/api/v1/live").json()

    assert [(node["kind"], node["id"]) for node in answer["work"]] == [
        ("roadmap", "road"),
        ("step", "step"),
        ("item", "item"),
    ]
    assert {node["id"] for node in answer["agents"]} == {first["agentId"], second["agentId"]}
    repository = answer["repositories"][0]
    assert repository["id"] == "repo"
    assert repository["overlapCount"] == 2
    assert repository["branches"] == ["agent-1", "agent-2"]
    assert {(edge["agentId"], edge["workId"]) for edge in answer["workEdges"]} >= {
        (first["agentId"], "item"),
        (second["agentId"], "item"),
    }
    assert {(edge["agentId"], edge["repositoryId"]) for edge in answer["repoEdges"]} == {
        (first["agentId"], "repo"),
        (second["agentId"], "repo"),
    }
    assert len(answer["lanes"]) == 2
    assert {
        key: answer["summary"][key]
        for key in ("activeAgents", "activeWorkItems", "activeSteps", "activeRepos", "repoOverlaps")
    } == {
        "activeAgents": 2,
        "activeWorkItems": 1,
        "activeSteps": 1,
        "activeRepos": 1,
        "repoOverlaps": 1,
    }


def test_live_communication_edges_pulses_sponsorship_and_waiting_proposal(client):
    first, second = base_graph(client)
    launcher = agent(client, "Launcher")
    at = stamp()
    with client.app.state.db.transaction() as c:
        # Agent A registered itself with the credential issued in Settings, as self-registering
        # agents all do, and then registered Agent B: only the second is a subagent.
        c.execute(
            "UPDATE communication_credentials SET registered_by=?,registered_by_label=? WHERE credential_id=?",
            (launcher["agentId"], "Launcher", first["id"]),
        )
        c.execute(
            "UPDATE communication_credentials SET registered_by=?,registered_by_label=? WHERE credential_id=?",
            (first["agentId"], "Agent A", second["id"]),
        )
        c.execute(
            "INSERT INTO discussions VALUES(?,?,?,?,?,?,?)",
            ("discussion", "Coordination", 0, 1, at, at, None),
        )
        c.execute(
            "INSERT INTO discussion_contexts VALUES(?,?,?,?,?,?,?)",
            ("discussion", "item", "item", 0, "local-user", "human", at),
        )
        c.execute(
            INSERT_MESSAGE,
            (
                "message",
                "discussion",
                "Please acknowledge",
                first["agentId"],
                "Agent A",
                "agent",
                at,
                None,
                None,
                None,
            ),
        )
        c.execute(
            "INSERT INTO discussion_requests(message_id,mode,state) VALUES(?,?,?)",
            ("message", "all", "pending"),
        )
        c.execute(
            "INSERT INTO discussion_targets VALUES(?,?,?,?)",
            ("target", "message", "agent", second["agentId"]),
        )
        c.execute(
            "INSERT INTO discussion_targets VALUES(?,?,?,?)",
            ("human-target", "message", "human", "local-user"),
        )
        c.execute(
            "INSERT INTO discussion_roles VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "role",
                "item",
                "item",
                "Implementation",
                "implementation",
                second["agentId"],
                1,
                1,
                at,
                at,
            ),
        )
        c.execute(
            INSERT_MESSAGE,
            (
                "role-message",
                "discussion",
                "Role owner ack",
                first["agentId"],
                "Agent A",
                "agent",
                at,
                None,
                None,
                None,
            ),
        )
        c.execute(
            "INSERT INTO discussion_requests(message_id,mode,state) VALUES(?,?,?)",
            ("role-message", "all", "pending"),
        )
        c.execute(
            "INSERT INTO discussion_targets VALUES(?,?,?,?)",
            ("role-target", "role-message", "role", "role"),
        )
        c.execute(
            "INSERT INTO discussion_role_offers VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "offer",
                "role",
                second["agentId"],
                first["agentId"],
                1,
                "Please take over",
                "pending",
                at,
                None,
            ),
        )
        c.execute(
            "INSERT INTO discussions VALUES(?,?,?,?,?,?,?)",
            ("untagged", "Open coordination", 0, 1, at, at, None),
        )
        c.execute(
            INSERT_MESSAGE,
            (
                "thread-agent",
                "untagged",
                "I have the change",
                first["agentId"],
                "Agent A",
                "agent",
                at,
                None,
                None,
                None,
            ),
        )
        c.execute(
            INSERT_MESSAGE,
            (
                "thread-human",
                "untagged",
                "Proceed",
                "local-user",
                "You",
                "human",
                stamp(),
                None,
                None,
                None,
            ),
        )
        operations = json.dumps(
            [
                {"action": "item.update", "payload": {"id": "item", "title": "Revised"}},
                {"action": "roadmap.update", "payload": {"id": "road", "title": "Revised"}},
            ]
        )
        c.execute(
            "INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "proposal",
                "Approve live work",
                "",
                "submitted",
                2,
                first["agentId"],
                "Agent A",
                "agent",
                operations,
                None,
                at,
                at,
                at,
                0,
            ),
        )

    pending = client.get("/api/v1/live").json()
    assert any(
        edge["kind"] == "ack_request"
        and edge["status"] == "pending"
        and edge["toId"] == second["agentId"]
        for edge in pending["commEdges"]
    )
    assert any(
        edge["kind"] == "ack_request"
        and edge["toKind"] == "person"
        and edge["toId"] == "local-user"
        for edge in pending["commEdges"]
    )
    assert any(
        edge["kind"] == "ack_request" and edge["label"] == "Ack (Implementation)"
        for edge in pending["commEdges"]
    )
    assert any(edge["kind"] == "role_offer" for edge in pending["commEdges"])
    assert any(
        edge["kind"] == "shared_thread" and edge["discussionId"] == "untagged"
        for edge in pending["commEdges"]
    )
    assert any(
        edge["kind"] == "sponsorship"
        and edge["fromId"] == first["agentId"]
        and edge["toId"] == second["agentId"]
        for edge in pending["commEdges"]
    )
    assert any(pulse["kind"] == "ack_requested" for pulse in pending["pulses"])
    assert any(pulse["kind"] == "role_handoff" for pulse in pending["pulses"])
    item = next(work for work in pending["work"] if work["id"] == "item")
    assert item["waitingProposals"] == 1 and item["needsYou"] is True
    # One request names the person; it, the role's request and the offer wait on Agent B.
    assert {
        key: pending["summary"][key]
        for key in ("pendingRequests", "waitingOnAgents", "waitingProposals", "needsYou")
    } == {"pendingRequests": 1, "waitingOnAgents": 3, "waitingProposals": 1, "needsYou": 2}
    everyone = client.get("/api/v1/live", params={"includeIdle": True}).json()
    assert launcher["agentId"] in {node["id"] for node in everyone["agents"]}
    assert [
        (edge["fromId"], edge["toId"])
        for edge in everyone["commEdges"]
        if edge["kind"] == "sponsorship"
    ] == [(first["agentId"], second["agentId"])]

    acknowledged = stamp()
    with client.app.state.db.transaction() as c:
        c.execute(
            "INSERT INTO discussion_acknowledgements VALUES(?,?,?,?,?,?,?,?,?)",
            ("ack", "target", second["agentId"], "Agent B", "agent", None, None, acknowledged, 0),
        )
    satisfied = client.get("/api/v1/live").json()
    assert any(
        edge["messageId"] == "message" and edge["status"] == "satisfied"
        for edge in satisfied["commEdges"]
    )
    assert any(pulse["kind"] == "ack_satisfied" for pulse in satisfied["pulses"])
    assert satisfied["summary"]["pendingRequests"] == 1
    assert satisfied["summary"]["waitingOnAgents"] == 2


def test_live_scopes_subtree_rejects_unknown_and_controls_idle_agents(client):
    at = stamp()
    active, unrelated, idle, quiet, finished = (
        agent(client, "Scoped"),
        agent(client, "Unrelated"),
        agent(client, "Idle"),
        agent(client, "Quiet"),
        agent(client, "Finished"),
    )
    with client.app.state.db.transaction() as c:
        for identity, title, parent in (
            ("root", "Root", None),
            ("child", "Child", "root"),
            ("other", "Other", None),
        ):
            c.execute(
                "INSERT INTO roadmaps VALUES(?,?,?,?,?,?,?,?)",
                (identity, title, "", 1, 0, at, at, parent),
            )
        for identity, roadmap in (
            ("root-item", "root"),
            ("child-item", "child"),
            ("other-item", "other"),
        ):
            c.execute(
                "INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (identity, identity, "", "doing", None, None, 1, 0, 1, at, at, roadmap),
            )
        c.execute(
            "INSERT INTO repositories(id,name,created_at,updated_at) VALUES(?,?,?,?)",
            ("root-repo", "Root repo", at, at),
        )
        c.execute(
            "INSERT INTO repositories(id,name,created_at,updated_at) VALUES(?,?,?,?)",
            ("other-repo", "Other repo", at, at),
        )
        for number, (issued, item, repository) in enumerate(
            ((active, "child-item", "root-repo"), (unrelated, "other-item", "other-repo")), 1
        ):
            c.execute(
                "INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"scope-run-{number}",
                    issued["agentId"],
                    issued["id"],
                    f"scope-{number}",
                    "Scope",
                    "codex",
                    None,
                    1,
                    at,
                    at,
                    None,
                    None,
                ),
            )
            c.execute(
                "INSERT INTO participations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"scope-part-{number}",
                    f"scope-run-{number}",
                    item,
                    "",
                    1,
                    at,
                    at,
                    at,
                    None,
                    None,
                    None,
                ),
            )
            c.execute(
                "INSERT INTO participation_contexts VALUES(?,?,?,?,?,?)",
                (f"scope-part-{number}", 0, repository, "", "", "main"),
            )
        # Quiet opened participation on the root's own item three days ago and never came back.
        reports(c, quiet, "quiet", "root-item", days_ago(3))
        # Finished worked on the child's item and released it three days ago.
        reports(c, finished, "finished", "child-item", days_ago(3), released=True)
        # Unrelated, at work on the other roadmap, still has a chat tracked on the root that has
        # sat for three days.
        c.execute(
            "UPDATE communication_credentials SET chat_id=? WHERE credential_id=?",
            ("old-chat", unrelated["id"]),
        )
        c.execute(
            "INSERT INTO agent_chats VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "old-chat-record",
                "roadmap",
                "root",
                "old-chat",
                "Old chat",
                days_ago(3)[:10],
                0,
                1,
                days_ago(3),
                days_ago(3),
                "codex",
                "",
                "[]",
            ),
        )

    unfiltered = client.get("/api/v1/live").json()
    assert {node["id"] for node in unfiltered["agents"]} == {
        active["agentId"],
        unrelated["agentId"],
    }
    # root-item is in progress, but nobody on the map is at it.
    assert {node["id"] for node in unfiltered["work"]} == {
        "child",
        "child-item",
        "other",
        "other-item",
    }
    with_idle = client.get("/api/v1/live", params={"includeIdle": True}).json()
    assert {idle["agentId"], quiet["agentId"]} <= {node["id"] for node in with_idle["agents"]}
    assert "root-item" in {node["id"] for node in with_idle["work"]}

    scoped = client.get("/api/v1/live", params={"roadmapId": "root"}).json()
    assert {node["id"] for node in scoped["work"]} == {"root", "child", "child-item"}
    # Unrelated is at work, but not here: its only tie to this roadmap is the chat that sat.
    assert {node["id"] for node in scoped["agents"]} == {active["agentId"]}
    assert {node["id"] for node in scoped["repositories"]} == {"root-repo"}
    assert scoped["untrackedSessions"] == []
    # A chosen roadmap narrows the agents to those tied to it and still honours the toggle, which
    # then gives every agent ever tied to it: Finished's released participation is such a tie.
    scoped_idle = client.get(
        "/api/v1/live", params={"roadmapId": "root", "includeIdle": True}
    ).json()
    assert {node["id"]: node["state"] for node in scoped_idle["agents"]} == {
        active["agentId"]: "reporting",
        quiet["agentId"]: "stale",
        unrelated["agentId"]: "reporting",
        finished["agentId"]: "idle",
    }
    assert (finished["agentId"], "child-item") in {
        (edge["agentId"], edge["workId"]) for edge in scoped_idle["workEdges"]
    }
    # The same agents as without a roadmap, less the one tied to nothing in it.
    assert {node["id"] for node in scoped_idle["agents"]} == {
        node["id"] for node in with_idle["agents"]
    } - {idle["agentId"]}
    assert {node["id"] for node in scoped_idle["work"]} == {
        "root",
        "root-item",
        "child",
        "child-item",
    }
    missing = client.get("/api/v1/live", params={"roadmapId": "missing"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_live_shows_work_for_an_agent_on_the_map_or_a_reason_of_its_own(client):
    at, old = stamp(), days_ago(3)
    chatty = agent(client, "Chatty")
    with client.app.state.db.transaction() as c:
        c.execute(
            "INSERT INTO roadmaps VALUES(?,?,?,?,?,?,?,?)",
            ("road", "Delivery", "", 1, 0, at, at, None),
        )
        for position, (identity, status) in enumerate(
            (
                ("busy", "doing"),
                ("stuck", "blocked"),
                ("finished", "done"),
                ("chatted", "open"),
                ("asked", "open"),
            )
        ):
            c.execute(
                "INSERT INTO steps VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identity, "road", identity, "", None, position, status, 1, 0, "", ""),
            )
        c.execute(
            "INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("loose", "loose", "", "doing", None, None, 1, 0, 1, at, at, "road"),
        )
        # Chatty's chat is tracked on the roadmap and assigned one step, and has sat for three days.
        c.execute(
            "UPDATE communication_credentials SET chat_id=? WHERE credential_id=?",
            ("chat-session", chatty["id"]),
        )
        c.execute(
            "INSERT INTO agent_chats VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "chat",
                "roadmap",
                "road",
                "chat-session",
                "Chat",
                old[:10],
                0,
                1,
                old,
                old,
                "codex",
                "",
                "[]",
            ),
        )
        c.execute("INSERT INTO chat_steps VALUES(?,?,?)", ("chat", "chatted", old))

    # Status brings nothing in, and neither does a chat nobody has touched inside the window.
    quiet = client.get("/api/v1/live").json()
    assert quiet["work"] == [] and quiet["agents"] == [] and quiet["workEdges"] == []
    assert quiet["summary"]["activeSteps"] == 0 and quiet["summary"]["activeWorkItems"] == 0

    with client.app.state.db.transaction() as c:
        c.execute("UPDATE agent_chats SET updated_at=? WHERE id='chat'", (stamp(),))
    running = client.get("/api/v1/live").json()
    assert [node["id"] for node in running["agents"]] == [chatty["agentId"]]
    assert [node["id"] for node in running["work"]] == ["road", "chatted"]
    assert all(edge["active"] and edge["via"] == ["chat"] for edge in running["workEdges"])

    # The person's message on a step is a reason of the step's own, with no agent on it.
    with client.app.state.db.transaction() as c:
        c.execute(
            "INSERT INTO discussions VALUES(?,?,?,?,?,?,?)",
            ("thread", "About a step", 0, 1, at, at, None),
        )
        c.execute(
            "INSERT INTO discussion_contexts VALUES(?,?,?,?,?,?,?)",
            ("thread", "step", "asked", 0, "local-user", "human", at),
        )
        c.execute(
            INSERT_MESSAGE,
            ("note", "thread", "Hold this", "local-user", "You", "human", stamp(), None, None, None),
        )
    noted = client.get("/api/v1/live").json()
    assert [(node["id"], node["agentCount"]) for node in noted["work"]] == [
        ("road", 1),
        ("chatted", 1),
        ("asked", 0),
    ]


def test_live_keeps_who_can_still_answer_and_counts_only_what_waits_on_the_person(client):
    at, old = stamp(), days_ago(3)
    asker, answerer, gone, dormant = (
        agent(client, "Asker"),
        agent(client, "Answerer"),
        agent(client, "Gone"),
        agent(client, "Dormant"),
    )
    with client.app.state.db.transaction() as c:
        c.execute(
            "INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("item", "item", "", "doing", None, None, 1, 0, 1, at, at, None),
        )
        c.execute(
            "INSERT INTO discussions VALUES(?,?,?,?,?,?,?)",
            ("thread", "Requests", 0, 1, old, old, None),
        )
        asks(c, "to-person", "thread", asker, [("human", "local-user")], old)
        asks(c, "to-answerer", "thread", None, [("agent", answerer["agentId"])], old)
        asks(c, "to-gone", "thread", None, [("agent", gone["agentId"])], old)
        reports(c, dormant, "dormant", "item", old)
        c.execute("UPDATE credentials SET revoked=1 WHERE id=?", (gone["id"],))

    answer = client.get("/api/v1/live").json()
    # Asker waits on the person and Answerer can still answer. A revoked credential cannot, and
    # nobody has renewed Dormant's report, so neither shows for requests three days old.
    assert {node["id"] for node in answer["agents"]} == {asker["agentId"], answerer["agentId"]}
    assert answer["work"] == []
    counts = ("pendingRequests", "waitingOnAgents", "waitingProposals", "openQuestions", "needsYou")
    expected = {
        "pendingRequests": 1,
        "waitingOnAgents": 2,
        "waitingProposals": 0,
        "openQuestions": 0,
        "needsYou": 1,
    }
    assert {key: answer["summary"][key] for key in counts} == expected
    # What waits does not depend on the window or on who shows.
    for params in ({"windowMinutes": 5}, {"windowMinutes": 1440}, {"includeIdle": True}):
        other = client.get("/api/v1/live", params=params).json()
        assert {key: other["summary"][key] for key in counts} == expected

    everyone = client.get("/api/v1/live", params={"includeIdle": True}).json()
    states = {node["id"]: node["state"] for node in everyone["agents"]}
    assert states[gone["agentId"]] == "revoked" and states[dormant["agentId"]] == "stale"
    assert [node["id"] for node in everyone["work"]] == ["item"]

    # A revoked agent that acted inside the window shows for what it did.
    with client.app.state.db.transaction() as c:
        c.execute(
            INSERT_MESSAGE,
            ("parting", "thread", "Handing over", gone["agentId"], "Gone", "agent", stamp(), None, None, None),
        )
    recent = client.get("/api/v1/live").json()
    assert gone["agentId"] in {node["id"] for node in recent["agents"]}


def road(c, at, *items, steps=()):
    """A roadmap "road" with open work items, each on no step unless given as (item, step)."""
    c.execute(
        "INSERT INTO roadmaps VALUES(?,?,?,?,?,?,?,?)",
        ("road", "Delivery", "", 1, 0, at, at, None),
    )
    for position, (identity, retired) in enumerate(steps):
        c.execute(
            "INSERT INTO steps VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (identity, "road", identity, "", None, position, "doing", 1, retired, "", ""),
        )
    for entry in items:
        identity, step = entry if isinstance(entry, tuple) else (entry, None)
        c.execute(
            "INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (identity, identity, "", "open", None, step, 1, 0, 1, at, at, "road"),
        )


def proposes(c, identity, status, item, at, *, actor=None):
    """A proposal to retitle `item`, last changed `at`, by the person unless `actor` is given."""
    c.execute(
        "INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            identity,
            identity,
            "",
            status,
            2,
            actor["agentId"] if actor else "local-user",
            "Agent" if actor else "You",
            "agent" if actor else "human",
            json.dumps([{"action": "item.update", "payload": {"id": item, "title": "Revised"}}]),
            None,
            at,
            at,
            at,
            0,
        ),
    )


def test_live_shows_work_that_waits_on_the_person_or_changed_inside_the_window(
    client, monkeypatch
):
    at, old = stamp(), days_ago(3)
    holder, taker, asker = agent(client, "Holder"), agent(client, "Taker"), agent(client, "Asker")
    with client.app.state.db.transaction() as c:
        road(c, at, "offered", "approved", "settled", "asked", "untouched")
        # Holder's credential is revoked, so nothing but the pending offer of its role shows the item.
        c.execute(
            "INSERT INTO discussion_roles VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("role", "item", "offered", "Owner", "owner", holder["agentId"], 1, 1, old, old),
        )
        c.execute(
            "INSERT INTO discussion_role_offers VALUES(?,?,?,?,?,?,?,?,?)",
            ("offer", "role", holder["agentId"], taker["agentId"], 1, "Yours?", "pending", old, None),
        )
        c.execute("UPDATE credentials SET revoked=1 WHERE id=?", (holder["id"],))
        proposes(c, "approved-now", "approved", "approved", at)
        proposes(c, "approved-before", "approved", "settled", old)
        c.execute(
            "INSERT INTO discussions VALUES(?,?,?,?,?,?,?)",
            ("thread", "A question", 0, 1, old, old, None),
        )
        c.execute(
            "INSERT INTO discussion_contexts VALUES(?,?,?,?,?,?,?)",
            ("thread", "item", "asked", 0, asker["agentId"], "agent", old),
        )
        asks(c, "to-person", "thread", asker, [("human", "local-user")], old)

    read = []
    targets = proposal_targets.targets
    monkeypatch.setattr(
        proposal_targets,
        "targets",
        lambda c, action, payload, *rest: read.append(payload["id"]) or targets(c, action, payload),
    )
    answer = client.get("/api/v1/live").json()
    work = {node["id"]: node for node in answer["work"]}
    assert set(work) == {"road", "offered", "approved", "asked"}
    assert work["asked"]["needsYou"] is True and work["approved"]["needsYou"] is False
    assert {node["id"] for node in answer["agents"]} == {taker["agentId"], asker["agentId"]}
    assert any(pulse["kind"] == "proposal_approved" for pulse in answer["pulses"])
    assert answer["summary"]["pendingRequests"] == 1 and answer["summary"]["needsYou"] == 1
    assert answer["summary"]["waitingOnAgents"] == 1
    # A proposal settled before the window is never read, so its operations are never parsed.
    assert read == ["approved"]

    # An offer that ended inside the window is a reason too; one that ended before it is not.
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE discussion_role_offers SET state='declined',ended_at=?", (stamp(),))
    assert "offered" in {node["id"] for node in client.get("/api/v1/live").json()["work"]}
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE discussion_role_offers SET ended_at=?", (old,))
    ended = client.get("/api/v1/live").json()
    assert "offered" not in {node["id"] for node in ended["work"]}
    assert ended["summary"]["waitingOnAgents"] == 0


def test_live_ties_a_chat_while_its_transcript_says_it_runs(client, monkeypatch):
    at, old = stamp(), days_ago(3)
    chatty = agent(client, "Chatty")
    with client.app.state.db.transaction() as c:
        road(c, at, "chatted")
        c.execute(
            "UPDATE communication_credentials SET chat_id=? WHERE credential_id=?",
            ("chat-session", chatty["id"]),
        )
        c.execute(
            "INSERT INTO agent_chats VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("chat", "item", "chatted", "chat-session", "Chat", old[:10], 0, 1, old, old, "codex", "", "[]"),
        )
    transcript = {"state": "idle", "lastActivityAt": old}
    monkeypatch.setattr(client.app.state.transcripts, "summary", lambda chat_id: dict(transcript))

    # The record was last changed three days ago, and so was the transcript.
    assert client.get("/api/v1/live").json()["agents"] == []

    # The transcript alone says the chat is at work.
    transcript["state"] = "working"
    working = client.get("/api/v1/live").json()
    assert [(node["id"], node["state"]) for node in working["agents"]] == [
        (chatty["agentId"], "working")
    ]
    assert [node["id"] for node in working["work"]] == ["road", "chatted"]
    assert all(edge["active"] for edge in working["workEdges"])
    assert working["summary"]["workingChats"] == 1 and working["summary"]["activeAgents"] == 1

    # Its turn has closed, but it was active inside the window: the tie holds, the agent is idle.
    transcript.update(state="idle", lastActivityAt=stamp())
    closed = client.get("/api/v1/live").json()
    assert [(node["id"], node["state"]) for node in closed["agents"]] == [
        (chatty["agentId"], "idle")
    ]
    assert [node["id"] for node in closed["work"]] == ["road", "chatted"]
    assert closed["summary"]["workingChats"] == 0 and closed["summary"]["activeAgents"] == 0


def test_live_leaves_out_retired_steps_and_archived_roadmaps_without_loose_ends(client):
    at = stamp()
    first, second = agent(client, "On a retired step"), agent(client, "On an archived roadmap")
    with client.app.state.db.transaction() as c:
        road(c, at, ("kept", "gone"), steps=(("gone", 1),))
        c.execute(
            "INSERT INTO roadmaps VALUES(?,?,?,?,?,?,?,?)",
            ("shelved", "Shelved", "", 1, 1, at, at, None),
        )
        c.execute(
            "INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("buried", "buried", "", "doing", None, None, 1, 0, 1, at, at, "shelved"),
        )
        reports(c, first, "first", "kept", at)
        reports(c, second, "second", "buried", at)

    answer = client.get("/api/v1/live").json()
    # Both agents report, so both show; the retired step and the archived roadmap's item do not.
    assert {node["id"] for node in answer["agents"]} == {first["agentId"], second["agentId"]}
    assert [node["id"] for node in answer["work"]] == ["road", "kept"]
    shown = {node["id"] for node in answer["work"]}
    assert {edge["workId"] for edge in answer["workEdges"]} <= shown
    assert {edge["agentId"] for edge in answer["workEdges"]} == {first["agentId"]}
    assert answer["summary"]["activeAgents"] == 2


def test_live_counts_for_a_roadmap_never_exceed_the_counts_for_everything(client):
    at, old = stamp(), days_ago(3)
    asker = agent(client, "Asker")
    with client.app.state.db.transaction() as c:
        road(c, at, "item")
        c.execute(
            "INSERT INTO roadmap_questions VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("question", "road", "Which way?", "", "", "open", 0, 1, at, at),
        )
        proposes(c, "waiting", "submitted", "item", old, actor=asker)
        for identity, archived, tag in (
            ("tagged", 0, ("item", "item")),
            ("archived", 1, ("roadmap", "road")),
            ("elsewhere", 0, None),
        ):
            c.execute(
                "INSERT INTO discussions VALUES(?,?,?,?,?,?,?)",
                (identity, identity, archived, 1, old, old, None),
            )
            if tag:
                c.execute(
                    "INSERT INTO discussion_contexts VALUES(?,?,?,?,?,?,?)",
                    (identity, *tag, 0, "local-user", "human", old),
                )
            asks(c, identity + "-ask", identity, asker, [("human", "local-user")], old)
            asks(c, identity + "-ask-agent", identity, None, [("agent", asker["agentId"])], old)

    counts = ("pendingRequests", "waitingOnAgents", "waitingProposals", "openQuestions", "needsYou")
    everything = client.get("/api/v1/live").json()["summary"]
    scoped = client.get("/api/v1/live", params={"roadmapId": "road"}).json()["summary"]
    # The archived discussion counts in neither; the untagged one only without a roadmap.
    assert {key: everything[key] for key in counts} == {
        "pendingRequests": 2,
        "waitingOnAgents": 2,
        "waitingProposals": 1,
        "openQuestions": 1,
        "needsYou": 4,
    }
    assert {key: scoped[key] for key in counts} == {
        "pendingRequests": 1,
        "waitingOnAgents": 1,
        "waitingProposals": 1,
        "openQuestions": 1,
        "needsYou": 3,
    }


def test_live_surfaces_untracked_running_session(client, monkeypatch):
    monkeypatch.setattr(
        live_api,
        "running_sessions",
        lambda _: [
            {
                "pid": 321,
                "sessionId": "session-1",
                "hostSessionId": None,
                "name": "Local task",
                "cwd": "C:/work",
                "entrypoint": "cli",
                "kind": "claude",
                "status": "waiting",
                "waitingFor": "permission",
                "startedAt": stamp(),
                "statusAt": stamp(),
                "version": "1",
            }
        ],
    )
    answer = client.get("/api/v1/live").json()
    # No pid: since audit 3.7 nothing reads the records that gave one, and nothing showed it.
    assert answer["untrackedSessions"] == [
        {
            "sessionId": "session-1",
            "hostSessionId": None,
            "name": "Local task",
            "state": "waiting",
            "entrypoint": "cli",
            "cwd": "C:/work",
            "model": "",
            "startedAt": answer["untrackedSessions"][0]["startedAt"],
        }
    ]

    # Once an agent on the map is tied to the session, the session is that agent's card.
    local = agent(client, "Local")
    with client.app.state.db.transaction() as c:
        c.execute(
            "UPDATE communication_credentials SET chat_id=? WHERE credential_id=?",
            ("session-1", local["id"]),
        )
    tied = client.get("/api/v1/live").json()
    assert [(node["id"], node["sessionState"]) for node in tied["agents"]] == [
        (local["agentId"], "waiting")
    ]
    assert tied["untrackedSessions"] == []


def test_live_is_person_only(client):
    issued = agent(client, "No live access")
    response = client.get("/api/v1/live", headers={"Authorization": "Bearer " + issued["token"]})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "agent_scope"
