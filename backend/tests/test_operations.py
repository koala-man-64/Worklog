"""The one registry of proposal operations (consolidation step 2.3): what a proposal may carry,
the keys and values each operation takes, and that submit checks values as approve does."""

import json
import sqlite3
import typing
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from worklog import models
from worklog.app import create_app
from worklog.common import AppError
from worklog.operations import OPERATIONS, SERVER_ONLY, validate
from worklog.proposals import FIELDS

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def call(c, method, path, payload=None, agent=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def agent(client):
    return ok(call(client, "POST", "/credentials", {"label": "Planner"}))


def propose(c, agent, *operations):
    return call(c, "POST", "/proposals", {"title": "Change", "rationale": "", "operations": list(operations)}, agent)


def submit(c, agent, proposal):
    return call(c, "POST", f"/proposals/{proposal['id']}/submit", {"revision": proposal["revision"]}, agent)


def error(response, status):
    assert response.status_code == status, response.text
    return response.json()["error"]


def test_each_operation_is_defined_once_and_fields_follow_from_it():
    assert list(FIELDS) == list(OPERATIONS)
    assert {action: (set(s.required), set(s.allowed)) for action, s in OPERATIONS.items()} == FIELDS
    # The three where a proposal's keys differ from the person's own route.
    assert "text" in FIELDS["note.create"][0] and not models.NoteCreate.model_fields["text"].is_required()
    assert "steps" in FIELDS["roadmap.revise"][0] and not models.RoadmapRevision.model_fields["steps"].is_required()
    assert "proposalId" not in FIELDS["attachment.create"][1] and "proposalId" in models.AttachmentCreate.model_fields


def test_the_request_model_lists_exactly_the_registered_operations():
    action = models.Operation.model_fields["action"].annotation
    assert set(typing.get_args(action)) == set(OPERATIONS)
    assert not SERVER_ONLY & set(OPERATIONS)


def test_an_unregistered_action_never_validates():
    for action in sorted(SERVER_ONLY) + ["nothing.at_all"]:
        with pytest.raises(AppError, match="Unsupported proposal action"):
            validate(action, {"id": "x", "revision": 1})


@pytest.mark.parametrize("action", sorted(SERVER_ONLY))
def test_a_proposal_cannot_carry_an_operation_only_the_person_runs(client, agent, action):
    refused = error(propose(client, agent, {"action": action, "payload": {"id": "x", "revision": 1}}), 422)
    assert refused["code"] == "validation"


def test_a_stored_proposal_naming_one_is_refused_at_approve_and_applies_nothing(client, agent, tmp_path):
    item = ok(call(client, "POST", "/work-items", {"title": "Keep me"}))
    proposal = ok(propose(client, agent, {"action": "item.update", "payload": {"id": item["id"], "revision": 1, "title": "Renamed"}}))
    submitted = ok(submit(client, agent, proposal))
    # As if a stored row had been changed behind Worklog's back.
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        conn.execute("UPDATE proposals SET operations=? WHERE id=?", (json.dumps(
            [{"action": "roadmap.archive", "payload": {"id": item["id"], "revision": 1}}]), proposal["id"]))
        conn.commit()
    refused = error(call(client, "POST", f"/proposals/{proposal['id']}/approve", {"revision": submitted["revision"]}), 422)
    assert refused["message"] == "Unsupported proposal action"
    assert ok(call(client, "GET", f"/work-items/{item['id']}"))["title"] == "Keep me"


def test_only_worklog_names_the_proposal_an_attached_file_comes_from(client, agent):
    item = ok(call(client, "POST", "/work-items", {"title": "Parser"}))
    refused = error(propose(client, agent, {"action": "attachment.create", "payload": {
        "ownerType": "item", "ownerId": item["id"], "attachmentId": "a", "proposalId": "elsewhere"}}), 422)
    assert refused["message"] == "Invalid fields for attachment.create: not accepted proposalId"


BAD_VALUES = [
    ("item.update", {"id": "w", "revision": 1, "status": "finished"}, "Invalid proposed operation"),
    ("item.create", {"title": 42}, "Invalid proposed operation"),
    ("note.create", {"ownerType": "proposal", "ownerId": "p", "text": "x"}, "Invalid proposed operation"),
    ("github.create", {"ownerType": "item", "ownerId": "w", "kind": "tag", "url": "https://github.com/o/r/pull/1"},
     "Invalid proposed operation"),
    ("question.update", {"id": "q", "revision": 1, "status": "closed"}, "Invalid proposed operation"),
    ("entry.upsert", {"date": 20260101, "itemId": "w", "revision": 1}, "date must be a string"),
    ("item.update", {"id": 123, "revision": 1}, "id must be a string"),
]


@pytest.mark.parametrize("action,payload,reason", BAD_VALUES)
def test_submit_refuses_a_value_the_operation_never_takes_and_keeps_the_draft(client, agent, action, payload, reason):
    good = {"action": "item.create", "payload": {"title": "Fine"}}
    proposal = ok(propose(client, agent, good, {"action": action, "payload": payload}))
    refused = error(submit(client, agent, proposal), 422)
    assert refused["code"] == "validation"
    assert refused["message"] == f"Change 2 of 2, {action}: {reason}"
    assert refused["fields"]["operation"] == 2 and refused["fields"]["action"] == action
    assert ok(call(client, "GET", f"/proposals/{proposal['id']}", agent=agent))["status"] == "draft"


def test_what_only_the_record_can_check_still_waits_for_approve(client, agent):
    item = ok(call(client, "POST", "/work-items", {"title": "Parser"}))
    # A link's address is checked by the record's own rules, which run when it applies.
    proposal = ok(propose(client, agent, {"action": "link.create", "payload": {
        "ownerType": "item", "ownerId": item["id"], "url": "javascript:alert(1)"}}))
    submitted = ok(submit(client, agent, proposal))
    assert submitted["status"] == "submitted"
    refused = error(call(client, "POST", f"/proposals/{proposal['id']}/approve", {"revision": submitted["revision"]}), 422)
    assert refused["message"].startswith("Change 1 of 1, link.create: ")


def test_evidence_with_its_own_proposal_submits_and_applies(client, agent):
    item = ok(call(client, "POST", "/work-items", {"title": "Parser"}))
    proposal = ok(propose(client, agent))
    file = ok(client.request("POST", f"/api/v1/attachments?ownerType=proposal&ownerId={proposal['id']}&name=report.txt",
                             content=b"findings", headers={"Idempotency-Key": str(uuid.uuid4()), "Content-Type": "text/plain",
                                                           "Authorization": "Bearer " + agent["token"]}))
    updated = ok(call(client, "PATCH", f"/proposals/{proposal['id']}", {"revision": proposal["revision"], "operations": [
        {"action": "attachment.create", "payload": {"ownerType": "item", "ownerId": item["id"], "attachmentId": file["id"]}}]}, agent))
    submitted = ok(submit(client, agent, updated))
    approved = ok(call(client, "POST", f"/proposals/{proposal['id']}/approve", {"revision": submitted["revision"]}))
    assert approved["status"] == "approved"
