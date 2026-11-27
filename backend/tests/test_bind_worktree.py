"""Acceptance tests for binding caller-owned Git worktrees to logical leases."""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
import worklog.leases as lease_service
from fastapi.testclient import TestClient
from test_leases import _agent_post, _issue
from test_leases import _create_repo as _register_repo
from test_leases import env as _lease_env
from test_slot_worktrees import _git, make_repo_pair
from worklog import cli


def _create_repo(client: TestClient, tmp_path: Path, name: str) -> tuple[dict, Path, str]:
    _, base = make_repo_pair(tmp_path, name)
    url = f"https://github.com/example/{name}"
    _git(base, "remote", "set-url", "origin", url)
    return _register_repo(client, name, url=url), base, url


# Reuse the lease suite's disposable app, bootstrap, credential and request fixtures.
env = _lease_env


def _acquire(
    client: TestClient,
    token: str,
    repo: dict,
    branch: str,
    *,
    run_id: str | None = None,
    provider: str = "",
    chat_id: str = "",
) -> dict:
    payload = {"repositoryId": repo["id"], "branch": branch}
    if run_id is not None:
        payload["runId"] = run_id
    if provider:
        payload["provider"] = provider
    if chat_id:
        payload["chatId"] = chat_id
    response = _agent_post(client, token, "/leases/acquire", payload)
    assert response.status_code == 200, response.text
    return response.json()


def _caller_worktree(base: Path, tmp_path: Path, branch: str, name: str = "caller") -> Path:
    path = tmp_path / "caller-worktrees" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(base, "worktree", "add", str(path), "-b", branch, "HEAD")
    return path.resolve()


