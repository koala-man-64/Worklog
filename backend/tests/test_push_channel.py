"""Claude MCP channel protocol tests using disposable subprocess fixtures."""

from __future__ import annotations

import json
import os
import subprocess
import sys

CHAT_ID = "123e4567-e89b-12d3-a456-426614174000"
ENROLLMENT_ID = "fixture-enrollment"
NOTICE = "Ticket TASK-123 changed status. Read the record in Worklog."
METADATA = {
    "delivery_id": "d_123",
    "kind": "ticket.status",
    "subject_type": "ticket",
    "subject_id": "TASK-123",
    "message_id": "m_456",
}


def _send(stream, payload):
    stream.write(json.dumps({"jsonrpc": "2.0", **payload}) + "\n")
    stream.flush()


def _read(stream, timeout=10):
    # MCP responses are line framed. Use a short-lived selector-free helper thread because
    # Windows anonymous pipes do not support select().
    import queue
    import threading

    result = queue.Queue(maxsize=1)

    def read_line():
        try:
            result.put(stream.readline())
        except BaseException as exc:  # propagate subprocess pipe errors to the assertion
            result.put(exc)

    thread = threading.Thread(target=read_line, daemon=True)
    thread.start()
    try:
        line = result.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError("MCP subprocess produced no line") from exc
    if isinstance(line, BaseException):
        raise line
    if not line:
        raise AssertionError("MCP subprocess closed stdout before the expected frame")
    return json.loads(line)


def test_stdio_channel_negotiates_and_delivers_fixture_event_from_real_subprocess():
    script = r"""
import os
import sys
from worklog.push_channel import run_stdio_channel

chat_id = os.environ["CLAUDE_CODE_SESSION_ID"]
notice = "Ticket TASK-123 changed status. Read the record in Worklog."
metadata = {"delivery_id": "d_123", "kind": "ticket.status", "subject_type": "ticket", "subject_id": "TASK-123", "message_id": "m_456"}

def receiver(submit_notice, stop_event):
    outcome = submit_notice(notice, metadata)
    print(outcome, file=sys.stderr, flush=True)
    while not stop_event.wait(0.01):
        pass

raise SystemExit(run_stdio_channel(receiver, enrollment_id="fixture-enrollment", chat_id=chat_id))
"""
    environment = {**os.environ, "CLAUDE_CODE_SESSION_ID": CHAT_ID}
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        _send(
            process.stdin,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2026-07-28",
                    "capabilities": {},
                    "clientInfo": {"name": "fixture-client", "version": "1"},
                },
            },
        )
        initialize = _read(process.stdout)
        assert initialize["result"]["protocolVersion"] == "2025-11-25"
        assert initialize["result"]["capabilities"]["experimental"] == {"claude/channel": {}}
        assert "normal permissions" in initialize["result"]["instructions"]

        _send(process.stdin, {"method": "notifications/initialized"})
        notice = _read(process.stdout)
        assert notice == {
            "jsonrpc": "2.0",
            "method": "notifications/claude/channel",
            "params": {"content": NOTICE, "meta": METADATA},
        }

        process.stdin.close()
        assert process.wait(timeout=5) == 0
        assert process.stderr is not None
        assert process.stderr.read().splitlines()[-1] == "submitted"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()


def test_channel_rejects_wrong_session_before_mcp_output(monkeypatch, capsys):
    from io import BytesIO

    from worklog.push_channel import run_stdio_channel

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "123e4567-e89b-12d3-a456-426614174001")
    output = BytesIO()
    result = run_stdio_channel(
        lambda submit, stop: None,
        enrollment_id=ENROLLMENT_ID,
        chat_id=CHAT_ID,
        stdin=BytesIO(),
        stdout=output,
    )

    assert result == 1
    assert output.getvalue() == b""
    assert "different Claude session" in capsys.readouterr().err


def test_invalid_event_content_cannot_be_sent_as_a_channel_notification():

    from worklog.push_channel import _safe_notice

    assert _safe_notice("arbitrary user text", METADATA) is None
    assert _safe_notice(NOTICE, {**METADATA, "body": "private"}) is None
    assert _safe_notice(NOTICE, {**METADATA, "subject_type": "command"}) is None


def test_submit_notice_rejects_metadata_values_outside_the_fixed_identifier_alphabet():
    from worklog.push_channel import _safe_notice

    assert _safe_notice(NOTICE, {**METADATA, "message_id": "bad\nvalue"}) is None


def test_discussion_events_use_the_same_fixed_notice_shape():
    from worklog.push_channel import _safe_notice

    discussion_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    message_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    metadata = {
        **METADATA,
        "kind": "discussion.reply",
        "subject_type": "discussion",
        "subject_id": discussion_id,
        "discussion_id": discussion_id,
        "message_id": message_id,
    }
    notice = f"Worklog reply. Read message {message_id} in discussion {discussion_id}."
    assert _safe_notice(notice, metadata) == (notice, metadata)
