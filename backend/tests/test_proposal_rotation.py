"""Rotating an agent's credential keeps its drafts its own.

Rotation issues a new credential bound to the same agent identity and revokes the old
one. Drafts record the credential that wrote them, so ownership compares through the
identity: the agent keeps seeing, editing, evidencing and submitting what it wrote,
while other agents still never see an unsubmitted draft.
"""

import secrets
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


@pytest.fixture
def wire(client):
    """What the agent CLI sends: a bearer token, no browser origin, no session cookie."""
    with TestClient(client.app, base_url=ORIGIN) as anonymous:
        yield anonymous


def call(c, method, path, body=None, token=None, **kwargs):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **({"Authorization": "Bearer " + token} if token else {})}
    return c.request(method, "/api/v1" + path, json=body, headers=headers, **kwargs)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def proposal_for(c, token, item):
    return ok(call(c, "POST", "/proposals", {"title": "Rename", "rationale": "Clearer", "operations": [
        {"action": "item.update", "payload": {"id": item["id"], "revision": item["revision"], "title": "Renamed"}}]},
        token))


def listed(c, token):
    return [p["id"] for p in ok(call(c, "GET", "/proposals", token=token))["items"]]


def test_a_rotated_credential_keeps_seeing_editing_and_submitting_its_drafts(client, wire):
    credential = ok(call(client, "POST", "/credentials", {"label": "Engineer"}))
    item = ok(call(client, "POST", "/work-items", {"title": "Target"}))
    draft = proposal_for(wire, credential["token"], item)
    rotated = ok(call(client, "POST", f"/credentials/{credential['id']}/rotate", {}))
    assert rotated["agentId"] == credential["agentId"] and rotated["id"] != credential["id"]
    token = rotated["token"]
    assert listed(wire, token) == [draft["id"]]
    assert ok(call(wire, "GET", f"/proposals/{draft['id']}", token=token))["status"] == "draft"
    edited = ok(call(wire, "PATCH", f"/proposals/{draft['id']}", {"revision": draft["revision"], "title": "Rename it"}, token))
    evidence = call(wire, "POST", f"/attachments?ownerType=proposal&ownerId={draft['id']}&name=why.txt", token=token,
                    content=b"Reasoning")
    assert evidence.status_code == 200, evidence.text
    submitted = ok(call(wire, "POST", f"/proposals/{draft['id']}/submit", {"revision": edited["revision"]}, token))
    assert submitted["status"] == "submitted"
    assert call(wire, "GET", "/proposals", token=credential["token"]).status_code == 401


def test_another_agents_draft_stays_private_after_either_rotates(client, wire):
    writer = ok(call(client, "POST", "/credentials", {"label": "Writer"}))
    reader = ok(call(client, "POST", "/credentials", {"label": "Reader"}))
    item = ok(call(client, "POST", "/work-items", {"title": "Target"}))
    draft = proposal_for(wire, writer["token"], item)
    rotated_reader = ok(call(client, "POST", f"/credentials/{reader['id']}/rotate", {}))
    rotated_writer = ok(call(client, "POST", f"/credentials/{writer['id']}/rotate", {}))
    for token in (rotated_reader["token"],):
        assert listed(wire, token) == []
        assert call(wire, "GET", f"/proposals/{draft['id']}", token=token).status_code == 404
        assert call(wire, "PATCH", f"/proposals/{draft['id']}", {"revision": draft["revision"], "title": "Taken"},
                    token).status_code == 404
    assert listed(wire, rotated_writer["token"]) == [draft["id"]]


def test_a_registered_agent_and_its_sponsor_keep_their_drafts_apart(client, wire):
    # Registration gives the new credential an identity of its own, never its sponsor's.
    sponsor = ok(call(client, "POST", "/credentials", {"label": "Shared agents"}))
    child_token = secrets.token_urlsafe(32)
    child = ok(call(wire, "POST", "/agent-registrations",
                    {"label": "Parser fix", "token": child_token, "chatId": str(uuid.uuid4())}, sponsor["token"]))
    assert child["agentId"] != sponsor["agentId"]
    item = ok(call(client, "POST", "/work-items", {"title": "Target"}))
    sponsors_draft = proposal_for(wire, sponsor["token"], item)
    childs_draft = proposal_for(wire, child_token, item)
    assert listed(wire, child_token) == [childs_draft["id"]]
    assert listed(wire, sponsor["token"]) == [sponsors_draft["id"]]
    assert call(wire, "GET", f"/proposals/{sponsors_draft['id']}", token=child_token).status_code == 404
    assert call(wire, "GET", f"/proposals/{childs_draft['id']}", token=sponsor["token"]).status_code == 404
