"""Worklog's opt-in, provider-bound push receiver commands."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from worklog.common import AgentRequestError
from worklog.push_notice import notice_text

PROVIDERS = frozenset({"codex", "claude"})
SUBJECT_TYPES = frozenset({"discussion", "ticket", "item", "run"})
SAFE_VALUE = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
RECEIPT_RETRY_DELAYS = (0.25, 0.5, 1, 2, 4, 8)
CLAIM_RETRY_DELAYS = (0.25, 0.5, 1, 2)
UNAVAILABLE_RETRY_BASE_SECONDS = 2
UNAVAILABLE_RETRY_MAX_SECONDS = 300
STREAM_RETRY_MAX_SECONDS = 30
STREAM_STABLE_SECONDS = 20
STREAM_HEARTBEAT_SECONDS = 20
SSE_LINE_LIMIT = 16_384
SSE_EVENT_LIMIT = 65_536


def _uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value)) if str(uuid.UUID(value)) == value.lower() else None
    except ValueError:
        return None


def _known_provider(client: Any, agent_id: str, chat_id: str) -> str | None:
    """Return a provider only when Worklog reports it for the registered chat."""
    if _uuid(agent_id) is None:
        return None
    profile = client.request("GET", f"/discussions/participants/{agent_id}")
    if not isinstance(profile, dict) or profile.get("kind") != "agent":
        return None
    run_providers = {
        run.get("provider", "").lower()
        for run in profile.get("runs", [])
        if isinstance(run, dict) and run.get("chatId") == chat_id
        and isinstance(run.get("provider"), str) and run.get("provider", "").lower() in PROVIDERS
    }
    if run_providers:
        if len(run_providers) != 1:
            raise ValueError("Worklog reports conflicting providers for this chat")
        return next(iter(run_providers))
    reported = profile.get("provider", "")
    if profile.get("chatId") == chat_id and isinstance(reported, str) and reported.lower() in PROVIDERS:
        return reported.lower()
    return None


def _verify_registered_chat(client: Any, chat_id: str, expected_provider: str | None = None) -> dict[str, Any]:
    if not isinstance(chat_id, str) or not chat_id or len(chat_id) > 500:
        raise ValueError("Give the exact chat ID registered to this Worklog agent")
    registration = client.request("GET", "/agent-registrations/self")
    if not isinstance(registration, dict) or registration.get("chatId") != chat_id:
        raise ValueError("The requested chat does not match this agent's Worklog registration")
    provider = _known_provider(client, registration.get("agentId", ""), chat_id)
    if provider is not None and expected_provider is not None and provider != expected_provider:
        raise ValueError("The requested provider does not match Worklog's session metadata")
    return {"registration": registration, "provider": provider}


def _binding(client: Any, enrollment_id: str, chat_id: str, *, allow_disabled: bool = False) -> dict[str, Any]:
    normalized = _uuid(enrollment_id)
    if normalized is None:
        raise ValueError("Enrollment ID must be a UUID")
    enrollment = client.request("GET", f"/push/enrollments/{normalized}")
    if not isinstance(enrollment, dict) or enrollment.get("id") != normalized:
        raise ValueError("Worklog returned an invalid push enrollment")
    if enrollment.get("provider") not in PROVIDERS or not isinstance(enrollment.get("chatId"), str):
        raise ValueError("Worklog returned an invalid push binding")
    if enrollment.get("chatId") != chat_id:
        raise ValueError("--chat does not match this push enrollment")
    if not allow_disabled and enrollment.get("enabled") is not True:
        raise ValueError("This Worklog push enrollment is disabled; enroll or enable it again")
    _verify_registered_chat(client, chat_id, enrollment["provider"])
    return enrollment


def _subscription(client: Any, args: Any, enabled: bool) -> Any:
    enrollment_id = _uuid(args.enrollment_id)
    if enrollment_id is None:
        raise ValueError("Enrollment ID must be a UUID")
    enrollment = client.request("GET", f"/push/enrollments/{enrollment_id}")
    if not isinstance(enrollment, dict) or enrollment.get("id") != enrollment_id:
        raise ValueError("Worklog returned an invalid push enrollment")
    if enrollment.get("enabled") is not True:
        raise ValueError("This Worklog push enrollment is disabled")
    _verify_registered_chat(client, enrollment.get("chatId", ""), enrollment.get("provider"))
    return client.request(
        "POST", "/push/subscriptions",
        {"enrollmentId": enrollment_id, "kind": args.kind, "ref": args.ref, "enabled": enabled},
        request_id=args.request_id,
    )


def _safe_delivery(delivery: Any) -> tuple[str, str, dict[str, str]] | None:
    if not isinstance(delivery, dict):
        return None
    delivery_id = _uuid(delivery.get("id"))
    source_kind = delivery.get("kind")
    subject_type = delivery.get("subjectType")
    subject_id = delivery.get("subjectId")
    if (delivery_id is None or not isinstance(source_kind, str) or SAFE_VALUE.fullmatch(source_kind) is None
            or not isinstance(subject_type, str) or subject_type not in SUBJECT_TYPES or not isinstance(subject_id, str)
            or SAFE_VALUE.fullmatch(subject_id) is None):
        return None
    metadata = {"delivery_id": delivery_id, "kind": source_kind,
                "subject_type": subject_type, "subject_id": subject_id}
    for field, key in (("discussionId", "discussion_id"), ("messageId", "message_id"),
                       ("sourceEventId", "source_event_id")):
        value = delivery.get(field)
        if value is None:
            continue
        normalized = _uuid(value)
        if normalized is None:
            return None
        metadata[key] = normalized
    notice = notice_text(metadata)
    return (notice, subject_type, metadata) if notice else None


def _denied(exc: AgentRequestError) -> bool:
    return exc.status in {401, 403} or exc.code in {"authentication", "credential_revoked", "push_enrollment"}


def _fatal_client_error(exc: AgentRequestError) -> bool:
    return _denied(exc) or (exc.status is not None and 400 <= exc.status < 500 and exc.status != 429)


def _pause(stop_event: threading.Event | None, seconds: float) -> bool:
    if stop_event is None:
        time.sleep(seconds)
        return False
    return stop_event.wait(seconds)


def _claim(client: Any, enrollment_id: str, delivery_id: str, attempt_id: str) -> Any:
    request_id = "worklog-push-claim-" + attempt_id
    payload = {"enrollmentId": enrollment_id, "attemptId": attempt_id}
    for index in range(len(CLAIM_RETRY_DELAYS) + 1):
        try:
            return client.request("POST", f"/push/deliveries/{delivery_id}/claim", payload,
                                  request_id=request_id)
        except AgentRequestError as exc:
            if _fatal_client_error(exc) or index == len(CLAIM_RETRY_DELAYS):
                raise
            # A lost response may hide a successful claim. Replay only the same claim, key and
            # attempt ID; never dispatch the provider until the server confirms acquisition.
            time.sleep(CLAIM_RETRY_DELAYS[index])
    raise AssertionError("unreachable")


def _receipt(client: Any, enrollment_id: str, delivery_id: str, attempt_id: str, outcome: str,
             *, resume_warning: bool = False,
             stop_event: threading.Event | None = None) -> Any:
    request_id = "worklog-push-receipt-" + attempt_id
    payload: dict[str, Any] = {"enrollmentId": enrollment_id, "attemptId": attempt_id, "outcome": outcome}
    if resume_warning:
        payload["resumeWarning"] = True
    for index in range(len(RECEIPT_RETRY_DELAYS) + 1):
        try:
            return client.request("POST", f"/push/deliveries/{delivery_id}/receipt", payload,
                                  request_id=request_id)
        except AgentRequestError as exc:
            if _fatal_client_error(exc) or index == len(RECEIPT_RETRY_DELAYS):
                raise
            if _pause(stop_event, RECEIPT_RETRY_DELAYS[index]):
                # The provider side effect already happened; finish recording its receipt even when
                # the channel's input closed, without ever invoking the provider a second time.
                stop_event = None
    raise AssertionError("unreachable")


def process_delivery(client: Any, enrollment_id: str, delivery: Any,
                     submit_notice: Callable[[str, dict[str, str]], str], *,
                     stop_event: threading.Event | None = None) -> str:
    """Claim, perform at most one provider call, and durably record that call's outcome."""
    if not isinstance(delivery, dict):
        raise ValueError("Worklog sent an invalid push delivery")
    delivery_id = _uuid(delivery.get("id"))
    if delivery_id is None:
        raise ValueError("Worklog sent an invalid push delivery ID")
    attempt_id = str(uuid.uuid4())
    claim = _claim(client, enrollment_id, delivery_id, attempt_id)
    if not isinstance(claim, dict) or claim.get("acquired") is not True:
        return "not-claimed"
    safe = _safe_delivery(delivery)
    resume_warning = False
    if safe is None:
        outcome = "unavailable"
    else:
        notice, _subject_type, metadata = safe
        try:
            result = submit_notice(notice, metadata)
        except Exception:
            result = "uncertain"
        resume_warning = result == "queued_resume_warning"
        if resume_warning:
            outcome = "queued"
        else:
            outcome = result if result in {"submitted", "queued", "unavailable", "uncertain"} else "uncertain"
    _receipt(client, enrollment_id, delivery_id, attempt_id, outcome,
             resume_warning=resume_warning, stop_event=stop_event)
    return outcome


