"""Roadmap deliverables retain revision-scoped review state and their plan links."""

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


def request(c, method, path, payload=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())} if method != "GET" else {}
    return c.request(method, "/api/v1" + path, headers=headers, json=payload) if payload is not None else c.request(
        method, "/api/v1" + path, headers=headers
    )


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status, code):
    assert response.status_code == status, response.text
    result = response.json()["error"]
    assert result["code"] == code
    return result


def create_roadmap(c, title="Plan", parent_id=None, steps=None):
    payload = {"title": title, "steps": steps or []}
    if parent_id is not None:
        payload["parentId"] = parent_id
    return ok(request(c, "POST", "/roadmaps", payload))


def create_deliverable(c, roadmap_id, title="Output", **fields):
    return ok(request(c, "POST", f"/roadmaps/{roadmap_id}/deliverables", {"title": title, **fields}))


def requirement(text, *, note="", evidence=None, checked=True, identity=None):
    result = {"text": text, "checked": checked, "verificationNote": note, "evidence": evidence or []}
    if identity is not None:
        result["id"] = identity
    return result


def test_create_retry_and_submit_accept_use_the_expected_revision(client):
    roadmap = create_roadmap(client)
    payload = {
        "title": "Release archive",
        "requirements": [requirement("Build files are present", note="Local build passed")],
    }
    key = str(uuid.uuid4())
    created = ok(request(client, "POST", f"/roadmaps/{roadmap['id']}/deliverables", payload, key=key))
    replayed = ok(request(client, "POST", f"/roadmaps/{roadmap['id']}/deliverables", payload, key=key))
    assert (created["id"], replayed["id"], replayed["revision"]) == (created["id"], created["id"], 1)
    listed = ok(request(client, "GET", f"/roadmaps/{roadmap['id']}/deliverables?scope=own"))
    assert listed["total"] == 1 and [item["id"] for item in listed["items"]] == [created["id"]]

    submitted = ok(request(client, "POST", f"/deliverables/{created['id']}/submit", {"revision": 1}))
    assert (submitted["status"], submitted["revision"]) == ("ready_for_review", 2)
    accepted = ok(request(client, "POST", f"/deliverables/{created['id']}/accept",
                          {"revision": 2, "note": "Reviewed the build output"}))
    assert (accepted["status"], accepted["revision"]) == ("accepted", 3)
    history = ok(request(client, "GET", f"/deliverables/{created['id']}/history"))
    assert history["total"] == 1
    assert history["items"][0]["revision"] == 2
    assert history["items"][0]["snapshot"]["requirements"][0]["checked"] is True
    assert history["items"][0]["note"] == "Reviewed the build output"


def test_submit_accepts_a_verification_note_or_available_reference(client):
    roadmap = create_roadmap(client)
    resource = ok(request(client, "POST", "/resources", {"title": "Design notes", "body": "Approved layout"}))
    deliverable = create_deliverable(
        client,
        roadmap["id"],
        title="Design handoff",
        requirements=[
            requirement("Run instructions are complete", note="Readme checked"),
            requirement("Design notes are included", evidence=[{"kind": "resource", "id": resource["id"]}]),
        ],
    )

    submitted = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/submit", {"revision": 1}))
    assert submitted["status"] == "ready_for_review"
    accepted = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/accept", {"revision": submitted["revision"]}))
    assert accepted["status"] == "accepted"
    evidence = accepted["requirements"][1]["evidence"][0]
    assert evidence["availability"] == "available" and evidence["kind"] == "resource"


