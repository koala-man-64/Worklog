"""Agents attach files to the person's records by proposal: a copy of evidence uploaded to that proposal."""

import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.common import AppError

ORIGIN = "http://127.0.0.1:8877"
REPORT = b"%PDF-1.7 parser findings"
NOT_EVIDENCE = "Change 1 of 1, attachment.create: attachmentId must name a file uploaded to this proposal as evidence"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def call(c, method, path, payload=None, agent=None, **kwargs):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **kwargs.pop("headers", {})}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload, **kwargs)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def agent(c, label="Report agent"):
    return ok(call(c, "POST", "/credentials", {"label": label}))


def draft(c, credential):
    return ok(call(c, "POST", "/proposals", {"title": "Attach the parser report", "rationale": "The findings", "operations": []}, credential))


def evidence(c, credential, proposal, name="parser-report.pdf", data=REPORT):
    return ok(call(c, "POST", f"/attachments?ownerType=proposal&ownerId={proposal['id']}&name={name}", agent=credential,
                   content=data, headers={"Content-Type": "application/pdf"}))


def attach(owner_type, owner_id, attachment_id):
    return {"action": "attachment.create", "payload": {"ownerType": owner_type, "ownerId": owner_id, "attachmentId": attachment_id}}


def submit(c, credential, proposal, operations):
    updated = ok(call(c, "PATCH", f"/proposals/{proposal['id']}", {"revision": proposal["revision"], "operations": operations}, credential))
    return call(c, "POST", f"/proposals/{proposal['id']}/submit", {"revision": updated["revision"]}, credential)


def approve(c, proposal):
    current = ok(call(c, "GET", f"/proposals/{proposal['id']}"))
    return call(c, "POST", f"/proposals/{proposal['id']}/approve", {"revision": current["revision"]})


def files(c, owner_type, owner_id):
    return ok(call(c, "GET", f"/attachments?ownerType={owner_type}&ownerId={owner_id}"))["items"]


def copies(c):
    with c.app.state.db.read() as conn:
        return conn.execute("SELECT count(*) FROM attachments WHERE owner_type<>'proposal'").fetchone()[0]


def records(c):
    item = ok(call(c, "POST", "/work-items", {"title": "Fix the parser"}))
    roadmap = ok(call(c, "POST", "/roadmaps", {"title": "Release", "steps": [{"title": "Cutover"}]}))
    repository = ok(call(c, "POST", "/repositories", {"name": "Service", "url": "https://github.com/team/service"}))
    return item, roadmap, repository


def test_an_approved_proposal_attaches_a_copy_of_its_evidence_to_each_record(client):
    item, roadmap, repository = records(client)
    credential = agent(client)
    proposal = draft(client, credential)
    file = evidence(client, credential, proposal)
    targets = [("item", item["id"]), ("step", roadmap["steps"][0]["id"]), ("roadmap", roadmap["id"]), ("repository", repository["id"])]
    submitted = submit(client, credential, proposal, [attach(kind, owner, file["id"]) for kind, owner in targets])
    assert submitted.status_code == 200, submitted.text
    # An agent still cannot attach a file to the person's records directly.
    direct = call(client, "POST", f"/attachments?ownerType=item&ownerId={item['id']}&name=direct.pdf", agent=credential, content=REPORT)
    assert direct.status_code == 403 and direct.json()["error"]["code"] == "agent_scope"
    assert copies(client) == 0
    assert ok(approve(client, proposal))["status"] == "approved"
    attached = []
    for kind, owner in targets:
        [copy] = files(client, kind, owner)
        assert copy["id"] != file["id"]
        assert {key: copy[key] for key in ("ownerType", "ownerId", "name", "mime", "size", "sha256", "archived", "revision")} == {
            "ownerType": kind, "ownerId": owner, "name": "parser-report.pdf", "mime": "application/pdf",
            "size": len(REPORT), "sha256": file["sha256"], "archived": False, "revision": 1}
        assert client.get(f"/api/v1/attachments/{copy['id']}?download=1").content == REPORT
        attached.append(copy["id"])
    # The proposal keeps the file it was reviewed with.
    assert [f["id"] for f in ok(call(client, "GET", f"/proposals/{proposal['id']}"))["attachments"]] == [file["id"]]
    # Each copy's history names the agent that proposed it.
    with client.app.state.db.read() as conn:
        actors = {r["entity_id"]: (r["actor_id"], r["actor_kind"]) for r in conn.execute(
            "SELECT entity_id,actor_id,actor_kind FROM events WHERE kind='attachment.create'")}
    assert {identity: actors[identity] for identity in attached} == dict.fromkeys(attached, (credential["id"], "agent"))
    # Search finds each copy by its name and opens its record; the evidence itself stays out of search.
    found = ok(call(client, "GET", "/search?q=parser-report"))["items"]
    assert sorted((r["id"], r["routeType"], r["routeId"]) for r in found) == sorted([
        (attached[0], "work", item["id"]), (attached[1], "roadmaps", roadmap["id"]),
        (attached[2], "roadmaps", roadmap["id"]), (attached[3], "repositories", repository["id"])])


