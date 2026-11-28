"""Agent proposal workflow and human-only authority boundaries for step gates."""

import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


class Actor:
    def __init__(self, client):
        self.client = client

    def call(self, method, path, *, expected=200, **kwargs):
        headers = {"Idempotency-Key": str(uuid.uuid4()), **kwargs.pop("headers", {})}
        response = self.client.request(method, "/api/v1" + path, headers=headers, **kwargs)
        assert response.status_code == expected, response.text
        return response.json()

    def draft(self, title="Gate update", operations=None):
        payload = {"title": title, "rationale": "Track completion criteria"}
        if operations is not None:
            payload["operations"] = operations
        return self.call("POST", "/proposals", json=payload)

    def advance(self, proposal, verb, *, expected=200):
        return self.call("POST", f"/proposals/{proposal['id']}/{verb}",
                         json={"revision": proposal["revision"]}, expected=expected)


@pytest.fixture
def world(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as p:
        p.headers["Origin"] = ORIGIN
        session = p.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        p.headers["X-CSRF-Token"] = session.json()["csrfToken"]
        person = Actor(p)

        token = person.call("POST", "/credentials", json={"label": "Builder"})["token"]
        a = TestClient(app, base_url=ORIGIN)
        a.headers["Authorization"] = "Bearer " + token
        agent = Actor(a)

        step_id = str(uuid.uuid4())
        roadmap = person.call("POST", "/roadmaps", json={
            "title": "Service rollout",
            "steps": [{"id": step_id, "title": "Deploy service", "parentId": None, "position": 0}],
        })
        yield person, agent, roadmap["id"], step_id
        a.close()


def test_agent_proposes_chained_gate_create_pass_and_step_completion(world):
    person, agent, roadmap_id, step_id = world

    # Agent cannot write gates directly.
    agent.call("POST", f"/steps/{step_id}/gates", json={"title": "Direct"}, expected=403)

    # Agent proposes creating a standard gate, passing it with evidence, and completing the step in one proposal.
    draft = agent.draft("Verify and finish", [
        {"action": "gate.create", "payload": {
            "id": "@ci", "stepId": step_id, "title": "CI suite green", "kind": "verification",
        }},
        {"action": "gate.pass", "payload": {
            "id": "@ci", "revision": 1, "note": "All 120 tests passed",
        }},
        {"action": "progress.set", "payload": {"id": step_id, "revision": 1, "status": "done"}},
    ])
    submitted = agent.advance(draft, "submit")
    approved = person.advance(submitted, "approve")
    assert approved["status"] == "approved"

    step = agent.call("GET", f"/steps/{step_id}")
    assert step["status"] == "done"
    assert len(step["gates"]) == 1
    assert step["gates"][0]["state"] == "passed"
    assert step["gates"][0]["resolution"]["proposalId"] == approved["id"]

    # Roadmap applied proposals include the gate proposal.
    applied = person.call("GET", f"/roadmaps/{roadmap_id}/proposals")
    assert applied["items"][0]["id"] == approved["id"]


def test_agent_proposes_deliverable_and_linked_gate_with_at_name(world):
    person, agent, roadmap_id, step_id = world
    draft = agent.draft("Link deliverable gate", [
        {"action": "deliverable.create", "payload": {
            "id": "@pkg", "roadmapId": roadmap_id, "title": "Release package", "stepIds": [step_id],
        }},
        {"action": "gate.create", "payload": {
            "id": "@gate", "stepId": step_id, "title": "Package accepted",
            "target": {"type": "deliverable", "id": "@pkg"},
        }},
    ])
    submitted = agent.advance(draft, "submit")
    person.advance(submitted, "approve")

    step_gates = agent.call("GET", f"/steps/{step_id}/gates")
    assert step_gates["total"] == 1
    assert step_gates["items"][0]["target"]["type"] == "deliverable"
    assert step_gates["items"][0]["target"]["title"] == "Release package"
    assert step_gates["items"][0]["state"] == "open"


def test_human_only_gates_and_server_only_actions_are_strictly_blocked_for_agents(world):
    person, agent, roadmap_id, step_id = world

    # Agent may propose creating a human-only gate (requesting human sign-off).
    create_draft = agent.draft("Request security sign-off", [
        {"action": "gate.create", "payload": {
            "stepId": step_id, "title": "Security sign-off", "kind": "approval", "humanOnly": True,
        }},
    ])
    submitted = agent.advance(create_draft, "submit")
    person.advance(submitted, "approve")
    gate = person.call("GET", f"/steps/{step_id}/gates")["items"][0]
    assert gate["humanOnly"] is True

    # Agent cannot propose gate.waive, gate.archive, or gate.recover (SERVER_ONLY).
    for forbidden_action, payload in (
        ("gate.waive", {"id": gate["id"], "revision": gate["revision"], "reason": "skip"}),
        ("gate.archive", {"id": gate["id"], "revision": gate["revision"]}),
        ("gate.recover", {"id": gate["id"], "revision": gate["revision"]}),
    ):
        agent.call("POST", "/proposals", json={
            "title": "Bad", "rationale": "x", "operations": [{"action": forbidden_action, "payload": payload}],
        }, expected=422)

    # Agent cannot submit gate.pass or gate.update on a human-only gate.
    pass_draft = agent.draft("Try pass human gate", [
        {"action": "gate.pass", "payload": {"id": gate["id"], "revision": gate["revision"], "note": "LGTM"}},
    ])
    err_pass = agent.advance(pass_draft, "submit", expected=403)
    assert err_pass["error"]["code"] == "agent_scope"

    weaken_draft = agent.draft("Try clear humanOnly", [
        {"action": "gate.update", "payload": {
            "id": gate["id"], "revision": gate["revision"], "humanOnly": False,
        }},
    ])
    err_weaken = agent.advance(weaken_draft, "submit", expected=403)
    assert err_weaken["error"]["code"] == "agent_scope"

    # Even if approval is turned OFF (auto-approve), agent still cannot pass a human-only gate
    # or create+pass a human-only gate in the same proposal.
    person.call("PATCH", "/settings", json={"requireProposalApproval": False})
    auto_pass = agent.draft("Auto pass human gate", [
        {"action": "gate.pass", "payload": {"id": gate["id"], "revision": gate["revision"]}},
    ])
    err_auto = agent.advance(auto_pass, "submit", expected=403)
    assert err_auto["error"]["code"] == "agent_scope"

    chained_human = agent.draft("Create and pass human gate", [
        {"action": "gate.create", "payload": {
            "id": "@h", "stepId": step_id, "title": "Human check", "humanOnly": True,
        }},
        {"action": "gate.pass", "payload": {"id": "@h", "revision": 1}},
    ])
    err_chained = agent.advance(chained_human, "submit", expected=403)
    assert err_chained["error"]["code"] == "agent_scope"

    # Attempting to complete the step while the human-only gate is open fails with open_gates.
    complete_draft = agent.draft("Complete gated step", [
        {"action": "progress.set", "payload": {"id": step_id, "revision": 1, "status": "done"}},
    ])
    err_open = agent.advance(complete_draft, "submit", expected=409)
    assert err_open["error"]["code"] == "open_gates"

    # Human waives the gate; agent cannot propose reopening a waived gate.
    waived = person.call("POST", f"/gates/{gate['id']}/waive", json={
        "revision": gate["revision"], "reason": "Reviewed out-of-band",
    })
    reopen_draft = agent.draft("Try reopen waived", [
        {"action": "gate.reopen", "payload": {
            "id": gate["id"], "revision": waived["revision"], "reason": "Try",
        }},
    ])
    assert agent.advance(reopen_draft, "submit", expected=403)["error"]["code"] == "agent_scope"


def test_stale_proposal_detection_on_gate_revision_change(world):
    person, agent, roadmap_id, step_id = world
    gate = person.call("POST", f"/steps/{step_id}/gates", json={"title": "Smoke test"})
    draft = agent.draft("Pass smoke test", [
        {"action": "gate.pass", "payload": {"id": gate["id"], "revision": gate["revision"], "note": "Green"}},
    ])
    submitted = agent.advance(draft, "submit")

    # Person updates the gate directly, bumping revision and invalidating the submitted proposal.
    person.call("PATCH", f"/gates/{gate['id']}", json={"revision": gate["revision"], "title": "Smoke test v2"})
    conflict = person.advance(submitted, "approve", expected=409)
    assert conflict["error"]["code"] == "conflict"
