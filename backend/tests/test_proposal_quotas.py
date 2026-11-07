"""Agent quotas on proposals (consolidation step 3.4, K10, C-05) and the paged proposal list (REM-17).

Each quota is counted from the live tables, per agent identity: the limit succeeds, and one more
gets 429 agent_limit. The person is never limited. The list pages, and an agent never sees another
agent's drafts on any page."""

import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.common import now

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


def limited(response, words):
    assert response.status_code == 429, response.text
    error = response.json()["error"]
    assert error["code"] == "agent_limit" and words in error["message"], error


def credential(client, label="Engineer"):
    return ok(call(client, "POST", "/credentials", {"label": label}))


def draft(c, token, item):
    return ok(call(c, "POST", "/proposals", {"title": "Rename", "rationale": "", "operations": [
        {"action": "item.update", "payload": {"id": item["id"], "revision": item["revision"], "title": "Renamed"}}]}, token))


def held(client, actor_id, status, count, kind="agent"):
    """Proposals an actor already holds, written straight into the table the quotas count."""
    ids, stamp = [], now()
    with client.app.state.db.transaction() as conn:
        for _ in range(count):
            ids.append(str(uuid.uuid4()))
            conn.execute("INSERT INTO proposals(id,title,rationale,status,revision,actor_id,actor_label,actor_kind,operations,"
                         "created_at,updated_at,submitted_at) VALUES(?,'Held','',?,1,?,'Held',?,'[]',?,?,?)",
                         (ids[-1], status, actor_id, kind, stamp, stamp, None if status == "draft" else stamp))
    return ids


def evidence(client, proposal_id, count, size):
    """Evidence files a proposal already holds, recorded at the size the quota adds up."""
    with client.app.state.db.transaction() as conn:
        for _ in range(count):
            conn.execute("INSERT INTO attachments(id,owner_type,owner_id,name,mime,size,sha256,content,created_at) "
                         "VALUES(?,'proposal',?,'held.txt','text/plain',?,'0',x'00',?)", (str(uuid.uuid4()), proposal_id, size, now()))


def test_an_agent_keeps_at_most_50_drafts_under_any_of_its_credentials(client, wire):
    agent, item = credential(client), ok(call(client, "POST", "/work-items", {"title": "Target"}))
    held(client, agent["id"], "draft", 49)
    fiftieth = draft(wire, agent["token"], item)
    limited(call(wire, "POST", "/proposals", {"title": "One more", "rationale": "", "operations": []}, agent["token"]), "50 drafts")
    # Withdrawing one frees its place.
    ok(call(wire, "POST", f"/proposals/{fiftieth['id']}/withdraw", {"revision": fiftieth["revision"]}, agent["token"]))
    replacement = draft(wire, agent["token"], item)
    # The count is the identity's: a rotated credential still holds the drafts its old one wrote.
    rotated = ok(call(client, "POST", f"/credentials/{agent['id']}/rotate", {}))
    limited(call(wire, "POST", "/proposals", {"title": "One more", "rationale": "", "operations": []}, rotated["token"]), "50 drafts")
    # Superseding makes a draft too.
    ok(call(wire, "POST", f"/proposals/{replacement['id']}/withdraw", {"revision": replacement["revision"]}, rotated["token"]))
    held(client, rotated["id"], "draft", 1)
    limited(call(wire, "POST", "/proposals", {"title": "Again", "rationale": "", "operations": [],
                                              "supersedesId": replacement["id"]}, rotated["token"]), "50 drafts")
    # Another agent, and the person, are not held to this agent's count.
    draft(wire, credential(client, "Reviewer")["token"], item)
    held(client, "local-user", "draft", 60, kind="human")
    draft(client, None, item)