def test_a_proposal_attaches_only_live_evidence_of_its_own(client):
    item, _, _ = records(client)
    credential, other = agent(client, "A"), agent(client, "B")
    mine = draft(client, credential)
    theirs = evidence(client, other, draft(client, other))
    the_persons = ok(call(client, "POST", f"/attachments?ownerType=item&ownerId={item['id']}&name=plan.txt", content=b"plan"))
    # Another draft's evidence, a file on the person's records and an unknown ID are refused alike.
    for foreign in (theirs["id"], the_persons["id"], str(uuid.uuid4())):
        mine = ok(call(client, "GET", f"/proposals/{mine['id']}", agent=credential))
        refused = submit(client, credential, mine, [attach("item", item["id"], foreign)])
        assert (refused.status_code, refused.json()["error"]) == (422, {
            "code": "validation", "message": NOT_EVIDENCE,
            "fields": {"operation": 1, "action": "attachment.create", "fields": {"attachmentId": "not_evidence"}}})
    # Only Worklog names the proposal that holds the file.
    smuggled = attach("item", item["id"], theirs["id"])
    smuggled["payload"]["proposalId"] = theirs["ownerId"]
    response = call(client, "POST", "/proposals", {"title": "Copy", "rationale": "", "operations": [smuggled]}, credential)
    assert (response.status_code, response.json()["error"]["message"]) == (422, "Invalid fields for attachment.create")
    # A file the person archived from the proposal stays where it is.
    mine = ok(call(client, "GET", f"/proposals/{mine['id']}", agent=credential))
    file = evidence(client, credential, mine)
    ok(call(client, "POST", f"/attachments/{file['id']}/archive", {"revision": file["revision"]}))
    refused = submit(client, credential, mine, [attach("item", item["id"], file["id"])])
    assert (refused.status_code, refused.json()["error"]["code"], refused.json()["error"]["message"]) == (
        409, "archived", "Change 1 of 1, attachment.create: That file was archived from this proposal, so it cannot be attached")
    assert ok(call(client, "GET", f"/proposals/{mine['id']}", agent=credential))["status"] == "draft"
    assert copies(client) == 1


def test_approval_refuses_a_file_its_record_cannot_take_and_applies_nothing(client):
    item, _, repository = records(client)
    credential = agent(client)

    def proposed(*operations):
        proposal = draft(client, credential)
        file = evidence(client, credential, proposal)
        resolved = [{**op, "payload": {**op["payload"], "attachmentId": file["id"]}} if op["action"] == "attachment.create" else op
                    for op in operations]
        assert submit(client, credential, proposal, resolved).status_code == 200
        return proposal

    # The person's own proposals hold evidence too, but no file lands on a proposal this way.
    response = approve(client, proposed(attach("proposal", item["id"], "")))
    assert (response.status_code, response.json()["error"]["message"]) == (
        422, "Change 1 of 1, attachment.create: Invalid proposed operation")
    archived = ok(call(client, "POST", f"/repositories/{repository['id']}/archive", {"revision": repository["revision"]}))
    response = approve(client, proposed(attach("repository", repository["id"], "")))
    assert (response.status_code, response.json()["error"]["code"], response.json()["error"]["message"]) == (
        409, "archived", "Change 1 of 1, attachment.create: Recover Service before attaching files to it")
    ok(call(client, "POST", f"/repositories/{repository['id']}/recover", {"revision": archived["revision"]}))
    # One stale change refuses the whole proposal, its files included.
    stale = {"action": "item.update", "payload": {"id": item["id"], "revision": item["revision"] + 1, "status": "doing"}}
    response = approve(client, proposed(attach("item", item["id"], ""), stale))
    assert (response.status_code, response.json()["error"]["code"]) == (409, "conflict")
    assert copies(client) == 0


def test_only_an_applied_proposal_names_the_file_to_copy(client):
    item, _, _ = records(client)
    credential = agent(client)
    file = evidence(client, credential, draft(client, credential))
    # Called without the proposal Worklog is applying, the operation copies nothing.
    person = {"id": "local-user", "label": "You", "kind": "human"}
    with pytest.raises(AppError) as refused, client.app.state.db.transaction() as conn:
        client.app.state.work.apply(conn, person, "attachment.create",
                                    {"ownerType": "item", "ownerId": item["id"], "attachmentId": file["id"]})
    assert (refused.value.code, refused.value.fields) == ("validation", {"attachmentId": "not_evidence"})
    assert copies(client) == 0


def test_with_approval_off_submitting_attaches_the_file_at_once(client):
    item, _, _ = records(client)
    credential = agent(client)
    ok(call(client, "PATCH", "/settings", {"requireProposalApproval": False}))
    proposal = draft(client, credential)
    file = evidence(client, credential, proposal)
    applied = ok(submit(client, credential, proposal, [attach("item", item["id"], file["id"])]))
    assert (applied["status"], applied["autoApproved"]) == ("approved", True)
    assert [f["sha256"] for f in files(client, "item", item["id"])] == [file["sha256"]]
    # A file it cannot attach refuses the submission, as any failed change does with approval off.
    proposal = draft(client, credential)
    refused = submit(client, credential, proposal, [attach("item", item["id"], file["id"])])
    assert refused.status_code == 422
    assert refused.json()["error"]["message"].startswith("Not submitted: with approval off, submitting applies the proposal, and that failed. " + NOT_EVIDENCE)
    assert ok(call(client, "GET", f"/proposals/{proposal['id']}", agent=credential))["status"] == "draft"
    assert copies(client) == 1
