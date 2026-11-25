"""Read-only Antigravity transcript liveness probe tests."""

from __future__ import annotations

import json
import os
import time
import uuid

from worklog import antigravity_sessions as ag


def _write_transcript(brain_root, chat_id: str, entries: list[dict], *, age_seconds: float = 0.0):
    path = brain_root / chat_id / ".system_generated" / "logs" / "transcript.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    if age_seconds:
        ts = time.time() - age_seconds
        os.utime(path, (ts, ts))
    return path


def test_missing_and_invalid_chat_ids(tmp_path):
    assert ag.session_state("not-a-uuid", brain_root=tmp_path) == "missing"
    assert ag.session_state(str(uuid.uuid4()), brain_root=tmp_path) == "missing"
    assert ag.live_session_state(str(uuid.uuid4()), brain_root=tmp_path) is None


def test_working_idle_and_ended_transcripts(tmp_path):
    chat_working = str(uuid.uuid4())
    _write_transcript(tmp_path, chat_working, [{"role": "assistant", "status": "completed"}], age_seconds=30)
    assert ag.session_state(chat_working, brain_root=tmp_path) == "working"
    assert ag.live_session_state(chat_working, brain_root=tmp_path) == "working"

    chat_open_tool = str(uuid.uuid4())
    _write_transcript(tmp_path, chat_open_tool, [{"type": "tool_call", "status": "running"}], age_seconds=420)
    assert ag.session_state(chat_open_tool, brain_root=tmp_path) == "working"

    chat_idle = str(uuid.uuid4())
    _write_transcript(tmp_path, chat_idle, [{"role": "assistant", "status": "completed"}], age_seconds=600)
    assert ag.session_state(chat_idle, brain_root=tmp_path) == "idle"
    assert ag.live_session_state(chat_idle, brain_root=tmp_path) == "idle"

    chat_stale = str(uuid.uuid4())
    _write_transcript(tmp_path, chat_stale, [{"type": "tool_call", "status": "running"}], age_seconds=1200)
    assert ag.session_state(chat_stale, brain_root=tmp_path) == "idle"

    chat_ended = str(uuid.uuid4())
    _write_transcript(tmp_path, chat_ended, [{"type": "session_end", "status": "ended"}], age_seconds=10)
    assert ag.session_state(chat_ended, brain_root=tmp_path) == "ended"
    assert ag.live_session_state(chat_ended, brain_root=tmp_path) == "idle"
