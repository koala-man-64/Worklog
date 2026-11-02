"""Coordination exercises real auth and transactions without Git or filesystem activity."""

import json
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.coordination import _repository_url, repository_identity

ORIGIN = "http://127.0.0.1:8877"
HUMAN = {"id": "local-user", "label": "You", "kind": "human"}
STAMP = "2026-01-01T00:00:00+00:00"


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
    assert first["url"] == "https://example.com/team/repo" and first["created"]
    again = repo(client, "Duplicate", url=first["url"])
    assert (again["id"], again["name"], again["created"]) == (first["id"], "Worklog", False)
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
    first = repo(client, url=remote)
    assert first["url"] == normalized
    # The address keeps its transport and case; the remote it names is registered once.
    https = "https://github.com/Team/Worklog.git" if "github" in remote.lower() else "https://dev.azure.com/Org/Project/_git/Repo"
    again = repo(client, "HTTPS form", url=https)
    assert (again["id"], again["url"], again["created"]) == (first["id"], normalized, False)


@pytest.mark.parametrize(("addresses", "identity"), [
    (["https://github.com/Team/Worklog", "https://GitHub.com/team/worklog.git", "http://github.com/Team/Worklog/",
      "git@github.com:Team/Worklog.git", "git@github.com:Team/Worklog.git/", "ssh://git@github.com/Team/Worklog.git",
      "ssh://git@github.com:22/team/worklog/", "https://token@github.com/Team/Worklog.git"], "github.com/team/worklog"),
    (["https://dev.azure.com/Org/Project/_git/Repo", "https://org@dev.azure.com/Org/Project/_git/Repo",
      "https://org.visualstudio.com/Project/_git/Repo", "https://Org.VisualStudio.com/DefaultCollection/Project/_git/Repo",
      "git@ssh.dev.azure.com:v3/Org/Project/Repo", "ssh://git@ssh.dev.azure.com:22/v3/org/project/repo"],
     "dev.azure.com/org/project/_git/repo"),
    (["https://dev.azure.com/Org/_git/Project", "https://org.visualstudio.com/_git/Project",
      "git@ssh.dev.azure.com:v3/Org/Project/Project"], "dev.azure.com/org/project/_git/project"),
    (["https://dev.azure.com/org/My%20Project/_git/My%20Repo", "git@ssh.dev.azure.com:v3/org/My%20Project/My%20Repo"],
     "dev.azure.com/org/my%20project/_git/my%20repo"),
    (["https://gitlab.example.com:8443/Group/Sub/Repo.git", "git@gitlab.example.com:group/sub/repo",
      "ssh://git@gitlab.example.com:2222/group/sub/repo.git"], "gitlab.example.com/group/sub/repo"),
    ([""], ""),
])
def test_every_address_of_a_remote_shares_one_identity(addresses, identity):
    assert {repository_identity(_repository_url(address)) for address in addresses} == {identity}


@pytest.mark.parametrize(("first", "second"), [
    ("https://github.com/team/worklog", "https://github.com/team/worklog-api"),
    ("https://github.com/team/repo", "https://gitlab.com/team/repo"),
    ("https://github.com/team/repo", "https://github.com/other/repo"),
    ("https://dev.azure.com/org/one/_git/repo", "https://dev.azure.com/org/two/_git/repo"),
    ("https://dev.azure.com/one/project/_git/repo", "https://two.visualstudio.com/project/_git/repo"),
])
def test_different_remotes_keep_different_identities(first, second):
    assert repository_identity(_repository_url(first)) != repository_identity(_repository_url(second))


def test_an_address_never_keeps_an_https_user_name(client):
    # Azure DevOps clone URLs carry the organization as a user name; others carry tokens.
    created = repo(client, url="https://org@dev.azure.com/org/project/_git/repo")
    assert created["url"] == "https://dev.azure.com/org/project/_git/repo"
    assert "org@" not in str(ok(request(client, "GET", f"/repositories/{created['id']}")))


