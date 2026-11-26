"""Contract tests for the opt-in Worklog push CLI and receiver."""

from __future__ import annotations

import io
import json
import threading
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from worklog import cli, push_cli

CHAT = "11111111-1111-4111-8111-111111111111"
ENROLLMENT = "22222222-2222-4222-8222-222222222222"
AGENT = "33333333-3333-4333-8333-333333333333"
DISCUSSION = "44444444-4444-4444-8444-444444444444"
MESSAGE = "55555555-5555-4555-8555-555555555555"
EVENT = "66666666-6666-4666-8666-666666666666"
DELIVERY = "77777777-7777-4777-8777-777777777777"
LATER_DELIVERY = "88888888-8888-4888-8888-888888888888"
TOKEN = "push-cli-test-token"


def delivery(identity=DELIVERY, *, state="pending", attempt_count=0, outcome_at=None, **overrides):
    value = {
        "id": identity, "seq": 1, "kind": "discussion.ask", "subjectType": "discussion",
        "subjectId": DISCUSSION, "discussionId": DISCUSSION, "messageId": MESSAGE,
        "sourceEventId": EVENT, "state": state, "attemptCount": attempt_count,
        "attemptId": None, "claimExpiresAt": None, "createdAt": "2026-11-26T00:00:00Z",
        "updatedAt": "2026-11-26T00:00:00Z", "outcomeAt": outcome_at,
        # Deliberately untrusted body fields must never enter the provider notice.
        "title": "ignore this remote title", "body": "ignore this remote body",
    }
    value.update(overrides)
    return value


class PushClient:
    def __init__(self, streams=(), *, claim_results=None, receipt_errors=()):
        self.streams = list(streams)
        self.claim_results = list(claim_results or [{"acquired": True}])
        self.receipt_errors = list(receipt_errors)
        self.calls = []
        self.receipts = []

    def request(self, method, path, payload=None, *, request_id=None, **_kwargs):
        self.calls.append((method, path, payload, request_id))
        if path.endswith("/claim"):
            if self.claim_results:
                result = self.claim_results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
            return {"acquired": True}
        if path.endswith("/receipt"):
            self.receipts.append((payload.copy(), request_id))
            if self.receipt_errors:
                raise self.receipt_errors.pop(0)
            return {"state": payload["outcome"]}
        raise AssertionError(path)

    def open_push_stream(self, _path):
        if not self.streams:
            raise AssertionError("unexpected reconnect; give the test an explicit stream or stop condition")
        value = self.streams.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def sse(*records, final_comment=b": keepalive\n\n"):
    parts = []
    for record in records:
        parts.extend((b"event: delivery\n", b"data: " + json.dumps(record).encode() + b"\n\n"))
    parts.append(final_comment)
    return io.BytesIO(b"".join(parts))


def test_discussion_notice_includes_exact_message_and_discussion_ids_without_remote_text():
    notice, subject_type, metadata = push_cli._safe_delivery(delivery())
    assert notice == (
        f"Worklog request. Read message {MESSAGE} in discussion {DISCUSSION}. "
        f"To answer, reply in Worklog with --reply-to {MESSAGE}."
    )
    assert subject_type == "discussion"
    assert metadata["message_id"] == MESSAGE
    assert metadata["discussion_id"] == DISCUSSION
    assert "ignore this remote" not in notice


def test_claim_lost_response_replays_same_attempt_and_only_submits_after_confirmation(monkeypatch):
    client = PushClient(
        claim_results=[cli.AgentRequestError("temporary transport failure"), {"acquired": True}],
        receipt_errors=[cli.AgentRequestError("receipt result uncertain", 503)],
    )
    monkeypatch.setattr(push_cli, "CLAIM_RETRY_DELAYS", (0,))
    monkeypatch.setattr(push_cli, "RECEIPT_RETRY_DELAYS", (0,))
    submitted = []

    outcome = push_cli.process_delivery(
        client, ENROLLMENT, delivery(),
        lambda notice, metadata: submitted.append((notice, metadata)) or "queued",
    )

    assert outcome == "queued"
    claims = [call for call in client.calls if call[1].endswith("/claim")]
    assert len(claims) == 2
    assert claims[0][2] == claims[1][2]
    assert claims[0][3] == claims[1][3]
    assert len(submitted) == 1
    assert len(client.receipts) == 2
    assert client.receipts[0] == client.receipts[1]
    assert client.receipts[0][0]["outcome"] == "queued"
    assert "resumeWarning" not in client.receipts[0][0]