def _dirty(path: Path) -> None:
    # Preserve both a staged version and a later unstaged edit, plus an untracked file.
    (path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(path, "add", "app.py")
    (path / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    (path / "caller-note.txt").write_text("keep this caller data\n", encoding="utf-8")


def _snapshot(path: Path) -> dict:
    path = path.resolve()
    files = {
        file.relative_to(path).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in path.rglob("*")
        if file.is_file() and file.name != ".git"
    }
    registration = _git(path, "worktree", "list", "--porcelain")
    registered_paths = [
        Path(line.removeprefix("worktree ")).resolve()
        for line in registration.splitlines()
        if line.startswith("worktree ")
    ]
    assert path in registered_paths, f"{path} is missing from git worktree registration"
    return {
        "path": _git(path, "rev-parse", "--show-toplevel"),
        "branch": _git(path, "symbolic-ref", "--short", "HEAD"),
        "head": _git(path, "rev-parse", "HEAD"),
        "index": _git(path, "write-tree"),
        "status": _git(path, "status", "--porcelain=v1", "--untracked-files=all"),
        "files": files,
        "registration": registration,
    }


def _bind(
    client: TestClient,
    token: str,
    lease: dict,
    path: Path | str,
    *,
    key: str | None = None,
    revision: int | None = None,
    handoff_message_id: str | None = None,
):
    payload = {
        "revision": lease["revision"] if revision is None else revision,
        "worktreePath": str(path),
    }
    if handoff_message_id is not None:
        payload["handoffMessageId"] = handoff_message_id
    return _agent_post(client, token, f"/leases/{lease['id']}/bind-worktree", payload, key)


def _human_handoff(client: TestClient, repo: dict, lease: dict, path: Path, key: str) -> dict:
    text = f"Hand off worktree to lease {lease['id']}\n{path.resolve()}"
    response = client.post(
        f"/api/v1/discussions/{repo['discussionId']}/messages",
        json={"text": text},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_api_bind_is_canonical_replayable_and_does_not_change_the_worktree(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-api")
    owner = _issue(client, "API owner")
    lease = _acquire(client, owner["token"], repo, "codex/api-bind")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    before = _snapshot(path)

    alias_dir = path.parent / "alias"
    alias_dir.mkdir()
    aliased_path = alias_dir / ".." / path.name
    assert aliased_path.resolve() == path

    missing_key = client.post(
        f"/api/v1/leases/{lease['id']}/bind-worktree",
        json={"revision": lease["revision"], "worktreePath": str(aliased_path)},
        headers={"Authorization": "Bearer " + owner["token"]},
    )
    assert missing_key.status_code == 428
    assert missing_key.json()["error"]["code"] == "idempotency"

    first = _bind(client, owner["token"], lease, aliased_path, key="bind-api-replay-1")
    assert first.status_code == 200, first.text
    bound = first.json()
    assert bound["worktreePath"] == str(path)
    assert bound["worktreeSource"] == "caller"
    assert _snapshot(path) == before

    other_path = _caller_worktree(base, tmp_path, "codex/competing-destination", "competing")
    other_before = _snapshot(other_path)
    second_destination = _bind(
        client,
        owner["token"],
        bound,
        other_path,
        key="bind-api-second-destination",
        revision=bound["revision"],
    )
    assert second_destination.status_code == 409
    assert second_destination.json()["error"]["code"] == "worktree_in_use"
    assert client.get(f"/api/v1/leases/{lease['id']}").json()["worktreePath"] == str(path)
    assert _snapshot(other_path) == other_before
    _dirty(path)
    dirty_after_bind = _snapshot(path)

    replay = _bind(client, owner["token"], lease, aliased_path, key="bind-api-replay-1")
    assert replay.status_code == 200, replay.text
    assert replay.json() == bound

    changed_payload = _bind(client, owner["token"], lease, path, key="bind-api-replay-1")
    assert changed_payload.status_code == 409
    assert changed_payload.json()["error"]["code"] == "idempotency_conflict"

    stale = _bind(
        client,
        owner["token"],
        bound,
        path,
        key="bind-api-stale-1",
        revision=lease["revision"],
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"
    assert _snapshot(path) == dirty_after_bind


def test_bind_rejects_other_actors_and_non_active_or_expired_leases(env):
    client, tmp_path = env
    repo, _, _ = _create_repo(client, tmp_path, "bind-owner")
    owner = _issue(client, "Lease owner")
    other = _issue(client, "Other agent")
    lease = _acquire(client, owner["token"], repo, "codex/owner-only")
    path = tmp_path / "not-a-worktree"

    wrong_agent = _bind(client, other["token"], lease, path, key="bind-wrong-agent")
    assert wrong_agent.status_code == 403
    assert wrong_agent.json()["error"]["code"] == "lease_scope"

    human = client.post(
        f"/api/v1/leases/{lease['id']}/bind-worktree",
        json={"revision": lease["revision"], "worktreePath": str(path)},
        headers={"Idempotency-Key": "bind-human-actor"},
    )
    assert human.status_code == 403
    assert human.json()["error"]["code"] == "lease_scope"

    released_initial = _agent_post(
        client,
        owner["token"],
        f"/leases/{lease['id']}/release",
        {"revision": lease["revision"]},
    )
    assert released_initial.status_code == 200, released_initial.text

    policy = client.patch(
        f"/api/v1/repositories/{repo['id']}/slot-policy", json={"maxActiveLeases": 0}
    )
    assert policy.status_code == 200, policy.text
    queued = _acquire(client, other["token"], repo, "codex/queued")
    assert queued["state"] == "queued"
    queued_bind = _bind(client, other["token"], queued, path, key="bind-queued-state")
    assert queued_bind.status_code == 409
    assert queued_bind.json()["error"]["code"] == "lease_closed"

    active_repo, _, _ = _create_repo(client, tmp_path, "bind-closed")
    released = _acquire(client, owner["token"], active_repo, "codex/released")
    closed = _agent_post(
        client,
        owner["token"],
        f"/leases/{released['id']}/release",
        {"revision": released["revision"]},
    )
    assert closed.status_code == 200, closed.text
    released_bind = _bind(
        client,
        owner["token"],
        closed.json(),
        path,
        key="bind-released-state",
    )
    assert released_bind.status_code == 409
    assert released_bind.json()["error"]["code"] == "lease_closed"

    expired_repo, _, _ = _create_repo(client, tmp_path, "bind-expired")
    expired = _acquire(client, owner["token"], expired_repo, "codex/expired")
    past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    with client.app.state.db.transaction() as connection:
        connection.execute(
            "UPDATE repository_leases SET expires_at=? WHERE id=?", (past, expired["id"])
        )
    expired_bind = _bind(client, owner["token"], expired, path, key="bind-expired-state")
    assert expired_bind.status_code == 409
    assert expired_bind.json()["error"]["code"] == "lease_closed"


@pytest.mark.parametrize("invalid_tree", ["not-git", "wrong-branch", "wrong-origin"])
def test_bind_validates_git_checkout_branch_and_catalog_origin(env, invalid_tree):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-validation-" + invalid_tree)
    owner = _issue(client, "Validation owner")
    lease = _acquire(client, owner["token"], repo, "codex/expected")

    if invalid_tree == "not-git":
        path = tmp_path / "plain-directory"
        path.mkdir()
    else:
        branch = "codex/other" if invalid_tree == "wrong-branch" else lease["branch"]
        path = _caller_worktree(base, tmp_path, branch)
        if invalid_tree == "wrong-origin":
            _git(
                path,
                "remote",
                "set-url",
                "origin",
                "https://github.com/example/not-this-repository",
            )

    response = _bind(client, owner["token"], lease, path, key="bind-invalid-" + invalid_tree)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation"
    current = client.get(f"/api/v1/leases/{lease['id']}").json()
    assert current["state"] == "active"
    assert current["worktreeSource"] == "unbound"


def test_preexisting_tree_rejects_wrong_author_then_accepts_person_handoff(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-handoff")
    path = _caller_worktree(base, tmp_path, "codex/preexisting")
    _dirty(path)
    before = _snapshot(path)

    owner = _issue(client, "New worktree holder")
    unrelated = _issue(client, "Unrelated agent")
    lease = _acquire(client, owner["token"], repo, "codex/preexisting")
    text = f"Hand off worktree to lease {lease['id']}\n{path}"
    wrong_author = _agent_post(
        client,
        unrelated["token"],
        f"/discussions/{repo['discussionId']}/messages",
        {"text": text},
        key="bind-wrong-handoff-author",
    )
    assert wrong_author.status_code == 200, wrong_author.text

    refused = _bind(
        client,
        owner["token"],
        lease,
        path,
        key="bind-wrong-handoff",
        handoff_message_id=wrong_author.json()["id"],
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "worktree_handoff_required"
    assert _snapshot(path) == before

    human_handoff = _human_handoff(client, repo, lease, path, "bind-human-handoff")
    accepted = _bind(
        client,
        owner["token"],
        lease,
        path,
        key="bind-valid-human-handoff",
        handoff_message_id=human_handoff["id"],
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["worktreePath"] == str(path)
    assert accepted.json()["worktreeSource"] == "caller"
    assert _snapshot(path) == before


def test_fresh_dirty_tree_requires_handoff_and_accepts_person_handoff(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-fresh-dirty")
    owner = _issue(client, "Fresh dirty owner")
    lease = _acquire(client, owner["token"], repo, "codex/fresh-dirty")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    _dirty(path)
    before = _snapshot(path)

    refused = _bind(client, owner["token"], lease, path, key="bind-fresh-dirty-refused")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "worktree_handoff_required"
    assert _snapshot(path) == before

    handoff = _human_handoff(client, repo, lease, path, "bind-fresh-dirty-handoff")
    accepted = _bind(
        client,
        owner["token"],
        lease,
        path,
        key="bind-fresh-dirty-accepted",
        handoff_message_id=handoff["id"],
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["worktreeSource"] == "caller"
    assert _snapshot(path) == before


def test_same_owner_can_rebind_its_registered_dirty_worktree_after_release(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-owner-reuse")
    owner = _issue(client, "Returning worktree owner")
    first_lease = _acquire(client, owner["token"], repo, "codex/returning")
    path = _caller_worktree(base, tmp_path, first_lease["branch"])
    first_bound = _bind(client, owner["token"], first_lease, path, key="bind-first-owner-use")
    assert first_bound.status_code == 200, first_bound.text

    _dirty(path)
    dirty_before_release = _snapshot(path)
    released = _agent_post(
        client,
        owner["token"],
        f"/leases/{first_lease['id']}/release",
        {"revision": first_bound.json()["revision"]},
    )
    assert released.status_code == 200, released.text
    assert _snapshot(path) == dirty_before_release

    next_lease = _acquire(client, owner["token"], repo, "codex/returning")
    rebound = _bind(client, owner["token"], next_lease, path, key="bind-second-owner-use")
    assert rebound.status_code == 200, rebound.text
    assert rebound.json()["worktreePath"] == str(path)
    assert rebound.json()["worktreeSource"] == "caller"
    assert _snapshot(path) == dirty_before_release

    released_again = _agent_post(
        client,
        owner["token"],
        f"/leases/{next_lease['id']}/release",
        {"revision": rebound.json()["revision"]},
    )
    assert released_again.status_code == 200, released_again.text
    next_owner = _issue(client, "Worktree handoff recipient")
    recipient_lease = _acquire(client, next_owner["token"], repo, "codex/returning")
    refused = _bind(
        client,
        next_owner["token"],
        recipient_lease,
        path,
        key="bind-previous-holder-required",
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "worktree_handoff_required"

    previous_holder_handoff = _agent_post(
        client,
        owner["token"],
        f"/discussions/{repo['discussionId']}/messages",
        {"text": f"Hand off worktree to lease {recipient_lease['id']}\n{path}"},
        key="bind-previous-holder-handoff",
    )
    assert previous_holder_handoff.status_code == 200, previous_holder_handoff.text
    accepted = _bind(
        client,
        next_owner["token"],
        recipient_lease,
        path,
        key="bind-previous-holder-accepted",
        handoff_message_id=previous_holder_handoff.json()["id"],
    )
    assert accepted.status_code == 200, accepted.text
    assert _snapshot(path) == dirty_before_release


def test_competing_bindings_canonicalize_aliases_and_claim_one_path_atomically(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-race")
    legacy_duplicate = _register_repo(
        client,
        "bind-race-legacy-duplicate",
        url="https://github.com/example/bind-race-legacy-duplicate",
    )
    with client.app.state.db.transaction() as connection:
        connection.execute(
            "UPDATE repositories SET remote_identity=?,duplicate_of=? WHERE id=?",
            (repo["remoteIdentity"], repo["id"], legacy_duplicate["id"]),
        )
    left = _issue(client, "Left claimant")
    right = _issue(client, "Right claimant")
    lease_left = _acquire(client, left["token"], repo, "codex/shared-tree")
    lease_right = _acquire(client, right["token"], legacy_duplicate, "codex/shared-tree")
    path = _caller_worktree(base, tmp_path, "codex/shared-tree")
    _dirty(path)
    before = _snapshot(path)
    handoff_left = _human_handoff(client, repo, lease_left, path, "bind-race-handoff-left")
    handoff_right = _human_handoff(
        client, legacy_duplicate, lease_right, path, "bind-race-handoff-right"
    )

    alias = path.parent / "junction"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(path), str(alias))
        alias_input = Path(str(alias).replace("caller-worktrees", "CALLER-WORKTREES", 1))
        canonical_input = Path(str(path).replace("caller-worktrees", "CALLER-WORKTREES", 1))
        assert str(alias_input) != str(alias) and str(canonical_input) != str(path)
    else:
        alias.symlink_to(path, target_is_directory=True)
        alias_input = alias
        canonical_input = path
    assert alias_input.resolve() == path
    assert canonical_input.resolve() == path

    def bind(token: str, lease: dict, request_id: str, input_path: Path, message_id: str):
        return _bind(
            client,
            token,
            lease,
            input_path,
            key=request_id,
            handoff_message_id=message_id,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                bind,
                left["token"],
                lease_left,
                "bind-race-left",
                alias_input,
                handoff_left["id"],
            ),
            pool.submit(
                bind,
                right["token"],
                lease_right,
                "bind-race-right",
                canonical_input,
                handoff_right["id"],
            ),
        ]
        results = [future.result() for future in futures]

    successes = [response for response in results if response.status_code == 200]
    conflicts = [response for response in results if response.status_code == 409]
    assert len(successes) == 1, [response.text for response in results]
    assert len(conflicts) == 1, [response.text for response in results]
    assert conflicts[0].json()["error"]["code"] == "worktree_in_use"
    winner = successes[0].json()
    assert winner["worktreePath"] == str(path)
    assert winner["worktreeSource"] == "caller"
    loser_id = lease_right["id"] if winner["id"] == lease_left["id"] else lease_left["id"]
    loser = client.get(f"/api/v1/leases/{loser_id}").json()
    assert loser["state"] == "active"
    assert loser["worktreeSource"] == "unbound"
    assert _snapshot(path) == before


def test_concurrent_identical_bind_requests_return_the_single_saved_receipt(env, monkeypatch):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-identical-replay")
    owner = _issue(client, "Identical request owner")
    lease = _acquire(client, owner["token"], repo, "codex/identical-request")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    before = _snapshot(path)

    original = lease_service.slot_worktrees.validate_caller_worktree
    both_validated = threading.Barrier(2)

    def synchronize_after_validation(*args, **kwargs):
        result = original(*args, **kwargs)
        both_validated.wait(timeout=10)
        return result

    monkeypatch.setattr(
        lease_service.slot_worktrees, "validate_caller_worktree", synchronize_after_validation
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_bind, client, owner["token"], lease, path, key="bind-identical-request"),
            pool.submit(_bind, client, owner["token"], lease, path, key="bind-identical-request"),
        ]
        responses = [future.result(timeout=15) for future in futures]

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["revision"] == lease["revision"] + 1
    assert _snapshot(path) == before
    _dirty(path)
    dirty_after_bind = _snapshot(path)
    assert client.get(f"/api/v1/leases/{lease['id']}").json()["worktreeSource"] == "caller"
    assert _snapshot(path) == dirty_after_bind


def test_release_wins_if_it_closes_the_lease_during_worktree_validation(env, monkeypatch):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-release-race")
    owner = _issue(client, "Release race owner")
    lease = _acquire(client, owner["token"], repo, "codex/release-race")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    before = _snapshot(path)

    original = lease_service.slot_worktrees.validate_caller_worktree
    validated = threading.Event()
    resume = threading.Event()

    def pause_after_validation(*args, **kwargs):
        result = original(*args, **kwargs)
        validated.set()
        assert resume.wait(timeout=10), "bind validator was not released"
        return result

    monkeypatch.setattr(
        lease_service.slot_worktrees, "validate_caller_worktree", pause_after_validation
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _bind, client, owner["token"], lease, path, key="bind-release-race-request"
        )
        assert validated.wait(timeout=10), "bind did not reach out-of-transaction validation"
        released = _agent_post(
            client,
            owner["token"],
            f"/leases/{lease['id']}/release",
            {"revision": lease["revision"]},
            key="bind-release-race-release",
        )
        assert released.status_code == 200, released.text
        resume.set()
        refused = future.result(timeout=15)

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "conflict"
    closed = client.get(f"/api/v1/leases/{lease['id']}").json()
    assert closed["state"] == "released"
    assert closed["worktreeSource"] == "unbound"
    assert _snapshot(path) == before


def _forward_to_test_client(client: TestClient, request, **_options):
    url = urlsplit(request.full_url)
    response = client.request(
        request.get_method(),
        url.path + (f"?{url.query}" if url.query else ""),
        headers=dict(request.header_items()),
        content=request.data,
    )
    if response.status_code >= 400:
        raise HTTPError(
            request.full_url,
            response.status_code,
            "err",
            response.headers,
            io.BytesIO(response.content),
        )
    return io.BytesIO(response.content)


def test_cli_bind_worktree_uses_the_active_lease_and_preserves_local_state(
    env, monkeypatch, capsys
):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-cli")
    owner = _issue(client, "CLI owner")
    lease = _acquire(client, owner["token"], repo, "codex/cli-bind")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    before = _snapshot(path)
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", owner["token"])
    monkeypatch.setattr(
        cli, "_open", lambda request, **options: _forward_to_test_client(client, request, **options)
    )

    exit_code = cli.main(
        [
            "agent",
            "leases",
            "bind-worktree",
            lease["id"],
            "--path",
            str(path),
            "--revision",
            str(lease["revision"]),
            "--request-id",
            "bind-cli-request-1",
        ]
    )
    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["worktreePath"] == str(path)
    assert result["worktreeSource"] == "caller"
    assert _snapshot(path) == before
    _dirty(path)
    assert _snapshot(path) != before


@pytest.mark.parametrize(
    "end_action",
    ["release", "force", "run-end", "retire", "expire", "dead-session", "archive"],
)
def test_lifecycle_endings_preserve_caller_worktree_and_release_capacity(
    env, end_action, monkeypatch
):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-lifecycle-" + end_action)
    policy = client.patch(
        f"/api/v1/repositories/{repo['id']}/slot-policy", json={"maxActiveLeases": 1}
    )
    assert policy.status_code == 200, policy.text
    owner = _issue(client, "Bound worktree owner")
    waiter = _issue(client, "Capacity waiter")
    lease_owner_token = owner["token"]
    if end_action == "retire":
        lease_owner_token = "C" * 20 + "abcdefghijklmnopqrstuvwxyz0123456"
        registered = _agent_post(
            client,
            owner["token"],
            "/agent-registrations",
            {
                "label": "Self-retiring worktree owner",
                "token": lease_owner_token,
                "chatId": "bind-lifecycle-retiring-agent",
            },
        )
        assert registered.status_code == 200, registered.text
    run = None
    chat_id = str(uuid.uuid4()) if end_action == "dead-session" else ""
    if end_action == "run-end":
        started = _agent_post(
            client,
            owner["token"],
            "/agent-runs",
            {"clientRunKey": "bind-lifecycle-run", "displayName": "Bound worktree"},
        )
        assert started.status_code == 200, started.text
        run = started.json()
    lease = _acquire(
        client,
        lease_owner_token,
        repo,
        "codex/lifecycle",
        run_id=run["id"] if run is not None else None,
        provider="antigravity" if chat_id else "",
        chat_id=chat_id,
    )
    path = _caller_worktree(base, tmp_path, lease["branch"])
    bound_response = _bind(client, lease_owner_token, lease, path, key="bind-lifecycle-bind")
    assert bound_response.status_code == 200, bound_response.text
    bound = bound_response.json()
    _dirty(path)
    before = _snapshot(path)

    queued = _acquire(client, waiter["token"], repo, "codex/waiter")
    assert queued["state"] == "queued"

    if end_action == "release":
        ended = _agent_post(
            client,
            lease_owner_token,
            f"/leases/{lease['id']}/release",
            {"revision": bound["revision"]},
        )
        assert ended.status_code == 200, ended.text
    elif end_action == "force":
        ended = client.post(
            f"/api/v1/leases/{lease['id']}/release",
            json={"revision": bound["revision"], "force": True},
            headers={"Idempotency-Key": "bind-lifecycle-force"},
        )
        assert ended.status_code == 200, ended.text
    elif end_action == "run-end":
        ended = _agent_post(
            client,
            owner["token"],
            f"/agent-runs/{run['id']}/end",
            {"revision": run["revision"]},
        )
        assert ended.status_code == 200, ended.text
    elif end_action == "retire":
        ended = _agent_post(client, lease_owner_token, "/agent-registrations/retire", {})
        assert ended.status_code == 200, ended.text
    elif end_action == "expire":
        past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        with client.app.state.db.transaction() as connection:
            connection.execute(
                "UPDATE repository_leases SET expires_at=? WHERE id=?", (past, lease["id"])
            )
        client.app.state.leases.sweep()
    elif end_action == "dead-session":
        past = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        with client.app.state.db.transaction() as connection:
            connection.execute(
                "UPDATE repository_leases SET last_heartbeat_at=? WHERE id=?", (past, lease["id"])
            )

        def ended_session(candidate: str, *, brain_root=None, now_epoch=None):
            assert candidate == chat_id
            return "ended"

        monkeypatch.setattr(lease_service.antigravity_sessions, "session_state", ended_session)
        client.app.state.leases.sweep()
    else:
        archived = client.post(
            f"/api/v1/repositories/{repo['id']}/archive",
            json={"revision": repo["revision"]},
            headers={"Idempotency-Key": "bind-lifecycle-archive"},
        )
        assert archived.status_code == 200, archived.text

    current = client.get(f"/api/v1/leases/{lease['id']}").json()
    assert current["state"] != "active"
    assert current["worktreePath"] == str(path)
    assert current["worktreeSource"] == "caller"
    if end_action == "dead-session":
        assert current["releaseReason"] == "session_ended"
    assert _snapshot(path) == before

    if end_action == "archive":
        active = client.get(
            "/api/v1/leases",
            params={"repositoryId": repo["id"], "state": "active"},
        ).json()["items"]
        assert not active
        assert client.get(f"/api/v1/leases/{queued['id']}").json()["state"] != "active"
    else:
        offered = client.get(f"/api/v1/leases/{queued['id']}").json()
        assert offered["state"] == "offered"
        if end_action == "release":
            pending = _bind(client, waiter["token"], offered, tmp_path / "next",
                            key="waiter-before-activation")
            assert pending.status_code == 409 and pending.json()["error"]["code"] == "lease_closed"
        promoted = _agent_post(
            client,
            waiter["token"],
            f"/leases/{queued['id']}/poll",
            {},
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["state"] == "active"
        if end_action == "release":
            active = promoted.json()
            next_path = _caller_worktree(base, tmp_path, active["branch"], "next")
            next_bind = _bind(client, waiter["token"], active, next_path, key="waiter-after-activation")
            assert next_bind.status_code == 200, next_bind.text
            assert (next_bind.json()["slotIndex"], next_bind.json()["worktreePath"]) == (bound["slotIndex"], str(next_path))


def test_caller_worktree_rescue_is_refused_without_releasing_or_changing_it(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-rescue")
    policy = client.patch(
        f"/api/v1/repositories/{repo['id']}/slot-policy", json={"maxActiveLeases": 1}
    )
    assert policy.status_code == 200, policy.text
    owner = _issue(client, "Rescue owner")
    waiter = _issue(client, "Rescue waiter")
    lease = _acquire(client, owner["token"], repo, "codex/rescue")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    bound = _bind(client, owner["token"], lease, path, key="bind-rescue-binding")
    assert bound.status_code == 200, bound.text
    _dirty(path)
    before = _snapshot(path)
    queued = _acquire(client, waiter["token"], repo, "codex/rescue-waiter")
    assert queued["state"] == "queued"

    refused = _agent_post(
        client,
        owner["token"],
        f"/leases/{lease['id']}/release",
        {"revision": bound.json()["revision"], "rescue": True},
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "caller_worktree"
    still_active = client.get(f"/api/v1/leases/{lease['id']}").json()
    assert still_active["state"] == "active"
    assert client.get(f"/api/v1/leases/{queued['id']}").json()["state"] == "queued"
    assert _snapshot(path) == before

    released = _agent_post(
        client,
        owner["token"],
        f"/leases/{lease['id']}/release",
        {"revision": bound.json()["revision"]},
    )
    assert released.status_code == 200, released.text
    assert _snapshot(path) == before
    assert client.get(f"/api/v1/leases/{queued['id']}").json()["state"] == "offered"
