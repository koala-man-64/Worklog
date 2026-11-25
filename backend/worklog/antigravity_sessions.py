"""Read-only session liveness probe for local Antigravity conversations.

Antigravity stores each conversation's compact transcript at
`~/.gemini/antigravity/brain/<chat_id>/.system_generated/logs/transcript.jsonl`.
Worklog only reads tail bytes and modification time, never modifying any file.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Literal

from .transcripts import UUID

_UUID_RE = re.compile(rf"^{UUID}$")
_TAIL_BYTES = 32 * 1024
WORKING_WINDOW_S = 15 * 60
IDLE_AFTER_S = 5 * 60
SessionLiveness = Literal["working", "idle", "ended", "missing"]


def default_brain_root() -> Path:
    """Default Antigravity brain root on this machine (~/.gemini/antigravity/brain)."""
    env_override = os.environ.get("ANTIGRAVITY_BRAIN_DIR")
    if env_override:
        return Path(env_override)
    return Path.home() / ".gemini" / "antigravity" / "brain"


def transcript_path(chat_id: str, brain_root: Path | None = None) -> Path | None:
    """Return the validated transcript.jsonl path for a UUID chat_id, or None if invalid."""
    cleaned = chat_id.strip().lower()
    if not _UUID_RE.fullmatch(cleaned):
        return None
    root = brain_root if brain_root is not None else default_brain_root()
    return root / cleaned / ".system_generated" / "logs" / "transcript.jsonl"


def _tail_lines(path: Path, max_bytes: int = _TAIL_BYTES) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size > max_bytes:
                stream.seek(size - max_bytes)
                raw = stream.read(max_bytes)
                # Drop potentially truncated leading partial line.
                _, _, raw = raw.partition(b"\n")
            else:
                raw = stream.read()
        return [line for line in raw.decode("utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return []


def _tail_status(lines: list[str]) -> Literal["working", "ended", "idle"]:
    """Inspect the last few JSONL entries to see if a turn is open or the session explicitly ended."""
    for raw in reversed(lines[-12:]):
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or entry.get("state") or "").lower()
        kind = str(entry.get("type") or entry.get("kind") or entry.get("role") or "").lower()
        if status in {"ended", "closed", "archived", "terminated", "deleted"} or kind in {"session_end", "conversation_end"}:
            return "ended"
        if status in {"running", "in_progress", "pending", "working"}:
            return "working"
        if kind in {"tool_call", "function_call", "user"}:
            return "working"
        if status in {"done", "completed", "finished", "idle", "error", "failed"}:
            return "idle"
        if kind in {"assistant", "model", "turn_end", "final"} and not entry.get("tool_calls"):
            return "idle"
    return "idle"


def session_state(
    chat_id: str,
    *,
    brain_root: Path | None = None,
    now_epoch: float | None = None,
) -> SessionLiveness:
    """Classify an Antigravity chat as 'working', 'idle', 'ended', or 'missing'.

    - 'missing': chat_id is not a valid UUID or no transcript.jsonl exists under brain_root.
    - 'ended': transcript tail records an explicit session termination marker.
    - 'working': transcript was modified within WORKING_WINDOW_S (15m) and either modified within
      IDLE_AFTER_S (5m) or has an open turn/tool call in its tail.
    - 'idle': transcript exists, last step finished, and mtime is older than IDLE_AFTER_S.
    """
    path = transcript_path(chat_id, brain_root=brain_root)
    if path is None:
        return "missing"
    try:
        st = path.stat()
    except OSError:
        return "missing"
    if not path.is_file():
        return "missing"
    lines = _tail_lines(path)
    tail = _tail_status(lines)
    if tail == "ended":
        return "ended"
    current = time.time() if now_epoch is None else now_epoch
    age = max(0.0, current - st.st_mtime)
    if age > WORKING_WINDOW_S:
        return "idle"
    if tail == "working" or age <= IDLE_AFTER_S:
        return "working"
    return "idle"


def live_session_state(
    chat_id: str,
    *,
    brain_root: Path | None = None,
    now_epoch: float | None = None,
) -> Literal["working", "idle"] | None:
    """Map session_state to LiveAgentNode.sessionState ('working', 'idle', or None)."""
    state = session_state(chat_id, brain_root=brain_root, now_epoch=now_epoch)
    if state == "missing":
        return None
    return "working" if state == "working" else "idle"