def test_an_archived_remote_is_recovered_rather_than_registered_again(client):
    first = repo(client, url="https://github.com/team/old")
    post(client, f"/repositories/{first['id']}/archive", {"revision": first["revision"]})
    response = request(client, "POST", "/repositories", {"name": "Old again", "url": "git@github.com:team/old.git"})
    assert response.status_code == 409
    assert response.json()["error"] == {"code": "repository_archived", "fields": {"repositoryId": first["id"]},
                                         "message": "Worklog is registered at that address but archived; recover it instead"}
    assert ok(request(client, "GET", "/repositories?archived=true"))["total"] == 1


def test_an_address_edit_cannot_take_another_records_remote(client):
    first, second = repo(client, url="https://github.com/team/one"), repo(client, "Two", url="https://github.com/team/two")
    response = request(client, "PATCH", f"/repositories/{second['id']}", {"revision": second["revision"], "url": "git@github.com:team/one.git"})
    assert response.status_code == 409
    assert response.json()["error"]["message"] == "That repository is already registered as Worklog"
    # Another address of its own remote is an ordinary edit.
    changed = ok(request(client, "PATCH", f"/repositories/{first['id']}", {"revision": first["revision"], "url": "git@github.com:team/one.git"}))
    assert (changed["url"], changed["remoteIdentity"]) == ("git@github.com:team/one.git", first["remoteIdentity"])


def test_concurrent_registrations_of_one_remote_produce_one_record(tmp_path):
    app = create_app(tmp_path, testing=True)
    barrier, results = threading.Barrier(2), []

    def register(address):
        barrier.wait()
        with app.state.db.transaction() as c:
            results.append(app.state.work.apply(c, HUMAN, "repository.create", {"name": "Shared", "url": address}))

    threads = [threading.Thread(target=register, args=(address,)) for address in ("https://github.com/team/shared", "git@github.com:team/shared.git")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(result["created"] for result in results) == [False, True]
    assert len({result["id"] for result in results}) == 1
    with app.state.db.read() as c:
        assert c.execute("SELECT count(*) FROM repositories").fetchone()[0] == 1


def test_a_proposal_mixing_registered_and_new_repositories_applies(client):
    existing, credential = repo(client, "Registered", url="https://github.com/team/registered"), agent(client)
    operations = [{"action": "repository.create", "payload": {"name": name, "url": url}} for name, url in (
        ("Registered over SSH", "git@github.com:team/registered.git"), ("New", "https://github.com/team/new"),
        ("New again", "https://github.com/Team/New.git"))]

    def approve(ops):
        draft = post(client, "/proposals", {"title": "Register repositories", "rationale": "", "operations": ops}, credential)
        submitted = post(client, f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, credential)
        return request(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]})

    # A stale revision still rolls back every operation, the registrations included.
    stale = {"action": "repository.update", "payload": {"id": existing["id"], "revision": existing["revision"] + 1, "name": "Stale"}}
    assert approve([*operations, stale]).status_code == 409
    assert ok(request(client, "GET", "/repositories"))["total"] == 1
    assert ok(approve(operations))["status"] == "approved"
    listed = ok(request(client, "GET", "/repositories"))["items"]
    assert sorted((r["name"], r["url"]) for r in listed) == [("New", "https://github.com/team/new"), ("Registered", existing["url"])]


