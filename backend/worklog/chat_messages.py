"""Messages the person writes into a tracked chat for the agents working in it.

A message goes to each agent bound to the chat when it is sent: an open run that
reported the chat's ID, on a credential that is still live, of an identity bound to
that chat. Since v13 an identity is bound to the chat it registered with, and to each
chat where the person confirmed it on the chat page. Any credential's run can report
any chat ID, so a run of an identity bound to neither is only a claim: the chat page
shows it, and it receives nothing. Agents take their messages with `worklog agent
inbox`. Delivery never reopens a run, recovers a chat or touches a provider session:
a pending delivery ends with its run, its credential, its chat or its confirmation,
and expires if nobody picks it up.
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

# Whether the identity of the run `ar` is bound to the chat that run reports: it registered with
# that chat, whichever of its credentials did, or the person confirmed it there.
REGISTERED = ("EXISTS(SELECT 1 FROM communication_credentials cc WHERE cc.agent_id=ar.agent_id "
              "AND cc.chat_id=ar.external_chat_id)")
CONFIRMED = ("EXISTS(SELECT 1 FROM chat_confirmations cf WHERE cf.agent_id=ar.agent_id "
             "AND cf.chat_id=ar.external_chat_id)")
BOUND = f"({REGISTERED} OR {CONFIRMED})"
# Open runs on live credentials that report the chat's ID, bound or not.
CLAIMS = ("SELECT ar.id,ar.display_name,ar.provider,ar.created_at,a.id AS agent_id,a.label AS agent_label,a.avatar,"
          f"{REGISTERED} AS registered,{CONFIRMED} AS confirmed "
          "FROM agent_runs ar JOIN communication_agents a ON a.id=ar.agent_id JOIN credentials cr ON cr.id=ar.credential_id "
          "WHERE ar.external_chat_id=? AND ar.ended_at IS NULL AND cr.revoked=0")
# Who a message sent now reaches.
RECIPIENTS = CLAIMS + f" AND {BOUND} ORDER BY ar.created_at,ar.id"


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


def _audience(c: sqlite3.Connection, chat_id: str) -> tuple[list[dict], list[dict]]:
    """Who a message sent now would reach, each with how it is bound, and the runs that only claim the chat."""
    recipients: list[dict] = []
    unconfirmed: list[dict] = []
    for r in c.execute(CLAIMS + " ORDER BY ar.created_at,ar.id", (chat_id,)):
        if r["registered"] or r["confirmed"]:
            recipients.append({**_recipient(r), "binding": "registered" if r["registered"] else "confirmed"})
        else:
            unconfirmed.append(_recipient(r))
    return recipients, unconfirmed


def chat_binding(c: sqlite3.Connection, chat_id: str, agent_id: str) -> str | None:
    """How an identity is bound to a chat: 'registered', 'confirmed', or None for not at all."""
    if c.execute("SELECT 1 FROM communication_credentials WHERE agent_id=? AND chat_id=?", (agent_id, chat_id)).fetchone():
        return "registered"
    if c.execute("SELECT 1 FROM chat_confirmations WHERE agent_id=? AND chat_id=?", (agent_id, chat_id)).fetchone():
        return "confirmed"
    return None


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
        recipients, unconfirmed = ([], []) if chat["archived"] else _audience(c, chat["chat_id"])
        return {"chat": {"id": chat["id"], "chatId": chat["chat_id"], "archived": bool(chat["archived"])},
                "recipients": recipients, "unconfirmed": unconfirmed, "items": self._records(c, rows),
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
            if c.execute(CLAIMS + " LIMIT 1", (chat["chat_id"],)).fetchone():
                raise AppError(409, "no_recipients", "No agent bound to this chat has an open run, so the message would reach no one; "
                               "confirm an agent on the chat page to reach it")
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
            # The run must still be tied to the chat, and its identity bound to it, as well as open,
            # when the message is handed out.
            "WHERE ar.credential_id=? AND ar.ended_at IS NULL AND ar.external_chat_id=ch.chat_id AND ch.archived=0 "
            f"AND {BOUND} AND d.status='pending' AND d.expires_at>?" + clause +
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

    def _confirmable(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> tuple[sqlite3.Row, str]:
        if actor["kind"] != "human":
            raise AppError(403, "agent_scope", "Only the person confirms who works in a chat")
        chat = self._chat(c, identity)
        if chat["archived"]:
            raise AppError(409, "chat_archived", "Archived chats don't receive messages; recover the chat to change who it reaches")
        agent_id = text(p["agentId"], "agentId", required=True, limit=200)
        if c.execute("SELECT 1 FROM communication_agents WHERE id=?", (agent_id,)).fetchone() is None:
            raise AppError(404, "not_found", "Agent not found")
        return chat, agent_id

    @staticmethod
    def _binding_record(c: sqlite3.Connection, chat: sqlite3.Row, agent_id: str) -> dict:
        return {"chat": {"id": chat["id"], "chatId": chat["chat_id"]}, "agentId": agent_id,
                "binding": chat_binding(c, chat["chat_id"], agent_id)}

    def confirm(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        """The person confirms that an identity works in this chat: what they write here from now on
        reaches its open runs on the chat, on any record that tracks it. Messages sent before stay
        as they were. An identity already bound stays as it is."""
        chat, agent_id = self._confirmable(c, actor, identity, p)
        if chat_binding(c, chat["chat_id"], agent_id) is None:
            c.execute("INSERT INTO chat_confirmations(chat_id,agent_id,confirmed_by,confirmed_by_label,created_at) VALUES(?,?,?,?,?)",
                      (chat["chat_id"], agent_id, actor["id"], actor["label"], now()))
            event(c, actor, "chat.confirm", chat["id"], None, {"chatId": chat["chat_id"], "agentId": agent_id})
        return self._binding_record(c, chat, agent_id)

    def unconfirm(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        """The person removes their confirmation: the identity receives nothing more in this chat,
        and what still waits for it here ends. The chat an identity registered with stays bound."""
        chat, agent_id = self._confirmable(c, actor, identity, p)
        removed = c.execute("DELETE FROM chat_confirmations WHERE chat_id=? AND agent_id=?", (chat["chat_id"], agent_id)).rowcount
        if removed and chat_binding(c, chat["chat_id"], agent_id) is None:
            stamp = now()
            # A run keeps the chat ID it started with, so these are all its deliveries for this chat.
            c.execute("UPDATE chat_message_deliveries SET status='expired',expired_at=?,expire_reason='unconfirmed' "
                      "WHERE status='pending' AND expires_at>? AND run_id IN (SELECT id FROM agent_runs WHERE agent_id=? AND external_chat_id=?)",
                      (stamp, stamp, agent_id, chat["chat_id"]))
        if removed:
            event(c, actor, "chat.unconfirm", chat["id"], {"chatId": chat["chat_id"], "agentId": agent_id}, None)
        return self._binding_record(c, chat, agent_id)