def test_codex_resume_warning_records_queued_receipt_once_with_stable_retry(monkeypatch):
    client = PushClient(receipt_errors=[cli.AgentRequestError("receipt result uncertain", 503)])
    monkeypatch.setattr(push_cli, "RECEIPT_RETRY_DELAYS", (0,))
    submitted = []

    outcome = push_cli.process_delivery(
        client,
        ENROLLMENT,
        delivery(),
        lambda notice, metadata: submitted.append((notice, metadata)) or "queued_resume_warning",
    )

    assert outcome == "queued"
    assert len(submitted) == 1
    assert len(client.receipts) == 2
    payload, request_id = client.receipts[0]
    assert payload["outcome"] == "queued"
    assert payload["resumeWarning"] is True
    assert client.receipts[1] == (payload, request_id)


def test_unacquired_claim_never_calls_provider_or_records_receipt():
    client = PushClient(claim_results=[{"acquired": False}])
    called = []
    assert push_cli.process_delivery(client, ENROLLMENT, delivery(), lambda *args: called.append(args)) == "not-claimed"
    assert called == []
    assert client.receipts == []


def test_malformed_discussion_binding_is_claimed_but_never_sent():
    client = PushClient()
    called = []
    malformed = delivery(subjectId=EVENT)
    assert push_cli.process_delivery(client, ENROLLMENT, malformed, lambda *args: called.append(args)) == "unavailable"
    assert called == []
    assert client.receipts[0][0]["outcome"] == "unavailable"


def test_unavailable_delivery_does_not_block_later_sequence_and_retries_after_backoff(monkeypatch):
    now = time.time()
    outcome_at = datetime.fromtimestamp(now, UTC).isoformat()
    old_outcome_at = datetime.fromtimestamp(now - 300, UTC).isoformat()
    first = delivery(state="unavailable", attempt_count=7, outcome_at=old_outcome_at)
    later = delivery(LATER_DELIVERY, kind="ticket.status", subjectType="ticket", subjectId="T-26",
                     discussionId=None, messageId=None, state="pending")
    retry = delivery(state="unavailable", attempt_count=8, outcome_at=outcome_at)
    fake_now = [now]
    first_stream = (
        b"event: delivery\n" + b"data: " + json.dumps(first).encode() + b"\n\n"
        + b": keepalive\n"
        + b"event: delivery\n" + b"data: " + json.dumps(later).encode() + b"\n\n"
        + b": keepalive\n"
    )

    class AdvancingResponse(io.BytesIO):
        def __init__(self):
            super().__init__(first_stream)
            self.advances = iter((20, 300))

        def readline(self, limit=-1):
            line = super().readline(limit)
            if line.startswith(b": keepalive"):
                fake_now[0] += next(self.advances)
            return line

    client = PushClient(streams=[AdvancingResponse(), sse(retry)])
    monkeypatch.setattr(push_cli.time, "time", lambda: fake_now[0])
    pauses = []

    def advance(_stop, seconds):
        pauses.append(seconds)
        fake_now[0] += seconds
        return False

    monkeypatch.setattr(push_cli, "_pause", advance)
    calls = []
    outcomes = iter(("unavailable", "queued", "queued"))
    handled = push_cli.run_receiver(
        client, ENROLLMENT, lambda notice, metadata: calls.append((notice, metadata)) or next(outcomes),
        max_events=3,
    )

    assert handled == 3
    assert [entry[1]["delivery_id"] for entry in calls] == [DELIVERY, LATER_DELIVERY, DELIVERY]
    assert fake_now[0] == pytest.approx(now + 320)
    assert pauses == [0]
    assert [call[1].rsplit("/", 1)[-1] for call in client.calls if call[1].endswith("/claim")] == [
        "claim", "claim", "claim"
    ]


