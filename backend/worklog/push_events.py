"""Write recipient delivery rows in the transaction that records a Worklog event."""

import sqlite3
import uuid
from typing import Any


def _active_enrollments(c: sqlite3.Connection, agent_id: str) -> list[sqlite3.Row]:
    return c.execute(
        "SELECT e.* FROM push_enrollments e JOIN communication_credentials cc ON cc.credential_id=e.credential_id "
        "JOIN credentials cr ON cr.id=e.credential_id WHERE cc.agent_id=? AND cc.registered_by IS NOT NULL "
        "AND cc.chat_id=e.chat_id AND cr.revoked=0 AND e.disabled_at IS NULL ORDER BY e.created_at,e.id",
        (agent_id,),
    ).fetchall()


def _enqueue(c: sqlite3.Connection, event_id: str, enrollment: sqlite3.Row, kind: str,
             subject_type: str, subject_id: str, stamp: str, *,
             discussion_id: str | None = None, message_id: str | None = None) -> None:
    c.execute("INSERT INTO push_deliveries(id,enrollment_id,source_event_id,kind,subject_type,subject_id,"
              "discussion_id,message_id,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'pending',?,?) "
              "ON CONFLICT(source_event_id,enrollment_id) DO NOTHING",
              (str(uuid.uuid4()), enrollment["id"], event_id, kind, subject_type, subject_id,
               discussion_id, message_id, stamp, stamp))


def _enqueue_agent(c: sqlite3.Connection, event_id: str, agent_id: str, kind: str,
                   discussion_id: str, message_id: str, stamp: str) -> None:
    for enrollment in _active_enrollments(c, agent_id):
        _enqueue(c, event_id, enrollment, kind, "discussion", discussion_id, stamp,
                 discussion_id=discussion_id, message_id=message_id)


def _discussion_deliveries(c: sqlite3.Connection, event_row: dict, after: Any) -> None:
    if not isinstance(after, dict):
        return
    message_id = after.get("id") or event_row.get("entityId")
    discussion_id = after.get("discussionId")
    if not isinstance(message_id, str) or not isinstance(discussion_id, str):
        return
    stamp = event_row["createdAt"]
    agent_recipients: dict[str, str] = {}
    request = c.execute("SELECT 1 FROM discussion_requests WHERE message_id=?", (message_id,)).fetchone()
    if request:
        for row in c.execute("SELECT kind,recipient_id FROM discussion_targets WHERE message_id=? AND kind='agent'",
                              (message_id,)).fetchall():
            agent_recipients[row["recipient_id"]] = "discussion.ask"
    if after.get("replyToMessageId"):
        # Both an agent and the person can answer a request naming them. An open or role slot
        # does not match the responder's stored identity, so it cannot wake the requester.
        requester = c.execute(
            "SELECT original.author_id FROM discussion_messages reply "
            "JOIN discussion_messages original ON original.id=reply.reply_to_message_id "
            "JOIN discussion_targets target ON target.message_id=original.id "
            "AND target.kind=reply.author_kind AND target.recipient_id=reply.author_id "
            "WHERE reply.id=? AND original.discussion_id=reply.discussion_id AND original.author_kind='agent'",
            (message_id,),
        ).fetchone()
        if requester:
            agent_recipients.setdefault(requester["author_id"], "discussion.reply")
    for agent_id, kind in agent_recipients.items():
        _enqueue_agent(c, event_row["id"], agent_id, kind, discussion_id, message_id, stamp)


def _enqueue_subscription(c: sqlite3.Connection, event_id: str, kind: str,
                          subject_type: str, subject_id: str, stamp: str) -> None:
    rows = c.execute(
        "SELECT e.* FROM push_subscriptions s JOIN push_enrollments e ON e.id=s.enrollment_id "
        "JOIN communication_credentials cc ON cc.credential_id=e.credential_id "
        "JOIN credentials cr ON cr.id=e.credential_id WHERE s.subject_type=? AND s.subject_id=? AND s.enabled=1 "
        "AND e.disabled_at IS NULL AND cc.registered_by IS NOT NULL AND cc.chat_id=e.chat_id AND cr.revoked=0 "
        "ORDER BY e.created_at,e.id",
        (subject_type, subject_id),
    ).fetchall()
    for enrollment in rows:
        _enqueue(c, event_id, enrollment, kind, subject_type, subject_id, stamp)


def _run_id(c: sqlite3.Connection, event_row: dict, before: Any, after: Any) -> str | None:
    for value in (after, before):
        if isinstance(value, dict):
            if isinstance(value.get("runId"), str):
                return value["runId"]
            if isinstance(value.get("run_id"), str):
                return value["run_id"]
            nested = value.get("participation")
            if isinstance(nested, dict):
                if isinstance(nested.get("runId"), str):
                    return nested["runId"]
                if isinstance(nested.get("run_id"), str):
                    return nested["run_id"]
    entity_id = event_row.get("entityId")
    if not isinstance(entity_id, str):
        return None
    row = c.execute("SELECT run_id FROM participations WHERE id=?", (entity_id,)).fetchone()
    return row["run_id"] if row else None


def enqueue_event(c: sqlite3.Connection, event_row: dict, before: Any, after: Any) -> None:
    """Enqueue a compact per-recipient reference for supported committed domain events.

    This runs inside common.event(), after the durable event insert and before its transaction
    commits. The unique event/enrollment constraint is the retry-safe dedupe boundary.
    """
    kind = event_row.get("kind")
    entity_id = event_row.get("entityId")
    stamp = event_row["createdAt"]
    if kind in {"discussion.create", "discussion.message"}:
        _discussion_deliveries(c, event_row, after)
        return
    if kind in {"ticket.update", "ticket.close", "ticket.reopen"} and isinstance(before, dict) and isinstance(after, dict):
        if isinstance(entity_id, str) and before.get("status") != after.get("status"):
            _enqueue_subscription(c, event_row["id"], "ticket.status", "ticket", entity_id, stamp)
        return
    if kind == "item.update" and isinstance(before, dict) and isinstance(after, dict):
        if isinstance(entity_id, str) and after.get("_statusChanged") and before.get("status") != after.get("status"):
            _enqueue_subscription(c, event_row["id"], "item.status", "item", entity_id, stamp)
        return
    if kind == "run.end" and isinstance(entity_id, str):
        _enqueue_subscription(c, event_row["id"], "run.end", "run", entity_id, stamp)
        return
    if kind == "coordination.update" or (isinstance(kind, str) and kind.startswith("participation.")):
        run_id = _run_id(c, event_row, before, after)
        if run_id:
            _enqueue_subscription(c, event_row["id"], "run.update", "run", run_id, stamp)
