"""Worklog-managed reusable slot worktrees and tracked-file read-only base locks.

Every Git subprocess follows Worklog's safety conventions (shell=False, scrubbed GIT_* env,
GIT_TERMINAL_PROMPT=0, --no-pager, strict local drive paths on Windows, explicit timeouts).
Never invoke these functions while holding a SQLite write transaction.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .common import AppError
from .repository_links import repository_identity

logger = logging.getLogger("worklog")

_LOCAL_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "staging", "production"})
_BRANCH_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_READONLY_NOTICE = ".worklog-readonly-base"
_SAFE_GIT = ("--no-pager", "-c", "core.fsmonitor=false", "-c", "log.showSignature=false")
_DEFAULT_TIMEOUT_S = 30.0
_FETCH_TIMEOUT_S = 90.0


def _git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra:
        env.update(extra)
    return env


def _is_local_absolute(path: str) -> bool:
    if not path or "\0" in path or len(path) > 2000:
        return False
    if path.startswith(("\\\\", "//")):
        return False
    if os.name == "nt":
        return bool(_LOCAL_DRIVE.match(path))
    return os.path.isabs(path)


def validate_local_path(path: str, field: str = "path") -> Path:
    if not isinstance(path, str) or not _is_local_absolute(path):
        raise AppError(422, "validation", f"Give an absolute local drive path for {field}", {field: "invalid"})
    try:
        return Path(path).resolve()
    except OSError as exc:
        raise AppError(422, "validation", f"Invalid local path for {field}", {field: "invalid"}) from exc


def validate_branch_name(branch: str) -> str:
    """Validate a requested working branch; refuse protected names and malformed refs."""
    cleaned = branch.strip()
    if not cleaned:
        return ""
    if cleaned.lower() in _PROTECTED_BRANCHES:
        raise AppError(
            422,
            "validation",
            f"Protected branch '{cleaned}' cannot be checked out in a slot; choose a feature branch",
            {"branch": "protected"},
        )
    if (
        not _BRANCH_SAFE.fullmatch(cleaned)
        or ".." in cleaned
        or "//" in cleaned
        or cleaned.endswith(("/", ".", ".lock"))
        or "@{" in cleaned
    ):
        raise AppError(422, "validation", "Invalid Git branch name", {"branch": "invalid"})
    return cleaned


def _run_git(
    cwd: Path | str,
    *args: str,
    timeout: float = _DEFAULT_TIMEOUT_S,
    extra_env: dict[str, str] | None = None,
    decode: bool = True,
) -> subprocess.CompletedProcess:
    cmd = ["git", *_SAFE_GIT, "-C", str(cwd), *args]
    try:
        completed = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=_git_env(extra_env),
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise AppError(503, "slot_provisioning_failed", f"Git command timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise AppError(503, "slot_provisioning_failed", "Git executable could not be started") from exc
    if decode:
        return subprocess.CompletedProcess(
            cmd,
            completed.returncode,
            (completed.stdout or b"").decode("utf-8", errors="replace"),
            (completed.stderr or b"").decode("utf-8", errors="replace"),
        )
    return completed


def _git_ok(cwd: Path | str, *args: str, timeout: float = _DEFAULT_TIMEOUT_S, extra_env: dict[str, str] | None = None) -> str:
    res = _run_git(cwd, *args, timeout=timeout, extra_env=extra_env)
    if res.returncode != 0:
        first = (res.stderr or res.stdout or "").strip().splitlines()
        detail = first[0][:240] if first else f"exit code {res.returncode}"
        raise AppError(503, "slot_provisioning_failed", f"Git operation failed: {detail}")
    return str(res.stdout)


def validate_base_checkout(base_path: str, expected_remote_identity: str = "") -> Path:
    """Confirm base_path is an existing main Git checkout (not a linked worktree) matching expected remote."""
    resolved = validate_local_path(base_path, "baseCheckoutPath")
    if not resolved.is_dir():
        raise AppError(422, "validation", "Base checkout path does not exist", {"baseCheckoutPath": "not_found"})
    rev = _run_git(resolved, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir")
    if rev.returncode != 0:
        raise AppError(422, "validation", "Base checkout path is not a Git repository", {"baseCheckoutPath": "not_git"})
    lines = [line.strip() for line in rev.stdout.splitlines() if line.strip()]
    if len(lines) < 3:
        raise AppError(422, "validation", "Could not inspect Git base checkout", {"baseCheckoutPath": "not_git"})
    top, git_dir, common_dir = Path(lines[0]).resolve(), Path(lines[1]).resolve(), Path(lines[2]).resolve()
    if git_dir != common_dir:
        raise AppError(
            422,
            "validation",
            "Base checkout path must be a main clone, not a linked worktree",
            {"baseCheckoutPath": "linked_worktree"},
        )
    if expected_remote_identity:
        origin = _run_git(top, "remote", "get-url", "origin")
        if origin.returncode != 0 or not origin.stdout.strip():
            raise AppError(
                422,
                "validation",
                "Base checkout has no origin remote",
                {"baseCheckoutPath": "no_origin"},
            )
        actual = repository_identity(origin.stdout.strip())
        if actual != expected_remote_identity:
            raise AppError(
                422,
                "validation",
                "Base checkout origin remote does not match this repository",
                {"baseCheckoutPath": "remote_mismatch"},
            )
    return top


def _repo_slug(repo_name: str, base_path: Path) -> str:
    raw = (repo_name or base_path.name or "repo").strip()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.")
    return (slug or "repo")[:80]


def resolve_slot_path(base_path: str | Path, slot_root: str, repo_name: str, slot_index: int) -> Path:
    """Resolve centralized slot path: <slot_root or base.parent/.worklog-slots>/<repo-slug>/slot-<N>."""
    if slot_index < 1:
        raise AppError(422, "validation", "slot_index must be >= 1")
    base = validate_local_path(str(base_path), "baseCheckoutPath")
    if slot_root.strip():
        root = validate_local_path(slot_root.strip(), "slotRootPath")
    else:
        root = (base.parent / ".worklog-slots").resolve()
    # Refuse putting slots inside the base checkout itself.
    try:
        root.relative_to(base)
    except ValueError:
        pass
    else:
        raise AppError(
            422,
            "validation",
            "Slot root path cannot be inside the base checkout",
            {"slotRootPath": "inside_base"},
        )
    return (root / _repo_slug(repo_name, base) / f"slot-{slot_index}").resolve()


@dataclass(frozen=True)
class Cleanliness:
    clean: bool
    dirty_files: tuple[str, ...]


def inspect_slot_cleanliness(slot_path: str | Path) -> Cleanliness:
    """Inspect whether a slot worktree has uncommitted staged, unstaged, or untracked changes."""
    path = Path(slot_path)
    if not path.is_dir():
        return Cleanliness(clean=True, dirty_files=())
    res = _run_git(path, "status", "--porcelain")
    if res.returncode != 0:
        raise AppError(503, "slot_provisioning_failed", "Could not inspect slot working tree status")
    dirty = tuple(line for line in res.stdout.splitlines() if line.strip())
    return Cleanliness(clean=len(dirty) == 0, dirty_files=dirty)


def _registered_worktrees(base: Path) -> set[Path]:
    res = _run_git(base, "worktree", "list", "--porcelain")
    if res.returncode != 0:
        return set()
    found: set[Path] = set()
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            try:
                found.add(Path(line[9:].strip()).resolve())
            except OSError:
                continue
    return found


def _resolve_start_ref(base: Path, default_branch: str) -> str:
    """Return origin/<default_branch> if present, falling back to local <default_branch> or HEAD."""
    for candidate in (f"origin/{default_branch}", f"refs/remotes/origin/{default_branch}", default_branch, "HEAD"):
        check = _run_git(base, "rev-parse", "--verify", "--quiet", candidate)
        if check.returncode == 0 and check.stdout.strip():
            return candidate
    raise AppError(503, "slot_provisioning_failed", f"Could not resolve base ref for branch '{default_branch}'")


@dataclass(frozen=True)
class ProvisionResult:
    worktree_path: str
    branch: str
    fetch_warning: str = ""


def provision_slot(
    base_path: str,
    slot_root: str,
    repo_name: str,
    slot_index: int,
    default_branch: str,
    requested_branch: str,
    lease_id: str,
) -> ProvisionResult:
    """Lazily create or reuse a slot worktree, fetch origin, clean non-ignored files, and checkout branch."""
    base = validate_local_path(base_path, "baseCheckoutPath")
    if not base.is_dir():
        raise AppError(503, "slot_provisioning_failed", "Configured base checkout path does not exist")
    branch = validate_branch_name(requested_branch) or f"worklog/slot-{slot_index}-{lease_id[:8]}"
    slot = resolve_slot_path(base, slot_root, repo_name, slot_index)

    # Fetch latest refs; tolerate offline mode by falling back to local origin/<default_branch>.
    fetch_warning = ""
    fetch = _run_git(base, "fetch", "origin", "--prune", timeout=_FETCH_TIMEOUT_S)
    if fetch.returncode != 0:
        first = (fetch.stderr or "").strip().splitlines()
        fetch_warning = first[0][:200] if first else "git fetch origin failed; used local refs"
        logger.warning("Slot fetch warning for %s: %s", base, fetch_warning)

    start_ref = _resolve_start_ref(base, default_branch or "main")

    if slot.exists():
        registered = _registered_worktrees(base)
        if slot not in registered:
            # Prune stale worktree metadata first, then check whether directory is empty or rogue.
            _run_git(base, "worktree", "prune")
            registered = _registered_worktrees(base)
        if slot not in registered:
            if any(slot.iterdir()):
                raise AppError(
                    409,
                    "slot_dirty",
                    f"Slot directory {slot} exists but is not a registered Git worktree",
                    {"worktreePath": str(slot)},
                )
            slot.rmdir()
            created = _run_git(base, "worktree", "add", "-B", branch, str(slot), start_ref, timeout=_FETCH_TIMEOUT_S)
            if created.returncode != 0:
                detail = (created.stderr or created.stdout or "").strip().splitlines()
                msg = detail[0][:240] if detail else "git worktree add failed"
                raise AppError(503, "slot_provisioning_failed", f"Could not checkout branch '{branch}' in slot: {msg}")
            return ProvisionResult(worktree_path=str(slot), branch=branch, fetch_warning=fetch_warning)
        status = inspect_slot_cleanliness(slot)
        if not status.clean:
            raise AppError(
                409,
                "slot_dirty",
                f"Slot {slot_index} has uncommitted changes; rescue or clean it before reusing",
                {"worktreePath": str(slot), "dirtyFiles": list(status.dirty_files[:20])},
            )
        # Clean untracked non-ignored files ONLY (never -x), preserving .venv and node_modules.
        _git_ok(slot, "clean", "-fd")
        # Check out the target branch from start_ref (or attach to existing local branch if not checked out elsewhere).
        checkout = _run_git(slot, "checkout", "-B", branch, start_ref)
        if checkout.returncode != 0:
            detail = (checkout.stderr or checkout.stdout or "").strip().splitlines()
            msg = detail[0][:240] if detail else "git checkout failed"
            raise AppError(503, "slot_provisioning_failed", f"Could not checkout branch '{branch}' in slot: {msg}")
    else:
        slot.parent.mkdir(parents=True, exist_ok=True)
        _run_git(base, "worktree", "prune")
        created = _run_git(base, "worktree", "add", "-B", branch, str(slot), start_ref, timeout=_FETCH_TIMEOUT_S)
        if created.returncode != 0:
            detail = (created.stderr or created.stdout or "").strip().splitlines()
            msg = detail[0][:240] if detail else "git worktree add failed"
            raise AppError(503, "slot_provisioning_failed", f"Could not checkout branch '{branch}' in slot: {msg}")
    return ProvisionResult(worktree_path=str(slot), branch=branch, fetch_warning=fetch_warning)


@dataclass(frozen=True)
class RecycleResult:
    clean: bool
    rescued: bool
    rescue_ref: str = ""
    dirty_files: tuple[str, ...] = ()
    error: str = ""


def _create_rescue_commit(slot: Path, lease_id: str, branch: str) -> str:
    """Snapshot staged + unstaged + untracked non-ignored files into refs/worklog-rescue/<lease_id>."""
    head_res = _run_git(slot, "rev-parse", "--verify", "HEAD")
    if head_res.returncode != 0 or not head_res.stdout.strip():
        raise AppError(503, "slot_provisioning_failed", "Slot has no valid HEAD commit for rescue")
    head_sha = head_res.stdout.strip()

    fd, tmp_index = tempfile.mkstemp(prefix="worklog-rescue-idx-", dir=slot.parent)
    os.close(fd)
    tmp_path = Path(tmp_index)
    # Remove empty temp file so git read-tree creates a clean index file.
    tmp_path.unlink(missing_ok=True)
    env = {
        "GIT_INDEX_FILE": str(tmp_path),
        "GIT_AUTHOR_NAME": "Worklog Rescue",
        "GIT_AUTHOR_EMAIL": "worklog-rescue@localhost",
        "GIT_COMMITTER_NAME": "Worklog Rescue",
        "GIT_COMMITTER_EMAIL": "worklog-rescue@localhost",
    }
    try:
        _git_ok(slot, "read-tree", head_sha, extra_env=env)
        _git_ok(slot, "add", "-A", extra_env=env)
        tree_sha = _git_ok(slot, "write-tree", extra_env=env).strip()
        label = branch or "detached"
        message = f"worklog rescue for lease {lease_id} ({label})"
        commit_sha = _git_ok(
            slot,
            "commit-tree",
            tree_sha,
            "-p",
            head_sha,
            "-m",
            message,
            extra_env=env,
        ).strip()
        ref = f"refs/worklog-rescue/{lease_id}"
        _git_ok(slot, "update-ref", ref, commit_sha)
        return ref
    finally:
        tmp_path.unlink(missing_ok=True)


def recycle_or_rescue_slot(
    slot_path: str,
    default_branch: str,
    lease_id: str,
    *,
    branch: str = "",
    allow_rescue: bool = False,
) -> RecycleResult:
    """Detach a clean slot worktree back to origin/<default_branch>, or rescue dirty files if allowed.

    Never deletes gitignored files (.venv, node_modules): uses `git clean -fd` without `-x`.
    """
    if not slot_path:
        return RecycleResult(clean=True, rescued=False)
    slot = Path(slot_path)
    if not slot.is_dir():
        return RecycleResult(clean=True, rescued=False)

    status = inspect_slot_cleanliness(slot)
    rescue_ref = ""
    if not status.clean:
        if not allow_rescue:
            return RecycleResult(clean=False, rescued=False, dirty_files=status.dirty_files)
        try:
            rescue_ref = _create_rescue_commit(slot, lease_id, branch)
            _git_ok(slot, "reset", "--hard", "HEAD")
            _git_ok(slot, "clean", "-fd")
            # Confirm reset + clean succeeded (e.g., no locked Windows file handle prevented removal).
            after = inspect_slot_cleanliness(slot)
            if not after.clean:
                return RecycleResult(
                    clean=False,
                    rescued=bool(rescue_ref),
                    rescue_ref=rescue_ref,
                    dirty_files=after.dirty_files,
                    error="Slot remained dirty after rescue reset/clean",
                )
        except (AppError, OSError) as exc:
            return RecycleResult(
                clean=False,
                rescued=bool(rescue_ref),
                rescue_ref=rescue_ref,
                dirty_files=status.dirty_files,
                error=str(exc),
            )

    # Detach so the feature branch is no longer pinned by this slot worktree.
    try:
        start_ref = _resolve_start_ref(slot, default_branch or "main")
        _git_ok(slot, "checkout", "--detach", start_ref)
    except AppError as exc:
        return RecycleResult(
            clean=False,
            rescued=bool(rescue_ref),
            rescue_ref=rescue_ref,
            dirty_files=status.dirty_files,
            error=str(exc),
        )
    return RecycleResult(clean=True, rescued=bool(rescue_ref), rescue_ref=rescue_ref)


def _tracked_files(base: Path) -> list[Path]:
    res = _run_git(base, "ls-files", "-z", decode=False)
    if res.returncode != 0:
        raise AppError(503, "slot_provisioning_failed", "Could not list tracked files in base checkout")
    raw: bytes = res.stdout or b""
    files: list[Path] = []
    for entry in raw.split(b"\x00"):
        if not entry:
            continue
        rel = entry.decode("utf-8", errors="surrogateescape")
        # Never touch anything inside .git/, .venv/, or node_modules/.
        parts = Path(rel).parts
        if any(part in {".git", ".venv", "node_modules"} for part in parts):
            continue
        target = base / rel
        if target.is_file() and not target.is_symlink():
            files.append(target)
    return files


def _ensure_exclude_entry(base: Path, pattern: str) -> None:
    exclude = base / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8", errors="replace") if exclude.is_file() else ""
    lines = [line.strip() for line in existing.splitlines()]
    if pattern not in lines:
        prefix = "" if (not existing or existing.endswith("\n")) else "\n"
        with exclude.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{prefix}{pattern}\n")


def _notice_text(repo_name: str) -> str:
    return (
        f"READ-ONLY BASE CLONE ({repo_name or 'repository'})\n"
        "This canonical base checkout is protected by Worklog.\n"
        "Acquire a writable checkout slot before editing files:\n"
        "  worklog agent --as NAME leases acquire --checkout <THIS_PATH> --branch <BRANCH> --wait --request-id <KEY>\n"
        "Then edit files strictly inside the returned worktreePath.\n"
    )


def lock_base_readonly(base_path: str, default_branch: str = "main", repo_name: str = "") -> dict:
    """Protect tracked working-tree files in base_path with OS read-only attribute (attrib +R / S_IREAD).

    Preflight refuses dirty working trees (409 base_dirty) or unpushed local commits (409 base_unpushed_commits).
    Detaches base HEAD at origin/<default_branch> so local main is never pinned by the base checkout.
    Leaves .git/, .venv/, and node_modules/ completely writable.
    """
    base = validate_base_checkout(base_path)
    status = inspect_slot_cleanliness(base)
    if not status.clean:
        raise AppError(
            409,
            "base_dirty",
            "Base checkout has uncommitted changes; commit, stash, or move them to a slot before locking",
            {"dirtyFiles": list(status.dirty_files[:20])},
        )
    target_ref = _resolve_start_ref(base, default_branch or "main")
    ahead = _run_git(base, "rev-list", "--count", f"{target_ref}..HEAD")
    if ahead.returncode == 0 and ahead.stdout.strip().isdigit() and int(ahead.stdout.strip()) > 0:
        raise AppError(
            409,
            "base_unpushed_commits",
            f"Base checkout has {ahead.stdout.strip()} commit(s) ahead of {target_ref}; push or merge them before locking",
            {"ahead": int(ahead.stdout.strip()), "ref": target_ref},
        )

    # First ensure existing files are writable during detach checkout (in case re-locking).
    for path in _tracked_files(base):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
    _git_ok(base, "checkout", "--detach", target_ref)

    locked_count = 0
    for path in _tracked_files(base):
        try:
            os.chmod(path, stat.S_IREAD)
            locked_count += 1
        except OSError as exc:
            raise AppError(503, "storage_error", f"Could not set read-only attribute on {path.name}") from exc

    _ensure_exclude_entry(base, _READONLY_NOTICE)
    notice = base / _READONLY_NOTICE
    if notice.exists():
        try:
            os.chmod(notice, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
    notice.write_text(_notice_text(repo_name or base.name), encoding="utf-8", newline="\n")
    os.chmod(notice, stat.S_IREAD)
    return {"basePath": str(base), "locked": True, "filesLocked": locked_count, "detachedAt": target_ref}


def unlock_base_readonly(base_path: str) -> dict:
    """Restore write permission (S_IWRITE | S_IREAD) to tracked files in base_path and remove notice."""
    base = validate_base_checkout(base_path)
    unlocked_count = 0
    for path in _tracked_files(base):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            unlocked_count += 1
        except OSError as exc:
            raise AppError(503, "storage_error", f"Could not unlock {path.name}") from exc
    notice = base / _READONLY_NOTICE
    if notice.exists():
        try:
            os.chmod(notice, stat.S_IWRITE | stat.S_IREAD)
            notice.unlink(missing_ok=True)
        except OSError:
            pass
    return {"basePath": str(base), "locked": False, "filesUnlocked": unlocked_count}


def is_base_locked_on_disk(base_path: str) -> bool:
    """Quick non-mutating check whether .worklog-readonly-base marker exists on disk."""
    if not base_path or not _is_local_absolute(base_path):
        return False
    return (Path(base_path) / _READONLY_NOTICE).is_file()
