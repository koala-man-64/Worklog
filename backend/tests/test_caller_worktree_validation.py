"""Read-only identity and registration checks for caller-owned linked worktrees."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from worklog import slot_worktrees as sw
from worklog.common import AppError
from worklog.repository_links import repository_identity


def _git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(cwd), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_git_env(),
        timeout=30,
        check=False,
        shell=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout.strip()


def _repo_pair(tmp_path: Path) -> tuple[Path, Path, str]:
    bare = tmp_path / "demo.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "--initial-branch=main")

    base = tmp_path / "demo"
    _git(tmp_path, "clone", str(bare), str(base))
    _git(base, "config", "user.name", "Test User")
    _git(base, "config", "user.email", "test@example.invalid")
    (base / "README.md").write_text("initial\n", encoding="utf-8")
    _git(base, "add", "README.md")
    _git(base, "commit", "-m", "initial")
    _git(base, "push", "-u", "origin", "main")
    origin = "https://example.invalid/team/demo.git"
    _git(base, "remote", "set-url", "origin", origin)
    return bare, base, repository_identity(origin)


def _add_worktree(base: Path, path: Path, branch: str = "codex/task") -> Path:
    _git(base, "worktree", "add", "-b", branch, str(path), "main")
    return path


def _git_admin(path: Path) -> Path:
    return Path(_git(path, "rev-parse", "--path-format=absolute", "--git-dir"))


def _state(base: Path, path: Path) -> tuple[bytes, bytes, bytes, str, str, str]:
    admin = _git_admin(path)
    return (
        (path / ".git").read_bytes(),
        (admin / "HEAD").read_bytes(),
        (admin / "gitdir").read_bytes(),
        _git(path, "status", "--porcelain"),
        _git(path, "rev-parse", "HEAD"),
        _git(base, "for-each-ref", "--format=%(refname):%(objectname)", "refs/heads"),
    )


def _rejects(path: str | Path, identity: str, branch: str = "codex/task", default_branch: str = "main") -> None:
    with pytest.raises(AppError) as exc:
        sw.validate_caller_worktree(str(path), identity, branch, default_branch)
    assert exc.value.status == 422


def test_validate_linked_worktree_and_claim_key_are_read_only(tmp_path):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    before = _state(base, linked)

    claim = sw.validate_caller_worktree(str(linked), identity, "codex/task", "main")

    assert isinstance(claim, sw.CallerWorktree)
    assert claim.path == linked.resolve()
    assert claim.registered_at_ns > 0
    assert len(claim.registration_id) == 64
    assert claim.clean is True
    link_stat = (linked / ".git").stat()
    birthtime_ns = getattr(link_stat, "st_birthtime_ns", None)
    has_birth_time = isinstance(birthtime_ns, int) and birthtime_ns > 0
    if not has_birth_time:
        birthtime = getattr(link_stat, "st_birthtime", None)
        has_birth_time = isinstance(birthtime, (int, float)) and birthtime > 0
        birthtime_ns = int(birthtime * 1_000_000_000) if has_birth_time else link_stat.st_mtime_ns
    if has_birth_time:
        assert claim.registered_at_ns == birthtime_ns
    else:
        reciprocal_mtime_ns = (_git_admin(linked) / "gitdir").stat().st_mtime_ns
        assert claim.registered_at_ns == min(link_stat.st_mtime_ns, reciprocal_mtime_ns)
    assert claim.require_current() is None
    assert sw.validate_caller_worktree(str(linked), identity, "codex/task", "main").registration_id == claim.registration_id
    assert _state(base, linked) == before

    assert sw.worktree_claim_key(linked) == sw.worktree_claim_key(linked / "." / ".." / linked.name)
    if os.name == "nt":
        assert sw.worktree_claim_key(str(linked).swapcase()) == sw.worktree_claim_key(linked)


def test_fallback_registration_age_cannot_be_advanced_by_touching_git_link(tmp_path, monkeypatch):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    reciprocal = _git_admin(linked) / "gitdir"
    reciprocal_mtime_ns = reciprocal.stat().st_mtime_ns
    original_snapshot = sw._snapshot_file

    def without_birth_time(path, **kwargs):
        snapshot = original_snapshot(path, **kwargs)
        return replace(snapshot, registered_at_ns=snapshot.link_metadata[4], has_birth_time=False)

    monkeypatch.setattr(sw, "_snapshot_file", without_birth_time)
    git_link = linked / ".git"
    link_stat = git_link.stat()
    os.utime(git_link, ns=(link_stat.st_atime_ns, reciprocal_mtime_ns + 1_000_000_000))
    first = sw.validate_caller_worktree(str(linked), identity, "codex/task", "main")
    os.utime(git_link, ns=(link_stat.st_atime_ns, reciprocal_mtime_ns + 60_000_000_000))
    second = sw.validate_caller_worktree(str(linked), identity, "codex/task", "main")

    assert first.registered_at_ns == second.registered_at_ns == reciprocal_mtime_ns
    assert first.registration_id == second.registration_id


def test_validate_rejects_root_that_resolves_to_unc_before_git_or_metadata(tmp_path, monkeypatch):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    original_resolve = Path.resolve

    def resolve_as_unc(path, *args, **kwargs):
        if os.path.normcase(str(path)) == os.path.normcase(str(linked)):
            return Path(r"\\server\share\caller")
        return original_resolve(path, *args, **kwargs)

    def unexpected(*_args, **_kwargs):
        pytest.fail("UNC-resolved paths must be rejected before metadata reads or Git")

    monkeypatch.setattr(Path, "resolve", resolve_as_unc)
    monkeypatch.setattr(sw, "_snapshot_file", unexpected)
    monkeypatch.setattr(sw, "_run_git", unexpected)
    with pytest.raises(AppError) as exc:
        sw.validate_caller_worktree(str(linked), identity, "codex/task", "main")
    assert exc.value.status == 422


def test_resolve_git_link_rejects_admin_path_that_resolves_to_unc(tmp_path, monkeypatch):
    candidate = tmp_path / "admin"
    candidate.mkdir()
    original_resolve = Path.resolve

    def resolve_as_unc(path, *args, **kwargs):
        if os.path.normcase(str(path)) == os.path.normcase(str(candidate)):
            return Path(r"\\server\share\admin")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_as_unc)
    with pytest.raises(AppError) as exc:
        sw._resolve_git_link(str(candidate), tmp_path, "worktree link")
    assert exc.value.status == 422


def test_validate_rejects_main_clone_subdirectory_and_nonlocal_paths(tmp_path):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    nested = linked / "nested"
    nested.mkdir()

    _rejects(base, identity)
    _rejects(nested, identity)
    _rejects("relative/caller", identity)
    _rejects(r"\\server\share\caller", identity)
    _rejects(tmp_path / "missing", identity)


def test_validate_rejects_remote_branch_and_default_branch_mismatches(tmp_path):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")

    _rejects(linked, repository_identity("https://example.invalid/other/repo.git"))
    _rejects(linked, identity, branch="codex/other")
    _rejects(linked, identity, default_branch="codex/task")

    protected = base.parent / "protected"
    _git(base, "worktree", "add", "-b", "develop", str(protected), "main")
    _rejects(protected, identity, branch="develop")

    detached = base.parent / "detached"
    _git(base, "worktree", "add", "--detach", str(detached), "main")
    _rejects(detached, identity)


def test_validate_rejects_readonly_base_marker_and_bad_git_registration(tmp_path):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    marker = linked / ".worklog-readonly-base"
    marker.write_text("read-only\n", encoding="utf-8")
    _rejects(linked, identity)
    marker.unlink()

    admin = _git_admin(linked)
    registration = admin / "gitdir"
    original = registration.read_bytes()
    try:
        registration.write_text(str(tmp_path / "other" / ".git"), encoding="utf-8")
        _rejects(linked, identity)
    finally:
        registration.write_bytes(original)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are not supported on this platform")
def test_validate_rejects_fifo_git_link_without_reading_it(tmp_path):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    git_link = linked / ".git"
    git_link.unlink()
    os.mkfifo(git_link)

    _rejects(linked, identity)
    assert git_link.is_fifo()


def test_validate_rejects_oversized_git_link_metadata(tmp_path):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    git_link = linked / ".git"
    git_link.unlink()
    git_link.write_bytes(b"x" * (70 * 1024))

    _rejects(linked, identity)


def test_require_current_detects_metadata_change_without_running_git(tmp_path, monkeypatch):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    claim = sw.validate_caller_worktree(str(linked), identity, "codex/task", "main")

    def unexpected_git(*_args, **_kwargs):
        pytest.fail("require_current must not spawn Git")

    monkeypatch.setattr(sw, "_run_git", unexpected_git)
    assert claim.require_current() is None

    marker = linked / ".worklog-readonly-base"
    marker.write_text("locked\n", encoding="utf-8")
    with pytest.raises(AppError) as marker_exc:
        claim.require_current()
    assert marker_exc.value.status == 409
    marker.unlink()

    admin_head = _git_admin(linked) / "HEAD"
    admin_head.write_text("ref: refs/heads/codex/else\n", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        claim.require_current()
    assert exc.value.status == 409 and exc.value.code == "conflict"


def test_validate_reports_dirty_staged_unstaged_and_untracked_state(tmp_path):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    (linked / "README.md").write_text("staged\n", encoding="utf-8")
    _git(linked, "add", "README.md")
    (linked / "README.md").write_text("unstaged\n", encoding="utf-8")
    (linked / "notes.txt").write_text("untracked\n", encoding="utf-8")

    claim = sw.validate_caller_worktree(str(linked), identity, "codex/task", "main")

    assert claim.clean is False
    assert (linked / "README.md").read_text(encoding="utf-8") == "unstaged\n"
    assert (linked / "notes.txt").read_text(encoding="utf-8") == "untracked\n"


def test_validate_fails_closed_if_git_status_cannot_be_read(tmp_path, monkeypatch):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    original = sw._run_git

    def fail_status(cwd, *args, **kwargs):
        if "status" in args:
            return subprocess.CompletedProcess(["git", *args], 128, "", "private path")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(sw, "_run_git", fail_status)
    with pytest.raises(AppError) as exc:
        sw.validate_caller_worktree(str(linked), identity, "codex/task", "main")
    assert exc.value.status == 422
    assert "private path" not in str(exc.value)


def test_validate_does_not_include_remote_credentials_in_errors(tmp_path):
    _, base, identity = _repo_pair(tmp_path)
    linked = _add_worktree(base, tmp_path / "caller")
    secret = "fixture-secret-value"
    _git(base, "remote", "set-url", "origin", f"https://fixture-user:{secret}@example.invalid/team/demo.git")

    with pytest.raises(AppError) as exc:
        sw.validate_caller_worktree(
            str(linked),
            repository_identity("https://example.invalid/other/repo.git"),
            "codex/task",
            "main",
        )
    assert secret not in str(exc.value)


def test_slot_pool_path_returns_repo_pool_and_preserves_slot_index_guard(tmp_path):
    _, base, _ = _repo_pair(tmp_path)
    pool = sw.slot_pool_path(base, "", "demo")

    assert pool == (base.parent / ".worklog-slots" / "demo").resolve()
    assert sw.resolve_slot_path(base, "", "demo", 2) == (pool / "slot-2").resolve()
    with pytest.raises(AppError):
        sw.resolve_slot_path(base, "", "demo", 0)
    with pytest.raises(AppError):
        sw.slot_pool_path(base, str(base / "slots"), "demo")
