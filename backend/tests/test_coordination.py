"""Coordination exercises real auth and transactions without Git or filesystem activity."""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

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


def request(c, method, path, payload=None, agent=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def post(c, path, payload=None, agent=None, key=None):
    return ok(request(c, "POST", path, payload or {}, agent, key))


def repo(c, name="Worklog", **extra):
    return post(c, "/repositories", {"name": name, **extra})


def agent(c, label="Connector"):
    return post(c, "/credentials", {"label": label})


def run(c, credential, key=None):
    return post(c, "/agent-runs", {"clientRunKey": key or str(uuid.uuid4()), "displayName": "Implementation"}, credential)


def report(c, credential, repository, running=None, **extra):
    running = running or run(c, credential)
    return post(c, "/participations", {"runId": running["id"], "contexts": [{"repositoryId": repository["id"]}], **extra}, credential)


def test_catalog_search_normalization_reviewed_writes_and_bounds(client):
    first = repo(client, url="https://EXAMPLE.com/team/repo/", description="Repository planning")
    assert first["url"] == "https://example.com/team/repo"
    assert request(client, "POST", "/repositories", {"name": "Duplicate", "url": first["url"]}).status_code == 409
    credential = agent(client)
    assert request(client, "POST", "/repositories", {"name": "Not allowed"}, credential).status_code == 403
    assert ok(request(client, "GET", "/repositories?q=planning"))["total"] == 1
    assert request(client, "GET", "/repositories?limit=201").status_code == 422
    assert request(client, "POST", "/repositories", {"name": "Secret", "url": "https://user:pass@example.com/repo"}).status_code == 422


@pytest.mark.parametrize(("remote", "normalized"), [
    ("git@GITHUB.com:Team/Worklog.git", "git@github.com:Team/Worklog.git"),
    ("ssh://git@GITHUB.com/Team/Worklog.git", "ssh://git@github.com/Team/Worklog.git"),
    ("git@ssh.dev.azure.com:v3/Org/Project/Repo", "git@ssh.dev.azure.com:v3/Org/Project/Repo"),
    ("ssh://git@SSH.dev.azure.com:22/v3/Org/Project/Repo", "ssh://git@ssh.dev.azure.com:22/v3/Org/Project/Repo"),
])
def test_inert_ssh_repository_remotes_preserve_case_and_transport(client, remote, normalized):
    assert repo(client, url=remote)["url"] == normalized
    # Transport forms are distinct catalog metadata, not inferred repository identity.
    assert repo(client, "HTTPS form", url="https://github.com/Team/Worklog.git")["url"] == "https://github.com/Team/Worklog.git"


@pytest.mark.parametrize("remote", [
    "ssh://git:password@github.com/Team/Repo.git",
    "ssh://git:@github.com/Team/Repo.git",
    "ssh://git@github.com/Team/Repo.git?token=secret",
    "git@github.com:Team/Repo.git#fragment",
    "git@github.com:Team/Repo.git\n",
    "javascript:alert(1)",
    "file:///C:/repo",
])
def test_repository_remote_rejects_passwords_controls_and_unsafe_schemes(client, remote):
    assert request(client, "POST", "/repositories", {"name": "Invalid", "url": remote}).status_code == 422


def test_named_runs_retry_and_originating_credential_scope(client):
    credential = agent(client)
    first = run(client, credential, "stable-client-key")
    assert run(client, credential, "stable-client-key")["id"] == first["id"]
    assert run(client, credential)["id"] != first["id"]
    # Even credentials sharing the durable discussion identity cannot alter each other's runs.
    second = agent(client, "Second")
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE communication_credentials SET agent_id=? WHERE credential_id=?", (first["agentId"], second["id"]))
    response = request(client, "POST", f"/agent-runs/{first['id']}/end", {"revision": first["revision"]}, second)
    assert response.status_code == 403
    assert request(client, "POST", "/agent-runs", {"clientRunKey": "human", "displayName": "Human"}).status_code == 403


def test_overlapping_reports_multiple_worktrees_and_no_canonical_link(client):
    repository, credential = repo(client), agent(client)
    running = run(client, credential)
    contexts = [{"repositoryId": repository["id"], "worktreePath": "C:/one"}, {"repositoryId": repository["id"], "worktreePath": "C:/two"}]
    first = report(client, credential, repository, running, contexts=contexts, description="Working across checkouts")
    second = report(client, credential, repository, running)
    assert first["id"] != second["id"]
    assert len(first["contexts"]) == 2
    assert first["description"] == "Working across checkouts"
    assert ok(request(client, "GET", f"/participations?repositoryId={repository['id']}"))["total"] == 2
    assert ok(request(client, "GET", f"/work-items?repositoryId={repository['id']}"))["total"] == 0


def test_late_attach_and_context_edit_do_not_retarget_update_snapshots(client):
    first_repo, second_repo, credential = repo(client), repo(client, "Second"), agent(client)
    participation = report(client, credential, first_repo)
    update = post(client, f"/participations/{participation['id']}/updates", {"kind": "handoff", "text": "Original context"}, credential)
    work = post(client, "/work-items", {"title": "Attach later"})
    current = ok(request(client, "GET", f"/participations/{participation['id']}"))
    changed = ok(request(client, "PATCH", f"/participations/{participation['id']}", {"revision": current["revision"], "itemId": work["id"], "contexts": [{"repositoryId": second_repo["id"]}]}, credential))
    assert changed["itemId"] == work["id"]
    old_feed = ok(request(client, "GET", f"/coordination-updates?repositoryId={first_repo['id']}"))
    assert old_feed["items"][0] == update
    assert update["itemId"] is None
    assert ok(request(client, "GET", f"/coordination-updates?itemId={work['id']}"))["total"] == 0
    assert ok(request(client, "GET", f"/coordination-updates?repositoryId={second_repo['id']}"))["total"] == 0


def test_release_rejoin_revisions_and_retry_safe_updates(client):
    repository, credential = repo(client), agent(client)
    participation = report(client, credential, repository)
    released = post(client, f"/participations/{participation['id']}/release", {"revision": participation["revision"]}, credential)
    assert request(client, "POST", f"/participations/{participation['id']}/updates", {"text": "Late", "kind": "update"}, credential).status_code == 409
    rejoined = post(client, f"/participations/{participation['id']}/rejoin", {"revision": released["revision"]}, credential)
    assert request(client, "POST", f"/participations/{participation['id']}/release", {"revision": participation["revision"]}, credential).status_code == 409
    key = str(uuid.uuid4())
    first = post(client, f"/participations/{participation['id']}/updates", {"text": "One update", "kind": "update"}, credential, key)
    assert post(client, f"/participations/{participation['id']}/updates", {"text": "One update", "kind": "update"}, credential, key) == first
    assert ok(request(client, "GET", f"/coordination-updates?participationId={participation['id']}"))["total"] == 1
    assert rejoined["releasedAt"] is None


def test_stale_is_display_only_and_checkin_is_explicit(client):
    repository, credential = repo(client), agent(client)
    participation = report(client, credential, repository)
    old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE participations SET last_seen_at=? WHERE id=?", (old, participation["id"]))
    observed = ok(request(client, "GET", f"/participations/{participation['id']}"))
    assert observed["stale"] and observed["releasedAt"] is None and observed["lastSeenAt"] == old
    checked = post(client, f"/participations/{participation['id']}/check-in", {"revision": observed["revision"]}, credential)
    assert not checked["stale"]


def test_close_revoke_and_archive_release_without_completing_work(client):
    repository, credential = repo(client), agent(client)
    running = run(client, credential)
    work = post(client, "/work-items", {"title": "Still open"})
    participation = report(client, credential, repository, running, itemId=work["id"])
    ended = post(client, f"/agent-runs/{running['id']}/end", {"revision": running["revision"]})
    assert ended["endReason"] == "human_closed"
    assert ok(request(client, "GET", f"/participations/{participation['id']}"))["releaseReason"] == "human_closed"
    assert ok(request(client, "GET", f"/work-items/{work['id']}"))["status"] == "open"
    second = report(client, credential, repository)
    post(client, f"/credentials/{credential['id']}/revoke")
    assert ok(request(client, "GET", f"/participations/{second['id']}"))["releaseReason"] == "credential_revoked"
    assert request(client, "POST", "/agent-runs", {"clientRunKey": "later", "displayName": "Later"}, credential).status_code == 401


def test_repository_archive_preserves_history_and_other_live_targets(client):
    first, other, credential = repo(client), repo(client, "Other"), agent(client)
    only = report(client, credential, first)
    multiple = report(client, credential, first, contexts=[{"repositoryId": first["id"]}, {"repositoryId": other["id"]}])
    post(client, f"/repositories/{first['id']}/archive", {"revision": first["revision"]})
    assert ok(request(client, "GET", f"/participations/{only['id']}"))["releasedAt"]
    current = ok(request(client, "GET", f"/participations/{multiple['id']}"))
    assert current["releasedAt"] is None and current["contexts"][0]["archived"]
    assert ok(request(client, "GET", f"/participations?repositoryId={first['id']}"))["total"] == 0
    assert ok(request(client, "GET", f"/participations?repositoryId={other['id']}"))["total"] == 1
    assert request(client, "POST", "/participations", {"runId": current["runId"], "contexts": [{"repositoryId": first["id"]}]}, credential).status_code == 409
    checked = post(client, f"/participations/{multiple['id']}/check-in", {"revision": current["revision"]}, credential)
    assert checked["releasedAt"] is None
    post(client, f"/repositories/{first['id']}/recover", {"revision": first["revision"] + 1})
    assert ok(request(client, "GET", f"/participations/{only['id']}"))["releasedAt"]


def test_empty_targets_spoofing_and_canonical_writes_are_denied(client):
    repository, credential = repo(client), agent(client)
    running = run(client, credential)
    assert request(client, "POST", "/participations", {"runId": running["id"]}, credential).status_code == 422
    participation = report(client, credential, repository, running)
    assert request(client, "PATCH", f"/participations/{participation['id']}", {"revision": participation["revision"], "contexts": []}, credential).status_code == 422
    assert request(client, "POST", "/work-items", {"title": "No direct edit"}, credential).status_code == 403
    assert request(client, "POST", "/agent-runs", {"clientRunKey": "bad", "displayName": "Bad", "credentialId": credential["id"]}, credential).status_code == 422


def test_rotation_closes_runs_and_requires_fresh_reports(client):
    repository, credential = repo(client), agent(client)
    participation = report(client, credential, repository)
    replacement = post(client, f"/credentials/{credential['id']}/rotate")
    closed = ok(request(client, "GET", f"/participations/{participation['id']}"))
    assert closed["releaseReason"] == "credential_rotated"
    assert closed["run"]["endReason"] == "credential_rotated"
    assert replacement["agentId"] == closed["run"]["agentId"]
    assert request(client, "POST", f"/participations/{participation['id']}/rejoin", {"revision": closed["revision"]}, replacement).status_code == 403
    fresh = report(client, replacement, repository)
    assert fresh["runId"] != participation["runId"]


def test_item_id_is_normalized_before_persistence(client):
    repository, credential = repo(client), agent(client)
    work = post(client, "/work-items", {"title": "Normalize target"})
    participation = report(client, credential, repository, itemId=" " + work["id"] + " ")
    assert participation["itemId"] == work["id"]
    changed = ok(request(client, "PATCH", f"/participations/{participation['id']}", {"revision": participation["revision"], "itemId": " " + work["id"] + " "}, credential))
    assert changed["itemId"] == work["id"]


def test_revocation_between_middleware_and_receipt_replay_is_rechecked(client, monkeypatch):
    credential = agent(client)
    key = str(uuid.uuid4())
    body = {"clientRunKey": "retry", "displayName": "Retry"}
    post(client, "/agent-runs", body, credential, key)
    database = client.app.state.db
    original = database.transaction

    @contextmanager
    def revoke_before_write():
        with original() as c:
            c.execute("UPDATE credentials SET revoked=1 WHERE id=?", (credential["id"],))
            yield c

    monkeypatch.setattr(database, "transaction", revoke_before_write)
    response = request(client, "POST", "/agent-runs", body, credential, key)
    assert response.status_code == 401


def test_context_preserves_work_default_and_uses_bounded_list_records(client, monkeypatch):
    human = {"id": "local-user", "label": "You", "kind": "human"}
    with client.app.state.db.transaction() as c:
        for index in range(101):
            client.app.state.work.apply(c, human, "item.create", {"title": f"Context work {index}"})
    baseline = ok(request(client, "GET", "/work-items"))
    context = ok(request(client, "GET", "/context"))
    assert context["workItems"] == baseline
    assert len(context["workItems"]["items"]) == 100
    target = baseline["items"][0]["id"]

    def no_detail(*args):
        pytest.fail("Context must not materialize work detail/history")

    monkeypatch.setattr(client.app.state.work, "detail", no_detail)
    selected = ok(request(client, "GET", f"/context?itemId={target}"))
    assert selected["workItems"]["items"] == [baseline["items"][0]]
    assert selected["workItems"]["total"] == 1
    assert "collectionRevision" in selected["workItems"]
    assert "history" not in selected["workItems"]["items"][0]
    page = ok(request(client, "GET", f"/context?itemId={target}&limit=1&offset=1"))
    assert page["workItems"]["items"] == [] and page["workItems"]["total"] == 1


def test_context_scopes_catalog_runs_and_keeps_reported_work_separate(client):
    linked, reported, unrelated = repo(client, "Linked"), repo(client, "Reported"), repo(client, "Unrelated")
    credential = agent(client)
    work = post(client, "/work-items", {"title": "Scoped", "repositoryIds": [linked["id"]]})
    other_work = post(client, "/work-items", {"title": "Unrelated", "repositoryIds": [unrelated["id"]]})
    related_report = report(client, credential, reported, itemId=work["id"])
    report(client, credential, unrelated, itemId=other_work["id"])
    run(client, credential)  # Unattached runs must not leak into scoped summaries.

    scoped = ok(request(client, "GET", f"/context?itemId={work['id']}"))
    assert {r["id"] for r in scoped["repositories"]["items"]} == {linked["id"], reported["id"]}
    assert [r["id"] for r in scoped["runs"]["items"]] == [related_report["runId"]]
    assert [r["id"] for r in scoped["participations"]["items"]] == [related_report["id"]]
    assert scoped["workItems"]["items"][0]["repositoryIds"] == [linked["id"]]

    repo_scope = ok(request(client, "GET", f"/context?repositoryId={reported['id']}"))
    assert [r["id"] for r in repo_scope["repositories"]["items"]] == [reported["id"]]
    assert [r["id"] for r in repo_scope["runs"]["items"]] == [related_report["runId"]]
    assert repo_scope["workItems"]["items"] == []  # Reporting is not canonical linking.
    page = ok(request(client, "GET", f"/context?repositoryId={reported['id']}&limit=1&offset=1"))
    assert page["repositories"]["items"] == [] and page["repositories"]["total"] == 1
    assert page["runs"]["items"] == [] and page["runs"]["total"] == 1
