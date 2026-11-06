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
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from .common import LIVE_AGENT, AppError, event, now, rows_in, text, uid
from .discussions import communication_actor

# A delivery expires this long after its message is sent. One still pending at expires_at stays
# 'pending' in the table: it reads as expired (not_picked_up) and is never handed out, so no
# background job is needed.
PICKUP_WINDOW = timedelta(hours=2)

# Whether the identity of the run `ar` is bound to the chat that run reports: it registered with
# that chat, whichever of its credentials did, or the person confirmed it there.
REGISTERED = ("EXISTS(SELECT 1 FROM communication_credentials cc WHERE cc.agent_id=ar.agent_id "
              "AND cc.chat_id=ar.external_chat_id)")
CONFIRMED = ("EXISTS(SELECT 1 FROM chat_confirmations cf WHERE cf.agent_id=ar.agent_id "
             "AND cf.chat_id=ar.external_chat_id)")
BOUND = f"({REGISTERED} OR {CONFIRMED})"
# The identity that registered the run's identity; none for a credential issued in Settings. Labels
# are each agent's own choice, so this is what tells the person where an identity comes from.
SPONSOR = "(SELECT cc.{} FROM communication_credentials cc WHERE cc.agent_id=ar.agent_id AND cc.registered_by IS NOT NULL LIMIT 1)"
# Open runs on live credentials that report the chat's ID, bound or not.
CLAIMS = ("SELECT ar.id,ar.display_name,ar.provider,ar.created_at,a.id AS agent_id,a.label AS agent_label,a.avatar,"
          f"{REGISTERED} AS registered,{CONFIRMED} AS confirmed,"
          f"{SPONSOR.format('registered_by')} AS sponsor_id,{SPONSOR.format('registered_by_label')} AS sponsor_label "
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
    """A CLAIMS row as the chat page shows it."""
    return {"runId": r["id"], "runName": r["display_name"], "provider": r["provider"], "startedAt": r["created_at"],
            "agentId": r["agent_id"], "agentLabel": r["agent_label"], "avatar": r["avatar"],
            "registeredBy": {"id": r["sponsor_id"], "label": r["sponsor_label"]} if r["sponsor_id"] else None}


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


# The identities tied to chat IDs, closest tie first, with the column each tie keeps its chat ID in.
TIES = (
    ("registered", "SELECT reg.chat_id AS chat_id,reg.agent_id AS ref FROM communication_credentials reg WHERE {}", "reg.chat_id"),
    ("confirmed", "SELECT cf.chat_id AS chat_id,cf.agent_id AS ref FROM chat_confirmations cf WHERE {}", "cf.chat_id"),
    ("run", "SELECT ar.external_chat_id AS chat_id,ar.agent_id AS ref FROM agent_runs ar JOIN credentials cred ON cred.id=ar.credential_id "
            "WHERE ar.ended_at IS NULL AND cred.revoked=0 AND {}", "ar.external_chat_id"),
)


def chat_agents(c: sqlite3.Connection, chat_ids: Iterable[str], *, exact: bool) -> dict[str, list[dict]]:
    """The agent identities tied to each chat ID.

    An identity is tied to the chat it registered with, to each chat where the person confirmed
    it, and, as only a claim, to the chat an open run on a live credential reports. Each comes
    once per chat, with its closest tie as `binding`, and `chatId` as that tie stored it. `exact`
    compares chat IDs as delivery does, character for character, and keys the result by them;
    otherwise they are compared and keyed without case or surrounding space, which finds every
    tie to a session that goes by more than one ID, though delivery reaches only the exact ones."""
    def key(value: str) -> str:
        return value if exact else value.strip().lower()

    keys = list({key(i) for i in chat_ids if i and i.strip()})
    ties: dict[str, dict[str, tuple[str, str]]] = {}
    for binding, sql, column in TIES:
        match = f"{column} IN ({{}})" if exact else f"lower(trim({column})) IN ({{}})"
        for r in rows_in(c, sql.format(match), keys):
            ties.setdefault(key(r["chat_id"]), {}).setdefault(r["ref"], (binding, r["chat_id"]))
    agents = {r["id"]: r for r in rows_in(c, f"SELECT a.id,a.label,a.avatar,{LIVE_AGENT} AS active FROM communication_agents a WHERE a.id IN ({{}})",
                                          {ref for found in ties.values() for ref in found})}
    order = {tie[0]: place for place, tie in enumerate(TIES)}
    result: dict[str, list[dict]] = {}
    for chat_id, found in ties.items():
        listed = [{"id": ref, "label": agents[ref]["label"], "avatar": agents[ref]["avatar"], "active": bool(agents[ref]["active"]),
                   "binding": binding, "chatId": stored} for ref, (binding, stored) in found.items() if ref in agents]
        result[chat_id] = sorted(listed, key=lambda a: (order[a["binding"]], a["label"].casefold(), a["id"]))
    return result


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

    @staticmethod
    def _person(actor: dict) -> None:
        if actor["kind"] != "human":
            raise AppError(403, "agent_scope", "Only the person confirms who works in a chat")

    @staticmethod
    def _agent_id(c: sqlite3.Connection, p: dict) -> str:
        agent_id = text(p["agentId"], "agentId", required=True, limit=200)
        if c.execute("SELECT 1 FROM communication_agents WHERE id=?", (agent_id,)).fetchone() is None:
            raise AppError(404, "not_found", "Agent not found")
        return agent_id

    def _confirmable(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> tuple[sqlite3.Row, str]:
        self._person(actor)
        chat = self._chat(c, identity)
        if chat["archived"]:
            raise AppError(409, "chat_archived", "Archived chats don't receive messages; recover the chat to change who it reaches")
        return chat, self._agent_id(c, p)

    @staticmethod
    def _confirm(c: sqlite3.Connection, actor: dict, chat_id: str, agent_id: str, entity: str) -> None:
        if chat_binding(c, chat_id, agent_id) is None:
            c.execute("INSERT INTO chat_confirmations(chat_id,agent_id,confirmed_by,confirmed_by_label,created_at) VALUES(?,?,?,?,?)",
                      (chat_id, agent_id, actor["id"], actor["label"], now()))
            event(c, actor, "chat.confirm", entity, None, {"chatId": chat_id, "agentId": agent_id})

    @staticmethod
    def _unconfirm(c: sqlite3.Connection, actor: dict, chat_id: str, agent_id: str, entity: str) -> None:
        removed = c.execute("DELETE FROM chat_confirmations WHERE chat_id=? AND agent_id=?", (chat_id, agent_id)).rowcount
        if removed and chat_binding(c, chat_id, agent_id) is None:
            stamp = now()
            # A run keeps the chat ID it started with, so these are all its deliveries for this chat.
            c.execute("UPDATE chat_message_deliveries SET status='expired',expired_at=?,expire_reason='unconfirmed' "
                      "WHERE status='pending' AND expires_at>? AND run_id IN (SELECT id FROM agent_runs WHERE agent_id=? AND external_chat_id=?)",
                      (stamp, stamp, agent_id, chat_id))
        if removed:
            event(c, actor, "chat.unconfirm", entity, {"chatId": chat_id, "agentId": agent_id}, None)

    @staticmethod
    def _binding_record(c: sqlite3.Connection, chat: sqlite3.Row, agent_id: str) -> dict:
        return {"chat": {"id": chat["id"], "chatId": chat["chat_id"]}, "agentId": agent_id,
                "binding": chat_binding(c, chat["chat_id"], agent_id)}

    def confirm(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        """The person confirms that an identity works in this chat: what they write here from now on
        reaches its open runs on the chat, on any record that tracks it. Messages sent before stay
        as they were. An identity already bound stays as it is."""
        chat, agent_id = self._confirmable(c, actor, identity, p)
        self._confirm(c, actor, chat["chat_id"], agent_id, chat["id"])
        return self._binding_record(c, chat, agent_id)

    def unconfirm(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        """The person removes their confirmation: the identity receives nothing more in this chat,
        and what still waits for it here ends. The chat an identity registered with stays bound."""
        chat, agent_id = self._confirmable(c, actor, identity, p)
        self._unconfirm(c, actor, chat["chat_id"], agent_id, chat["id"])
        return self._binding_record(c, chat, agent_id)

    def confirm_chat_id(self, c: sqlite3.Connection, actor: dict, p: dict, *, confirmed: bool) -> dict:
        """Confirm an identity in a chat, or take that back, by the chat's ID, whether or not a
        record tracks the chat yet, such as a Claude Code session running on this machine. It
        binds the identity as `confirm` does, wherever the chat is tracked now or later. With no
        tracked chat to hold it, the identity's own history records the change. Delivery matches
        the ID exactly, so give it as the identity's runs report it."""
        self._person(actor)
        agent_id = self._agent_id(c, p)
        chat_id = text(p["chatId"], "chatId", required=True, limit=500)
        (self._confirm if confirmed else self._unconfirm)(c, actor, chat_id, agent_id, agent_id)
        return {"chatId": chat_id, "agentId": agent_id, "binding": chat_binding(c, chat_id, agent_id)}