def test_an_agent_has_at_most_100_proposals_awaiting_review(client, wire):
    agent, item = credential(client), ok(call(client, "POST", "/work-items", {"title": "Target"}))
    held(client, agent["id"], "submitted", 99)
    first, second = draft(wire, agent["token"], item), draft(wire, agent["token"], item)
    first = ok(call(wire, "POST", f"/proposals/{first['id']}/submit", {"revision": first["revision"]}, agent["token"]))
    limited(call(wire, "POST", f"/proposals/{second['id']}/submit", {"revision": second["revision"]}, agent["token"]),
            "100 proposals awaiting review")
    # Once the person reviews one, the agent may submit again.
    ok(call(client, "POST", f"/proposals/{first['id']}/reject", {"revision": first["revision"]}))
    second = ok(call(wire, "POST", f"/proposals/{second['id']}/submit", {"revision": second["revision"]}, agent["token"]))
    assert second["status"] == "submitted"
    # With approval off nothing waits for review: submitting applies the proposal.
    ok(call(client, "PATCH", "/settings", {"requireProposalApproval": False}))
    third = draft(wire, agent["token"], item)
    assert ok(call(wire, "POST", f"/proposals/{third['id']}/submit", {"revision": third["revision"]}, agent["token"]))["status"] == "approved"


def test_reopening_takes_a_place_among_the_drafts(client, wire):
    agent, item = credential(client), ok(call(client, "POST", "/work-items", {"title": "Target"}))
    proposal = draft(wire, agent["token"], item)
    submitted = ok(call(wire, "POST", f"/proposals/{proposal['id']}/submit", {"revision": proposal["revision"]}, agent["token"]))
    held(client, agent["id"], "draft", 50)
    limited(call(wire, "POST", f"/proposals/{proposal['id']}/reopen", {"revision": submitted["revision"]}, agent["token"]), "50 drafts")
    # The person reopens it for the agent, past the agent's count.
    assert ok(call(client, "POST", f"/proposals/{proposal['id']}/reopen", {"revision": submitted["revision"]}))["status"] == "draft"


def test_a_proposal_holds_at_most_20_evidence_files_and_100_mb(client, wire):
    agent, item = credential(client), ok(call(client, "POST", "/work-items", {"title": "Target"}))

    def upload(proposal, token=None, content=b"x"):
        headers = {"Idempotency-Key": str(uuid.uuid4()), "Content-Type": "text/plain",
                   **({"Authorization": "Bearer " + token} if token else {})}
        return (wire if token else client).post(
            f"/api/v1/attachments?ownerType=proposal&ownerId={proposal['id']}&name=evidence.txt", content=content, headers=headers)

    files = draft(wire, agent["token"], item)
    evidence(client, files["id"], 19, 1)
    ok(upload(files, agent["token"]))
    limited(upload(files, agent["token"]), "20 evidence files")
    big = draft(wire, agent["token"], item)
    evidence(client, big["id"], 3, 33_333_333)
    ok(upload(big, agent["token"]))
    limited(upload(big, agent["token"]), "100,000,000 bytes")
    # The person attaches what they like.
    ok(upload(files))


def test_the_list_pages_and_never_shows_an_agent_another_agents_drafts(client, wire):
    mine, theirs = credential(client, "Mine"), credential(client, "Theirs")
    my_drafts = set(held(client, mine["id"], "draft", 3))
    held(client, theirs["id"], "draft", 3)
    their_submitted = set(held(client, theirs["id"], "submitted", 2))
    seen = []
    for offset in range(6):
        page = ok(call(wire, "GET", f"/proposals?limit=1&offset={offset}", token=mine["token"]))
        assert page["total"] == 5
        seen += [p["id"] for p in page["items"]]
    assert len(seen) == 5 and set(seen) == my_drafts | their_submitted
    # The person sees every proposal, 50 to a page unless asked for more, up to 200.
    held(client, "local-user", "approved", 52, kind="human")
    first = ok(call(client, "GET", "/proposals"))
    assert (len(first["items"]), first["total"]) == (50, 60)
    assert len(ok(call(client, "GET", "/proposals?limit=200"))["items"]) == 60
    assert call(client, "GET", "/proposals?limit=201").status_code == 422
    drafts = ok(call(client, "GET", "/proposals?status=draft&offset=4"))
    assert (len(drafts["items"]), drafts["total"]) == (2, 6)
