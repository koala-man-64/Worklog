"""Acceptance tests for caller-worktree cleanliness and freshness at bind time."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
import worklog.leases as lease_service
from test_bind_worktree import (
    _acquire,
    _bind,
    _caller_worktree,
    _create_repo,
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