def test_flagged_duplicates_answer_for_their_remote_and_take_over_when_it_moves(client):
    first = repo(client, "First", url="https://github.com/team/shared")
    # Only an upgrade leaves two records on one remote; a duplicate stays flagged until merged.
    with client.app.state.db.transaction() as c:
        c.execute("INSERT INTO repositories(id,name,url,remote_identity,duplicate_of,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  ("duplicate", "Second", "git@github.com:team/shared.git", first["remoteIdentity"], first["id"], STAMP, STAMP))
    assert ok(request(client, "GET", "/repositories/duplicate"))["duplicateOf"] == first["id"]
    assert repo(client, url="https://github.com/team/shared.git")["id"] == first["id"]
    archived = post(client, f"/repositories/{first['id']}/archive", {"revision": first["revision"]})
    # An active record answers before an archived one.
    assert repo(client, url="https://github.com/team/shared.git")["id"] == "duplicate"
    recovered = post(client, f"/repositories/{first['id']}/recover", {"revision": archived["revision"]})
    moved = ok(request(client, "PATCH", f"/repositories/{first['id']}", {"revision": recovered["revision"], "url": "https://github.com/team/moved"}))
    assert moved["duplicateOf"] is None
    heir = ok(request(client, "GET", "/repositories/duplicate"))
    assert (heir["duplicateOf"], heir["revision"]) == (None, 2)
    assert repo(client, url="https://github.com/team/shared")["id"] == "duplicate"
    with client.app.state.db.read() as c:
        assert c.execute("SELECT actor_id FROM events WHERE entity_id='duplicate' AND kind='repository.update'").fetchone()[0] == HUMAN["id"]


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


def merge(c, source, target, agent=None):
    return request(c, "POST", f"/repositories/{source['id']}/merge",
                   {"revision": source["revision"], "intoId": target["id"], "intoRevision": target["revision"]}, agent)


def links(c, table, column, owner):
    with c.app.state.db.read() as conn:
        return [row[0] for row in conn.execute(f"SELECT repository_id FROM {table} WHERE {column}=?", (owner,))]


def test_merge_moves_links_reports_and_updates_and_the_old_remote_answers_with_the_survivor(client):
    old, new = repo(client, "Old name", url="https://github.com/team/old-name"), repo(client, "New name", url="https://github.com/team/new-name")
    credential = agent(client)
    both = post(client, "/work-items", {"title": "Both", "repositoryIds": [old["id"], new["id"]]})
    only = post(client, "/work-items", {"title": "Only old", "repositoryIds": [old["id"]]})
    chat = post(client, "/chats", {"ownerType": "item", "ownerId": only["id"], "chatId": "merge-chat", "title": "Chat", "repositoryIds": [old["id"]]})
    ticket = post(client, "/tickets", {"title": "Ticket", "repositoryIds": [old["id"]]})
    participation = report(client, credential, old)
    update = post(client, f"/participations/{participation['id']}/updates", {"kind": "update", "text": "Before the merge"}, credential)
    reported = ok(request(client, "GET", f"/participations/{participation['id']}"))
    # Merging is the person's call: agents cannot reach it.
    assert merge(client, old, new, credential).status_code == 403
    merged = ok(merge(client, old, new))
    assert (merged["mergedInto"], merged["archived"], merged["duplicateOf"]) == (new["id"], True, None)
    assert links(client, "item_repositories", "item_id", both["id"]) == [new["id"]]
    assert links(client, "item_repositories", "item_id", only["id"]) == [new["id"]]
    assert links(client, "chat_repositories", "chat_id", chat["id"]) == [new["id"]]
    assert links(client, "ticket_repositories", "ticket_id", ticket["id"]) == [new["id"]]
    # Reports move without a new revision, so the agent keeps working; snapshots keep their names.
    moved = ok(request(client, "GET", f"/participations/{participation['id']}"))
    assert (moved["contexts"][0]["repositoryId"], moved["revision"], moved["releasedAt"]) == (new["id"], reported["revision"], None)
    assert ok(request(client, "GET", f"/coordination-updates?repositoryId={new['id']}"))["items"] == [update]
    assert ok(request(client, "GET", f"/coordination-updates?repositoryId={old['id']}"))["total"] == 0
    with client.app.state.db.read() as c:
        history = {row[0]: json.loads(row[1]) for row in c.execute("SELECT entity_id,after_json FROM events WHERE kind='repository.merge'")}
    assert history[old["id"]]["moved"] == {"contexts": 1, "items": 2, "chats": 1, "tickets": 1, "updates": 1}
    assert history[new["id"]]["mergedFrom"] == old["id"]
    # The old remote now answers with the survivor, so agents on old clones land there.
    again = repo(client, "Old again", url="git@github.com:team/old-name.git")
    assert (again["id"], again["created"]) == (new["id"], False)
    resolved = ok(request(client, "GET", "/repositories/resolve?url=https://github.com/Team/Old-Name.git"))
    assert (resolved["remoteIdentity"], resolved["repository"]["id"]) == ("github.com/team/old-name", new["id"])
    # A merged record is read-only and takes no new reports.
    response = request(client, "POST", f"/repositories/{old['id']}/recover", {"revision": merged["revision"]})
    assert (response.status_code, response.json()["error"]["code"]) == (409, "repository_merged")
    response = request(client, "POST", "/participations", {"runId": participation["runId"], "contexts": [{"repositoryId": old["id"]}]}, credential)
    assert response.json()["error"] == {"code": "repository_merged", "fields": {"repositoryId": new["id"]},
                                         "message": "Old name was merged into New name; use that repository instead"}
    # With the survivor archived, the merged remote has nothing active to answer for it.
    post(client, f"/repositories/{new['id']}/archive", {"revision": new["revision"]})
    response = request(client, "POST", "/repositories", {"name": "Old", "url": "https://github.com/team/old-name"})
    assert (response.status_code, response.json()["error"]["fields"]) == (409, {"repositoryId": new["id"]})


def test_merging_flagged_duplicates_leaves_one_record_for_their_remote(client):
    first = repo(client, "First", url="https://github.com/team/shared")
    with client.app.state.db.transaction() as c:
        for identity, day in (("second", "02"), ("third", "03")):
            c.execute("INSERT INTO repositories(id,name,url,remote_identity,duplicate_of,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (identity, identity.title(), "git@github.com:team/shared.git", first["remoteIdentity"], first["id"],
                       f"2026-01-{day}T00:00:00+00:00", STAMP))
    second = ok(request(client, "GET", "/repositories/second"))
    # Keep a duplicate: the canonical record folds into it, and the other duplicate follows.
    ok(merge(client, first, second))
    kept, third = ok(request(client, "GET", "/repositories/second")), ok(request(client, "GET", "/repositories/third"))
    assert (kept["duplicateOf"], third["duplicateOf"]) == (None, "second")
    assert repo(client, url="https://github.com/team/shared.git")["id"] == "second"
    ok(merge(client, third, kept))
    assert ok(request(client, "GET", "/repositories/third"))["mergedInto"] == "second"
    with client.app.state.db.read() as c:
        holders = c.execute("SELECT id FROM repositories WHERE remote_identity=? AND duplicate_of IS NULL AND merged_into IS NULL",
                            (first["remoteIdentity"],)).fetchall()
    assert [row[0] for row in holders] == ["second"]


