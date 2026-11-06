"""Agents change releases by proposal: a release, what it holds and its checks, under the person's
approval like their other records. Archiving and recovering stay the person's, and the person's own
release routes refuse an agent as before."""

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


def refused(response, status=422):
    assert response.status_code == status, response.text
    return response.json()["error"]


@pytest.fixture
def agent(client):
    return ok(call(client, "POST", "/credentials", {"label": "Implementer"}))


def draft(c, agent, *operations):
    return call(c, "POST", "/proposals", {"title": "Release prep", "rationale": "What the export needs before it ships.",
                                          "operations": list(operations)}, agent)


def submitted(c, agent, *operations):
    proposal = ok(draft(c, agent, *operations))
    return ok(call(c, "POST", f"/proposals/{proposal['id']}/submit", {"revision": proposal["revision"]}, agent))


def approve(c, proposal):
    return call(c, "POST", f"/proposals/{proposal['id']}/approve", {"revision": proposal["revision"]})


def history(c, identity):
    return ok(c.get("/api/v1/history", params={"entityId": identity}))["items"]


def test_an_agent_proposes_a_release_with_its_work_and_checks_and_approval_applies_it(client, agent):
    work = ok(call(client, "POST", "/work-items", {"title": "Export button"}))
    ana = ok(call(client, "POST", "/people", {"name": "Ana", "team": "Support"}))
    proposal = submitted(
        client, agent,
        {"action": "release.create", "payload": {"id": "@nov", "title": "November release", "releaseDate": "2026-11-20"}},
        {"action": "release.add", "payload": {"releaseId": "@nov", "targetType": "item", "targetId": work["id"]}},
        {"action": "check.create", "payload": {"id": "@tell", "releaseId": "@nov", "kind": "notify",
                                               "text": "Tell support the export button moves", "personId": ana["id"]}},
        {"action": "check.create", "payload": {"releaseId": "@nov", "kind": "prepare", "text": "Set EXPORT_MAX_ROWS",
                                               "dueDate": "2026-11-13", "status": "waiting"}},
    )
    # Nothing changes until the person approves.
    assert ok(client.get("/api/v1/releases"))["total"] == 0
    assert ok(approve(client, proposal))["status"] == "approved"
    (listed,) = ok(client.get("/api/v1/releases"))["items"]
    release = ok(client.get("/api/v1/releases/" + listed["id"]))
    assert (release["title"], release["releaseDate"]) == ("November release", "2026-11-20")
    assert [i["id"] for i in release["items"]] == [work["id"]]
    assert {(c["kind"], c["text"], c["personId"], c["dueDate"], c["status"]) for c in release["checks"]} == {
        ("notify", "Tell support the export button moves", ana["id"], None, "open"),
        ("prepare", "Set EXPORT_MAX_ROWS", None, "2026-11-13", "waiting")}
    # The history credits the agent behind each change.
    assert {e["actorKind"] for e in release["history"]} == {"agent"}
    assert [e["kind"] for e in history(client, release["id"])] == ["release.add", "release.create"]
    # A release is neither a work item nor a roadmap: holding the work changed it in no way.
    assert ok(client.get(f"/api/v1/work-items/{work['id']}/proposals"))["items"] == []


