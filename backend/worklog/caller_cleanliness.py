"""Positive evidence for first-use attestation in a caller-owned linked worktree."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .common import AppError
from .windows_file_time import change_time_ns

READ_ONLY_ENV = {
    "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
    "GIT_ALLOW_PROTOCOL": "",
}
_BUILTIN_SETTINGS = {
    b"core.autocrlf": {b"true", b"false", b"input"},
    b"core.eol": {b"lf", b"crlf", b"native"},
}
_MAX_INDEX_BYTES = 64 * 1024 * 1024
_MAX_PROOF_PATHS = 16_384
_MAX_COMPARISON_BYTES = 512 * 1024 * 1024
# A large or slow checkout can use the handoff path without monopolizing SQLite's writer.
_MAX_LOCK_CHECK_SECONDS = 0.5
_Stamp = tuple[int, int, int, int, int, int]
_Git = Callable[..., subprocess.CompletedProcess]


def _stamp(path: Path) -> _Stamp | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("indirect filesystem entry")
    # Windows lstat can report creation time as ctime. Use the native change time
    # so that a save which preserves size and last-write time still invalidates the proof.
    changed = change_time_ns(path) if os.name == "nt" else info.st_ctime_ns
    if changed is None:
        raise ValueError("metadata change time unavailable")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, changed)


@dataclass(frozen=True)
class CleanEvidence:
    observed: tuple[tuple[Path, _Stamp | None], ...]

    def current(self, *, bounded: bool = False) -> bool:
        deadline = time.monotonic() + _MAX_LOCK_CHECK_SECONDS if bounded else None
        try:
            for path, expected in self.observed:
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                if _stamp(path) != expected:
                    return False
        except (OSError, ValueError):
            return False
        return True


def _copy_index(source: Path, destination: Path, expected: _Stamp | None) -> bool:
    if expected is None or not stat.S_ISREG(expected[2]) or expected[3] > _MAX_INDEX_BYTES:
        return False
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(source, flags), "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size) != expected[:4]:
            return False
        header = handle.read(12)
        if len(header) != 12 or header[:4] != b"DIRC" or int.from_bytes(header[8:12], "big") > _MAX_PROOF_PATHS:
            return False
        data = header + handle.read(expected[3] - 12 + 1)
    if len(data) != expected[3] or _stamp(source) != expected:
        return False
    destination.write_bytes(data)
    return True


def _tracked_files(index: bytes, tree: bytes, root: Path) -> dict[Path, bytes] | None:
    paths = {}
    # Both native -z listings use Git's path order and return the raw filename.
    # Compare full entries so staged changes and intent-to-add cannot look clean.
    for entry, committed in zip(index.split(b"\0"), tree.split(b"\0"), strict=True):
        if not entry and not committed:
            continue
        header, separator, name = committed.partition(b"\t")
        blob = re.fullmatch(rb"(100(?:644|755)) blob ([0-9a-f]+)", header)
        # H is an ordinary, visible cached entry. Sparse, assume-unchanged, unmerged,
        # symlink and gitlink entries cannot establish clean first use here.
        if not separator or not blob or entry != b"H " + blob[1] + b" " + blob[2] + b" 0\t" + name:
            return None
        relative = Path(os.fsdecode(name))
        if (relative.anchor or not relative.parts or (os.name == "nt" and (b":" in name or b"\\" in name))
                or any(part in ("..", ".") or part.casefold() == ".git" for part in relative.parts)):
            return None
        path = root / relative
        if path in paths:
            return None
        paths[path] = name
    return paths


def _observe_checkout(root: Path, tracked: dict[Path, bytes], observed: dict[Path, _Stamp | None]) -> list[bytes]:
    # The parent directories cover creations, removals and renames. Every untracked
    # entry must itself be ignored before we omit it (including whole ignored directories).
    directories = {root}
    for path in tracked:
        parent = path.parent
        while parent not in directories:
            directories.add(parent)
            parent = parent.parent
    if len(tracked) + len(directories) > _MAX_PROOF_PATHS:
        raise ValueError("checkout exceeds first-use inspection budget")
    extras = []
    for directory in sorted(directories, key=lambda path: len(path.parts)):
        info = observed[directory] = _stamp(directory)
        if info is None or not stat.S_ISDIR(info[2]):
            raise ValueError("missing or indirect tracked directory")
        for entry in directory.iterdir():
            if entry in directories or entry in tracked or entry == root / ".git":
                continue
            extras.append(os.fsencode(entry.relative_to(root).as_posix()))
            if len(extras) + len(tracked) + len(directories) > _MAX_PROOF_PATHS:
                raise ValueError("checkout exceeds first-use inspection budget")
            if entry.name in {".gitignore", ".gitattributes"}:
                # An ignored local rules file can still influence other entries.
                raise ValueError("untracked rules file")
    total_size = 0
    for path in tracked:
        info = observed[path] = _stamp(path)
        if info is None or not stat.S_ISREG(info[2]):
            raise ValueError("missing or indirect tracked file")
        total_size += info[3]
        if total_size > _MAX_COMPARISON_BYTES:
            raise ValueError("checkout exceeds first-use comparison budget")
    return extras


def _conversion_attributes(output: bytes) -> dict[tuple[bytes, bytes], bytes] | None:
    fields = output.split(b"\0")
    if fields.pop() or len(fields) % 3:
        return None
    conversions = {}
    for path, attribute, value in zip(fields[::3], fields[1::3], fields[2::3], strict=True):
        if attribute in {b"filter", b"ident", b"working-tree-encoding"}:
            return None
        if attribute in {b"text", b"eol", b"crlf"}:
            conversions[path, attribute] = value
    return conversions


def _initialize_private_git(private: Path, common: Path, oid: str) -> None:
    """Use caller objects as an alternate. Keep config, hooks, refs and indexes private."""
    (private / "refs").mkdir()
    objects_info = private / "objects" / "info"
    objects_info.mkdir(parents=True)
    (objects_info / "alternates").write_bytes(os.fsencode((common / "objects").as_posix()) + b"\n")
    (private / "HEAD").write_text(oid + "\n", encoding="ascii")
    config = "[core]\n\tbare = false\n"
    if len(oid) == 64:
        config += "\trepositoryFormatVersion = 1\n[extensions]\n\tobjectFormat = sha256\n"
    (private / "config").write_text(config, encoding="ascii")


def _inspect(root: Path, git_dir: Path, common: Path, branch: str, run: _Git, private: Path) -> CleanEvidence | None:
    index = git_dir / "index"
    sources = (
        index, common / "refs" / "heads" / branch, common / "packed-refs", common / "reftable" / "tables.list",
        common / "config", git_dir / "config.worktree", common / "info" / "attributes", common / "info" / "exclude",
        git_dir / "info" / "attributes", git_dir / "info" / "exclude",
    )
    observed = {path: _stamp(path) for path in sources}
    if not _copy_index(index, private / "index", observed[index]):
        return None
    tip = run(root, "rev-parse", "--verify", "HEAD", extra_env=READ_ONLY_ENV)
    oid = tip.stdout.strip()
    if tip.returncode or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", oid):
        return None
    _initialize_private_git(private, common, oid)
    env = {
        **READ_ONLY_ENV, "GIT_DIR": str(private), "GIT_WORK_TREE": str(root),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
    }
    # On POSIX, disabling fileMode can hide a local executable-bit change. Treat
    # that difference as indeterminate for first use even when the caller ignores it.
    options = ["-c", f"core.attributesFile={os.devnull}", "-c", f"core.fileMode={'false' if os.name == 'nt' else 'true'}"]
    settings = run(root, "config", "--null", "--type=bool-or-str", "--get-regexp", r"^core\.(autocrlf|eol)$",
                   extra_env=READ_ONLY_ENV, decode=False)
    if settings.returncode not in (0, 1):
        return None
    for setting in settings.stdout.split(b"\0"):
        if not setting:
            continue
        key, _, value = setting.partition(b"\n")
        value = value.lower()
        if value not in _BUILTIN_SETTINGS.get(key, set()):
            return None
        options.extend(["-c", (key + b"=" + value).decode("ascii")])

    def isolated(*args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
        return run(root, *options, *args, extra_env=env, input_bytes=data, decode=False)

    listing = isolated("ls-files", "--stage", "-v", "-z")
    if listing.returncode:
        # A split index has no shared index in our private Git directory.
        return None
    tree = isolated("ls-tree", "-r", "-z", "--full-tree", "HEAD")
    if tree.returncode:
        return None
    tracked = _tracked_files(listing.stdout, tree.stdout, root)
    if tracked is None:
        return None
    source_env = {**READ_ONLY_ENV, "GIT_INDEX_FILE": str(private / "index")}
    names = b"".join(name + b"\0" for name in tracked.values())
    attr_args = ("check-attr", "--all", "--stdin", "-z")
    actual = run(root, *attr_args, extra_env=source_env, input_bytes=names, decode=False)
    local = isolated(*attr_args, data=names)
    attrs = _conversion_attributes(actual.stdout)
    if actual.returncode or local.returncode or attrs is None or attrs != _conversion_attributes(local.stdout):
        return None
    extras = _observe_checkout(root, tracked, observed)
    while extras:
        # Prefix each pathname so a leading colon is a filename, not pathspec syntax.
        ignored = run(root, "check-ignore", "--no-index", "--stdin", "-z", extra_env=source_env,
                      input_bytes=b"".join(b"./" + name + b"\0" for name in extras), decode=False)
        if ignored.returncode not in (0, 1):
            return None
        remaining = set(extras).difference(name.removeprefix(b"./") for name in ignored.stdout.split(b"\0"))
        extras = []
        for name in remaining:
            if len(observed) >= _MAX_PROOF_PATHS:
                return None
            path = root / os.fsdecode(name)
            info = observed[path] = _stamp(path)
            if info is None or not stat.S_ISDIR(info[2]) or path.name.casefold() == ".git":
                return None
            # Git allows empty untracked directories. Watch them for later additions.
            for entry in path.iterdir():
                if entry.name in {".gitignore", ".gitattributes"} or len(observed) + len(extras) >= _MAX_PROOF_PATHS:
                    return None
                extras.append(os.fsencode(entry.relative_to(root).as_posix()))
    # A fresh private index omits the caller's cached stat data. Status must actually
    # compare the working files. Submodules were excluded above and are never entered.
    env["GIT_INDEX_FILE"] = str(private / "comparison-index")
    if isolated("read-tree", "HEAD").returncode:
        return None
    status = isolated("status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=no", "--ignore-submodules=all")
    evidence = CleanEvidence(tuple(observed.items()))
    return evidence if not status.returncode and not status.stdout and evidence.current() else None


def inspect(root: Path, git_dir: Path, common: Path, branch: str, run: _Git) -> CleanEvidence | None:
    """Return evidence only when Worklog can establish cleanliness without caller-provided programs."""
    try:
        with tempfile.TemporaryDirectory(prefix="worklog-attest-") as scratch:
            return _inspect(root, git_dir, common, branch, run, Path(scratch))
    except (AppError, OSError, ValueError):
        return None
