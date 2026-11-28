"""Acceptance tests for caller-worktree cleanliness and freshness at bind time."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import worklog.leases as lease_service
from test_bind_worktree import (
    _acquire,
    _bind,
    _caller_worktree,
    _create_repo,
    _dirty,
    _human_handoff,
    _issue,
)
from test_leases import _agent_post
from test_leases import env as _lease_env
from test_slot_worktrees import _git, make_repo_pair

env = _lease_env


@pytest.mark.parametrize("stable_identity", [True, False], ids=["stable-file-ids", "missing-file-ids"])
def test_missing_birth_requires_handoff_and_holder_reuse_requires_file_identity(env, monkeypatch, stable_identity):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-missing-native-evidence")
    token = _issue(client, "Worktree holder")["token"]
    lease = _acquire(client, token, repo, "codex/missing-native-evidence")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    original_snapshot = lease_service.slot_worktrees._snapshot_file

    def filesystem_without_birth(file, **kwargs):
        snapshot = original_snapshot(file, **kwargs)
        if file.name in {".git", "gitdir"}:
            return replace(snapshot, registered_at_ns=snapshot.link_metadata[4], has_birth_time=False,
                           has_file_identity=stable_identity)
        return snapshot

    monkeypatch.setattr(lease_service.slot_worktrees, "_snapshot_file", filesystem_without_birth)
    before = _safe_snapshot(path)
    key = "bind-without-birth-evidence"
    refused = _bind(client, token, lease, path, key=key)
    assert refused.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(client, refused, lease, key, path, before)

    handoff = _human_handoff(client, repo, lease, path, "handoff-without-birth-evidence")
    bound = _bind(client, token, lease, path, key="bind-with-handoff", handoff_message_id=handoff["id"])
    assert bound.status_code == 200, bound.text
    released = _agent_post(client, token, f"/leases/{lease['id']}/release", {"revision": bound.json()["revision"]})
    assert released.status_code == 200, released.text

    returning = _acquire(client, token, repo, lease["branch"])
    key = "bind-with-prior-handoff"
    rebound = _bind(client, token, returning, path, key=key)
    if stable_identity:
        assert rebound.status_code == 200, rebound.text
        assert rebound.json()["worktreePath"] == str(path)
    else:
        assert rebound.json()["error"]["code"] == "worktree_handoff_required"
        _assert_refusal_preserved_state(client, rebound, returning, key, path, before)


def _safe_snapshot(path: Path) -> dict[str, object]:
    """Capture caller data without invoking Git status or checkout operations."""
    root = path.resolve()
    files = {
        file.relative_to(root).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in root.rglob("*")
        if file.is_file() and file.name != ".git"
    }
    registration = _git(root, "worktree", "list", "--porcelain")
    registered_paths = [
        Path(line.removeprefix("worktree ")).resolve()
        for line in registration.splitlines()
        if line.startswith("worktree ")
    ]
    assert root in registered_paths, f"{root} is missing from Git worktree registration"
    return {
        "path": _git(root, "rev-parse", "--show-toplevel"),
        "branch": _git(root, "symbolic-ref", "--short", "HEAD"),
        "head": _git(root, "rev-parse", "HEAD"),
        "index": _git(root, "write-tree"),
        "indexFlags": _git(root, "ls-files", "-v"),
        "files": files,
        "registration": registration,
    }


def _inject_after_inspection(monkeypatch, mutation) -> list[Path]:
    """Change only ordinary caller data after the public validator returns."""
    original = lease_service.slot_worktrees.validate_caller_worktree
    inspected_paths: list[Path] = []

    def inspect_then_mutate(path: str, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if not inspected_paths:
            worktree_path = Path(path).resolve()
            inspected_paths.append(worktree_path)
            mutation(worktree_path)
        return result

    monkeypatch.setattr(
        lease_service.slot_worktrees,
        "validate_caller_worktree",
        inspect_then_mutate,
    )
    return inspected_paths


def _assert_refusal_preserved_state(client, response, lease, key, path, preserved) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] in {"conflict", "worktree_handoff_required"}

    current = client.get(f"/api/v1/leases/{lease['id']}").json()
    assert current["state"] == "active"
    assert current["revision"] == lease["revision"]
    assert current["worktreeSource"] == "unbound"

    with client.app.state.db.read() as connection:
        assert connection.execute("SELECT 1 FROM receipts WHERE key=?", (key,)).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM events WHERE kind='lease.bind_worktree' AND entity_id=?",
            (lease["id"],),
        ).fetchone() is None

    assert _safe_snapshot(path) == preserved


def test_bind_rejects_index_change_after_inspection_without_claim_or_receipt(env, monkeypatch):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-attestation-index-race")
    owner = _issue(client, "Index freshness owner")
    lease = _acquire(client, owner["token"], repo, "codex/index-freshness")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    before = _safe_snapshot(path)
    key = "bind-index-freshness-race"
    changed: dict[str, object] = {}

    def change_index(worktree_path: Path) -> None:
        # Replace one index entry with an already-present blob. The worktree files
        # stay untouched, isolating the index change from a working-tree edit.
        other_blob = _git(worktree_path, "rev-parse", "HEAD:README.md")
        _git(worktree_path, "update-index", "--cacheinfo", f"100644,{other_blob},app.py")
        changed["snapshot"] = _safe_snapshot(worktree_path)

    inspected_paths = _inject_after_inspection(monkeypatch, change_index)
    response = _bind(client, owner["token"], lease, path, key=key)

    assert inspected_paths == [path.resolve()]
    assert changed["snapshot"]["index"] != before["index"]
    _assert_refusal_preserved_state(client, response, lease, key, path, changed["snapshot"])


@pytest.mark.parametrize("edit_kind", ["tracked", "untracked"])
def test_bind_rejects_nested_file_edits_after_inspection(env, monkeypatch, edit_kind):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-attestation-nested-race")
    nested_tracked = base / "nested" / "tracked.txt"
    nested_tracked.parent.mkdir(parents=True)
    nested_tracked.write_text("original nested content\n", encoding="utf-8")
    _git(base, "add", "nested/tracked.txt")
    _git(base, "commit", "-m", "Add nested tracked file")

    owner = _issue(client, "Nested freshness owner")
    lease = _acquire(client, owner["token"], repo, "codex/nested-freshness")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    before = _safe_snapshot(path)
    key = f"bind-nested-{edit_kind}-freshness-race"
    changed: dict[str, object] = {}

    def edit_nested_files(worktree_path: Path) -> None:
        if edit_kind == "tracked":
            (worktree_path / "nested" / "tracked.txt").write_text(
                "edited after inspection\n", encoding="utf-8"
            )
        else:
            untracked = worktree_path / "nested" / "deeper" / "new.txt"
            untracked.parent.mkdir(parents=True)
            untracked.write_text("caller data remains here\n", encoding="utf-8")
        changed["snapshot"] = _safe_snapshot(worktree_path)

    inspected_paths = _inject_after_inspection(monkeypatch, edit_nested_files)
    response = _bind(client, owner["token"], lease, path, key=key)

    assert inspected_paths == [path.resolve()]
    files = changed["snapshot"]["files"]
    if edit_kind == "tracked":
        assert files["nested/tracked.txt"] != before["files"]["nested/tracked.txt"]
        assert "nested/deeper/new.txt" not in files
    else:
        assert files["nested/tracked.txt"] == before["files"]["nested/tracked.txt"]
        assert "nested/deeper/new.txt" in files
    _assert_refusal_preserved_state(client, response, lease, key, path, changed["snapshot"])


@pytest.mark.parametrize("visibility_flag", ["--skip-worktree", "--assume-unchanged"])
def test_index_visibility_flags_require_authentic_handoff(env, visibility_flag):
    client, tmp_path = env
    suffix = visibility_flag.removeprefix("--").replace("-", "_")
    repo, base, _ = _create_repo(client, tmp_path, f"bind-attestation-{suffix}")
    owner = _issue(client, f"Visibility flag owner {suffix}")
    lease = _acquire(client, owner["token"], repo, f"codex/visibility-{suffix}")
    path = _caller_worktree(base, tmp_path, lease["branch"])

    # Mark a clean tracked entry as potentially hidden from ordinary status checks;
    # leave its content unchanged so this does not construct a dirty-state bypass.
    _git(path, "update-index", visibility_flag, "app.py")
    before = _safe_snapshot(path)
    refused_key = f"bind-visibility-refused-{suffix}"
    refused = _bind(client, owner["token"], lease, path, key=refused_key)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(client, refused, lease, refused_key, path, before)

    handoff = _human_handoff(client, repo, lease, path, f"bind-visibility-handoff-{suffix}")
    bound = _bind(
        client,
        owner["token"],
        lease,
        path,
        key=f"bind-visibility-accepted-{suffix}",
        handoff_message_id=handoff["id"],
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["worktreeSource"] == "caller"
    assert _safe_snapshot(path) == before


def test_file_ignored_only_by_external_rules_requires_handoff(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-external-ignore")
    external_rules = tmp_path / "user-gitignore"
    external_rules.write_text("local-notes.txt\n", encoding="utf-8")
    _git(base, "config", "core.excludesFile", external_rules.as_posix())
    token = _issue(client, "External ignore owner")["token"]
    lease = _acquire(client, token, repo, "codex/external-ignore")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    (path / "local-notes.txt").write_text("unfinished work\n", encoding="utf-8")
    assert _git(path, "check-ignore", "--", "local-notes.txt") == "local-notes.txt"
    before = _safe_snapshot(path)

    key = "bind-external-ignore-refused"
    refused = _bind(client, token, lease, path, key=key)
    assert refused.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(client, refused, lease, key, path, before)


def test_unverifiable_filter_attribute_requires_handoff_without_caller_configured_status_or_checkout(env, monkeypatch):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-attestation-filter")
    (base / ".gitattributes").write_text("*.opaque filter=opaque\n", encoding="utf-8")
    filtered_file = base / "nested" / "payload.opaque"
    filtered_file.parent.mkdir(parents=True)
    filtered_file.write_text("opaque but ordinary tracked data\n", encoding="utf-8")
    _git(base, "add", ".gitattributes", "nested/payload.opaque")
    _git(base, "commit", "-m", "Add unverified filter attribute")

    owner = _issue(client, "Filter fallback owner")
    lease = _acquire(client, owner["token"], repo, "codex/filter-fallback")
    path = _caller_worktree(base, tmp_path, lease["branch"])
    assert _git(path, "check-attr", "filter", "--", "nested/payload.opaque").endswith(
        "filter: opaque"
    )
    before = _safe_snapshot(path)

    original_run_git = lease_service.slot_worktrees._run_git

    def forbid_caller_configured_status_or_checkout(cwd, *args, **kwargs):
        command_words = {str(arg) for arg in args}
        if "status" in command_words:
            isolated_env = kwargs.get("extra_env") or {}
            assert isolated_env.get("GIT_DIR"), "status must use the private index/config"
            assert isolated_env.get("GIT_WORK_TREE") == str(Path(cwd).resolve())
            assert isolated_env.get("GIT_CONFIG_NOSYSTEM") == "1"
            assert isolated_env.get("GIT_CONFIG_GLOBAL")
            assert isolated_env.get("GIT_ATTR_NOSYSTEM") == "1"
        assert "checkout" not in command_words, "binding must not run checkout-configured programs"
        return original_run_git(cwd, *args, **kwargs)

    monkeypatch.setattr(
        lease_service.slot_worktrees,
        "_run_git",
        forbid_caller_configured_status_or_checkout,
    )
    refused_key = "bind-filter-unverified-refused"
    refused = _bind(client, owner["token"], lease, path, key=refused_key)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(client, refused, lease, refused_key, path, before)

    handoff = _human_handoff(client, repo, lease, path, "bind-filter-unverified-handoff")
    bound = _bind(
        client,
        owner["token"],
        lease,
        path,
        key="bind-filter-unverified-accepted",
        handoff_message_id=handoff["id"],
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["worktreeSource"] == "caller"
    assert _safe_snapshot(path) == before


def test_submodule_entry_falls_back_to_authentic_handoff(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-attestation-submodule")
    submodule_root = tmp_path / "submodule-source"
    submodule_root.mkdir()
    submodule_bare, _ = make_repo_pair(submodule_root, "nested-repository")
    _git(
        base,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(submodule_bare),
        "nested/module",
    )
    _git(base, "commit", "-m", "Add a nested submodule")

    owner = _issue(client, "Submodule fallback owner")
    lease = _acquire(client, owner["token"], repo, "codex/submodule-fallback")
    path = tmp_path / "caller-worktrees" / "submodule"
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(base, "-c", "submodule.recurse=false", "worktree", "add", str(path), "-b", lease["branch"], "HEAD")
    path = path.resolve()
    before = _safe_snapshot(path)

    refused_key = "bind-submodule-unverified-refused"
    refused = _bind(client, owner["token"], lease, path, key=refused_key)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(client, refused, lease, refused_key, path, before)

    handoff = _human_handoff(client, repo, lease, path, "bind-submodule-unverified-handoff")
    bound = _bind(
        client,
        owner["token"],
        lease,
        path,
        key="bind-submodule-unverified-accepted",
        handoff_message_id=handoff["id"],
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["worktreeSource"] == "caller"
    assert _safe_snapshot(path) == before


def _register_agent(
    client,
    sponsor_token: str,
    label: str,
    *,
    directory: Path | str | None = None,
    chat_id: str | None = None,
) -> dict:
    token = secrets.token_urlsafe(32)
    payload: dict[str, object] = {
        "label": label,
        "token": token,
        "chatId": chat_id or f"chat-{secrets.token_hex(8)}",
    }
    if directory is not None:
        payload["directory"] = str(directory)
    response = _agent_post(client, sponsor_token, "/agent-registrations", payload)
    assert response.status_code == 200, response.text
    return {**response.json(), "token": token}


def _backdate_lease_after_worktree(client, lease_id: str, seconds_ahead: int = 120) -> None:
    """Guarantee acquired_at is strictly after Git created the worktree (simulating app-made startup trees)."""
    future = (datetime.now(UTC) + timedelta(seconds=seconds_ahead)).isoformat()
    with client.app.state.db.transaction() as connection:
        connection.execute("UPDATE repository_leases SET acquired_at=? WHERE id=?", (future, lease_id))


def test_preexisting_clean_worktree_matching_registered_directory_binds_without_handoff(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-t31-app-made")
    # Simulate Claude desktop creating .claude/worktrees/<name> inside the repo before registration or acquire.
    branch = "claude/t31-session"
    app_worktree = base / ".claude" / "worktrees" / "elastic-kepler-85bd6d"
    app_worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(base, "worktree", "add", str(app_worktree), "-b", branch, "HEAD")
    app_worktree = app_worktree.resolve()
    before = _safe_snapshot(app_worktree)

    sponsor = _issue(client, "T-31 sponsor")
    agent = _register_agent(client, sponsor["token"], "Claude session", directory=app_worktree)
    lease = _acquire(client, agent["token"], repo, branch)
    _backdate_lease_after_worktree(client, lease["id"])

    bound = _bind(client, agent["token"], lease, app_worktree, key="bind-t31-registered-dir")
    assert bound.status_code == 200, bound.text
    assert bound.json()["worktreePath"] == str(app_worktree)
    assert bound.json()["worktreeSource"] == "caller"
    assert _safe_snapshot(app_worktree) == before

    with client.app.state.db.read() as connection:
        row = connection.execute(
            "SELECT after_json FROM events WHERE kind='lease.bind_worktree' AND entity_id=?",
            (lease["id"],),
        ).fetchone()
    assert row is not None
    assert json.loads(row["after_json"])["provenance"] == "registered_directory"

    # Same owner can later rebind with local edits after release; another agent registering in the
    # same directory still requires a handoff because previous_owner is set.
    _dirty(app_worktree)
    dirty_snapshot = _safe_snapshot(app_worktree)
    released = _agent_post(
        client, agent["token"], f"/leases/{lease['id']}/release", {"revision": bound.json()["revision"]}
    )
    assert released.status_code == 200, released.text

    second_lease = _acquire(client, agent["token"], repo, branch)
    rebound = _bind(client, agent["token"], second_lease, app_worktree, key="bind-t31-owner-rebind")
    assert rebound.status_code == 200, rebound.text
    assert _safe_snapshot(app_worktree) == dirty_snapshot
    assert _agent_post(
        client, agent["token"], f"/leases/{second_lease['id']}/release", {"revision": rebound.json()["revision"]}
    ).status_code == 200

    # Clean the tree so only previous_owner guards against another identity adopting it.
    _git(app_worktree, "reset", "--hard", "HEAD")
    (app_worktree / "caller-note.txt").unlink()
    clean_again = _safe_snapshot(app_worktree)
    other = _register_agent(client, sponsor["token"], "Second session", directory=app_worktree)
    other_lease = _acquire(client, other["token"], repo, branch)
    _backdate_lease_after_worktree(client, other_lease["id"])
    refused = _bind(client, other["token"], other_lease, app_worktree, key="bind-t31-prior-owner-blocks")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(client, refused, other_lease, "bind-t31-prior-owner-blocks", app_worktree, clean_again)


def test_preexisting_registered_directory_requires_clean_tree_and_exact_path(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-t31-guards")
    branch = "claude/t31-guards"
    nested = base / ".claude" / "worktrees" / "nested-guard"
    nested.parent.mkdir(parents=True, exist_ok=True)
    _git(base, "worktree", "add", str(nested), "-b", branch, "HEAD")
    nested = nested.resolve()
    clean_before = _safe_snapshot(nested)
    sponsor = _issue(client, "T-31 guard sponsor")

    # Registering in the parent base checkout must NOT authorize binding a nested linked worktree.
    parent_agent = _register_agent(client, sponsor["token"], "Parent checkout agent", directory=base)
    parent_lease = _acquire(client, parent_agent["token"], repo, branch)
    _backdate_lease_after_worktree(client, parent_lease["id"])
    parent_refused = _bind(client, parent_agent["token"], parent_lease, nested, key="bind-t31-parent-refused")
    assert parent_refused.status_code == 409, parent_refused.text
    assert parent_refused.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(
        client, parent_refused, parent_lease, "bind-t31-parent-refused", nested, clean_before
    )
    assert _agent_post(
        client, parent_agent["token"], f"/leases/{parent_lease['id']}/release", {"revision": parent_lease["revision"]}
    ).status_code == 200

    # A dirty pre-existing worktree must still require a handoff even when registered directory matches.
    _dirty(nested)
    dirty_before = _safe_snapshot(nested)
    dirty_agent = _register_agent(client, sponsor["token"], "Dirty app-made agent", directory=nested)
    dirty_lease = _acquire(client, dirty_agent["token"], repo, branch)
    _backdate_lease_after_worktree(client, dirty_lease["id"])
    dirty_refused = _bind(client, dirty_agent["token"], dirty_lease, nested, key="bind-t31-dirty-refused")
    assert dirty_refused.status_code == 409, dirty_refused.text
    assert dirty_refused.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(
        client, dirty_refused, dirty_lease, "bind-t31-dirty-refused", nested, dirty_before
    )


def test_preexisting_registered_directory_blocks_competing_peer_registration_and_participation(env):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-t31-peers")
    branch = "claude/t31-peers"
    path = _caller_worktree(base, tmp_path, branch, "peer-contended")
    subdir = path / "nested"
    subdir.mkdir()
    sponsor = _issue(client, "T-31 peer sponsor")

    claimant = _register_agent(client, sponsor["token"], "Claimant", directory=path)
    peer = _register_agent(client, sponsor["token"], "Peer in subdir", directory=subdir)
    subdir.rmdir()
    before = _safe_snapshot(path)

    lease = _acquire(client, claimant["token"], repo, branch)
    _backdate_lease_after_worktree(client, lease["id"])
    refused = _bind(client, claimant["token"], lease, path, key="bind-t31-peer-registered")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(client, refused, lease, "bind-t31-peer-registered", path, before)

    # Once the competing peer retires, no competing unrevoked registration remains.
    assert _agent_post(client, peer["token"], "/agent-registrations/retire", {}).status_code == 200

    # An open participation from another identity naming the worktree (even via checkoutPath) still blocks.
    watcher = _register_agent(client, sponsor["token"], "Peer participant", directory=base)
    run = _agent_post(
        client,
        watcher["token"],
        "/agent-runs",
        {"clientRunKey": "t31-watch-run", "displayName": "Watching run"},
    )
    assert run.status_code == 200, run.text
    part = _agent_post(
        client,
        watcher["token"],
        "/participations",
        {
            "runId": run.json()["id"],
            "description": "Inspecting worktree",
            "contexts": [{"repositoryId": repo["id"], "checkoutPath": str(path), "branch": branch}],
        },
    )
    assert part.status_code == 200, part.text
    disputed = _bind(client, claimant["token"], lease, path, key="bind-t31-peer-participation")
    assert disputed.status_code == 409, disputed.text
    assert disputed.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(client, disputed, lease, "bind-t31-peer-participation", path, before)

    # After the peer releases its participation, claimant can bind cleanly.
    rel_part = _agent_post(
        client,
        watcher["token"],
        f"/participations/{part.json()['id']}/release",
        {"revision": part.json()["revision"]},
    )
    assert rel_part.status_code == 200, rel_part.text
    bound = _bind(client, claimant["token"], lease, path, key="bind-t31-after-peer-clear")
    assert bound.status_code == 200, bound.text
    assert bound.json()["worktreeSource"] == "caller"
    assert _safe_snapshot(path) == before


def test_preexisting_registered_directory_supports_continue_and_guards_unknown_prior_registration(env, monkeypatch):
    client, tmp_path = env
    repo, base, _ = _create_repo(client, tmp_path, "bind-t31-continue")
    branch = "claude/t31-continue"
    path = _caller_worktree(base, tmp_path, branch, "continued-tree")
    before = _safe_snapshot(path)
    sponsor = _issue(client, "T-31 continue sponsor")

    # Register in base, acquire lease, then continue identity inside the pre-existing worktree.
    agent = _register_agent(client, sponsor["token"], "Continuing session", directory=base)
    lease = _acquire(client, agent["token"], repo, branch)
    continued_token = secrets.token_urlsafe(32)
    continued = _agent_post(
        client,
        agent["token"],
        "/agent-registrations/continue",
        {
            "token": continued_token,
            "chatId": f"chat-continued-{secrets.token_hex(6)}",
            "directory": str(path),
        },
    )
    assert continued.status_code == 200, continued.text
    transferred = client.get(f"/api/v1/leases/{lease['id']}").json()
    _backdate_lease_after_worktree(client, transferred["id"])

    # Even without filesystem birth time, matching registered directory binds when file identity is present.
    original_snapshot = lease_service.slot_worktrees._snapshot_file

    def without_birth(file, **kwargs):
        snapshot = original_snapshot(file, **kwargs)
        if file.name in {".git", "gitdir"}:
            return replace(snapshot, registered_at_ns=snapshot.link_metadata[4], has_birth_time=False,
                           has_file_identity=True)
        return snapshot

    monkeypatch.setattr(lease_service.slot_worktrees, "_snapshot_file", without_birth)
    bound = _bind(client, continued_token, transferred, path, key="bind-t31-continued-no-birth")
    assert bound.status_code == 200, bound.text
    assert bound.json()["worktreePath"] == str(path)
    assert _safe_snapshot(path) == before

    # If a prior holder bound without file identity (registrationId=""), a different agent must NOT
    # bypass handoff via registered_directory because the prior registration cannot be distinguished.
    assert _agent_post(
        client, continued_token, f"/leases/{transferred['id']}/release", {"revision": bound.json()["revision"]}
    ).status_code == 200
    with client.app.state.db.transaction() as connection:
        row = connection.execute(
            "SELECT id, after_json FROM events WHERE kind='lease.bind_worktree' AND entity_id=?",
            (transferred["id"],),
        ).fetchone()
        payload = json.loads(row["after_json"])
        payload["registrationId"] = ""
        connection.execute("UPDATE events SET after_json=? WHERE id=?", (json.dumps(payload), row["id"]))

    stranger = _register_agent(client, sponsor["token"], "Unrelated successor", directory=path)
    stranger_lease = _acquire(client, stranger["token"], repo, branch)
    _backdate_lease_after_worktree(client, stranger_lease["id"])
    refused = _bind(client, stranger["token"], stranger_lease, path, key="bind-t31-unknown-prior-id")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "worktree_handoff_required"
    _assert_refusal_preserved_state(client, refused, stranger_lease, "bind-t31-unknown-prior-id", path, before)