def test_an_agent_proposes_changes_to_an_existing_release_and_its_checks(client, agent):
    release = ok(call(client, "POST", "/releases", {"title": "December release"}))
    check = ok(call(client, "POST", "/release-checks", {"releaseId": release["id"], "kind": "test",
                                                         "text": "Export a large CSV"}))
    pr_owner = ok(call(client, "POST", "/work-items", {"title": "Raise the row limit"}))
    pr = ok(call(client, "POST", "/github", {"ownerType": "item", "ownerId": pr_owner["id"], "kind": "pr",
                                             "url": "https://github.com/acme/app/pull/9"}))
    proposal = submitted(
        client, agent,
        {"action": "release.update", "payload": {"id": release["id"], "revision": release["revision"],
                                                 "releaseDate": "2026-12-11"}},
        {"action": "release.add", "payload": {"releaseId": release["id"], "targetType": "github", "targetId": pr["id"]}},
        {"action": "check.update", "payload": {"id": check["id"], "revision": check["revision"], "status": "done",
                                               "details": "Passed on staging with 50,000 rows."}},
    )
    assert ok(approve(client, proposal))["status"] == "approved"
    shown = ok(client.get("/api/v1/releases/" + release["id"]))
    assert shown["releaseDate"] == "2026-12-11"
    assert [(c["id"], c["direct"]) for c in shown["changes"]] == [(pr["id"], True)]
    (done,) = shown["checks"]
    assert (done["status"], done["details"]) == ("done", "Passed on staging with 50,000 rows.")
    # A second proposal to take the PR out again, and one made stale by the person's edit.
    removal = submitted(client, agent, {"action": "release.remove", "payload": {
        "releaseId": release["id"], "targetType": "github", "targetId": pr["id"]}})
    stale = submitted(client, agent, {"action": "release.update", "payload": {
        "id": release["id"], "revision": shown["revision"], "title": "Agent's title"}})
    ok(call(client, "PATCH", "/releases/" + release["id"], {"revision": shown["revision"], "title": "Your title"}))
    assert ok(approve(client, removal))["status"] == "approved"
    assert ok(client.get("/api/v1/releases/" + release["id"]))["changes"] == []
    conflict = refused(approve(client, stale), 409)
    assert conflict["code"] == "conflict" and "Change 1 of 1, release.update" in conflict["message"]
    assert ok(client.get("/api/v1/releases/" + release["id"]))["title"] == "Your title"


def test_automatic_approval_applies_a_release_change_when_its_agent_submits_it(client, agent):
    release = ok(call(client, "POST", "/releases", {"title": "Hotfix"}))
    ok(call(client, "PATCH", "/settings", {"requireProposalApproval": False}))
    applied = submitted(client, agent, {"action": "check.create", "payload": {
        "releaseId": release["id"], "kind": "test", "text": "Smoke test the fix"}})
    assert (applied["status"], applied["autoApproved"]) == ("approved", True)
    assert [c["text"] for c in ok(client.get("/api/v1/releases/" + release["id"]))["checks"]] == ["Smoke test the fix"]


def test_a_proposal_names_only_what_a_release_change_can_take(client, agent):
    release = ok(call(client, "POST", "/releases", {"title": "Q1 release"}))
    # Archiving and recovering stay the person's.
    for action in ("release.archive", "release.recover", "check.archive", "check.recover"):
        error = refused(draft(client, agent, {"action": action, "payload": {"id": release["id"], "revision": 1}}))
        assert error["code"] == "validation", action
    # A draft must give each required key; its values are checked at submit, as every proposed change's are.
    assert refused(draft(client, agent, {"action": "check.create", "payload": {
        "releaseId": release["id"], "kind": "test"}}))["code"] == "validation"
    for operation in ({"action": "check.create", "payload": {"releaseId": release["id"], "kind": "announce", "text": "T"}},
                      {"action": "release.add", "payload": {"releaseId": release["id"], "targetType": "ticket",
                                                            "targetId": str(uuid.uuid4())}}):
        held = ok(draft(client, agent, operation))
        error = refused(call(client, "POST", f"/proposals/{held['id']}/submit", {"revision": held["revision"]}, agent))
        assert error["code"] == "validation", operation
    # A name must stand for a record of the kind its field takes.
    error = refused(draft(
        client, agent,
        {"action": "item.create", "payload": {"id": "@work", "title": "Export button"}},
        {"action": "check.create", "payload": {"releaseId": "@work", "kind": "test", "text": "T"}}))
    assert error["fields"] == {"operation": 2, "action": "check.create", "fields": {"releaseId": "wrong_kind"}}
    error = refused(draft(client, agent, {"action": "release.add", "payload": {
        "releaseId": release["id"], "targetType": "step", "targetId": "@first"}}))
    assert "step" in error["message"]
    # A name may stand for a work item or GitHub reference the same proposal creates.
    proposal = submitted(
        client, agent,
        {"action": "item.create", "payload": {"id": "@work", "title": "Export button"}},
        {"action": "github.create", "payload": {"id": "@pr", "ownerType": "item", "ownerId": "@work", "kind": "pr",
                                                "url": "https://github.com/acme/app/pull/12"}},
        {"action": "release.add", "payload": {"releaseId": release["id"], "targetType": "item", "targetId": "@work"}},
        {"action": "release.add", "payload": {"releaseId": release["id"], "targetType": "github", "targetId": "@pr"}})
    assert ok(approve(client, proposal))["status"] == "approved"
    shown = ok(client.get("/api/v1/releases/" + release["id"]))
    assert [i["title"] for i in shown["items"]] == ["Export button"]
    assert [(c["url"], c["direct"]) for c in shown["changes"]] == [("https://github.com/acme/app/pull/12", True)]


