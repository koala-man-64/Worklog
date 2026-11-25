"""Reusable slot worktrees, auto-rescue commits, and tracked-file read-only base locks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from worklog import slot_worktrees as sw
from worklog.common import AppError


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"git {' '.join(args)} failed: {res.stderr}\n{res.stdout}"
    return res.stdout.strip()


def make_repo_pair(tmp_path: Path, name: str = "demo-repo") -> tuple[Path, Path]:
    """Create a bare origin remote and a clean cloned base checkout on main."""
    bare = tmp_path / f"{name}.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "--initial-branch=main")

    base = tmp_path / name
    _git(tmp_path, "clone", str(bare), str(base))
    _git(base, "config", "user.name", "Test User")
    _git(base, "config", "user.email", "test@example.local")
    (base / ".gitignore").write_text(".venv/\nnode_modules/\n", encoding="utf-8")
    (base / "README.md").write_text("# Demo\n", encoding="utf-8")
    (base / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(base, "add", "-A")
    _git(base, "commit", "-m", "initial commit")
    _git(base, "push", "-u", "origin", "main")
    return bare, base


def test_resolve_slot_path_centralized_and_rejects_inside_base(tmp_path):
    _, base = make_repo_pair(tmp_path, "asset-allocation-jobs")
    slot1 = sw.resolve_slot_path(base, "", "asset-allocation-jobs", 1)
    assert slot1 == (tmp_path / ".worklog-slots" / "asset-allocation-jobs" / "slot-1").resolve()

    with pytest.raises(AppError) as exc:
        sw.resolve_slot_path(base, str(base / "slots"), "asset-allocation-jobs", 1)
    assert exc.value.code == "validation"


def test_provision_preserves_venv_and_node_modules_and_blocks_protected_branch(tmp_path):
    _, base = make_repo_pair(tmp_path, "jobs")
    # Protected branches are refused.
    with pytest.raises(AppError) as exc:
        sw.provision_slot(str(base), "", "jobs", 1, "main", "main", "11111111-1111-4111-8111-111111111111")
    assert exc.value.code == "validation"

    p1 = sw.provision_slot(str(base), "", "jobs", 1, "main", "claude/task-a", "11111111-1111-4111-8111-111111111111")
    slot1 = Path(p1.worktree_path)
    assert slot1.is_dir()
    assert _git(slot1, "rev-parse", "--abbrev-ref", "HEAD") == "claude/task-a"

    # Populate gitignored caches inside slot-1 plus an untracked junk file.
    (slot1 / ".venv").mkdir()
    (slot1 / ".venv" / "marker.txt").write_text("keep-venv", encoding="utf-8")
    (slot1 / "node_modules").mkdir()
    (slot1 / "node_modules" / "pkg.json").write_text("{}", encoding="utf-8")

    # Clean recycle detaches slot-1 while keeping .venv and node_modules.
    rec = sw.recycle_or_rescue_slot(str(slot1), "main", "11111111-1111-4111-8111-111111111111", branch="claude/task-a")
    assert rec.clean is True and rec.rescued is False
    assert _git(slot1, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"

    # Re-provisioning slot-1 for a second task preserves .venv and node_modules.
    p2 = sw.provision_slot(str(base), "", "jobs", 1, "main", "codex/task-b", "22222222-2222-4222-8222-222222222222")
    assert Path(p2.worktree_path) == slot1
    assert _git(slot1, "rev-parse", "--abbrev-ref", "HEAD") == "codex/task-b"
    assert (slot1 / ".venv" / "marker.txt").read_text(encoding="utf-8") == "keep-venv"
    assert (slot1 / "node_modules" / "pkg.json").read_text(encoding="utf-8") == "{}"


def test_dirty_slot_guard_and_auto_rescue_commit_recovery(tmp_path):
    _, base = make_repo_pair(tmp_path, "jobs")
    lease_id = "33333333-3333-4333-8333-333333333333"
    prov = sw.provision_slot(str(base), "", "jobs", 1, "main", "claude/dirty-work", lease_id)
    slot1 = Path(prov.worktree_path)
    (slot1 / ".venv").mkdir()
    (slot1 / ".venv" / "keep.txt").write_text("venv-intact", encoding="utf-8")
    # Unstaged edit + untracked file.
    (slot1 / "app.py").write_text("VALUE = 999\n", encoding="utf-8")
    (slot1 / "notes.txt").write_text("important draft\n", encoding="utf-8")

    # Without rescue allowed, recycle refuses and preserves working tree untouched.
    blocked = sw.recycle_or_rescue_slot(str(slot1), "main", lease_id, branch="claude/dirty-work", allow_rescue=False)
    assert blocked.clean is False and blocked.rescued is False
    assert any("app.py" in line for line in blocked.dirty_files)
    assert (slot1 / "notes.txt").read_text(encoding="utf-8") == "important draft\n"

    # Provisioning over a dirty slot also refuses with 409 slot_dirty.
    with pytest.raises(AppError) as exc:
        sw.provision_slot(str(base), "", "jobs", 1, "main", "codex/next", "44444444-4444-4444-8444-444444444444")
    assert exc.value.code == "slot_dirty"

    # With allow_rescue=True, uncommitted edits are saved to refs/worklog-rescue/<lease-id> and slot is cleanly detached.
    rescued = sw.recycle_or_rescue_slot(str(slot1), "main", lease_id, branch="claude/dirty-work", allow_rescue=True)
    assert rescued.clean is True and rescued.rescued is True
    assert rescued.rescue_ref == f"refs/worklog-rescue/{lease_id}"
    assert sw.inspect_slot_cleanliness(slot1).clean is True
    assert (slot1 / ".venv" / "keep.txt").read_text(encoding="utf-8") == "venv-intact"
    assert not (slot1 / "notes.txt").exists()
    assert (slot1 / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    # Verify full byte recovery from the rescue ref in base!
    assert _git(base, "show", f"{rescued.rescue_ref}:app.py") == "VALUE = 999"
    assert _git(base, "show", f"{rescued.rescue_ref}:notes.txt") == "important draft"


def test_lock_base_readonly_preflight_and_slot_operations(tmp_path):
    _, base = make_repo_pair(tmp_path, "jobs")

    # Preflight refuses dirty base.
    (base / "app.py").write_text("DIRTY = True\n", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        sw.lock_base_readonly(str(base), "main", "jobs")
    assert exc.value.code == "base_dirty"
    _git(base, "checkout", "--", "app.py")

    # Preflight refuses unpushed commits.
    (base / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(base, "add", "app.py")
    _git(base, "commit", "-m", "unpushed local commit")
    with pytest.raises(AppError) as exc:
        sw.lock_base_readonly(str(base), "main", "jobs")
    assert exc.value.code == "base_unpushed_commits"
    _git(base, "reset", "--hard", "origin/main")

    # Clean base locks cleanly, writes notice, and makes tracked files read-only.
    res = sw.lock_base_readonly(str(base), "main", "jobs")
    assert res["locked"] is True and res["filesLocked"] >= 3
    assert sw.is_base_locked_on_disk(str(base)) is True

    # Accidental edit in base fails with PermissionError.
    with pytest.raises(PermissionError):
        (base / "app.py").write_text("ACCIDENTAL\n", encoding="utf-8")

    # Meanwhile, git fetch and linked slot-1 edits/commits succeed without touching base permissions!
    _git(base, "fetch", "origin", "--prune")
    prov = sw.provision_slot(str(base), "", "jobs", 1, "main", "claude/safe-slot", "55555555-5555-4555-8555-555555555555")
    slot1 = Path(prov.worktree_path)
    (slot1 / "app.py").write_text("VALUE = 42\n", encoding="utf-8")
    _git(slot1, "add", "app.py")
    _git(slot1, "commit", "-m", "slot commit works while base locked")

    # Unlock restores write permission and removes notice.
    unlocked = sw.unlock_base_readonly(str(base))
    assert unlocked["locked"] is False and unlocked["filesUnlocked"] >= 3
    assert sw.is_base_locked_on_disk(str(base)) is False
    (base / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
