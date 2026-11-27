"""Managed slot provisioning, read-only caller worktree validation, and base checkout locks.

Every Git subprocess follows Worklog's safety conventions (shell=False, scrubbed GIT_* env,
GIT_TERMINAL_PROMPT=0, --no-pager, strict local drive paths on Windows, explicit timeouts).
Run Git outside SQLite write transactions. Claim commits only recheck local paths and metadata.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import caller_cleanliness
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
_MAX_GIT_METADATA_BYTES = 64 * 1024
_MAX_GIT_CONFIG_BYTES = 1024 * 1024
_SYS_PLATFORM = sys.platform


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
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess:
    cmd = ["git", *_SAFE_GIT, "-C", str(cwd), *args]
    try:
        completed = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL if input_bytes is None else None,
            input=input_bytes,
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


def _physical_path_key(path: Path) -> str:
    """Normalize a resolved physical path with the host filesystem's comparison rules."""
    value = os.path.normpath(os.fspath(path))
    if os.name == "nt":
        value = os.path.normcase(value)
        if value.startswith("\\\\?\\"):
            value = value[4:]
        return value.replace("\\", "/")
    return value


def worktree_claim_key(path: str | Path) -> str:
    """Return a canonical physical path key for an existing or future local worktree path."""
    if not isinstance(path, (str, Path)):
        raise AppError(422, "validation", "Give an absolute local drive path for worktreePath", {"worktreePath": "invalid"})
    raw = os.fspath(path)
    if not _is_local_absolute(raw):
        raise AppError(422, "validation", "Give an absolute local drive path for worktreePath", {"worktreePath": "invalid"})
    try:
        resolved = Path(raw).resolve()
    except (OSError, RuntimeError) as exc:
        raise AppError(422, "validation", "Invalid local path for worktreePath", {"worktreePath": "invalid"}) from exc
    if not _is_local_absolute(str(resolved)):
        raise AppError(422, "validation", "Invalid local path for worktreePath", {"worktreePath": "invalid"})
    return _physical_path_key(resolved)


def _stat_key(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
    )


def _descriptor_matches_path(descriptor_stat: os.stat_result, path_stat: os.stat_result) -> bool:
    # Windows may report a changing ctime for an open handle, while lstat's
    # creation/change metadata remains stable. Compare ctime only between path
    # observations there; the file ID, type, size, and mtime still bind the handle.
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if any(getattr(descriptor_stat, field_name) != getattr(path_stat, field_name) for field_name in fields):
        return False
    return os.name == "nt" or descriptor_stat.st_ctime_ns == path_stat.st_ctime_ns


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    link_metadata: tuple[int, int, int, int, int, int]
    target_metadata: tuple[int, int, int, int, int, int]
    content: bytes | None = field(default=None, repr=False)
    registered_at_ns: int = 0
    has_birth_time: bool = False
    has_file_identity: bool = False


class _StatxTimestamp(ctypes.Structure):
    _fields_ = [
        ("seconds", ctypes.c_int64),
        ("nanoseconds", ctypes.c_uint32),
        ("reserved", ctypes.c_int32),
    ]


