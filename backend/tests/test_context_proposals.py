"""What GET /context lists of proposals (K18, REMEDIATION D3): the open ones the caller may see,
drafts and those submitted for review, oldest first, as summaries with their number of changes and
without the changes themselves, capped by the context's limit. An agent never finds another
agent's draft there, and a closed proposal stays on GET /proposals and its own read."""

import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.common import now

ORIGIN = "http://127.0.0.1:8877"
SUMMARY = {"id", "title", "status", "revision", "actorLabel", "agentId", "createdAt", "submittedAt", "updatedAt",
           "operationCount"}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def call(c, method, path, body=None, token=None):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **({"Authorization": "Bearer " + token} if token else {})}
    response = c.request(method, "/api/v1" + path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def proposal(c, token, item, title, changes=1):
    return call(c, "POST", "/proposals", {"title": title, "rationale": "", "operations": [
        {"action": "item.update", "payload": {"id": item["id"], "revision": item["revision"], "title": f"{title} {n}"}}
        for n in range(changes)]}, token)


def closed(client, actor_id, status):
    """A proposal already closed, written straight into its table as the quota tests do."""
    identity, stamp = str(uuid.uuid4()), now()
    with client.app.state.db.transaction() as conn:
        conn.execute("INSERT INTO proposals(id,title,rationale,status,revision,actor_id,actor_label,actor_kind,operations,"
                     "created_at,updated_at,submitted_at) VALUES(?,?,'',?,2,?,'Closed','agent','[]',?,?,?)",
                     (identity, status.title(), status, actor_id, stamp, stamp, stamp))
    return identity


def listed(c, token=None, query=""):
    return call(c, "GET", "/context" + query, token=token)["proposals"]


def test_the_context_lists_the_open_proposals_each_caller_may_see_as_summaries(client):
    item = call(client, "POST", "/work-items", {"title": "Parser"})
    builder, reviewer = (call(client, "POST", "/credentials", {"label": label}) for label in ("Builder", "Reviewer"))
    mine = proposal(client, builder["token"], item, "Builder's draft", changes=3)
    theirs = proposal(client, reviewer["token"], item, "Reviewer's draft")
    sent = proposal(client, builder["token"], item, "Builder's submitted", changes=2)
    sent = call(client, "POST", f"/proposals/{sent['id']}/submit", {"revision": sent["revision"]}, builder["token"])
    persons = proposal(client, None, item, "The person's draft")
    for status in ("approved", "rejected", "withdrawn"):
        closed(client, builder["id"], status)

    # The person sees every open proposal, oldest first; closed ones are left out.
    everyone = listed(client)
    assert [p["title"] for p in everyone["items"]] == [
        "Builder's draft", "Reviewer's draft", "Builder's submitted", "The person's draft"]
    assert everyone["total"] == 4
    assert all(set(p) == SUMMARY for p in everyone["items"])
    by_id = {p["id"]: p for p in everyone["items"]}
    assert (by_id[mine["id"]]["operationCount"], by_id[sent["id"]]["operationCount"]) == (3, 2)
    assert (by_id[mine["id"]]["status"], by_id[mine["id"]]["submittedAt"]) == ("draft", None)
    assert (by_id[sent["id"]]["status"], by_id[sent["id"]]["submittedAt"]) == ("submitted", sent["submittedAt"])
    assert (by_id[sent["id"]]["revision"], by_id[persons["id"]]["agentId"]) == (sent["revision"], None)

    # An agent sees its own drafts and what is submitted, never another's draft or the person's.
    assert [p["id"] for p in listed(client, builder["token"])["items"]] == [mine["id"], sent["id"]]
    assert [p["id"] for p in listed(client, reviewer["token"])["items"]] == [theirs["id"], sent["id"]]

    # The context's limit and offset page this list as they page the others, and the total counts all.
    page = listed(client, query="?limit=2&offset=1")
    assert [p["title"] for p in page["items"]] == ["Reviewer's draft", "Builder's submitted"]
    assert page["total"] == 4

    # A closed proposal, and any proposal's changes, stay one read away.
    assert call(client, "GET", "/proposals?status=approved")["total"] == 1
    assert len(call(client, "GET", f"/proposals/{mine['id']}", token=builder["token"])["operations"]) == 3


def test_a_reopened_proposal_is_a_draft_again_and_odd_operations_count_as_none(client):
    item = call(client, "POST", "/work-items", {"title": "Parser"})
    builder, reviewer = (call(client, "POST", "/credentials", {"label": label}) for label in ("Builder", "Reviewer"))
    sent = proposal(client, builder["token"], item, "Sent, then reopened")
    sent = call(client, "POST", f"/proposals/{sent['id']}/submit", {"revision": sent["revision"]}, builder["token"])
    assert [p["id"] for p in listed(client, reviewer["token"])["items"]] == [sent["id"]]
    # Back in draft, it is its author's again: another agent no longer finds it.
    reopened = call(client, "POST", f"/proposals/{sent['id']}/reopen", {"revision": sent["revision"]}, builder["token"])
    assert reopened["status"] == "draft"
    assert listed(client, reviewer["token"]) == {"items": [], "total": 0}
    own, = listed(client, builder["token"])["items"]
    assert (own["status"], own["submittedAt"], own["revision"]) == ("draft", None, reopened["revision"])
    # Operations Worklog never writes count as none, rather than failing the read.
    with client.app.state.db.transaction() as conn:
        conn.execute("UPDATE proposals SET operations='not json', status='submitted', submitted_at=? WHERE id=?",
                     (now(), sent["id"]))
    assert [p["operationCount"] for p in listed(client)["items"]] == [0]
    # A page past the end holds nothing, and the total still counts every one.
    assert listed(client, query="?limit=200&offset=5") == {"items": [], "total": 1}