def test_merge_refuses_itself_stale_revisions_archived_survivors_and_merged_records(client):
    source, target, other = repo(client, "Source"), repo(client, "Target"), repo(client, "Other")
    assert merge(client, source, source).status_code == 422
    assert merge(client, source, {**target, "revision": target["revision"] + 1}).status_code == 409
    archived = post(client, f"/repositories/{other['id']}/archive", {"revision": other["revision"]})
    response = merge(client, source, archived)
    assert (response.status_code, response.json()["error"]["message"]) == (409, "Recover Other before merging into it")
    merged = ok(merge(client, source, target))
    assert merge(client, merged, target).json()["error"]["code"] == "repository_merged"
    assert merge(client, ok(request(client, "GET", f"/repositories/{target['id']}")), merged).json()["error"]["code"] == "repository_merged"


def test_resolve_reports_what_registering_an_address_would_do(client):
    existing = repo(client, url="https://dev.azure.com/org/project/_git/repo")
    found = ok(request(client, "GET", "/repositories/resolve?url=git@ssh.dev.azure.com:v3/org/project/repo"))
    assert (found["url"], found["repository"]["id"]) == ("git@ssh.dev.azure.com:v3/org/project/repo", existing["id"])
    assert ok(request(client, "GET", "/repositories/resolve?url=https://github.com/team/unknown"))["repository"] is None
    assert request(client, "GET", "/repositories/resolve?url=file:///C:/repo").status_code == 422


def test_a_failed_approval_names_the_change_that_failed(client):
    existing, credential = repo(client, url="https://github.com/team/existing"), agent(client)
    draft = post(client, "/proposals", {"title": "Register", "rationale": "", "operations": [
        {"action": "repository.create", "payload": {"name": "New", "url": "https://github.com/team/new"}},
        {"action": "repository.update", "payload": {"id": existing["id"], "revision": existing["revision"] + 1, "name": "Stale"}}]}, credential)
    submitted = post(client, f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, credential)
    response = request(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]})
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["message"] == "Change 2 of 2, repository.update: This record changed. Reload and review your draft."
    assert (error["fields"]["operation"], error["fields"]["action"]) == (2, "repository.update")
    assert ok(request(client, "GET", "/repositories"))["total"] == 1