class _StatxResult(ctypes.Structure):
    _fields_ = [
        ("mask", ctypes.c_uint32),
        ("block_size", ctypes.c_uint32),
        ("attributes", ctypes.c_uint64),
        ("links", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("mode", ctypes.c_uint16),
        ("spare0", ctypes.c_uint16),
        ("inode", ctypes.c_uint64),
        ("size", ctypes.c_uint64),
        ("blocks", ctypes.c_uint64),
        ("attributes_mask", ctypes.c_uint64),
        ("atime", _StatxTimestamp),
        ("birthtime", _StatxTimestamp),
        ("ctime", _StatxTimestamp),
        ("mtime", _StatxTimestamp),
        ("rdev_major", ctypes.c_uint32),
        ("rdev_minor", ctypes.c_uint32),
        ("dev_major", ctypes.c_uint32),
        ("dev_minor", ctypes.c_uint32),
        ("mount_id", ctypes.c_uint64),
        ("dio_mem_align", ctypes.c_uint32),
        ("dio_offset_align", ctypes.c_uint32),
        ("spare3", ctypes.c_uint64 * 12),
    ]


def _linux_statx_birth_time_ns(path: Path, expected_stat: os.stat_result) -> int | None:
    """Read STATX_BTIME only when statx identifies the same regular file as lstat."""
    if _SYS_PLATFORM != "linux":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        statx_call = libc.statx
    except (AttributeError, OSError):
        return None
    statx_call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_StatxResult)]
    statx_call.restype = ctypes.c_int
    result = _StatxResult()
    # AT_FDCWD | AT_SYMLINK_NOFOLLOW; request basic identity plus STATX_BTIME.
    requested = 0x07FF | 0x0800
    if statx_call(-100, os.fsencode(path), 0x100, requested, ctypes.byref(result)) != 0:
        return None
    if result.mask & 0x0800 == 0 or result.mask & 0x0101 != 0x0101:
        return None
    major = getattr(os, "major", None)
    minor = getattr(os, "minor", None)
    if not callable(major) or not callable(minor):
        return None
    if (
        result.inode != int(expected_stat.st_ino)
        or (result.dev_major, result.dev_minor) != (major(expected_stat.st_dev), minor(expected_stat.st_dev))
        or stat.S_IFMT(result.mode) != stat.S_IFMT(expected_stat.st_mode)
        or not 0 <= result.birthtime.nanoseconds < 1_000_000_000
        or result.birthtime.seconds < 0
    ):
        return None
    return int(result.birthtime.seconds) * 1_000_000_000 + int(result.birthtime.nanoseconds)


def _birth_time_ns(path: Path, file_stat: os.stat_result) -> int | None:
    """Return a trustworthy native creation time, or None so callers use mtime evidence."""
    raw_birth_ns: object = getattr(file_stat, "st_birthtime_ns", None)
    if isinstance(raw_birth_ns, int) and raw_birth_ns > 0:
        return raw_birth_ns
    raw_birth_time: object = getattr(file_stat, "st_birthtime", None)
    if isinstance(raw_birth_time, (int, float)) and raw_birth_time > 0:
        return int(raw_birth_time * 1_000_000_000)
    return _linux_statx_birth_time_ns(path, file_stat)


def _snapshot_file(
    path: Path,
    *,
    include_content: bool,
    max_bytes: int = _MAX_GIT_METADATA_BYTES,
) -> _FileSnapshot:
    link_stat = path.lstat()
    if not stat.S_ISREG(link_stat.st_mode) or link_stat.st_size > max_bytes:
        raise OSError("Git metadata is not a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        target_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(target_stat.st_mode)
            or target_stat.st_size > max_bytes
            or not _descriptor_matches_path(target_stat, link_stat)
        ):
            raise OSError("Git metadata changed or is not a bounded regular file")
        content: bytes | None = None
        if include_content:
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 8192))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) > max_bytes:
                raise OSError("Git metadata exceeds the read limit")
        after = os.fstat(descriptor)
        if not _descriptor_matches_path(after, target_stat) or _stat_key(path.lstat()) != _stat_key(link_stat):
            raise OSError("Git metadata changed while being inspected")
    finally:
        os.close(descriptor)
    birth_ns = _birth_time_ns(path, link_stat)
    has_birth_time = birth_ns is not None
    if birth_ns is None:
        birth_ns = int(link_stat.st_mtime_ns)
    has_file_identity = int(link_stat.st_dev) > 0 and int(link_stat.st_ino) > 0
    target_key = _stat_key(target_stat)
    if os.name == "nt":
        # Keep the stable path-observed ctime in the snapshot, not the transient
        # value returned by a Windows open handle.
        target_key = (*target_key[:5], int(link_stat.st_ctime_ns))
    return _FileSnapshot(
        path, _stat_key(link_stat), target_key, content, int(birth_ns), has_birth_time, has_file_identity
    )