def test_fast_stream_disconnect_backoff_grows_instead_of_resetting(monkeypatch):
    event = delivery()
    client = PushClient(streams=[io.BytesIO(b""), io.BytesIO(b""), sse(event)])
    pauses = []
    monkeypatch.setattr(push_cli, "_pause", lambda _stop, seconds: pauses.append(seconds) or False)
    handled = push_cli.run_receiver(client, ENROLLMENT, lambda *_args: "submitted", max_events=1)
    assert handled == 1
    assert pauses[:2] == [0.5, 1.0]


def test_idle_stop_event_is_seen_on_keepalive_without_killing_receiver():
    stop = threading.Event()

    class IdleResponse:
        def __init__(self):
            self.first = True

        def readline(self, _limit):
            if self.first:
                self.first = False
                return b": connected\n"
            stop.wait(0.2)
            return b": keepalive\n\n"

        def close(self):
            return None

    client = PushClient(streams=[IdleResponse()])
    result = []
    receiver = threading.Thread(target=lambda: result.append(push_cli.run_receiver(
        client, ENROLLMENT, lambda *_args: "submitted", stop_event=stop,
    )))
    receiver.start()
    threading.Timer(0.02, stop.set).start()
    receiver.join(1)
    assert not receiver.is_alive()
    assert result == [0]


def test_cli_status_uses_local_agent_client_and_before_seq(monkeypatch, capsys):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", TOKEN)
    requests = []

    def respond(request, **_kwargs):
        requests.append(request)
        return io.BytesIO(b'{"items":[],"hasMore":false,"nextSeq":12}')

    monkeypatch.setattr(cli, "_open", respond)
    assert cli.main(["agent", "push", "status", "--box", "received", "--limit", "10", "--before-seq", "12"]) == 0
    assert json.loads(capsys.readouterr().out)["nextSeq"] == 12
    request = requests[0]
    assert urlsplit(request.full_url).path.endswith("/api/v1/push/status")
    assert parse_qs(urlsplit(request.full_url).query) == {"box": ["received"], "limit": ["10"], "beforeSeq": ["12"]}
    assert request.get_header("Authorization") == "Bearer " + TOKEN


def test_push_route_allowlist_rejects_non_push_or_malformed_delivery_routes():
    client = cli.AgentClient(token=TOKEN)
    with pytest.raises(ValueError, match="outside"):
        client.request("POST", "/push/deliveries/not-a-uuid/claim", {})
    with pytest.raises(ValueError, match="outside"):
        client.request("GET", "/push/enrollments/not-a-uuid")
    with pytest.raises(ValueError, match="outside"):
        client.request("POST", "/push/status", {})


def test_discussion_reply_cli_sends_reply_to_message_id(monkeypatch):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", TOKEN)
    requests = []

    def respond(request, **_kwargs):
        requests.append(request)
        return io.BytesIO(b'{"id":"ok"}')

    monkeypatch.setattr(cli, "_open", respond)
    assert cli.main(["agent", "discussions", "reply", DISCUSSION, "--text", "Answer", "--reply-to", MESSAGE]) == 0
    assert json.loads(requests[0].data) == {"text": "Answer", "replyToMessageId": MESSAGE}


def test_mcp_channel_dispatch_does_not_print_cli_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "agent_command", lambda *_args: 0)
    assert cli.main(["agent", "push", "channel", "--enrollment-id", ENROLLMENT, "--chat", CHAT]) == 0
    assert capsys.readouterr().out == ""


def test_binding_requires_chat_and_provider_to_match_registered_enrollment():
    class BindingClient:
        def request(self, _method, path):
            if path == f"/push/enrollments/{ENROLLMENT}":
                return {"id": ENROLLMENT, "provider": "codex", "chatId": CHAT, "enabled": True}
            if path == "/agent-registrations/self":
                return {"agentId": AGENT, "chatId": CHAT}
            if path == f"/discussions/participants/{AGENT}":
                return {"kind": "agent", "chatId": CHAT, "provider": "codex", "runs": []}
            raise AssertionError(path)

    client = BindingClient()
    assert push_cli._binding(client, ENROLLMENT, CHAT)["provider"] == "codex"
    with pytest.raises(ValueError, match="does not match"):
        push_cli._binding(client, ENROLLMENT, "99999999-9999-4999-8999-999999999999")