def test_a_proposal_creates_a_release_and_a_check_and_changes_both_by_name(client, agent):
    proposal = submitted(
        client, agent,
        {"action": "release.create", "payload": {"id": "@jan", "title": "January release"}},
        {"action": "check.create", "payload": {"id": "@smoke", "releaseId": "@jan", "kind": "test", "text": "Smoke test"}},
        {"action": "check.update", "payload": {"id": "@smoke", "revision": 1, "status": "done"}},
        {"action": "release.update", "payload": {"id": "@jan", "revision": 1, "releaseDate": "2027-01-15"}},
    )
    assert ok(approve(client, proposal))["status"] == "approved"
    (listed,) = ok(client.get("/api/v1/releases"))["items"]
    shown = ok(client.get("/api/v1/releases/" + listed["id"]))
    assert (shown["title"], shown["releaseDate"]) == ("January release", "2027-01-15")
    assert [(c["text"], c["status"]) for c in shown["checks"]] == [("Smoke test", "done")]


def test_staged_changes_keep_their_numbers_and_a_failure_applies_nothing(client, agent):
    # GitHub references apply after the other changes, and a release change that holds one after
    # them; an error still names the change by its place in the proposal.
    work = ok(call(client, "POST", "/work-items", {"title": "Export button"}))
    release = ok(call(client, "POST", "/releases", {"title": "Spring release"}))
    proposal = submitted(
        client, agent,
        {"action": "github.create", "payload": {"id": "@pr", "ownerType": "item", "ownerId": work["id"], "kind": "pr",
                                                "url": "https://github.com/acme/app/pull/3"}},
        {"action": "release.add", "payload": {"releaseId": release["id"], "targetType": "github", "targetId": "@pr"}},
        {"action": "release.update", "payload": {"id": release["id"], "revision": release["revision"],
                                                 "title": "Agent's title"}},
    )
    ok(call(client, "PATCH", "/releases/" + release["id"], {"revision": release["revision"], "title": "Your title"}))
    error = refused(approve(client, proposal), 409)
    assert error["code"] == "conflict" and "Change 3 of 3, release.update" in error["message"]
    assert ok(client.get("/api/v1/releases/" + release["id"]))["changes"] == []
    assert ok(client.get("/api/v1/work-items/" + work["id"]))["github"] == []


def test_an_approved_proposal_reaches_the_release_service_for_its_agent(tmp_path):
    # Only an approved proposal (app.dispatch) passes proposed=True; the person's routes never do.
    app = create_app(tmp_path, testing=True)
    proposer = {"id": "agent-credential", "label": "Implementer", "kind": "agent"}
    with TestClient(app, base_url=ORIGIN):
        with app.state.db.transaction() as c:
            made = app.state.work.apply(c, proposer, "release.create", {"title": "Theirs"}, proposed=True)
        assert made["title"] == "Theirs"


def test_a_proposal_cannot_change_an_archived_release(client, agent):
    release = ok(call(client, "POST", "/releases", {"title": "Shelved"}))
    proposal = submitted(client, agent, {"action": "check.create", "payload": {
        "releaseId": release["id"], "kind": "test", "text": "Too late"}})
    ok(call(client, "POST", f"/releases/{release['id']}/archive", {"revision": 1}))
    error = refused(approve(client, proposal), 409)
    assert error["code"] == "archived" and "Change 1 of 1, check.create" in error["message"]


def test_the_person_s_release_routes_still_refuse_an_agent(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        c.headers["X-CSRF-Token"] = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"},
                                           json={}).json()["csrfToken"]
        agent = ok(call(c, "POST", "/credentials", {"label": "Implementer"}))
        assert refused(call(c, "POST", "/releases", {"title": "Mine"}, agent), 403)["code"] == "agent_scope"