def _read_single_line(snapshot: _FileSnapshot, label: str) -> str:
    if (
        not stat.S_ISREG(snapshot.link_metadata[2])
        or not stat.S_ISREG(snapshot.target_metadata[2])
        or snapshot.content is None
    ):
        raise AppError(422, "validation", f"Could not inspect Git {label}", {"worktreePath": "invalid_git_metadata"})
    try:
        lines = snapshot.content.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise AppError(422, "validation", f"Could not inspect Git {label}", {"worktreePath": "invalid_git_metadata"}) from exc
    if len(lines) != 1 or not lines[0] or any(ord(ch) < 32 or ord(ch) == 127 for ch in lines[0]):
        raise AppError(422, "validation", f"Could not inspect Git {label}", {"worktreePath": "invalid_git_metadata"})
    return lines[0]


def _resolve_git_link(value: str, relative_to: Path, label: str) -> Path:
    if not value or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise AppError(422, "validation", f"Could not inspect Git {label}", {"worktreePath": "invalid_git_metadata"})
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = relative_to / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AppError(422, "validation", f"Could not inspect Git {label}", {"worktreePath": "invalid_git_metadata"}) from exc
    if not _is_local_absolute(str(resolved)):
        raise AppError(422, "validation", f"Could not inspect Git {label}", {"worktreePath": "invalid_git_metadata"})
    return resolved


@dataclass(frozen=True)
class CallerWorktree:
    """A caller-owned linked worktree and the local registration evidence checked for it."""

    path: Path
    registered_at_ns: int
    registration_id: str
    registration_trustworthy: bool
    _clean_evidence: caller_cleanliness.CleanEvidence | None = field(repr=False, compare=False)
    _root_identity: tuple[int, int, int] = field(repr=False, compare=False)
    _common_dir: Path = field(repr=False, compare=False)
    _common_identity: tuple[int, int, int] = field(repr=False, compare=False)
    _files: tuple[_FileSnapshot, ...] = field(repr=False, compare=False)

    @property
    def clean(self) -> bool:
        return self._clean_evidence is not None

    def still_clean(self) -> bool:
        return self._clean_evidence is not None and self._clean_evidence.current(bounded=True)

    def require_current(self) -> None:
        """Recheck cached local metadata immediately before binding, without spawning Git."""
        try:
            resolved = self.path.resolve(strict=True)
            root_stat = resolved.stat()
            common_stat = self._common_dir.stat()
            if (
                not resolved.is_dir()
                or os.path.lexists(resolved / _READONLY_NOTICE)
                or _physical_path_key(resolved) != _physical_path_key(self.path)
                or (int(root_stat.st_dev), int(root_stat.st_ino), int(root_stat.st_mode)) != self._root_identity
                or (int(common_stat.st_dev), int(common_stat.st_ino), int(common_stat.st_mode)) != self._common_identity
            ):
                raise ValueError("root changed")
            for expected in self._files:
                max_bytes = _MAX_GIT_CONFIG_BYTES if expected.content is None else _MAX_GIT_METADATA_BYTES
                current = _snapshot_file(
                    expected.path,
                    include_content=expected.content is not None,
                    max_bytes=max_bytes,
                )
                if current != expected:
                    raise ValueError("Git metadata changed")
        except (OSError, RuntimeError, ValueError) as exc:
            raise AppError(409, "conflict", "Caller worktree changed during validation") from exc


def _origin_config_snapshots(path: Path) -> tuple[_FileSnapshot, ...]:
    """Capture metadata for files that supplied remote.origin.url without retaining its value."""
    result = _run_git(
        path,
        "config",
        "--show-origin",
        "--get-all",
        "remote.origin.url",
        extra_env=caller_cleanliness.READ_ONLY_ENV,
    )
    if result.returncode != 0:
        return ()
    sources: set[Path] = set()
    for line in result.stdout.splitlines():
        origin, separator, _ = line.partition("\t")
        if not separator or not origin.startswith("file:"):
            continue
        raw_path = origin[5:]
        source = Path(raw_path)
        if not source.is_absolute():
            source = path / source
        sources.add(source.absolute())
    snapshots: list[_FileSnapshot] = []
    for source in sorted(sources, key=lambda item: str(item).casefold()):
        try:
            snapshot = _snapshot_file(source, include_content=False, max_bytes=_MAX_GIT_CONFIG_BYTES)
        except OSError as exc:
            raise AppError(409, "conflict", "Git origin configuration changed during validation") from exc
        if not stat.S_ISREG(snapshot.target_metadata[2]):
            raise AppError(422, "validation", "Could not inspect Git origin configuration", {"worktreePath": "invalid_git_metadata"})
        snapshots.append(snapshot)
    return tuple(snapshots)


