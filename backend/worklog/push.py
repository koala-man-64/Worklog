"""Durable, provider-neutral push delivery records.

The outbox stores references to Worklog records, never peer-authored text. Domain events and their
recipient rows are written by the same SQLite transaction; a provider sender claims and receipts
them later.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .common import AppError, boolean, now, text, uid
from .tickets import TicketService
from .transcripts import CHAT_ID

PROVIDERS = frozenset({"codex", "claude"})
SUBJECT_TYPES = frozenset({"ticket", "item", "run"})
RECEIPT_OUTCOMES = frozenset({"submitted", "queued", "unavailable", "uncertain"})
CLAIM_LEASE_SECONDS = 120
DELIVERY_QUERY = ("SELECT d.*,cc.agent_id AS recipient_agent_id FROM push_deliveries d "
                  "JOIN push_enrollments e ON e.id=d.enrollment_id "
                  "JOIN communication_credentials cc ON cc.credential_id=e.credential_id ")


def _principal(c: sqlite3.Connection, actor: dict) -> sqlite3.Row:
    """Resolve the raw authenticated credential to its stable Worklog agent identity."""
    if not isinstance(actor, dict) or actor.get("kind") != "agent" or not isinstance(actor.get("id"), str):
        raise AppError(403, "push_agent_required", "Push delivery requires an authenticated agent credential")
    row = c.execute(
        "SELECT cc.agent_id,cc.registered_by,cc.chat_id FROM communication_credentials cc "
        "JOIN credentials cr ON cr.id=cc.credential_id WHERE cc.credential_id=? AND cr.revoked=0",
        (actor["id"],),
    ).fetchone()
    if row is None:
        raise AppError(401, "authentication", "Credential is invalid or revoked")
    return row


def _enrollment(c: sqlite3.Connection, actor: dict, enrollment_id: str, *, allow_disabled: bool = False) -> sqlite3.Row:
    _principal(c, actor)
    row = c.execute(
        "SELECT e.*,cc.agent_id,cc.registered_by,cc.chat_id AS registered_chat_id,cr.revoked "
        "FROM push_enrollments e JOIN communication_credentials cc ON cc.credential_id=e.credential_id "
        "JOIN credentials cr ON cr.id=e.credential_id "
        "WHERE e.id=? AND e.credential_id=? AND cr.revoked=0 AND cc.registered_by IS NOT NULL "
        "AND cc.chat_id=e.chat_id",
        (enrollment_id, actor["id"]),
    ).fetchone()
    if row is None:
        raise AppError(404, "delivery_enrollment", "Push enrollment not found")
    if row["disabled_at"] is not None and not allow_disabled:
        raise AppError(409, "delivery_disabled", "Push enrollment is disabled")
    return row


def _enrollment_record(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "provider": row["provider"], "chatId": row["chat_id"],
            "enabled": row["disabled_at"] is None, "createdAt": row["created_at"],
            "updatedAt": row["updated_at"]}


def _delivery_record(row: sqlite3.Row) -> dict:
    state = row["state"]
    if state == "claimed" and row["claim_expires_at"] is not None and row["claim_expires_at"] <= now():
        # An expired send claim has an unknown provider outcome. Surface that uncertainty without
        # retrying it; claim() persists the transition when a sender next examines the row.
        state = "uncertain"
    return {"id": row["id"], "seq": row["seq"], "kind": row["kind"],
            "subjectType": row["subject_type"], "subjectId": row["subject_id"],
            "discussionId": row["discussion_id"], "messageId": row["message_id"],
            "sourceEventId": row["source_event_id"], "recipientAgentId": row["recipient_agent_id"], "state": state,
            "attemptCount": row["attempt_count"], "attemptId": row["attempt_id"],
            "claimExpiresAt": row["claim_expires_at"], "createdAt": row["created_at"],
            "updatedAt": row["updated_at"], "outcomeAt": row["outcome_at"], "resumeWarning": bool(row["resume_warning"])}


def _read_delivery(c: sqlite3.Connection, delivery_id: str, enrollment_id: str) -> sqlite3.Row:
    row = c.execute(DELIVERY_QUERY + "WHERE d.id=? AND d.enrollment_id=?",
                    (delivery_id, enrollment_id)).fetchone()
    if row is None:
        raise AppError(404, "delivery_not_found", "Push delivery not found")
    return row


def _stop_enrollment(c: sqlite3.Connection, enrollment_id: str, stamp: str) -> None:
    """Cancel sends that provably have not started and retain uncertainty for in-flight sends."""
    c.execute("UPDATE push_deliveries SET state='cancelled',updated_at=?,outcome_at=? "
              "WHERE enrollment_id=? AND state IN('pending','unavailable')", (stamp, stamp, enrollment_id))
    c.execute("UPDATE push_deliveries SET state='uncertain',claim_expires_at=NULL,updated_at=?,outcome_at=? "
              "WHERE enrollment_id=? AND state='claimed'", (stamp, stamp, enrollment_id))


def _disable_enrollment(c: sqlite3.Connection, enrollment_id: str, stamp: str) -> None:
    c.execute("UPDATE push_enrollments SET disabled_at=?,updated_at=? WHERE id=?", (stamp, stamp, enrollment_id))
    _stop_enrollment(c, enrollment_id, stamp)


def disable_credential_enrollments(c: sqlite3.Connection, credential_id: str) -> None:
    """Close outstanding delivery when the destination credential is retired, replaced or revoked."""
    rows = c.execute("SELECT id FROM push_enrollments WHERE credential_id=? AND disabled_at IS NULL",
                     (credential_id,)).fetchall()
    stamp = now()
    for row in rows:
        _disable_enrollment(c, row["id"], stamp)


class DeliveryService:
    """Database-only delivery operations. This service never calls a provider."""

    def enrollment(self, c: sqlite3.Connection, actor: dict, enrollment_id: str) -> dict:
        """Return the provider/chat binding only to its own live registered credential."""
        return _enrollment_record(_enrollment(c, actor, enrollment_id))

    def enroll(self, c: sqlite3.Connection, actor: dict, provider: str, chat_id: str) -> dict:
        registration = _principal(c, actor)
        provider = text(provider, "provider", required=True, limit=30).lower()
        if provider not in PROVIDERS:
            raise AppError(422, "validation", "Choose a provider: codex or claude", {"provider": "invalid"})
        chat_id = text(chat_id, "chatId", required=True, limit=500)
        if registration["registered_by"] is None or registration["chat_id"] != chat_id:
            raise AppError(403, "delivery_registration", "Enroll only the chat registered to this credential")
        match = CHAT_ID.fullmatch(chat_id.lower())
        if not match or (match[1] and provider != "claude"):
            raise AppError(422, "validation", "Use the session UUID, or a Claude desktop local_ chat ID",
                           {"chatId": "invalid"})
        stamp = now()
        existing = c.execute(
            "SELECT * FROM push_enrollments WHERE credential_id=? AND provider=? AND chat_id=?",
            (actor["id"], provider, chat_id),
        ).fetchone()
        if existing is None:
            identity = uid()
            c.execute("INSERT INTO push_enrollments(id,credential_id,provider,chat_id,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?)", (identity, actor["id"], provider, chat_id, stamp, stamp))
        else:
            identity = existing["id"]
            if existing["disabled_at"] is not None:
                # Re-enabling an existing destination does not recreate or replay old deliveries:
                # disable() has already cancelled every safe-to-retry row.
                _stop_enrollment(c, identity, stamp)
                c.execute("UPDATE push_enrollments SET disabled_at=NULL,updated_at=? WHERE id=?",
                          (stamp, identity))
        row = c.execute("SELECT * FROM push_enrollments WHERE id=?", (identity,)).fetchone()
        return _enrollment_record(row)

    def disable(self, c: sqlite3.Connection, actor: dict, enrollment_id: str) -> dict:
        row = _enrollment(c, actor, enrollment_id, allow_disabled=True)
        stamp = now()
        if row["disabled_at"] is None:
            _disable_enrollment(c, enrollment_id, stamp)
        return _enrollment_record(c.execute("SELECT * FROM push_enrollments WHERE id=?", (enrollment_id,)).fetchone())

    def subscribe(self, c: sqlite3.Connection, actor: dict, enrollment_id: str,
                  kind: str, ref: str, enabled: bool) -> dict:
        _enrollment(c, actor, enrollment_id)
        if kind not in SUBJECT_TYPES:
            raise AppError(422, "validation", "Choose a subscription type: ticket, item, or run", {"kind": "invalid"})
        ref = text(ref, "ref", required=True, limit=500)
        enabled = boolean(enabled, "enabled")
        if kind == "ticket":
            subject_id = TicketService.resolve(c, ref)["id"]
        elif kind == "item":
            row = c.execute("SELECT id FROM work_items WHERE id=?", (ref,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Work item not found")
            subject_id = row["id"]
        else:
            row = c.execute("SELECT id FROM agent_runs WHERE id=?", (ref,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Run not found")
            subject_id = row["id"]
        stamp = now()
        row = c.execute("SELECT * FROM push_subscriptions WHERE enrollment_id=? AND subject_type=? AND subject_id=?",
                        (enrollment_id, kind, subject_id)).fetchone()
        if row is None:
            identity = uid()
            c.execute("INSERT INTO push_subscriptions(id,enrollment_id,subject_type,subject_id,enabled,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?)", (identity, enrollment_id, kind, subject_id, int(enabled), stamp, stamp))
        else:
            identity = row["id"]
            if bool(row["enabled"]) != enabled:
                c.execute("UPDATE push_subscriptions SET enabled=?,updated_at=? WHERE id=?",
                          (int(enabled), stamp, identity))
        record = c.execute("SELECT * FROM push_subscriptions WHERE id=?", (identity,)).fetchone()
        return {"id": record["id"], "enrollmentId": record["enrollment_id"],
                "subjectType": record["subject_type"], "subjectId": record["subject_id"],
                "enabled": bool(record["enabled"]), "createdAt": record["created_at"],
                "updatedAt": record["updated_at"]}

    def pending(self, c: sqlite3.Connection, actor: dict, enrollment_id: str,
                after_seq: int = 0, limit: int = 50) -> dict:
        enrollment = _enrollment(c, actor, enrollment_id)
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise AppError(422, "validation", "after_seq must be a nonnegative integer", {"afterSeq": "invalid"})
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise AppError(422, "validation", "limit must be between 1 and 200", {"limit": "invalid"})
        rows = c.execute(DELIVERY_QUERY + "WHERE d.enrollment_id=? AND d.seq>? "
                          "AND d.state IN('pending','unavailable') ORDER BY d.seq LIMIT ?",
                          (enrollment["id"], after_seq, limit + 1)).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_seq = rows[-1]["seq"] if rows else after_seq
        return {"items": [_delivery_record(row) for row in rows], "hasMore": has_more, "nextSeq": next_seq}

    def claim(self, c: sqlite3.Connection, actor: dict, enrollment_id: str,
              delivery_id: str, attempt_id: str) -> dict:
        enrollment = _enrollment(c, actor, enrollment_id)
        attempt_id = text(attempt_id, "attemptId", required=True, limit=200)
        row = _read_delivery(c, delivery_id, enrollment["id"])
        stamp = now()
        if row["state"] == "claimed":
            if row["claim_expires_at"] is not None and row["claim_expires_at"] <= stamp:
                c.execute("UPDATE push_deliveries SET state='uncertain',claim_expires_at=NULL,updated_at=?,outcome_at=? "
                          "WHERE id=?", (stamp, stamp, delivery_id))
                row = _read_delivery(c, delivery_id, enrollment["id"])
                return {"acquired": False, "delivery": _delivery_record(row)}
            return {"acquired": row["attempt_id"] == attempt_id, "delivery": _delivery_record(row)}
        if row["state"] not in {"pending", "unavailable"}:
            return {"acquired": False, "delivery": _delivery_record(row)}
        if row["state"] == "unavailable" and row["attempt_id"] == attempt_id:
            # Replaying the completed attempt cannot turn its pre-send refusal into a new claim.
            return {"acquired": False, "delivery": _delivery_record(row)}
        expires = (datetime.now(UTC) + timedelta(seconds=CLAIM_LEASE_SECONDS)).isoformat()
        c.execute("UPDATE push_deliveries SET state='claimed',attempt_id=?,claim_expires_at=?,attempt_count=attempt_count+1,"
                  "updated_at=?,outcome_at=NULL WHERE id=? AND state IN('pending','unavailable')",
                  (attempt_id, expires, stamp, delivery_id))
        row = _read_delivery(c, delivery_id, enrollment["id"])
        return {"acquired": row["state"] == "claimed" and row["attempt_id"] == attempt_id,
                "delivery": _delivery_record(row)}

    def receipt(self, c: sqlite3.Connection, actor: dict, enrollment_id: str,
                delivery_id: str, attempt_id: str, outcome: str, *, resume_warning: bool = False) -> dict:
        enrollment = _enrollment(c, actor, enrollment_id, allow_disabled=True)
        attempt_id = text(attempt_id, "attemptId", required=True, limit=200)
        if outcome not in RECEIPT_OUTCOMES:
            raise AppError(422, "validation", "Choose a receipt outcome: submitted, queued, unavailable, or uncertain",
                           {"outcome": "invalid"})
        if resume_warning and (outcome != "queued" or enrollment["provider"] != "codex"):
            raise AppError(422, "validation", "A resume warning requires a queued Codex delivery",
                           {"resumeWarning": "invalid"})
        row = _read_delivery(c, delivery_id, enrollment["id"])
        if row["attempt_id"] != attempt_id or row["attempt_id"] is None:
            raise AppError(409, "delivery_attempt", "Receipt does not match the current delivery attempt")
        if row["state"] == outcome:
            if bool(row["resume_warning"]) != resume_warning:
                raise AppError(409, "delivery_state", "The recorded receipt has a different resume result")
            return _delivery_record(row)
        if row["state"] == "uncertain" and outcome == "unavailable":
            # A claim made before disable/restore may report late. Keeping the record uncertain
            # prevents that stale outcome from becoming claimable after the enrollment is enabled.
            return _delivery_record(row)
        if row["state"] not in {"claimed", "uncertain"}:
            raise AppError(409, "delivery_state", "This delivery attempt no longer accepts a receipt")
        stamp = now()
        c.execute("UPDATE push_deliveries SET state=?,claim_expires_at=NULL,updated_at=?,outcome_at=?,resume_warning=? "
                  "WHERE id=?", (outcome, stamp, stamp, int(resume_warning), delivery_id))
        return _delivery_record(_read_delivery(c, delivery_id, enrollment["id"]))

    def status(self, c: sqlite3.Connection, actor: dict, box: str, limit: int = 50,
               before_seq: int | None = None) -> dict:
        principal = _principal(c, actor)
        if box not in {"received", "sent"}:
            raise AppError(422, "validation", "Choose a delivery box: received or sent", {"box": "invalid"})
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise AppError(422, "validation", "limit must be between 1 and 200", {"limit": "invalid"})
        if before_seq is not None and (isinstance(before_seq, bool) or not isinstance(before_seq, int) or before_seq < 0):
            raise AppError(422, "validation", "before_seq must be a nonnegative integer", {"beforeSeq": "invalid"})
        clause = ""
        cursor_args: list[Any] = []
        if before_seq is not None:
            clause = " AND d.seq<?"
            cursor_args.append(before_seq)
        if box == "received":
            query = DELIVERY_QUERY + "WHERE e.credential_id=?"
            scope = [actor["id"]]
        else:
            query = (DELIVERY_QUERY + "JOIN events ev ON ev.id=d.source_event_id WHERE ev.actor_kind='agent' "
                     "AND (ev.actor_id=? OR ev.actor_id IN "
                     "(SELECT credential_id FROM communication_credentials WHERE agent_id=?))")
            scope = [principal["agent_id"], principal["agent_id"]]
        rows = c.execute(query + clause + " ORDER BY d.seq DESC LIMIT ?", [*scope, *cursor_args, limit + 1]).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_seq = rows[-1]["seq"] if rows else (before_seq or 0)
        return {"items": [_delivery_record(row) for row in rows], "hasMore": has_more, "nextSeq": next_seq}