def test_resource_revision_drift_blocks_acceptance_until_reference_is_refreshed(client):
    roadmap = create_roadmap(client)
    resource = ok(request(client, "POST", "/resources", {"title": "Install guide", "body": "Install"}))
    deliverable = create_deliverable(
        client,
        roadmap["id"],
        title="Build package",
        inputs=[{"kind": "resource", "id": resource["id"]}],
        requirements=[requirement("The install guide is current", evidence=[{"kind": "resource", "id": resource["id"]}])],
    )
    submitted = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/submit", {"revision": deliverable["revision"]}))
    updated_resource = ok(request(client, "PATCH", f"/resources/{resource['id']}",
                                  {"revision": resource["revision"], "body": "Install and verify"}))
    assert updated_resource["revision"] == resource["revision"] + 1
    changed = ok(request(client, "GET", f"/deliverables/{deliverable['id']}"))
    assert changed["requirements"][0]["evidence"][0]["availability"] == "changed"
    error(request(client, "POST", f"/deliverables/{deliverable['id']}/accept", {"revision": submitted["revision"]}),
          422, "validation")
    still_review = ok(request(client, "GET", f"/deliverables/{deliverable['id']}"))
    assert (still_review["status"], still_review["revision"]) == ("ready_for_review", submitted["revision"])

    reopened = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/reopen",
                          {"revision": submitted["revision"], "reason": "Refresh the source"}))
    req = reopened["requirements"][0]
    refreshed = ok(request(client, "PATCH", f"/deliverables/{deliverable['id']}", {
        "revision": reopened["revision"],
        "inputs": [{"kind": "resource", "id": resource["id"], "refresh": True}],
        "requirements": [requirement(req["text"], checked=True,
                                     evidence=[{"kind": "resource", "id": resource["id"], "refresh": True}],
                                     identity=req["id"])],
    }))
    assert refreshed["inputs"][0]["availability"] == "available"
    assert refreshed["requirements"][0]["evidence"][0]["availability"] == "available"
    ready = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/submit", {"revision": refreshed["revision"]}))
    assert ok(request(client, "POST", f"/deliverables/{deliverable['id']}/accept", {"revision": ready["revision"]}))["status"] == "accepted"


def test_reopen_resets_checks_and_keeps_the_accepted_snapshot(client):
    roadmap = create_roadmap(client)
    deliverable = create_deliverable(client, roadmap["id"], requirements=[
        requirement("Package installs", note="Checked on a clean machine"),
    ])
    ready = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/submit", {"revision": 1}))
    accepted = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/accept", {"revision": ready["revision"]}))

    reopened = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/reopen",
                          {"revision": accepted["revision"], "reason": "Revise the package"}))
    assert (reopened["status"], reopened["requirements"][0]["checked"]) == ("planned", False)
    history = ok(request(client, "GET", f"/deliverables/{deliverable['id']}/history"))
    assert history["total"] == 1
    assert history["items"][0]["revision"] == ready["revision"]
    assert history["items"][0]["snapshot"]["requirements"][0]["checked"] is True
    assert any(event["kind"] == "deliverable.reopen" and event["reason"] == "Revise the package"
               for event in history["events"])


def test_archive_and_recover_deliverable(client):
    roadmap = create_roadmap(client)
    deliverable = create_deliverable(client, roadmap["id"])
    archived = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/archive", {"revision": 1}))
    assert (archived["archived"], archived["revision"]) == (True, 2)
    assert ok(request(client, "GET", f"/roadmaps/{roadmap['id']}/deliverables"))["total"] == 0
    archived_list = ok(request(client, "GET", f"/roadmaps/{roadmap['id']}/deliverables?archived=true"))
    assert [item["id"] for item in archived_list["items"]] == [deliverable["id"]]

    recovered = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/recover", {"revision": 2}))
    assert (recovered["archived"], recovered["revision"]) == (False, 3)
    assert ok(request(client, "GET", f"/roadmaps/{roadmap['id']}/deliverables"))["total"] == 1


def test_parent_tree_rollup_backlinks_and_unrelated_step_rejection(client):
    parent = create_roadmap(client, "Program")
    child = create_roadmap(client, "Component", parent_id=parent["id"], steps=[{"title": "Build component"}])
    child_step = child["steps"][0]["id"]
    parent_output = create_deliverable(client, parent["id"], title="Program package", stepIds=[child_step])
    child_output = create_deliverable(client, child["id"], title="Component package", stepIds=[child_step])

    tree = ok(request(client, "GET", f"/roadmaps/{parent['id']}/deliverables"))
    assert tree["total"] == 2 and tree["counts"]["planned"] == 2
    assert {item["id"] for item in tree["items"]} == {parent_output["id"], child_output["id"]}
    assert {item["roadmapId"] for item in tree["items"]} == {parent["id"], child["id"]}
    assert ok(request(client, "GET", f"/roadmaps/{parent['id']}/deliverables?scope=own"))["total"] == 1
    assert ok(request(client, "GET", f"/roadmaps/{child['id']}/deliverables"))["total"] == 1
    options = ok(request(client, "GET", f"/roadmaps/{parent['id']}/deliverable-options?kind=step"))
    assert child_step in {item["id"] for item in options["items"]}
    backlinks = ok(request(client, "GET", f"/steps/{child_step}/deliverables"))
    assert backlinks["total"] == 2 and {item["id"] for item in backlinks["items"]} == {
        parent_output["id"], child_output["id"]
    }

    unrelated = create_roadmap(client, "Other program", steps=[{"title": "Unrelated work"}])
    response = request(client, "POST", f"/roadmaps/{parent['id']}/deliverables",
                       {"title": "Invalid cross-tree link", "stepIds": [unrelated["steps"][0]["id"]]})
    error(response, 422, "validation")
    assert ok(request(client, "GET", f"/roadmaps/{parent['id']}/deliverables"))["total"] == 2