def _registration_time(link: _FileSnapshot, reciprocal: _FileSnapshot) -> int:
    """Use the earliest birth-time or mtime evidence from both registration endpoints."""
    return min(link.registered_at_ns, reciprocal.registered_at_ns)


def _registration_has_identity(link: _FileSnapshot, reciprocal: _FileSnapshot) -> bool:
    """Both registration endpoints need stable local file IDs for holder reuse."""
    return link.has_file_identity and reciprocal.has_file_identity


def _registration_is_trustworthy(link: _FileSnapshot, reciprocal: _FileSnapshot) -> bool:
    """Automatic first-use attribution requires birth time and file identity at both endpoints."""
    return (
        _registration_has_identity(link, reciprocal)
        and link.has_birth_time
        and reciprocal.has_birth_time
        and link.registered_at_ns > 0
        and reciprocal.registered_at_ns > 0
    )


def _registration_id(link: _FileSnapshot, reciprocal: _FileSnapshot, registered_at_ns: int) -> str:
    """Hash stable endpoint identities; an unknown file ID must never match a prior receipt."""
    if not _registration_has_identity(link, reciprocal):
        return ""
    parts = (
        link.link_metadata[0],
        link.link_metadata[1],
        registered_at_ns,
        reciprocal.link_metadata[0],
        reciprocal.link_metadata[1],
    )
    return hashlib.sha256(
        b"caller-worktree-registration\\0"
        + b"\\0".join(str(part).encode("ascii") for part in parts)
    ).hexdigest()


def _registered_worktree_paths(common_dir: Path) -> set[str]:
    result = _run_git(common_dir, "worktree", "list", "--porcelain", "-z", extra_env=caller_cleanliness.READ_ONLY_ENV)
    nul_delimited = result.returncode == 0
    if not nul_delimited:
        result = _run_git(common_dir, "worktree", "list", "--porcelain", extra_env=caller_cleanliness.READ_ONLY_ENV)
    if result.returncode != 0:
        raise AppError(422, "validation", "Could not verify Git worktree registration", {"worktreePath": "unregistered"})
    found: set[str] = set()
    records = result.stdout.split("\0") if nul_delimited else result.stdout.splitlines()
    for record in records:
        if not record.startswith("worktree "):
            continue
        raw_path = record[len("worktree "):]
        try:
            found.add(worktree_claim_key(raw_path))
        except AppError:
            continue
    return found


