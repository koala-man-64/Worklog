"""The Claude Code sessions running on this machine, from the record Claude Code keeps of each.

While a session runs, Claude Code keeps a small JSON file for it in the sessions folder of its
configuration directory (CLAUDE_CONFIG_DIR, or ~/.claude), named after the session's process ID.
It holds the session's ID, the Claude desktop session that hosts it, its folder, its name, and
whether it is busy, idle or waiting on the person. A process that ends without tidying up leaves
its file behind, and process IDs are reused, so a file counts only while a process with its ID
runs and started when the file says it did. Worklog only reads these files, and never signals,
opens for anything but that check, or attaches to the processes they name.
"""

from __future__ import annotations

import ctypes
import functools
import json
import os
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .transcripts import NAME_LIMIT, UUID, one_line, string

RECORD = re.compile(r"[1-9][0-9]{0,9}\.json")
HOST = re.compile(rf"local_{UUID}")
RECORD_BYTES = 64 * 1024  # a larger file is not a session record
RECORD_LIMIT = 1_000  # the newest records read; a fuller folder holds mostly leftovers
PATH_LIMIT = 2_000  # characters of a working folder kept
PID_LIMIT = 2**31 - 1  # the largest process ID every system takes; a larger one is no process
# Windows counts a process's start in 100 ns ticks since 1601; this many of them come before 1970.
EPOCH_TICKS = 116_444_736_000_000_000
# A session starts after its process does; this much clock difference between the two is allowed.
START_SLACK_MS = 2_000
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# What Claude Code says a session is doing, as Chats says it. Anything else reads as idle.
STATES = {"busy": "working", "waiting": "waiting", "idle": "idle"}
ORDER = {"waiting": 0, "working": 1, "idle": 2}


@functools.cache
def _kernel() -> Any:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    return kernel


def process_start(pid: int) -> int | None:
    """When the running process with this ID started, or None when none of this account's runs.

    On Windows this is its creation time in 100 ns ticks since 1601, which Claude Code records as
    procStart. Elsewhere a running process gives 0, for a start that is not compared."""
    if not 0 < pid <= PID_LIMIT:
        return None
    if os.name != "nt":
        try:
            # Signal 0 only asks whether the process exists. Never on Windows, where os.kill ends it.
            os.kill(pid, 0)
        except (OSError, OverflowError):  # gone, another account's, or no process ID here
            return None
        return 0
    from ctypes import wintypes

    kernel = _kernel()
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        # A handle still held elsewhere opens a process that has already exited.
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return None
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(system), ctypes.byref(user)):
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def _millis(value: Any) -> int | None:
    """An epoch time in milliseconds, as Claude Code writes them, when it is one."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 10**14:
        return None
    return value


def _iso(millis: int | None) -> str | None:
    if millis is None:
        return None
    return datetime.fromtimestamp(millis / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ticks(value: Any) -> int | None:
    """A recorded process start: digits, as Claude Code writes it, or a whole number."""
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,24}", value.strip()):
        return int(value.strip())
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def read_record(path: Path) -> dict | None:
    """One session record, or None for a file that is not one."""
    try:
        if not RECORD.fullmatch(path.name) or path.stat().st_size > RECORD_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    pid, session = data.get("pid"), string(data.get("sessionId")).strip().lower()
    # The file is named after the session's process; one whose name and content disagree is not trusted.
    if (isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= PID_LIMIT or path.stem != str(pid)
            or not re.fullmatch(UUID, session)):
        return None
    # Process IDs mean something only on the system that gave them, such as Windows rather than
    # WSL sharing the folder. A record names its system, as win32:msi does, when it was written there.
    domain = string(data.get("pidDomain")).strip().lower()
    if domain and os.name == "nt" and not domain.startswith("win32"):
        return None
    host = string(data.get("hostSessionId")).strip().lower()
    folder = string(data.get("cwd"))
    if len(folder) > PATH_LIMIT or any(ord(ch) < 32 for ch in folder):
        folder = ""
    started = _millis(data.get("startedAt"))
    return {
        "pid": pid, "procStart": _ticks(data.get("procStart")), "startedMs": started,
        "updatedMs": _millis(data.get("statusUpdatedAt")) or _millis(data.get("updatedAt")) or started,
        "sessionId": session, "hostSessionId": host if HOST.fullmatch(host) else None,
        "name": one_line(data.get("name"), NAME_LIMIT), "cwd": folder,
        "entrypoint": one_line(data.get("entrypoint"), 60), "kind": one_line(data.get("kind"), 60),
        "status": STATES.get(string(data.get("status")), "idle"),
        "waitingFor": one_line(data.get("waitingFor"), NAME_LIMIT) if data.get("status") == "waiting" else "",
        "version": one_line(data.get("version"), 60),
    }


def running(record: dict, start: Callable[[int], int | None] | None = None) -> bool:
    """Whether the record's process still runs and is the one that wrote it: its start matches
    the recorded one, or, with none recorded, it is no younger than the session."""
    started = (start or process_start)(record["pid"])
    if started is None:
        return False
    if not started:
        return True  # a start this platform does not give is not compared
    if record["procStart"] is not None:
        return started == record["procStart"]
    if record["startedMs"] is not None:
        return (started - EPOCH_TICKS) // 10_000 <= record["startedMs"] + START_SLACK_MS
    return True


def running_sessions(folders: Iterable[Path], start: Callable[[int], int | None] | None = None) -> list[dict]:
    """The sessions running now, those waiting on the person first, then working, then idle,
    each the most recently changed first. One session recorded twice counts once."""
    paths: list[tuple[float, Path]] = []
    for folder in folders:
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if RECORD.fullmatch(entry.name):
                        try:
                            paths.append((entry.stat().st_mtime, Path(entry.path)))
                        except OSError:
                            continue
        except OSError:
            continue
    found: dict[str, dict] = {}
    for _, path in sorted(paths, reverse=True)[:RECORD_LIMIT]:
        record = read_record(path)
        if record is None or not running(record, start):
            continue
        known = found.get(record["sessionId"])
        if known is None or (record["updatedMs"] or 0) > (known["updatedMs"] or 0):
            found[record["sessionId"]] = record
    sessions = sorted(found.values(), key=lambda r: -(r["updatedMs"] or 0))
    sessions.sort(key=lambda r: ORDER[r["status"]])
    return [{"sessionId": r["sessionId"], "hostSessionId": r["hostSessionId"], "name": r["name"], "cwd": r["cwd"],
             "entrypoint": r["entrypoint"], "kind": r["kind"], "status": r["status"], "waitingFor": r["waitingFor"],
             "startedAt": _iso(r["startedMs"]), "statusAt": _iso(r["updatedMs"]), "version": r["version"]}
            for r in sessions]
