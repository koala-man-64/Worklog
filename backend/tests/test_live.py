"""The live workflow is one deterministic, person-only read across the existing v31 tables."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from worklog import live_api
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


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


def base_graph(client):
    at = stamp()
    first, second = agent(client, "Agent A"), agent(client, "Agent B")
    with client.app.state.db.transaction() as c:
        c.execute(
            "INSERT INTO roadmaps VALUES(?,?,?,?,?,?,?,?)",
            ("road", "Delivery", "", 1, 0, at, at, None),
        )
        c.execute(
            "INSERT INTO steps VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("step", "road", "Build", "", None, 0, "doing", 1, 0, ""),
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
                "INSERT INTO participations VALUES(?,?,?,?,?,?,?,?,?,?)",
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
    at = stamp()
    with client.app.state.db.transaction() as c:
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
            "INSERT INTO discussion_messages VALUES(?,?,?,?,?,?,?,?,?,?)",
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
            "INSERT INTO discussion_requests VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("message", "all", "pending", None, None, None, None, None, None, None),
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
            "INSERT INTO discussion_messages VALUES(?,?,?,?,?,?,?,?,?,?)",
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
            "INSERT INTO discussion_requests VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("role-message", "all", "pending", None, None, None, None, None, None, None),
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
            "INSERT INTO discussion_messages VALUES(?,?,?,?,?,?,?,?,?,?)",
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
            "INSERT INTO discussion_messages VALUES(?,?,?,?,?,?,?,?,?,?)",
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


def test_live_scopes_subtree_rejects_unknown_and_controls_idle_agents(client):
    at = stamp()
    active, unrelated, idle = (
        agent(client, "Scoped"),
        agent(client, "Unrelated"),
        agent(client, "Idle"),
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
                "INSERT INTO participations VALUES(?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            c.execute(
                "INSERT INTO participation_contexts VALUES(?,?,?,?,?,?)",
                (f"scope-part-{number}", 0, repository, "", "", "main"),
            )

    unfiltered = client.get("/api/v1/live").json()
    assert idle["agentId"] not in {node["id"] for node in unfiltered["agents"]}
    with_idle = client.get("/api/v1/live", params={"includeIdle": True}).json()
    assert idle["agentId"] in {node["id"] for node in with_idle["agents"]}

    scoped = client.get("/api/v1/live", params={"roadmapId": "root"}).json()
    assert {node["id"] for node in scoped["work"]} >= {"root", "child", "root-item", "child-item"}
    assert "other-item" not in {node["id"] for node in scoped["work"]}
    assert {node["id"] for node in scoped["agents"]} == {active["agentId"]}
    assert {node["id"] for node in scoped["repositories"]} == {"root-repo"}
    assert scoped["untrackedSessions"] == []
    missing = client.get("/api/v1/live", params={"roadmapId": "missing"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


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
    assert answer["untrackedSessions"] == [
        {
            "pid": 321,
            "sessionId": "session-1",
            "hostSessionId": None,
            "name": "Local task",
            "state": "waiting",
            "entrypoint": "cli",
            "cwd": "C:/work",
            "model": "",
            "startedAt": answer["untrackedSessions"][0]["startedAt"],
            "agentIds": [],
        }
    ]


def test_live_is_person_only(client):
    issued = agent(client, "No live access")
    response = client.get("/api/v1/live", headers={"Authorization": "Bearer " + issued["token"]})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "agent_scope"