def validate_caller_worktree(
    path: str,
    expected_remote_identity: str,
    branch: str,
    default_branch: str,
    *,
    inspect_cleanliness: bool = True,
) -> CallerWorktree:
    """Validate an existing, caller-owned linked worktree without changing Git or file state."""
    if not isinstance(path, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        raise AppError(422, "validation", "Give a valid local Git worktree path", {"worktreePath": "invalid"})
    resolved = validate_local_path(path, "worktreePath")
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AppError(422, "validation", "Caller worktree path does not exist", {"worktreePath": "not_found"}) from exc
    if not _is_local_absolute(str(resolved)):
        raise AppError(422, "validation", "Caller worktree path must resolve to a local drive", {"worktreePath": "invalid"})
    if not resolved.is_dir():
        raise AppError(422, "validation", "Caller worktree path is not a directory", {"worktreePath": "not_directory"})
    if os.path.lexists(resolved / _READONLY_NOTICE):
        raise AppError(422, "validation", "A read-only base checkout cannot be bound as a caller worktree", {"worktreePath": "readonly_base"})
    if not isinstance(expected_remote_identity, str) or not expected_remote_identity.strip():
        raise AppError(422, "validation", "A registered repository identity is required", {"repositoryId": "required"})
    # The caller supplies the already-normalized catalog identity. Re-normalizing an
    # identity string as though it were a remote URL changes its path.
    expected_identity = expected_remote_identity.strip()
    if not expected_identity:
        raise AppError(422, "validation", "A registered repository identity is required", {"repositoryId": "required"})
    expected_branch = validate_branch_name(branch)
    if not expected_branch:
        raise AppError(422, "validation", "A task branch is required to bind a caller worktree", {"branch": "required"})
    if default_branch and expected_branch.casefold() == default_branch.strip().casefold():
        raise AppError(422, "validation", "The repository default branch cannot be bound as a task worktree", {"branch": "protected"})

    try:
        root_stat = resolved.stat()
        root_identity = (int(root_stat.st_dev), int(root_stat.st_ino), int(root_stat.st_mode))
        link_path = resolved / ".git"
        link_snapshot = _snapshot_file(link_path, include_content=True)
        if not stat.S_ISREG(link_snapshot.link_metadata[2]) or not stat.S_ISREG(link_snapshot.target_metadata[2]):
            raise AppError(422, "validation", "Caller worktree must have a Git worktree link file", {"worktreePath": "not_linked_worktree"})
        link_line = _read_single_line(link_snapshot, "worktree link")
        if not link_line.startswith("gitdir: "):
            raise AppError(422, "validation", "Caller worktree has an invalid Git worktree link", {"worktreePath": "invalid_git_metadata"})
        git_dir = _resolve_git_link(link_line[len("gitdir: "):], resolved, "worktree link")
        head_path = git_dir / "HEAD"
        reciprocal_path = git_dir / "gitdir"
        commondir_path = git_dir / "commondir"
        head_snapshot = _snapshot_file(head_path, include_content=True)
        reciprocal_snapshot = _snapshot_file(reciprocal_path, include_content=True)
        commondir_snapshot = _snapshot_file(commondir_path, include_content=True)
        reciprocal_line = _read_single_line(reciprocal_snapshot, "worktree registration")
        reciprocal_target = _resolve_git_link(reciprocal_line, git_dir, "worktree registration")
        commondir_line = _read_single_line(commondir_snapshot, "common directory")
        declared_common_dir = _resolve_git_link(commondir_line, git_dir, "common directory")
    except AppError:
        raise
    except (OSError, RuntimeError) as exc:
        raise AppError(422, "validation", "Could not inspect caller Git worktree metadata", {"worktreePath": "invalid_git_metadata"}) from exc
    if _physical_path_key(reciprocal_target) != _physical_path_key(link_path):
        raise AppError(422, "validation", "Git worktree registration does not point back to this worktree", {"worktreePath": "unregistered"})

    env = caller_cleanliness.READ_ONLY_ENV
    rev = _run_git(resolved, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir", extra_env=env)
    lines = rev.stdout.splitlines()
    if rev.returncode != 0 or len(lines) != 3 or any(not line for line in lines):
        raise AppError(422, "validation", "Caller path is not a complete Git worktree", {"worktreePath": "not_git"})
    try:
        top, reported_git_dir, common_dir = (Path(line).resolve(strict=True) for line in lines)
        worktree_root = (common_dir / "worktrees").resolve(strict=True)
        common_stat = common_dir.stat()
    except (OSError, RuntimeError) as exc:
        raise AppError(422, "validation", "Could not resolve Git worktree registration", {"worktreePath": "invalid_git_metadata"}) from exc
    if _physical_path_key(top) != _physical_path_key(resolved):
        raise AppError(422, "validation", "Bind the exact Git worktree root, not a subdirectory", {"worktreePath": "not_root"})
    if _physical_path_key(reported_git_dir) == _physical_path_key(common_dir):
        raise AppError(422, "validation", "A main checkout cannot be bound as a caller worktree", {"worktreePath": "not_linked_worktree"})
    if _physical_path_key(reported_git_dir) != _physical_path_key(git_dir):
        raise AppError(422, "validation", "Git worktree link does not match Git metadata", {"worktreePath": "invalid_git_metadata"})
    if not _is_local_absolute(str(top)) or not _is_local_absolute(str(reported_git_dir)) or not _is_local_absolute(str(common_dir)):
        raise AppError(422, "validation", "Git worktree paths must resolve to local drives", {"worktreePath": "invalid_git_metadata"})
    if _physical_path_key(declared_common_dir) != _physical_path_key(common_dir):
        raise AppError(422, "validation", "Git worktree points to a different common repository", {"worktreePath": "unregistered"})
    git_dir_key = _physical_path_key(git_dir)
    root_key = _physical_path_key(worktree_root).rstrip("/")
    if git_dir_key == root_key or not git_dir_key.startswith(root_key + "/"):
        raise AppError(422, "validation", "Git directory is not registered beneath this repository", {"worktreePath": "unregistered"})

    branch_result = _run_git(resolved, "symbolic-ref", "--quiet", "HEAD", extra_env=env)
    if branch_result.returncode != 0 or not branch_result.stdout.strip():
        raise AppError(422, "validation", "Caller worktree HEAD is detached", {"branch": "detached"})
    actual_ref = branch_result.stdout.strip()
    if actual_ref != f"refs/heads/{expected_branch}":
        raise AppError(422, "validation", "Caller worktree branch does not match the lease branch", {"branch": "mismatch"})
    actual_branch = validate_branch_name(actual_ref.removeprefix("refs/heads/"))
    if default_branch and actual_branch.casefold() == default_branch.strip().casefold():
        raise AppError(422, "validation", "The repository default branch cannot be bound as a task worktree", {"branch": "protected"})

    config_before = _origin_config_snapshots(resolved)
    origin = _run_git(resolved, "remote", "get-url", "origin", extra_env=env)
    origin_values = [line for line in origin.stdout.splitlines() if line]
    if origin.returncode != 0 or len(origin_values) != 1:
        raise AppError(422, "validation", "Caller worktree must have exactly one origin remote", {"worktreePath": "origin_required"})
    if repository_identity(origin_values[0]) != expected_identity:
        raise AppError(422, "validation", "Caller worktree origin does not match the registered repository", {"worktreePath": "remote_mismatch"})

    target_key = worktree_claim_key(resolved)
    if target_key not in _registered_worktree_paths(common_dir):
        raise AppError(422, "validation", "Git does not list this path as a registered worktree", {"worktreePath": "unregistered"})

    clean_evidence = (
        caller_cleanliness.inspect(resolved, git_dir, common_dir, expected_branch, _run_git)
        if inspect_cleanliness else None
    )

    config_after = _origin_config_snapshots(resolved)
    if config_after != config_before:
        raise AppError(409, "conflict", "Caller worktree changed during validation")
    registered_at_ns = _registration_time(link_snapshot, reciprocal_snapshot)
    registration_id = _registration_id(link_snapshot, reciprocal_snapshot, registered_at_ns)
    common_identity = (int(common_stat.st_dev), int(common_stat.st_ino), int(common_stat.st_mode))
    files = (link_snapshot, head_snapshot, reciprocal_snapshot, commondir_snapshot, *config_before)
    registration_trustworthy = _registration_is_trustworthy(link_snapshot, reciprocal_snapshot)
    caller_worktree = CallerWorktree(
        resolved,
        int(registered_at_ns),
        registration_id,
        registration_trustworthy,
        clean_evidence,
        root_identity,
        common_dir,
        common_identity,
        tuple(files),
    )
    caller_worktree.require_current()
    return caller_worktree


def _repo_slug(repo_name: str, base_path: Path) -> str:
    raw = (repo_name or base_path.name or "repo").strip()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.")
    return (slug or "repo")[:80]


def slot_pool_path(base_path: str | Path, slot_root: str, repo_name: str) -> Path:
    """Resolve the canonical repository slot pool without resolving any slot leaf."""
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
    return (root / _repo_slug(repo_name, base)).resolve()


def resolve_slot_path(base_path: str | Path, slot_root: str, repo_name: str, slot_index: int) -> Path:
    """Resolve centralized slot path: <slot_root or base.parent/.worklog-slots>/<repo-slug>/slot-<N>."""
    if slot_index < 1:
        raise AppError(422, "validation", "slot_index must be >= 1")
    return (slot_pool_path(base_path, slot_root, repo_name) / f"slot-{slot_index}").resolve()


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
    reserved_slot_path: str | Path | None = None,
    *,
    before_reuse: Callable[[Path], None] | None = None,
) -> ProvisionResult:
    """Lazily create or reuse a slot worktree, fetch origin, clean non-ignored files, and checkout branch."""
    base = validate_local_path(base_path, "baseCheckoutPath")
    if not base.is_dir():
        raise AppError(503, "slot_provisioning_failed", "Configured base checkout path does not exist")
    branch = validate_branch_name(requested_branch) or f"worklog/slot-{slot_index}-{lease_id[:8]}"
    slot = Path(reserved_slot_path) if reserved_slot_path is not None else resolve_slot_path(base, slot_root, repo_name, slot_index)

    def check_reservation() -> None:
        if reserved_slot_path is not None:
            current = resolve_slot_path(base, slot_root, repo_name, slot_index)
            if not _is_local_absolute(str(slot)) or slot != current or slot.resolve() != slot:
                raise AppError(409, "conflict", "Managed slot path changed after its lease claim was reserved")

    # Fetch latest refs; tolerate offline mode by falling back to local origin/<default_branch>.
    check_reservation()
    fetch_warning = ""
    fetch = _run_git(base, "fetch", "origin", "--prune", timeout=_FETCH_TIMEOUT_S)
    if fetch.returncode != 0:
        first = (fetch.stderr or "").strip().splitlines()
        fetch_warning = first[0][:200] if first else "git fetch origin failed; used local refs"
        logger.warning("Slot fetch warning for %s: %s", base, fetch_warning)
    check_reservation()

    # Reattach an existing local branch at its exact tip, including unpushed commits.
    # Use non-forcing creation so a branch created concurrently cannot be reset.
    create_args = []
    target = branch
    if _run_git(base, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode != 0:
        create_args = ["-b", branch]
        target = f"refs/remotes/origin/{branch}"
        if _run_git(base, "show-ref", "--verify", "--quiet", target).returncode != 0:
            target = _resolve_start_ref(base, default_branch or "main")

    reuse_slot = slot.exists()
    if reuse_slot:
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
            check_reservation()
            slot.rmdir()
            reuse_slot = False

    if reuse_slot:
        if before_reuse:
            check_reservation()
            before_reuse(slot)
        status = inspect_slot_cleanliness(slot)
        if not status.clean:
            raise AppError(
                409,
                "slot_dirty",
                f"Slot {slot_index} has uncommitted changes; rescue or clean it before reusing",
                {"worktreePath": str(slot), "dirtyFiles": list(status.dirty_files[:20])},
            )
        # Clean untracked non-ignored files ONLY (never -x), preserving .venv and node_modules.
        check_reservation()
        _git_ok(slot, "clean", "-fd")
        check_reservation()
        checkout = _run_git(slot, "checkout", *create_args, target, "--")
        if checkout.returncode != 0:
            detail = (checkout.stderr or checkout.stdout or "").strip().splitlines()
            msg = detail[0][:240] if detail else "git checkout failed"
            raise AppError(503, "slot_provisioning_failed", f"Could not checkout branch '{branch}' in slot: {msg}")
    else:
        check_reservation()
        slot.parent.mkdir(parents=True, exist_ok=True)
        _run_git(base, "worktree", "prune")
        check_reservation()
        created = _run_git(base, "worktree", "add", *create_args, str(slot), target, timeout=_FETCH_TIMEOUT_S)
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
