"""Dependencies are directional, advisory, attributable and readable through the normal API."""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from uuid import uuid4

from test_registration import agent_http, register
from test_registration_continue import carry_on
from test_step_update import approve, change, client, ok, read, refused, submit, write

__all__ = ["agent_http", "client"]


def _ref(kind, identity):
    return {"type": kind, "id": identity}


def _links(c, ref, agent=None):
    return ok(read(c, "/dependencies?" + urlencode(ref), agent))


def _link(c, dependent, prerequisite, **extra):
    return write(c, "POST", "/dependencies", {"dependent": dependent, "prerequisite": prerequisite, **extra})


def _roadmap(c, title, steps):
    return ok(write(c, "POST", "/roadmaps", {"title": title, "overview": "", "steps": steps}))


def test_shared_dependency_follows_done_and_reopened_across_roadmaps(client):
    target = str(uuid4())
    upstream = _roadmap(client, "Publish", [])
    downstream = _roadmap(client, "Roll out", [{"id": target, "title": "Adopt", "position": 0}])
    work = ok(write(client, "POST", "/work-items", {"title": "Publication", "roadmapId": upstream["id"]}))
    ticket = ok(write(client, "POST", "/tickets", {"title": "Publish artifact", "itemId": work["id"]}))
    dependent, prerequisite = _ref("step", target), _ref("ticket", ticket["id"])
    edge = ok(_link(client, dependent, prerequisite))
    assert (edge["kind"], edge["state"], edge["revision"]) == ("shared", "waiting", 1)
    assert _links(client, dependent)["prerequisites"] == [edge]
    assert _links(client, prerequisite)["dependents"] == [edge]
    assert edge["prerequisite"]["roadmapId"] == upstream["id"]
    summary = f"/dependencies/roadmaps/{downstream['id']}"
    assert ok(read(client, summary)) == {"openByStep": {target: 1}}
    refused(write(client, "POST", f"/dependencies/{edge['id']}/confirm", {"revision": 1}), 422, "validation")

    # The dependency is visible while the human and agent retain their current progress paths.
    step = ok(read(client, f"/steps/{target}"))
    changed = ok(write(client, "PATCH", f"/steps/{target}/progress", {"status": "doing", "revision": step["revision"]}))
    assert changed["status"] == "doing"
    ticket = ok(write(client, "PATCH", f"/tickets/{ticket['id']}", {"revision": ticket["revision"], "status": "done", "resolution": "Artifact published"}))
    assert ok(read(client, summary)) == {"openByStep": {}}
    assert _links(client, dependent)["prerequisites"][0]["state"] == "satisfied"
    ticket = ok(write(client, "PATCH", f"/tickets/{ticket['id']}", {"revision": ticket["revision"], "status": "open"}))
    assert ok(read(client, summary)) == {"openByStep": {target: 1}}
    ok(write(client, "POST", f"/tickets/{ticket['id']}/archive", {"revision": ticket["revision"]}))
    assert _links(client, dependent)["prerequisites"][0]["state"] == "unavailable"


def test_a_phase_prerequisite_uses_active_leaf_progress(client):
    phase, leaf = str(uuid4()), str(uuid4())
    plan = _roadmap(client, "Contracts", [
        {"id": phase, "title": "Publication phase", "position": 0},
        {"id": leaf, "title": "Publish build", "parentId": phase, "position": 0},
    ])
    item = ok(write(client, "POST", "/work-items", {"title": "Consume build"}))
    dependent = _ref("item", item["id"])
    assert ok(_link(client, dependent, _ref("step", phase)))["state"] == "waiting"
    step = ok(read(client, f"/steps/{leaf}"))
    ok(write(client, "PATCH", f"/steps/{leaf}/progress", {"revision": step["revision"], "status": "done"}))
    assert _links(client, dependent)["prerequisites"][0]["state"] == "satisfied"
    # Archived planning still retains recorded progress.
    current = ok(read(client, f"/roadmaps/{plan['id']}"))
    ok(write(client, "POST", f"/roadmaps/{plan['id']}/archive", {"revision": current["revision"]}))
    assert _links(client, dependent)["prerequisites"][0]["state"] == "satisfied"


