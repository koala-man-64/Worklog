"""Messages the person writes into a tracked chat for the agents working in it.

A message goes to each agent tied to the chat when it is sent: an open run that
reported the chat's ID, on a credential that is still live (the agent registry's
current tie). Agents take their messages with `worklog agent inbox`. Delivery never
reopens a run, recovers a chat or touches a provider session: a pending delivery
ends with its run, its credential or its chat, and expires if nobody picks it up.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .common import AppError, event, now, text, uid
from .discussions import communication_actor

PICKUP_WINDOW = timedelta(hours=2)

# A delivery still pending at expires_at stays 'pending' in the table: it reads as
# expired (not_picked_up) and is never handed out, so no background job is needed.
SCHEMA = """
CREATE TABLE chat_messages(id TEXT PRIMARY KEY,chat_id TEXT NOT NULL REFERENCES agent_chats(id),text TEXT NOT NULL,author_id TEXT NOT NULL,author_label TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX chat_messages_chat ON chat_messages(chat_id,created_at,id);
CREATE TABLE chat_message_deliveries(message_id TEXT NOT NULL REFERENCES chat_messages(id),run_id TEXT NOT NULL REFERENCES agent_runs(id),status TEXT NOT NULL CHECK(status IN('pending','delivered','expired')),expires_at TEXT NOT NULL,delivered_at TEXT,expired_at TEXT,expire_reason TEXT,PRIMARY KEY(message_id,run_id));
CREATE INDEX chat_message_deliveries_run ON chat_message_deliveries(run_id,status);
"""

RECIPIENTS = ("SELECT ar.id,ar.display_name,ar.provider,ar.created_at,a.id AS agent_id,a.label AS agent_label,a.avatar "
              "FROM agent_runs ar JOIN communication_agents a ON a.id=ar.agent_id JOIN credentials cr ON cr.id=ar.credential_id "
              "WHERE ar.external_chat_id=? AND ar.ended_at IS NULL AND cr.revoked=0 ORDER BY ar.created_at,ar.id")


def expire_pending(c: sqlite3.Connection, reason: str, *, run_id: str | None = None,
                   chat_id: str | None = None, stamp: str | None = None) -> None:
    """End live pending deliveries of a run, of a chat, or (restore) all of them.

    A delivery already past its pickup window keeps reading as not picked up.
    """
    stamp = stamp or now()
    clause, args = "", []
    if run_id is not None:
        clause, args = " AND run_id=?", [run_id]
    elif chat_id is not None:
        clause, args = " AND message_id IN (SELECT id FROM chat_messages WHERE chat_id=?)", [chat_id]
    c.execute("UPDATE chat_message_deliveries SET status='expired',expired_at=?,expire_reason=? WHERE status='pending' AND expires_at>?" + clause,
              [stamp, reason, stamp, *args])


def _recipient(r: sqlite3.Row) -> dict:
    return {"runId": r["id"], "runName": r["display_name"], "provider": r["provider"], "startedAt": r["created_at"],
            "agentId": r["agent_id"], "agentLabel": r["agent_label"], "avatar": r["avatar"]}


def _recipients(c: sqlite3.Connection, chat_id: str) -> list[dict]:
    """Who a message sent now would reach, each marked by whether its identity registered with this chat.

    Any credential's run can report any chat ID, so a run's claim alone doesn't show that
    the agent works in this chat. Until a chat is bound to identities, the person sees
    which recipients only made that claim.
    """
    from .registration import identity_chat

    rows = c.execute(RECIPIENTS, (chat_id,)).fetchall()
    return [{**_recipient(r), "registered": identity_chat(c, r["agent_id"]) == chat_id} for r in rows]


class ChatMessageService:
    @staticmethod
    def _chat(c: sqlite3.Connection, identity: str) -> sqlite3.Row:
        row = c.execute("SELECT * FROM agent_chats WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Agent chat not found")
        return row

    @staticmethod
    def _records(c: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
        """Messages with each recipient's delivery, as it stands now."""
        deliveries: dict[str, list[dict]] = {}
        if rows:
            stamp = now()
            ids = [r["id"] for r in rows]
            for d in c.execute(
                    "SELECT d.*,ar.display_name,ar.agent_id,a.label AS agent_label,a.avatar FROM chat_message_deliveries d "
                    "JOIN agent_runs ar ON ar.id=d.run_id JOIN communication_agents a ON a.id=ar.agent_id "
                    "WHERE d.message_id IN (" + ",".join("?" * len(ids)) + ") ORDER BY ar.created_at,ar.id", ids):
                lapsed = d["status"] == "pending" and d["expires_at"] <= stamp
                deliveries.setdefault(d["message_id"], []).append({
                    "runId": d["run_id"], "runName": d["display_name"], "agentId": d["agent_id"],
                    "agentLabel": d["agent_label"], "avatar": d["avatar"],
                    "status": "expired" if lapsed else d["status"],
                    "reason": "not_picked_up" if lapsed else d["expire_reason"],
                    "expiresAt": d["expires_at"], "deliveredAt": d["delivered_at"],
                    "expiredAt": d["expires_at"] if lapsed else d["expired_at"]})
        return [{"id": r["id"], "chatId": r["chat_id"], "text": r["text"], "authorId": r["author_id"],
                 "authorLabel": r["author_label"], "createdAt": r["created_at"],
                 "deliveries": deliveries.get(r["id"], [])} for r in rows]

    def messages(self, c: sqlite3.Connection, identity: str, limit: int = 50, offset: int = 0) -> dict:
        """Newest first, with who a message sent now would reach; nobody, once archived."""
        chat = self._chat(c, identity)
        total = c.execute("SELECT COUNT(*) FROM chat_messages WHERE chat_id=?", (identity,)).fetchone()[0]
        rows = c.execute("SELECT * FROM chat_messages WHERE chat_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                         (identity, limit, offset)).fetchall()
        recipients = [] if chat["archived"] else _recipients(c, chat["chat_id"])
        return {"chat": {"id": chat["id"], "chatId": chat["chat_id"], "archived": bool(chat["archived"])},
                "recipients": recipients, "items": self._records(c, rows),
                "total": total, "limit": limit, "offset": offset}

    def post(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        if actor["kind"] != "human":
            raise AppError(403, "agent_scope", "Only the person writes into a chat")
        chat = self._chat(c, identity)
        if chat["archived"]:
            raise AppError(409, "chat_archived", "Archived chats don't receive messages, and Worklog never recovers a chat to deliver one")
        body = text(p["text"], "text", required=True)
        runs = c.execute(RECIPIENTS, (chat["chat_id"],)).fetchall()
        if not runs:
            raise AppError(409, "no_recipients", "No open agent run reports this chat's ID, so the message would reach no one")
        message_id, stamp = uid(), now()
        expires = (datetime.fromisoformat(stamp) + PICKUP_WINDOW).isoformat()
        c.execute("INSERT INTO chat_messages(id,chat_id,text,author_id,author_label,created_at) VALUES(?,?,?,?,?,?)",
                  (message_id, chat["id"], body, actor["id"], actor["label"], stamp))
        c.executemany("INSERT INTO chat_message_deliveries(message_id,run_id,status,expires_at) VALUES(?,?,'pending',?)",
                      [(message_id, run["id"], expires) for run in runs])
        result = self._records(c, [c.execute("SELECT * FROM chat_messages WHERE id=?", (message_id,)).fetchone()])[0]
        event(c, actor, "chat.message", message_id, None, result)
        return result

    def receive(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        """Hand this credential its waiting messages once, oldest first, and mark them delivered."""
        if actor["kind"] != "agent":
            raise AppError(403, "agent_scope", "Agents receive their own messages; open the chat to see yours")
        # Authentication preceded the write lock; a credential revoked since then gets nothing.
        communication_actor(c, actor)
        stamp = now()
        clause, args = "", [actor["id"], stamp]
        if p.get("chatId") is not None:
            clause = " AND ar.external_chat_id=?"
            args.append(text(p["chatId"], "chatId", required=True, limit=500))
        rows = c.execute(
            "SELECT d.message_id,d.run_id,m.text,m.author_id,m.author_label,m.created_at,ch.id AS tracked_id,ch.chat_id,ch.title "
            "FROM chat_message_deliveries d JOIN agent_runs ar ON ar.id=d.run_id JOIN chat_messages m ON m.id=d.message_id "
            "JOIN agent_chats ch ON ch.id=m.chat_id "
            # The run must still be tied to the chat, as well as open, when the message is handed out.
            "WHERE ar.credential_id=? AND ar.ended_at IS NULL AND ar.external_chat_id=ch.chat_id AND ch.archived=0 "
            "AND d.status='pending' AND d.expires_at>?" + clause +
            " ORDER BY m.created_at,m.id,d.run_id", args).fetchall()
        c.executemany("UPDATE chat_message_deliveries SET status='delivered',delivered_at=? WHERE message_id=? AND run_id=? AND status='pending'",
                      [(stamp, r["message_id"], r["run_id"]) for r in rows])
        # Two open runs on one chat still get one copy of each message.
        items: dict[str, dict[str, Any]] = {}
        for r in rows:
            item = items.setdefault(r["message_id"], {
                "id": r["message_id"], "text": r["text"], "authorKind": "human", "authorId": r["author_id"],
                "authorLabel": r["author_label"], "sentAt": r["created_at"],
                "chat": {"id": r["tracked_id"], "chatId": r["chat_id"], "title": r["title"]}, "runIds": []})
            item["runIds"].append(r["run_id"])
        for item in items.values():
            event(c, actor, "chat.message.deliver", item["id"], None, {"runIds": item["runIds"], "deliveredAt": stamp})
        return {"items": list(items.values()), "total": len(items)}
