"""Apply deliverable changes through the same Review and evidence paths agents use."""

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

    def draft(self):
        return self.call("POST", "/proposals", json={"title": "Handoff", "rationale": "Deliver the build"})

    def upload(self, proposal):
        return self.call("POST", f"/attachments?ownerType=proposal&ownerId={proposal['id']}&name=build.txt",
                         content=b"Build passed", headers={"Content-Type": "text/plain"})

    def changes(self, proposal, operations):
        return self.call("PATCH", f"/proposals/{proposal['id']}", json={
            "revision": proposal["revision"], "operations": operations,
        })

    def advance(self, proposal, verb, *, expected=200):
        return self.call("POST", f"/proposals/{proposal['id']}/{verb}",
                         json={"revision": proposal["revision"]}, expected=expected)


def agent_session(app, person, label):
    client = TestClient(app, base_url=ORIGIN)
    token = person.call("POST", "/credentials", json={"label": label})["token"]
    client.headers["Authorization"] = "Bearer " + token
    return Actor(client)


@pytest.fixture
def world(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as p:
        p.headers["Origin"] = ORIGIN
        session = p.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        p.headers["X-CSRF-Token"] = session.json()["csrfToken"]
        person = Actor(p)
        agent = agent_session(app, person, "Builder")
        roadmap = person.call("POST", "/roadmaps", json={"title": "Application"})
        yield app, person, agent, roadmap["id"]
        agent.client.close()


def proof_ref(upload):
    return {"kind": "proposal_attachment", "id": upload["id"]}


def create_change(roadmap, evidence, **kwargs):
    return {"action": "deliverable.create", "payload": {
        "roadmapId": roadmap, "title": "Buildable repository", "outputs": [evidence],
        "requirements": [{"id": "build", "text": "Clean build", "checked": True, "evidence": [evidence]}],
        **kwargs,
    }}


@pytest.mark.parametrize("approval_required", [True, False])
def test_proposed_handoff_copies_one_file_and_records_the_accepted_revision(world, approval_required):
    _, person, agent, roadmap = world
    person.call("PATCH", "/settings", json={"requireProposalApproval": approval_required})
    draft = agent.draft()
    upload = agent.upload(draft)
    draft = agent.changes(draft, [
        create_change(roadmap, proof_ref(upload), id="@repo"),
        {"action": "deliverable.submit", "payload": {"id": "@repo", "revision": 1}},
        {"action": "deliverable.accept", "payload": {"id": "@repo", "revision": 2, "note": "Reviewed the clean build"}},
    ])
    result = agent.advance(draft, "submit")
    list_path = f"/roadmaps/{roadmap}/deliverables"
    files_path = f"/attachments?ownerType=roadmap&ownerId={roadmap}"
    if approval_required:
        assert result["status"] == "submitted"
        assert person.call("GET", list_path)["total"] == 0
        assert person.call("GET", files_path)["items"] == []
        result = person.advance(result, "approve")
    assert result["status"] == "approved"
    assert result["autoApproved"] == (not approval_required)

    listing = agent.call("GET", list_path)
    assert listing["counts"] == {"planned": 0, "readyForReview": 0, "accepted": 1}
    identity = listing["items"][0]["id"]
    detail = agent.call("GET", f"/deliverables/{identity}")
    files = person.call("GET", files_path)["items"]
    assert len(files) == 1
    assert files[0]["id"] != upload["id"]
    assert files[0]["sha256"] == upload["sha256"]
    assert detail["outputs"][0]["id"] == detail["requirements"][0]["evidence"][0]["id"] == files[0]["id"]

    acceptance = agent.call("GET", f"/deliverables/{identity}/history")["items"][0]
    assert (detail["status"], detail["revision"], acceptance["revision"]) == ("accepted", 3, 2)
    assert acceptance["actor"]["kind"] == "agent" and acceptance["actor"]["label"] == "Builder"
    assert acceptance["proposalId"] == draft["id"]
    assert acceptance["snapshot"]["requirements"] == detail["requirements"]
    history = person.call("GET", f"/roadmaps/{roadmap}/proposals")
    assert history["items"][0]["id"] == draft["id"]


def test_stale_operation_rolls_back_the_delivery_and_copied_file(world):
    _, person, agent, roadmap = world
    path = f"/roadmaps/{roadmap}/deliverables"
    original = person.call("POST", path, json={"title": "Original"})
    item = f"/deliverables/{original['id']}"
    draft = agent.draft()
    upload = agent.upload(draft)
    draft = agent.changes(draft, [
        create_change(roadmap, proof_ref(upload)),
        {"action": "deliverable.update", "payload": {"id": original["id"], "revision": 1, "title": "Proposed"}},
    ])
    submitted = agent.advance(draft, "submit")
    person.call("PATCH", item, json={"revision": 1, "title": "Concurrent"})
    result = person.advance(submitted, "approve", expected=409)
    assert result["error"]["code"] == "conflict"
    assert person.call("GET", f"/proposals/{draft['id']}")["status"] == "submitted"
    assert person.call("GET", path)["total"] == 1
    assert person.call("GET", item)["title"] == "Concurrent"
    assert person.call("GET", f"/attachments?ownerType=roadmap&ownerId={roadmap}")["items"] == []


def test_draft_files_require_the_proposals_own_evidence_scope(world):
    app, person, agent, roadmap = world
    other = agent_session(app, person, "Another agent")
    upload = other.upload(other.draft())
    path = f"/roadmaps/{roadmap}/deliverables"
    detail = person.call("POST", path, json={"title": "Handoff"})
    identity = detail["id"]
    agent.call("POST", path, json={"title": "No"}, expected=403)
    agent.call("PATCH", f"/deliverables/{identity}", json={"revision": 1, "title": "No"}, expected=403)
    for verb in ("submit", "accept", "reopen", "archive", "recover"):
        agent.call("POST", f"/deliverables/{identity}/{verb}", json={"revision": 1}, expected=403)

    options = agent.call("GET", f"/roadmaps/{roadmap}/deliverable-options?kind=attachment")
    assert options["total"] == 0
    for ref in (proof_ref(upload), {"kind": "attachment", "id": upload["id"]}):
        person.call("POST", path, json={"title": "Invalid", "outputs": [ref]}, expected=422)
        draft = agent.changes(agent.draft(), [create_change(roadmap, ref)])
        if ref["kind"] == "proposal_attachment":
            agent.advance(draft, "submit", expected=422)
        else:
            submitted = agent.advance(draft, "submit")
            person.advance(submitted, "approve", expected=422)
    assert person.call("GET", path)["total"] == 1
    other.client.close()


@pytest.mark.parametrize("approval_required", [True, False])
def test_implicit_file_copies_count_against_the_proposal_operation_limit(world, approval_required):
    _, person, agent, roadmap = world
    person.call("PATCH", "/settings", json={"requireProposalApproval": approval_required})
    draft = agent.draft()
    upload = agent.upload(draft)
    create = {"action": "deliverable.create", "payload": {"roadmapId": roadmap, "title": "Pending"}}
    draft = agent.changes(draft, [create] * 499 + [create_change(roadmap, proof_ref(upload))])
    error = agent.advance(draft, "submit", expected=422)["error"]
    assert "500 operations including deliverable file copies" in error["message"]
    assert person.call("GET", f"/roadmaps/{roadmap}/deliverables")["total"] == 0
    assert person.call("GET", f"/attachments?ownerType=roadmap&ownerId={roadmap}")["items"] == []