def test_catalog_and_delivery_keep_independent_meaning_and_merge_evidence(client):
    upstream = ok(write(client, "POST", "/repositories", {"name": "Contracts"}))
    downstream = ok(write(client, "POST", "/repositories", {"name": "Consumer", "upstreamIds": [upstream["id"]]}))
    survivor = ok(write(client, "POST", "/repositories", {"name": "Canonical contracts"}))
    dependent, prerequisite = _ref("repository", downstream["id"]), _ref("repository", upstream["id"])
    refused(_link(client, dependent, prerequisite), 422, "validation")
    manual = ok(_link(client, dependent, prerequisite, condition="Publish the signed build"))
    incoming = _links(client, dependent)["prerequisites"]
    assert [(link["kind"], link["state"]) for link in incoming] == [("catalog", "linked"), ("shared", "waiting")]

    ok(write(client, "POST", f"/repositories/{upstream['id']}/merge", {
        "intoId": survivor["id"], "revision": upstream["revision"], "intoRevision": survivor["revision"],
    }))
    catalog, pending = _links(client, dependent)["prerequisites"]
    assert catalog["prerequisite"]["id"] == survivor["id"]
    assert (pending["id"], pending["state"], pending["condition"]) == (
        manual["id"], "unavailable", "Publish the signed build",
    )
    assert pending["prerequisite"]["mergedInto"] == survivor["id"]
    confirmed = ok(write(client, "POST", f"/dependencies/{manual['id']}/confirm", {"revision": 1, "note": "Verified build"}))
    assert confirmed["state"] == "satisfied"
    assert confirmed["confirmation"]["note"] == "Verified build"
    refused(write(client, "POST", f"/dependencies/{manual['id']}/archive", {"revision": 1}), 409, "conflict")


def test_wait_is_scoped_and_owned_by_the_continued_agent(client, agent_http):
    sponsor = ok(write(client, "POST", "/credentials", {"label": "Sponsor"}))
    registered, token = register(agent_http, sponsor["token"], "Reporter")
    reporter = {**ok(registered), "token": token}
    other = ok(write(client, "POST", "/credentials", {"label": "Publisher"}))
    step_id = str(uuid4())
    roadmap = _roadmap(client, "Delivery", [{"id": step_id, "title": "Consume build", "position": 0}])
    prerequisite, context = _ref("agent", other["agentId"]), _ref("step", step_id)
    payload = {"prerequisite": prerequisite, "context": context, "condition": "Send the signed build"}
    waiting = ok(write(client, "POST", "/dependencies/waits", payload, reporter))
    assert waiting["reportedByAgentId"] == reporter["agentId"]
    assert waiting["context"]["roadmapId"] == roadmap["id"]
    assert waiting["state"] == "waiting"
    assigned = ok(_link(client, _ref("agent", reporter["agentId"]), prerequisite,
                        context=context, condition="Review the checksum"))
    assert assigned["kind"] == "shared" and assigned["reportedByAgentId"] is None
    refused(write(client, "POST", f"/dependencies/{assigned['id']}/confirm", {"revision": 1}, reporter), 403, "agent_scope")
    scoped = f"/dependencies/waits?roadmapId={roadmap['id']}"
    refused(write(client, "POST", "/dependencies/waits", payload, reporter), 409, "conflict")
    separate_work = ok(write(client, "POST", "/work-items", {"title": "A separate integration"}))
    other_wait = ok(write(client, "POST", "/dependencies/waits", {
        **payload, "context": _ref("item", separate_work["id"]),
    }, reporter))
    second_step_id = str(uuid4())
    second_roadmap = _roadmap(client, "Second tree", [{"id": second_step_id, "title": "Second step", "position": 0}])
    second_wait = ok(write(client, "POST", "/dependencies/waits", {
        **payload, "context": _ref("step", second_step_id),
    }, reporter))
    assert ok(read(client, scoped)) == {"items": [waiting]}
    multi_scoped = f"/dependencies/waits?roadmapIds={roadmap['id']}&roadmapIds={second_roadmap['id']}"
    assert ok(read(client, multi_scoped)) == {"items": [waiting, second_wait]}
    assert _links(client, prerequisite, other)["dependents"] == [waiting, assigned, other_wait, second_wait]
    refused(write(client, "POST", f"/dependencies/{waiting['id']}/confirm", {"revision": 1}, other), 403, "agent_scope")
    refused(write(client, "POST", "/dependencies", {"dependent": _ref("step", step_id),
            "prerequisite": prerequisite, "condition": "Publish"}, reporter), 403, "agent_scope")

    ok(write(client, "POST", "/agent-runs", {"clientRunKey": "independent-work", "displayName": "Parallel work"}, reporter))
    response, new_token = carry_on(agent_http, token, chat=str(uuid4()))
    continued = {**ok(response), "token": new_token}
    assert continued["agentId"] == reporter["agentId"]
    confirmed = ok(write(client, "POST", f"/dependencies/{waiting['id']}/confirm", {"revision": 1}, continued))
    assert confirmed["state"] == "satisfied"
    assert ok(read(client, scoped)) == {"items": []}
    assert ok(read(client, multi_scoped)) == {"items": [second_wait]}
    assert ok(read(client, "/dependencies/waits")) == {"items": [other_wait, second_wait]}
    again = ok(write(client, "POST", "/dependencies/waits", payload, continued))
    assert again["id"] != waiting["id"]
    assert ok(write(client, "POST", f"/dependencies/{again['id']}/archive", {"revision": 1}, continued))["archived"]