def _sse_events(response: Any):
    event_name = "message"
    data: list[str] = []
    byte_count = 0
    while True:
        raw = response.readline(SSE_LINE_LIMIT + 1)
        if not raw:
            if data:
                yield event_name, "\n".join(data)
            return
        if len(raw) > SSE_LINE_LIMIT:
            raise ValueError("Worklog push event line is too long")
        try:
            line = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError:
            raise ValueError("Worklog push event is not UTF-8") from None
        if not line:
            if data:
                yield event_name, "\n".join(data)
            event_name, data, byte_count = "message", [], 0
            continue
        if line.startswith(":"):
            yield "comment", line[1:].strip()
            continue
        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        elif value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            byte_count += len(value.encode("utf-8"))
            if byte_count > SSE_EVENT_LIMIT:
                raise ValueError("Worklog push event is too large")
            data.append(value)


def _unavailable_delay(attempt_count: Any) -> float:
    attempts = attempt_count if isinstance(attempt_count, int) and not isinstance(attempt_count, bool) else 1
    attempts = max(1, attempts)
    return min(UNAVAILABLE_RETRY_BASE_SECONDS * (2 ** min(attempts - 1, 20)), UNAVAILABLE_RETRY_MAX_SECONDS)


def _unavailable_due(delivery: Any) -> float | None:
    if not isinstance(delivery, dict) or delivery.get("state") != "unavailable":
        return None
    outcome_at = delivery.get("outcomeAt")
    if not isinstance(outcome_at, str):
        return None
    try:
        moment = datetime.fromisoformat(outcome_at.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.timestamp() + _unavailable_delay(delivery.get("attemptCount"))
    except ValueError:
        return None


def run_receiver(client: Any, enrollment_id: str,
                 submit_notice: Callable[[str, dict[str, str]], str], *,
                 stop_event: threading.Event | None = None,
                 max_events: int | None = None) -> int:
    """Keep SSE alive; idle stop signals are checked at Worklog's ~20-second heartbeat."""
    if _uuid(enrollment_id) is None:
        raise ValueError("Enrollment ID must be a UUID")
    if max_events is not None and max_events < 1:
        raise ValueError("max_events must be positive")
    completed = 0
    delay = 0.5
    deferred: dict[str, float] = {}
    while stop_event is None or not stop_event.is_set():
        response = None
        opened_at = time.monotonic()
        reconnect_due: float | None = None
        try:
            response = client.open_push_stream("/push/stream?" + urlencode({"enrollmentId": enrollment_id}))
            for event_name, event_data in _sse_events(response):
                if stop_event is not None and stop_event.is_set():
                    return completed
                if event_name == "comment":
                    # Worklog sends a keepalive after draining all currently pending pages. Closing
                    # here lets us retry unavailable deliveries without starving later sequence IDs.
                    if event_data == "keepalive":
                        now = time.time()
                        if deferred:
                            next_due = min(deferred.values())
                            if next_due <= now:
                                deferred = {identity: due for identity, due in deferred.items() if due > now}
                                reconnect_due = now
                                break
                            # Keep the stream open during a longer retry backoff so newer delivery
                            # rows can still pass while this one waits.
                    continue
                if event_name == "closed":
                    return completed
                if event_name != "delivery":
                    continue
                try:
                    delivery = json.loads(event_data)
                except (json.JSONDecodeError, TypeError):
                    raise ValueError("Worklog sent an invalid push event") from None
                delivery_id = _uuid(delivery.get("id")) if isinstance(delivery, dict) else None
                if delivery_id is None:
                    raise ValueError("Worklog sent an invalid push delivery ID")
                if delivery.get("state") == "unavailable":
                    server_due = _unavailable_due(delivery)
                    due = max(server_due or 0, deferred.get(delivery_id, 0)) or None
                    if due is not None and due > time.time():
                        deferred[delivery_id] = due
                        continue
                deferred.pop(delivery_id, None)
                try:
                    outcome = process_delivery(client, enrollment_id, delivery, submit_notice, stop_event=stop_event)
                except AgentRequestError as exc:
                    if _fatal_client_error(exc):
                        if _denied(exc):
                            raise ValueError("Worklog denied this push receiver; stop and refresh the agent credential or enrollment") from None
                        raise ValueError("Worklog rejected this push delivery operation") from None
                    # Claims are never followed by a provider call unless acquisition was confirmed.
                    # Reconnect to ask the server for whatever remains safe to attempt.
                    break
                else:
                    # A definitive pre-submit "unavailable" is safe to retry. Defer just that
                    # delivery and continue through the SSE page so it cannot block later work.
                    if outcome in {"unavailable", "not-claimed"}:
                        count = delivery.get("attemptCount", 0)
                        if not isinstance(count, int) or isinstance(count, bool):
                            count = 0
                        deferred[delivery_id] = time.time() + _unavailable_delay(count + 1)
                completed += 1
                if max_events is not None and completed >= max_events:
                    return completed
            else:
                # A clean EOF or transport close asks us to reconnect.
                pass
        except AgentRequestError as exc:
            if _fatal_client_error(exc):
                if _denied(exc):
                    raise ValueError("Worklog denied this push receiver; stop and refresh the agent credential or enrollment") from None
                raise ValueError("Worklog rejected this push receiver") from None
        except (OSError, TimeoutError):
            pass
        finally:
            if response is not None:
                try:
                    response.close()
                except OSError:
                    pass
            if time.monotonic() - opened_at >= STREAM_STABLE_SECONDS:
                delay = 0.5
        if stop_event is not None and stop_event.is_set():
            break
        if reconnect_due is not None:
            if _pause(stop_event, max(0, reconnect_due - time.time())):
                break
            delay = 0.5
            continue
        if _pause(stop_event, delay):
            break
        delay = min(delay * 2, STREAM_RETRY_MAX_SECONDS)
    return completed


def command(client: Any, args: Any) -> Any:
    action = args.push_action
    if action == "enroll":
        verification = _verify_registered_chat(client, args.chat, args.provider)
        provider = args.provider or verification["provider"]
        if provider not in PROVIDERS:
            raise ValueError("Give --provider codex or claude; Worklog has no provider metadata for this chat")
        return client.request("POST", "/push/enroll", {"provider": provider, "chatId": args.chat},
                              request_id=args.request_id)
    if action == "disable":
        enrollment_id = _uuid(args.enrollment_id)
        if enrollment_id is None:
            raise ValueError("Enrollment ID must be a UUID")
        enrollment = client.request("GET", f"/push/enrollments/{enrollment_id}")
        if not isinstance(enrollment, dict) or not isinstance(enrollment.get("chatId"), str):
            raise ValueError("Worklog returned an invalid push enrollment")
        _verify_registered_chat(client, enrollment["chatId"], enrollment.get("provider"))
        return client.request("POST", "/push/disable", {"enrollmentId": enrollment_id},
                              request_id=args.request_id)
    if action in {"watch", "unwatch"}:
        return _subscription(client, args, action == "watch")
    if action == "status":
        if not 1 <= args.limit <= 200:
            raise ValueError("--limit must be between 1 and 200")
        if args.before_seq is not None and args.before_seq < 1:
            raise ValueError("--before-seq must be positive")
        query = {"box": args.box, "limit": args.limit}
        if args.before_seq is not None:
            query["beforeSeq"] = args.before_seq
        return client.request("GET", "/push/status?" + urlencode(query))
    if action == "connect":
        enrollment = _binding(client, args.enrollment_id, args.chat)
        if enrollment["provider"] != "codex":
            raise ValueError("Use `push channel` inside Claude Code; `push connect` runs the Codex receiver in this terminal")
        from worklog.push_providers import submit_notice

        try:
            run_receiver(client, enrollment["id"],
                         lambda notice, _metadata: submit_notice("codex", args.chat, notice, resume=args.resume),
                         )
        except KeyboardInterrupt:
            return {"connected": False, "stopped": True}
        return {"connected": False, "stopped": True}
    if action == "channel":
        enrollment = _binding(client, args.enrollment_id, args.chat)
        if enrollment["provider"] != "claude":
            raise ValueError("Claude Code push channel requires a Claude enrollment")
        from worklog.transcripts import TranscriptReader, TranscriptRoots

        if args.chat.lower().startswith("local_"):
            located = TranscriptReader(TranscriptRoots.from_environment()).locate(args.chat)
            local_session = _uuid(located[1]) if located is not None and located[0] == "claude" else None
            if local_session is None:
                raise ValueError("Cannot resolve this Claude Desktop chat to a local Claude Code session; reconnect with the original chat open")
            session_id = local_session
        else:
            session_uuid = _uuid(args.chat)
            if session_uuid is None:
                raise ValueError("Claude channel chat ID must be a Claude session UUID or a resolvable local_ ID")
            session_id = session_uuid
        from worklog.push_channel import run_stdio_channel

        return run_stdio_channel(
            lambda submit, stop: run_receiver(client, enrollment["id"], submit, stop_event=stop),
            enrollment_id=enrollment["id"], chat_id=session_id, stderr=sys.stderr,
        )
    raise ValueError("Unknown Worklog push command")