def test_step_progress_and_empty_dispatch_do_not_gate_acceptance(client):
    roadmap = create_roadmap(client, steps=[{"title": "Build"}])
    step_id = roadmap["steps"][0]["id"]
    assert ok(request(client, "GET", f"/roadmaps/{roadmap['id']}/waves"))["total"] == 0
    deliverable = create_deliverable(
        client, roadmap["id"], stepIds=[step_id], requirements=[requirement("Build is complete", note="Reviewed output")]
    )
    ready = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/submit", {"revision": 1}))
    accepted = ok(request(client, "POST", f"/deliverables/{deliverable['id']}/accept", {"revision": ready["revision"]}))
    assert accepted["status"] == "accepted"
    assert accepted["steps"][0]["available"] is True

    step = ok(request(client, "GET", f"/steps/{step_id}"))
    assert step["status"] == "open"
    ok(request(client, "PATCH", f"/steps/{step_id}/progress", {"revision": step["revision"], "status": "done"}))
    after_progress = ok(request(client, "GET", f"/deliverables/{deliverable['id']}"))
    assert (after_progress["status"], after_progress["revision"]) == ("accepted", accepted["revision"])


def test_step_links_survive_step_move_retirement_and_roadmap_restore(client):
    parent_a, parent_b, leaf = (str(uuid.uuid4()) for _ in range(3))
    roadmap = create_roadmap(client, steps=[
        {"id": parent_a, "title": "Phase A"},
        {"id": parent_b, "title": "Phase B"},
        {"id": leaf, "title": "Build", "parentId": parent_a},
    ])
    deliverable = create_deliverable(
        client, roadmap["id"], stepIds=[leaf], requirements=[requirement("Build passes", note="Reviewed")]
    )
    moved = ok(request(client, "POST", f"/steps/{leaf}/move", {"revision": roadmap["revision"], "parentId": parent_b}))
    assert moved["id"] == leaf
    assert ok(request(client, "GET", f"/deliverables/{deliverable['id']}"))["stepIds"] == [leaf]

    current = ok(request(client, "GET", f"/roadmaps/{roadmap['id']}"))
    saved_steps = [{key: step[key] for key in (
        "id", "title", "description", "outcome", "parentId", "position", "repositoryIds", "workClass"
    )} for step in current["steps"] if step["id"] != leaf and not step["retired"]]
    revised = ok(request(client, "POST", f"/roadmaps/{roadmap['id']}/revisions", {
        "revision": current["revision"], "title": current["title"], "overview": current["overview"], "steps": saved_steps,
    }))
    retired = ok(request(client, "GET", f"/deliverables/{deliverable['id']}"))
    assert retired["stepIds"] == [leaf] and retired["steps"][0]["available"] is False
    assert ok(request(client, "GET", f"/steps/{leaf}/deliverables"))["total"] == 0

    restored = ok(request(client, "POST", f"/roadmaps/{roadmap['id']}/restore",
                          {"revision": revised["revision"], "number": 1}))
    assert next(step for step in restored["steps"] if step["id"] == leaf)["retired"] is False
    active = ok(request(client, "GET", f"/deliverables/{deliverable['id']}"))
    assert active["stepIds"] == [leaf] and active["steps"][0]["available"] is True
    assert active["revision"] == deliverable["revision"]