def test_concurrent_duplicates_idempotent_retry_and_cross_type_cycle(client):
    ticket = ok(write(client, "POST", "/tickets", {"title": "Publish"}))
    item = ok(write(client, "POST", "/work-items", {"title": "Integrate"}))
    dependent, prerequisite = _ref("item", item["id"]), _ref("ticket", ticket["id"])
    with ThreadPoolExecutor(max_workers=2) as threads:
        pending = [threads.submit(_link, client, dependent, prerequisite) for _ in range(2)]
        assert sorted(request.result().status_code for request in pending) == [200, 409]
    error = refused(_link(client, prerequisite, dependent), 422, "validation")
    assert error["fields"] == {"prerequisite": "cycle"}
    refused(_link(client, dependent, dependent), 422, "validation")

    other = ok(write(client, "POST", "/work-items", {"title": "Follow-up"}))
    payload = {"dependent": _ref("item", other["id"]), "prerequisite": dependent}
    key = {"Idempotency-Key": "dependency-test-retry"}
    def request():
        return client.post("/api/v1/dependencies", json=payload, headers=key)

    assert ok(request()) == ok(request())
    assert len(_links(client, payload["dependent"])["prerequisites"]) == 1


def test_shared_dependency_uses_approved_proposals_and_named_references(client):
    agent = ok(write(client, "POST", "/credentials", {"label": "Dependency planner"}))
    pub = ok(write(client, "POST", "/repositories", {"name": "Publisher"}))
    prerequisite = _ref("repository", pub["id"])
    proposed = submit(client, agent, "Add prerequisite", [
        change("item.create", id="@integration", title="Integrate"),
        change("dependency.create", dependent=_ref("item", "@integration"), prerequisite=prerequisite, condition="Publish"),
    ])
    assert _links(client, prerequisite)["dependents"] == []
    assert ok(approve(client, proposed))["status"] == "approved"
    [edge] = _links(client, prerequisite)["dependents"]
    assert edge["dependent"]["title"] == "Integrate"
    direct = write(client, "POST", f"/dependencies/{edge['id']}/confirm", {"revision": edge["revision"]}, agent)
    refused(direct, 403, "agent_scope")
    proposed = submit(client, agent, "Confirm publication", [
        change("dependency.confirm", id=edge["id"], revision=edge["revision"], note="Published"),
    ])
    assert ok(approve(client, proposed))["status"] == "approved"
    [confirmed] = _links(client, prerequisite)["dependents"]
    assert confirmed["confirmation"]["note"] == "Published"
    proposed = submit(client, agent, "Remove prerequisite", [
        change("dependency.archive", id=edge["id"], revision=confirmed["revision"]),
    ])
    assert ok(approve(client, proposed))["status"] == "approved"
    assert _links(client, prerequisite)["dependents"] == []



def test_dependency_proposal_targets_the_linked_ticket_item_and_roadmap(client):
    plan = _roadmap(client, "Rollout", [])
    item = ok(write(client, "POST", "/work-items", {"title": "Adopt", "roadmapId": plan["id"]}))
    ticket = ok(write(client, "POST", "/tickets", {"title": "Check rollout", "itemId": item["id"]}))
    repo = ok(write(client, "POST", "/repositories", {"name": "Publisher"}))
    planner = ok(write(client, "POST", "/credentials", {"label": "Planner"}))
    proposed = submit(client, planner, "Wait for the signed build", [
        change("dependency.create", dependent=_ref("ticket", ticket["id"]),
               prerequisite=_ref("repository", repo["id"]), condition="Publish the signed build"),
    ])
    board = ok(read(client, f"/roadmaps/{plan['id']}/board"))
    assert [entry["id"] for entry in board["waiting"]] == [proposed["id"]]
    assert ok(approve(client, proposed))["status"] == "approved"
    history = ok(read(client, f"/work-items/{item['id']}/proposals"))
    assert [entry["id"] for entry in history["items"]] == [proposed["id"]]
