"""Submit fixed Worklog notices through installed provider CLIs.

This module deliberately accepts provider session IDs separately from the notice text. It never
parses destinations or commands out of a message body, and never exposes provider output.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from typing import Any

from .session_launches import (
    _child_environment as _launch_child_environment,
)
from .session_launches import (
    _codex_executable as _launch_codex_executable,
)

TIMEOUT_SECONDS = 10
_RESUME_WARNING = b"Warning: message was queued, but could not resume "
_SESSION_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _session_id(value: str) -> str | None:
    if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None


def _codex_executable() -> list[str] | None:
    """Reuse the provider launcher's shell-free executable resolution."""
    try:
        return _launch_codex_executable()
    except FileNotFoundError:
        return None


def _child_environment() -> dict[str, str]:
    """Reuse the provider launcher's least-privilege Codex environment."""
    return _launch_child_environment("codex")


def _run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "timeout": TIMEOUT_SECONDS,
        "check": False,
        "shell": False,
        "close_fds": True,
        "env": _child_environment(),
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(command, **options)


def _submit_codex(chat_id: str, notice: str, *, resume: bool) -> str:
    command = _codex_executable()
    if command is None:
        return "unavailable"
    args = [*command, "queue", "--thread", chat_id, "--message", notice]
    if resume:
        args.append("--resume")
    try:
        completed = _run(args)
    except subprocess.TimeoutExpired:
        # The CLI may have queued the message before it stopped responding.
        return "uncertain"
    except OSError:
        # A process-creation failure happens before Codex can accept the message.
        return "unavailable"
    # A zero exit means the CLI accepted the queue request, not that the thread read it. The
    # optional resume can then emit this fixed warning and still exit 0; classify that separately
    # without retaining or displaying any raw provider output.
    if completed.returncode == 0:
        # Codex CLI 0.167 names the thread ("could not resume thread ID: reason"), for example when
        # no local app-server daemon is running. Keep matching the form without "thread " as well.
        thread = chat_id.encode("ascii")
        warnings = (_RESUME_WARNING + b"thread " + thread + b":", _RESUME_WARNING + thread + b":")
        if resume and any(line.startswith(warnings) for line in completed.stderr.splitlines()):
            return "queued_resume_warning"
        return "queued"
    # For a non-zero exit, avoid claiming it was not delivered: provider acceptance may have raced.
    return "uncertain"


def submit_notice(provider: str, chat_id: str, notice: str, *, resume: bool = False) -> str:
    """Submit one fixed notice; return only the delivery state defined by the provider adapter."""
    normalized = _session_id(chat_id)
    if normalized is None or not isinstance(notice, str) or not notice or len(notice) > 4_000:
        return "unavailable"
    if any(ord(char) < 32 or ord(char) == 127 for char in notice):
        return "unavailable"
    if provider == "codex":
        return _submit_codex(normalized, notice, resume=resume)
    # Claude delivery is handled by the session-local MCP channel server, not by a
    # cross-session sender. Do not imply this Codex CLI adapter can target Claude.
    return "unavailable"
