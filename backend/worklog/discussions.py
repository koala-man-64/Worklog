"""Durable communication, deliberately independent of work approval and chat liveness.

Every write is called inside the app's SQLite write transaction. A role transition
and an acknowledgement therefore have a definite order, including across callers.
"""

from __future__ import annotations

import base64
import binascii
import builtins
import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from . import record_kinds, search
from .avatars import AVATARS, choose, is_avatar
from .common import (
    LIVE_AGENT,
    AppError,
    event,
    now,
    require_revision,
    rows_in,
    setting,
    text,
    uid,
)
from .tickets import TicketService

# What a discussion can be tagged with (v22), in the order its tags list them. The first three are
# v1's work contexts, which roles keep to.
TAG_KINDS = ("item", "roadmap", "step", "repository", "ticket", "person", "resource", "question", "decision")
ROLE_KINDS = ("item", "roadmap", "step")
TAG_LIMIT = 30
PERSON = {"id": "local-user", "label": "You", "kind": "human"}
# How threads list: pinned ones first, the latest pinned first, then the most recently active.
LISTED = "d.pinned_at IS NULL,d.pinned_at DESC,d.updated_at DESC,d.id"
# A repository's own discussion, which Worklog opens as the person when the repository is registered.
# Its first message begins as migrations.REPOSITORY_DISCUSSION_TEXT does and names the repository's ID
# in backticks, which is how the v22 upgrade finds one again after the way back. What agents do here is
# the agent guide's to say.
REPOSITORY_TITLE = "Repository: {name}"
REPOSITORY_TEXT = "Worklog opened this discussion for the repository **{name}** (`{id}`)."
# A request's reach (v33). An urgent request without a deadline escalates after this long, and an
# agent nothing was heard from for STALE_AFTER is stale, as registry.STALE_AFTER counts it.
URGENT_DEADLINE = timedelta(minutes=15)
STALE_AFTER = timedelta(hours=2)
# How far a target slot got, in order: delivery takes the furthest.
DELIVERY = ("pending", "seen", "acknowledged", "answered")
# The feed changes that end or escalate a request, which its sender's inbox reports.
REQUEST_CHANGES = ("discussion.acknowledge", "discussion.request.cancel", "discussion.request.release",
                   "discussion.request.escalate")
INBOX_LIMIT = 50
# How often, in seconds, the serve watcher escalates requests whose deadline passed.
ESCALATION_SWEEP = 15


def avatars_in_use(conn: sqlite3.Connection, *, exclude: str | None = None) -> list[str]:
    """Avatars agents with a live credential show; a revoked agent's returns to the pool."""
    return [row[0] for row in conn.execute(
        f"SELECT a.avatar FROM communication_agents a WHERE a.avatar IS NOT NULL AND a.id IS NOT ? AND {LIVE_AGENT}",
        (exclude,))]


def pick_avatar(conn: sqlite3.Connection, requested: str | None, *, agent_id: str | None = None) -> str:
    """A requested avatar must be free among active agents; otherwise one is drawn."""
    taken = avatars_in_use(conn, exclude=agent_id)
    if requested is None:
        return choose(taken)
    if not is_avatar(requested):
        raise AppError(422, "validation", "Choose an avatar from the avatar list")
    if requested in taken:
        raise AppError(409, "avatar_in_use", "Another active agent shows that avatar; choose another")
    return requested


def assign_missing_avatars(conn: sqlite3.Connection) -> None:
    """Upgrade backfill: active agents first, spread across every agent's history."""
    taken = [row[0] for row in conn.execute("SELECT avatar FROM communication_agents WHERE avatar IS NOT NULL")]
    # Frozen: every upgrade runs this, so its rule stays the one it shipped with, not LIVE_AGENT's.
    for row in conn.execute("SELECT a.id FROM communication_agents a WHERE a.avatar IS NULL ORDER BY EXISTS(SELECT 1 FROM communication_credentials cc JOIN credentials cr ON cr.id=cc.credential_id WHERE cc.agent_id=a.id AND cr.revoked=0) DESC,a.created_at,a.id").fetchall():
        avatar = choose(taken)
        conn.execute("UPDATE communication_agents SET avatar=? WHERE id=?", (avatar, row[0]))
        taken.append(avatar)


def bind_credential(conn: sqlite3.Connection, credential_id: str, label: str,
                    agent_id: str | None = None, avatar: str | None = None) -> dict:
    """Credential issuance, rotation and agent registration use this; labels never grant identity.

    A new identity gets the requested avatar or a drawn one. Rotation keeps the avatar
    unless another active agent took it while this one was revoked; then it draws anew.
    """
    existing = conn.execute("SELECT a.* FROM communication_agents a JOIN communication_credentials cc ON cc.agent_id=a.id WHERE cc.credential_id=?", (credential_id,)).fetchone()
    if existing is not None:
        if agent_id is not None and existing["id"] != agent_id:
            raise AppError(409, "identity_binding", "A credential cannot change agent identity")
        return dict(existing)
    if not conn.execute("SELECT 1 FROM credentials WHERE id=?", (credential_id,)).fetchone():
        raise AppError(404, "not_found", "Credential not found")
    if agent_id is None:
        agent_id = credential_id
        conn.execute("INSERT INTO communication_agents(id,label,created_at,avatar) VALUES(?,?,?,?)",
                     (agent_id, text(label, "label", required=True, limit=200), now(), pick_avatar(conn, avatar)))
    else:
        profile = conn.execute("SELECT avatar FROM communication_agents WHERE id=?", (agent_id,)).fetchone()
        if profile is None:
            raise AppError(404, "not_found", "Agent profile not found")
        taken = avatars_in_use(conn, exclude=agent_id)
        if profile[0] is None or profile[0] in taken:
            conn.execute("UPDATE communication_agents SET avatar=? WHERE id=?", (choose(taken), agent_id))
    conn.execute("INSERT INTO communication_credentials(credential_id,agent_id) VALUES(?,?)", (credential_id, agent_id))
    return dict(conn.execute("SELECT * FROM communication_agents WHERE id=?", (agent_id,)).fetchone())


def communication_actor(conn: sqlite3.Connection, original_actor: dict) -> dict:
    if original_actor["kind"] == "human":
        return {"id": "local-user", "label": "You", "kind": "human"}
    row = conn.execute("SELECT a.id,a.label FROM communication_agents a JOIN communication_credentials cc ON cc.agent_id=a.id JOIN credentials c ON c.id=cc.credential_id WHERE c.id=? AND c.revoked=0", (original_actor["id"],)).fetchone()
    if row is None:
        raise AppError(401, "authentication", "Credential is invalid or revoked")
    return {"id": row["id"], "label": row["label"], "kind": "agent"}


def proposer_identity(conn: sqlite3.Connection, actor: dict) -> dict:
    """The identity a proposal's change is written as, where a record names its author as the
    stable identity: the agent whose credential wrote the proposal, even if that credential was
    rotated or revoked before the person approved it, or the person as `local-user`. A credential
    no identity holds stays as it is."""
    if actor["kind"] == "human":
        return {"id": "local-user", "label": "You", "kind": "human"}
    row = conn.execute("SELECT a.id,a.label FROM communication_agents a JOIN communication_credentials cc ON cc.agent_id=a.id "
                       "WHERE cc.credential_id=?", (actor["id"],)).fetchone()
    return {"id": row["id"], "label": row["label"], "kind": "agent"} if row else actor


def registered_by(c: sqlite3.Connection, credential_ids: Iterable[str]) -> dict[str, dict]:
    """The registering agent of each credential; ones issued in Settings are absent."""
    rows = rows_in(c, "SELECT credential_id,registered_by,registered_by_label FROM communication_credentials "
                      "WHERE registered_by IS NOT NULL AND credential_id IN ({})", credential_ids)
    return {row[0]: {"id": row[1], "label": row[2]} for row in rows}


def registered_chats(c: sqlite3.Connection, credential_ids: Iterable[str]) -> dict[str, str]:
    """The chat each registered credential's identity works in; ones issued in Settings, or
    registered before registration named a chat, are absent."""
    rows = rows_in(c, "SELECT credential_id,chat_id FROM communication_credentials "
                      "WHERE chat_id IS NOT NULL AND credential_id IN ({})", credential_ids)
    return {row[0]: row[1] for row in rows}


def identity_chat(c: sqlite3.Connection, agent_id: str) -> str | None:
    """The chat an identity works in: the one its newest credential that names a chat registered
    with, a live one first. Rotating the credential in Settings keeps the identity, and so its chat;
    continuing it in another chat (registration.continue_identity) names the new one."""
    row = c.execute("SELECT cc.chat_id FROM communication_credentials cc JOIN credentials cr ON cr.id=cc.credential_id "
                    "WHERE cc.agent_id=? AND cc.chat_id IS NOT NULL ORDER BY cr.revoked,cr.created_at DESC,cr.rowid DESC LIMIT 1",
                    (agent_id,)).fetchone()
    return row[0] if row else None


class DiscussionService:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _get(c: sqlite3.Connection, table: str, identity: str) -> sqlite3.Row:
        # Table names are internal constants, never input.
        row = c.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Discussion record not found")
        return row

    @staticmethod
    def _tag_id(c: sqlite3.Connection, context_type: str, context_id: str) -> str:
        """A tagged record's own id: a ticket may also be named by its key, T-12, or its number. A key
        that names no ticket stays as it is, as an unknown id of any kind does: a tag of it fails as a
        record that doesn't exist, and a filter by it finds nothing."""
        context_id = text(context_id, "contextId", required=True, limit=500)
        if context_type != "ticket":
            return context_id
        try:
            return str(TicketService.resolve(c, context_id)["id"])
        except AppError as missing:
            if missing.status != 404:
                raise
            return context_id

    @classmethod
    def _scope(cls, c: sqlite3.Connection, context_type: str | None, context_id: str | None,
               kinds: tuple[str, ...] = TAG_KINDS) -> tuple[str, str] | None:
        if context_type is None and context_id is None:
            return None
        if context_type not in kinds or not context_id:
            raise AppError(422, "validation", "Choose both a context type and ID")
        return context_type, cls._tag_id(c, context_type, context_id)

    @classmethod
    def _context(cls, c: sqlite3.Connection, context_type: str, context_id: str) -> str:
        """The id of a record a discussion can be tagged with, which must exist. A merged repository
        answers with the one it was merged into."""
        if context_type not in TAG_KINDS:
            raise AppError(422, "validation", "Choose a kind of record a discussion can be tagged with")
        identity = cls._tag_id(c, context_type, context_id)
        found = record_kinds.rows(c, context_type, [identity])
        if not found:
            raise AppError(422, "validation", "The record to tag does not exist", {"contextId": "not_found"})
        if context_type == "repository" and found[0]["mergedInto"]:
            survivor = c.execute("SELECT name FROM repositories WHERE id=?", (found[0]["mergedInto"],)).fetchone()
            raise AppError(409, "repository_merged", f"{found[0]['title']} was merged into {survivor['name']}; use that repository instead",
                           {"repositoryId": found[0]["mergedInto"]})
        return identity

    @staticmethod
    def _agent(c: sqlite3.Connection, identity: str, *, active: bool = False) -> sqlite3.Row:
        row = c.execute(f"SELECT a.*,{LIVE_AGENT} AS active FROM communication_agents a WHERE a.id=?", (identity,)).fetchone()
        if row is None or (active and not row["active"]):
            raise AppError(422, "validation", "Choose an enrolled agent with an active credential" if active else "Agent profile does not exist")
        return row

    def participants(self, c: sqlite3.Connection, actor: dict) -> dict:
        rows = c.execute(f"SELECT a.id,a.label,a.avatar,{LIVE_AGENT} AS active FROM communication_agents a ORDER BY a.label COLLATE NOCASE,a.id").fetchall()
        return {"items": [{"id": "local-user", "label": "You", "kind": "human", "active": True, "avatar": None},
                          *[{"id": r["id"], "label": r["label"], "kind": "agent", "active": bool(r["active"]), "avatar": r["avatar"]} for r in rows]], "self": actor}

    @staticmethod
    def avatars(c: sqlite3.Connection, actor: dict) -> dict:
        taken = avatars_in_use(c, exclude=actor["id"] if actor["kind"] == "agent" else None)
        own = c.execute("SELECT avatar FROM communication_agents WHERE id=?", (actor["id"],)).fetchone()
        return {"items": [{"name": name, "available": name not in taken} for name in AVATARS],
                "suggested": choose(taken), "current": own["avatar"] if own else None}

    def choose_avatar(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        if actor["kind"] != "agent":
            raise AppError(403, "agent_scope", "Agents choose their own avatar; issue a credential in Settings to choose one for a new agent")
        before = self._agent(c, actor["id"])["avatar"]
        avatar = pick_avatar(c, p["avatar"], agent_id=actor["id"])
        c.execute("UPDATE communication_agents SET avatar=? WHERE id=?", (avatar, actor["id"]))
        event(c, actor, "discussion.avatar", actor["id"], {"avatar": before}, {"avatar": avatar})
        # communication_actor already refused a revoked credential.
        return {**actor, "active": True, "avatar": avatar}

    def role(self, c: sqlite3.Connection, identity: str) -> dict:
        r = self._get(c, "discussion_roles", identity)
        context_table = {"item": "work_items", "roadmap": "roadmaps", "step": "steps"}[r["context_type"]]
        context = c.execute(f"SELECT title FROM {context_table} WHERE id=?", (r["context_id"],)).fetchone()
        owner = self._agent(c, r["owner_id"]) if r["owner_id"] else None
        offer = c.execute("SELECT o.*,a.label AS to_label FROM discussion_role_offers o JOIN communication_agents a ON a.id=o.to_id WHERE role_id=? AND state='pending'", (identity,)).fetchone()
        return {"id": r["id"], "contextType": r["context_type"], "contextId": r["context_id"], "label": r["label"],
                "contextTitle": context["title"] if context else r["context_id"],
                "ownerId": r["owner_id"], "ownerLabel": owner["label"] if owner else None,
                "revision": r["revision"], "generation": r["generation"],
                "offer": {"id": offer["id"], "fromId": offer["from_id"], "toId": offer["to_id"], "toLabel": offer["to_label"], "note": offer["note"]} if offer else None}

    def roles(self, c: sqlite3.Connection, context_type: str | None = None,
              context_id: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        # A role sits on a work item, roadmap or step; any other tag's context holds none.
        scope = self._scope(c, context_type, context_id)
        clause, args = (" WHERE context_type=? AND context_id=?", list(scope)) if scope else ("", [])
        total = c.execute("SELECT count(*) FROM discussion_roles" + clause, args).fetchone()[0]
        ids = c.execute("SELECT id FROM discussion_roles" + clause + " ORDER BY context_type,context_id,label_key,id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return {"items": [self.role(c, r["id"]) for r in ids], "total": total, "limit": limit, "offset": offset}

    def create_role(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        if p["contextType"] not in ROLE_KINDS:
            raise AppError(422, "validation", "A role sits on a work item, roadmap or step")
        self._context(c, p["contextType"], p["contextId"])
        label = text(p["label"], "label", required=True, limit=200)
        if c.execute("SELECT 1 FROM discussion_roles WHERE context_type=? AND context_id=? AND label_key=?", (p["contextType"], p["contextId"], label.casefold())).fetchone():
            raise AppError(409, "role_exists", "This work context already has that responsibility role")
        identity, at = uid(), now()
        c.execute("INSERT INTO discussion_roles(id,context_type,context_id,label,label_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (identity, p["contextType"], p["contextId"], label, label.casefold(), at, at))
        result = self.role(c, identity)
        self._emit(c, actor, "discussion.role.create", identity, None, result, role_id=identity)
        return result

    def change_role(self, c: sqlite3.Connection, actor: dict, identity: str,
                    action: str, p: dict) -> dict:
        row = self._get(c, "discussion_roles", identity)
        require_revision(row, p["revision"])
        before = self.role(c, identity)
        owner, generation, at = row["owner_id"], row["generation"], now()
        offer = c.execute("SELECT * FROM discussion_role_offers WHERE role_id=? AND state='pending'", (identity,)).fetchone()
        new_owner = owner
        ownership_change = False
        if action == "reassign":
            self._human(actor)
            text(p.get("reason"), "reason", required=True, limit=2000)
            new_owner = p.get("ownerId")
            if new_owner is not None:
                self._agent(c, new_owner, active=True)
            if new_owner == owner:
                raise AppError(409, "unchanged_owner", "Choose a different owner or release the role")
            ownership_change = True
        else:
            if actor["kind"] != "agent":
                raise AppError(403, "role_authority", "Only an agent may claim or accept an agent role; use human reassignment")
            if action == "claim":
                if owner is not None:
                    raise AppError(409, "role_owned", "This role already has an owner")
                new_owner, ownership_change = actor["id"], True
            elif action in {"release", "offer", "withdraw"}:
                if owner != actor["id"]:
                    raise AppError(403, "role_authority", "Only the current role owner may do that")
                if action == "release":
                    new_owner, ownership_change = None, True
                elif action == "offer":
                    successor = p["successorId"]
                    self._agent(c, successor, active=True)
                    if successor == owner:
                        raise AppError(422, "validation", "Choose another agent for the handoff")
                    if offer is not None:
                        raise AppError(409, "handoff_pending", "Withdraw the pending offer before offering another handoff")
                    c.execute("INSERT INTO discussion_role_offers(id,role_id,from_id,to_id,generation,note,state,created_at) VALUES(?,?,?,?,?,?,'pending',?)", (uid(), identity, owner, successor, generation, text(p.get("note", ""), "note", limit=2000), at))
                else:
                    if offer is None:
                        raise AppError(409, "no_handoff", "There is no pending handoff offer")
                    c.execute("UPDATE discussion_role_offers SET state='withdrawn',ended_at=? WHERE id=?", (at, offer["id"]))
            elif action in {"accept", "decline"}:
                if offer is None:
                    raise AppError(409, "no_handoff", "There is no pending handoff offer")
                if offer["to_id"] != actor["id"]:
                    raise AppError(403, "role_authority", "Only the named successor may respond to this offer")
                if offer["from_id"] != owner or offer["generation"] != generation:
                    raise AppError(409, "stale_handoff", "The role owner changed; this handoff is no longer valid")
                c.execute("UPDATE discussion_role_offers SET state=?,ended_at=? WHERE id=?", ("accepted" if action == "accept" else "declined", at, offer["id"]))
                if action == "accept":
                    new_owner, ownership_change = actor["id"], True
            else:
                raise AppError(422, "validation", "Unknown role action")
        if ownership_change:
            c.execute("UPDATE discussion_role_offers SET state='invalidated',ended_at=? WHERE role_id=? AND state='pending'", (at, identity))
        c.execute("UPDATE discussion_roles SET owner_id=?,generation=?,revision=revision+1,updated_at=? WHERE id=?", (new_owner, generation + int(ownership_change), at, identity))
        result = self.role(c, identity)
        self._emit(c, actor, "discussion.role." + action, identity, before,
                   {**result, **({"reason": p["reason"]} if action == "reassign" else {})}, role_id=identity)
        return result

    @staticmethod
    def _human(actor: dict) -> None:
        if actor["kind"] != "human":
            raise AppError(403, "agent_scope", "Only the person may do that")

    @staticmethod
    def _contexts(c: sqlite3.Connection, discussion_id: str) -> list[dict]:
        rows = c.execute("""SELECT context_type,context_id FROM discussion_contexts WHERE discussion_id=?
            UNION SELECT r.context_type,r.context_id FROM discussion_roles r JOIN discussion_messages m ON m.on_behalf_of_role_id=r.id WHERE m.discussion_id=?
            UNION SELECT r.context_type,r.context_id FROM discussion_roles r JOIN discussion_targets t ON t.kind='role' AND t.recipient_id=r.id JOIN discussion_messages m ON m.id=t.message_id WHERE m.discussion_id=?
            ORDER BY context_type,context_id""", (discussion_id, discussion_id, discussion_id)).fetchall()
        return [{"type": row[0], "id": row[1]} for row in rows]

    @staticmethod
    def _identities(c: sqlite3.Connection, ids: set[str]) -> dict[str, dict]:
        """The person and the agent identities these ids name, by the labels they have now."""
        found = {"local-user": dict(PERSON)} if "local-user" in ids else {}
        for row in rows_in(c, "SELECT id,label FROM communication_agents WHERE id IN ({})", [i for i in ids if i != "local-user"]):
            found[row["id"]] = {"id": row["id"], "label": row["label"], "kind": "agent"}
        return found

    @staticmethod
    def _described(c: sqlite3.Connection, contexts: builtins.list[dict], tags: dict[tuple[str, str], sqlite3.Row]) -> builtins.list[dict]:
        """Each context with what a reader needs to show it: its record's title and archived state, what
        a page needs to link to it, and, for a tag, who added it and when, and whether it is home. A
        context a role brings in, with no tag of its own, has no `addedBy`."""
        records: dict[tuple[str, str], dict] = {}
        for kind in TAG_KINDS:
            ids = [ctx["id"] for ctx in contexts if ctx["type"] == kind]
            for row in record_kinds.rows(c, kind, ids) if ids else []:
                records[(kind, row["id"])] = record_kinds.summary(row)
        adders = DiscussionService._identities(c, {tag["added_by"] for tag in tags.values()})
        order = {kind: position for position, kind in enumerate(TAG_KINDS)}
        described = []
        for ctx in sorted(contexts, key=lambda ctx: (order.get(ctx["type"], len(order)), ctx["id"])):
            if ctx["type"] not in order:
                # A kind Worklog no longer takes, such as a release tag written behind its back since v28
                # dropped releases (decision R6): reads leave it out, and `worklog doctor` reports it.
                continue
            key = (ctx["type"], ctx["id"])
            tag, details = tags.get(key), {k: v for k, v in records.get(key, {}).items() if k != "id"}
            described.append({"type": ctx["type"], "id": ctx["id"], "title": None, "archived": False, **details,
                              "home": bool(tag["home"]) if tag else False,
                              "addedBy": adders.get(tag["added_by"]) if tag else None,
                              "addedAt": tag["created_at"] if tag else None})
        return described

    def summary(self, c: sqlite3.Connection, identity: str) -> dict:
        r = self._get(c, "discussions", identity)
        tags = {(t["context_type"], t["context_id"]): t
                for t in c.execute("SELECT * FROM discussion_contexts WHERE discussion_id=?", (identity,))}
        contexts = self._described(c, self._contexts(c, identity), tags)
        home = next((ctx for ctx in contexts if ctx["home"]), None)
        repos = [row[0] for row in c.execute("SELECT repo FROM discussion_repos WHERE discussion_id=? ORDER BY repo", (identity,))]
        pending = c.execute("SELECT count(*) FROM discussion_requests r JOIN discussion_messages m ON m.id=r.message_id WHERE m.discussion_id=? AND r.state='pending'", (identity,)).fetchone()[0]
        return {"id": r["id"], "title": r["title"], "archived": bool(r["archived"]), "revision": r["revision"],
                "createdAt": r["created_at"], "updatedAt": r["updated_at"],
                "pinned": r["pinned_at"] is not None, "pinnedAt": r["pinned_at"], "contexts": contexts, "repos": repos,
                "pendingCount": pending, "repository": {"id": home["id"], "name": home["title"]} if home else None}

    def create(self, c: sqlite3.Connection, actor: dict, p: dict, *, home: tuple[str, str] | None = None) -> dict:
        """Start a thread with its first message. `home` names the tag that makes it a repository's own
        discussion, which only Worklog opens (open_repository_discussion)."""
        body = text(p["text"], "text", required=True)
        if p.get("repos"):
            # K8: the labels a discussion already has stay readable, and new ones are refused.
            raise AppError(422, "validation", "Free-text repository labels are retired: tag the discussion with a "
                           "repository from the catalog instead, as a context of type repository", {"repos": "retired"})
        title = text(p.get("title") or body.splitlines()[0][:120], "title", required=True, limit=1000)
        requested = p.get("contexts", [])
        if len(requested) > TAG_LIMIT:
            raise AppError(422, "validation", f"A discussion holds at most {TAG_LIMIT} tags", {"contexts": "limit"})
        contexts = list(dict.fromkeys((ctx["type"], self._context(c, ctx["type"], ctx["id"])) for ctx in requested))
        identity, at = uid(), now()
        c.execute("INSERT INTO discussions(id,title,created_at,updated_at) VALUES(?,?,?,?)", (identity, title, at, at))
        c.executemany("INSERT INTO discussion_contexts(discussion_id,context_type,context_id,home,added_by,added_by_kind,created_at) "
                      "VALUES(?,?,?,?,?,?,?)", [(identity, kind, context_id, int((kind, context_id) == home), actor["id"],
                                                 actor["kind"], at) for kind, context_id in contexts])
        search.put(c, identity, "discussion", title, "", identity, replace=False)
        message = self.post(c, actor, identity, p, first=True)
        return {"discussion": self.summary(c, identity), "message": message}

    def post(self, c: sqlite3.Connection, actor: dict, discussion_id: str,
             p: dict, *, first: bool = False) -> dict:
        discussion = self._get(c, "discussions", discussion_id)
        body = text(p["text"], "text", required=True)
        chat = p.get("chatId")
        if chat is not None:
            chat = text(chat, "chatId", required=True, limit=500)
        role_id = p.get("onBehalfOfRoleId")
        generation = None
        if role_id:
            role = self._get(c, "discussion_roles", role_id)
            if actor["kind"] != "agent" or role["owner_id"] != actor["id"]:
                raise AppError(403, "role_authority", "Only the current role owner may speak on its behalf")
            generation = role["generation"]
        identity, at = uid(), now()
        c.execute("INSERT INTO discussion_messages(id,discussion_id,text,author_id,author_label,author_kind,created_at,chat_id,"
                  "on_behalf_of_role_id,ownership_generation) VALUES(?,?,?,?,?,?,?,?,?,?)", (identity, discussion_id, body, actor["id"], actor["label"], actor["kind"], at, chat, role_id, generation))
        ack = p.get("acknowledgement")
        if ack is not None:
            self._create_request(c, identity, ack, actor)
        if not first:
            c.execute("UPDATE discussions SET revision=revision+1,updated_at=? WHERE id=?", (at, discussion_id))
        search.put(c, identity, "discussion_message", discussion["title"], body, discussion_id, bool(discussion["archived"]),
                   replace=False)
        result = self.message(c, identity, actor)
        self._emit(c, actor, "discussion.create" if first else "discussion.message", identity, None, result, discussion_id=discussion_id, message_id=identity)
        return result

    def _create_request(self, c: sqlite3.Connection, message_id: str, ack: dict, author: dict) -> None:
        mode = ack.get("mode", "all")
        targets = ack.get("targets", [])
        if mode not in {"all", "any", "any_agent", "anyone"} or ((mode in {"all", "any"}) != bool(targets)):
            raise AppError(422, "validation", "Choose named targets or an open audience")
        seen = set()
        normalized = []
        if mode in {"any_agent", "anyone"}:
            normalized.append(("open_agent" if mode == "any_agent" else "open_any", ""))
        for index, target in enumerate(targets):
            kind, recipient = target["kind"], target["id"]
            if (kind, recipient) in seen:
                raise AppError(422, "validation", "Choose each target only once")
            seen.add((kind, recipient))
            # Nobody acknowledges their own message, so a request naming its author could never be met.
            if (kind, recipient) == (author["kind"], author["id"]):
                raise AppError(422, "validation", "Nobody acknowledges their own message; name someone else",
                               {f"acknowledgement.targets.{index}.id": "self"})
            if kind == "agent":
                self._agent(c, recipient)
            elif kind == "role":
                self._get(c, "discussion_roles", recipient)
            elif kind != "human" or recipient != "local-user":
                raise AppError(422, "validation", "Unknown acknowledgement target")
            normalized.append((kind, recipient))
        urgent = bool(ack.get("urgent"))
        seconds = ack.get("deadlineSeconds")
        wait = timedelta(seconds=seconds) if seconds is not None else URGENT_DEADLINE if urgent else None
        deadline = (datetime.now(UTC) + wait).isoformat() if wait is not None else None
        c.execute("INSERT INTO discussion_requests(message_id,mode,deadline_at,urgent) VALUES(?,?,?,?)",
                  (message_id, mode, deadline, int(urgent)))
        c.executemany("INSERT INTO discussion_targets(id,message_id,kind,recipient_id) VALUES(?,?,?,?)",
                      [(uid(), message_id, kind, recipient) for kind, recipient in normalized])

    @staticmethod
    def _receipt(r: sqlite3.Row) -> dict:
        return {"id": r["id"], "targetId": r["target_id"], "actorId": r["actor_id"], "actorLabel": r["actor_label"], "actorKind": r["actor_kind"],
                "roleId": r["role_id"], "ownershipGeneration": r["ownership_generation"], "createdAt": r["created_at"], "late": bool(r["late"])}

    def _target(self, c: sqlite3.Connection, r: sqlite3.Row, actor: dict,
                author_id: str) -> tuple[dict, bool]:
        kind, recipient = r["kind"], r["recipient_id"]
        owner_id, generation = None, None
        if kind == "agent":
            label = self._agent(c, recipient)["label"]
            eligible = actor["kind"] == "agent" and actor["id"] == recipient
        elif kind == "human":
            label = "You"
            eligible = actor["kind"] == "human"
        elif kind == "role":
            role = self._get(c, "discussion_roles", recipient)
            owner_id, generation, label = role["owner_id"], role["generation"], role["label"]
            eligible = actor["kind"] == "agent" and owner_id == actor["id"]
        else:
            label = "Any other agent" if kind == "open_agent" else "Any other participant"
            eligible = kind == "open_any" or actor["kind"] == "agent"
        receipts = c.execute("SELECT * FROM discussion_acknowledgements WHERE target_id=? ORDER BY created_at,id", (r["id"],)).fetchall()
        eligible = eligible and actor["id"] != author_id
        return ({"id": r["id"], "kind": kind, "recipientId": recipient, "label": label,
                 "ownerId": owner_id, "ownershipGeneration": generation, "acknowledged": bool(receipts),
                 "acknowledgedBy": receipts[0]["actor_id"] if receipts else None}, eligible)

    def message(self, c: sqlite3.Connection, identity: str, actor: dict) -> dict:
        m = self._get(c, "discussion_messages", identity)
        result = {"id": m["id"], "discussionId": m["discussion_id"], "text": m["text"], "authorId": m["author_id"], "authorLabel": m["author_label"],
                  "authorKind": m["author_kind"], "createdAt": m["created_at"], "chatId": m["chat_id"], "onBehalfOfRoleId": m["on_behalf_of_role_id"],
                  "ownershipGeneration": m["ownership_generation"], "request": None}
        r = c.execute("SELECT * FROM discussion_requests WHERE message_id=?", (identity,)).fetchone()
        if r is None:
            return result
        targets, actionable = [], []
        for target in c.execute("SELECT * FROM discussion_targets WHERE message_id=? ORDER BY id", (identity,)).fetchall():
            view, eligible = self._target(c, target, actor, m["author_id"])
            view.update(self._delivery(c, m, view))
            targets.append(view)
            if eligible and not view["acknowledged"]:
                actionable.append(view["id"])
        managed = m["author_id"] == actor["id"] if m["on_behalf_of_role_id"] is None else self._get(c, "discussion_roles", m["on_behalf_of_role_id"])["owner_id"] == actor["id"]
        classifications = []
        if m["author_id"] == actor["id"]:
            classifications.append("personal")
        if managed:
            classifications.append("managed")
            if m["author_id"] != actor["id"]:
                classifications.append("predecessor")
        if actionable:
            classifications.append("actionable")
        sender_unassigned = m["on_behalf_of_role_id"] and self._get(c, "discussion_roles", m["on_behalf_of_role_id"])["owner_id"] is None
        if sender_unassigned or any(t["kind"] == "role" and t["ownerId"] is None and not t["acknowledged"] for t in targets):
            classifications.append("unassigned")
        receipts = [self._receipt(row) for row in c.execute("SELECT a.* FROM discussion_acknowledgements a JOIN discussion_targets t ON t.id=a.target_id WHERE t.message_id=? ORDER BY a.created_at,a.id", (identity,))]
        result["request"] = {"id": identity, "mode": r["mode"], "state": r["state"], "targets": targets, "receipts": receipts,
                             "actionableTargetIds": actionable, "canCancel": bool(managed and r["state"] == "pending"),
                             "canRelease": actor["kind"] == "human" and r["state"] == "pending", "classifications": classifications,
                             "reason": r["reason"], "terminalAt": r["terminal_at"], "terminalActorId": r["terminal_actor_id"],
                             "terminalActorLabel": r["terminal_actor_label"], "terminalActorKind": r["terminal_actor_kind"],
                             "authorityRoleId": r["authority_role_id"], "authorityGeneration": r["authority_generation"],
                             "deadlineAt": r["deadline_at"], "urgent": bool(r["urgent"]), "escalatedAt": r["escalated_at"],
                             # Pending past its deadline: escalated, or due at the watcher's next sweep.
                             "overdue": bool(r["state"] == "pending" and r["deadline_at"] and (
                                 r["escalated_at"] is not None or datetime.fromisoformat(r["deadline_at"]) <= datetime.now(UTC)))}
        return result

    @staticmethod
    def presence(c: sqlite3.Connection, agent_id: str) -> dict:
        """Whether an agent can be reached, as far as its own reports say: whether its credential is
        live, when it last did anything (a run started, participation reported, an update or a
        message, as the Agents page counts it), whether that is STALE_AFTER ago or more, and how
        many runs it has open. A fresh report does not prove its chat is still running."""
        row = c.execute(
            f"SELECT {LIVE_AGENT} AS active,(SELECT count(*) FROM agent_runs WHERE agent_id=a.id AND ended_at IS NULL) AS open_runs,"
            "max(COALESCE((SELECT max(created_at) FROM agent_runs WHERE agent_id=a.id),''),"
            "COALESCE((SELECT max(p.last_seen_at) FROM participations p JOIN agent_runs ar ON ar.id=p.run_id WHERE ar.agent_id=a.id),''),"
            "COALESCE((SELECT max(created_at) FROM coordination_updates WHERE agent_id=a.id),''),"
            "COALESCE((SELECT max(created_at) FROM discussion_messages WHERE author_id=a.id AND author_kind='agent'),'')) AS last "
            "FROM communication_agents a WHERE a.id=?", (agent_id,)).fetchone()
        if row is None:
            return {"active": False, "lastSeenAt": None, "stale": True, "openRuns": 0}
        last = row["last"] or None
        return {"active": bool(row["active"]), "lastSeenAt": last, "openRuns": row["open_runs"],
                "stale": last is None or datetime.fromisoformat(last) < datetime.now(UTC) - STALE_AFTER}

    def _delivery(self, c: sqlite3.Connection, m: sqlite3.Row, view: dict) -> dict:
        """How far a target slot of message m's request got (v33): pending, then seen when a command of
        the target's listed or read it, acknowledged, and answered once the target posted in the
        thread after the request. Delivery is the furthest; each step keeps its own time. An agent
        slot, or a role's owner, says whether it can be reached (`presence`)."""
        kind, recipient = view["kind"], view["recipientId"]
        seen = c.execute("SELECT min(seen_at) FROM discussion_seen WHERE target_id=?", (view["id"],)).fetchone()[0]
        acknowledged = c.execute("SELECT min(created_at) FROM discussion_acknowledgements WHERE target_id=?", (view["id"],)).fetchone()[0]
        if kind == "agent":
            who, args = "author_kind='agent' AND author_id=?", [recipient]
        elif kind == "human":
            who, args = "author_kind='human'", []
        elif kind == "role":
            who, args = "(on_behalf_of_role_id=? OR (author_kind='agent' AND author_id=?))", [recipient, view["ownerId"] or ""]
        else:
            who, args = ("author_kind='agent' AND " if kind == "open_agent" else "") + "author_id<>?", [m["author_id"]]
        answer = c.execute(f"SELECT id,created_at FROM discussion_messages WHERE discussion_id=? AND created_at>? AND {who} "
                           "ORDER BY created_at,id LIMIT 1", [m["discussion_id"], m["created_at"], *args]).fetchone()
        reached = 3 if answer else 2 if acknowledged else 1 if seen else 0
        holder = recipient if kind == "agent" else view["ownerId"] if kind == "role" else None
        return {"delivery": DELIVERY[reached], "seenAt": seen, "acknowledgedAt": acknowledged,
                "answeredAt": answer["created_at"] if answer else None, "answerMessageId": answer["id"] if answer else None,
                "presence": self.presence(c, holder) if holder else None}

    def seen_candidates(self, c: sqlite3.Connection, actor: dict, message_ids: Iterable[str]) -> builtins.list[str]:
        """The slots of these messages' pending requests that wait on the caller and that it has not
        seen yet: what reading them now delivers. Read in the caller's snapshot, so that a read
        writes only when there is something to record."""
        found = []
        for row in rows_in(c, "SELECT t.*,m.author_id FROM discussion_targets t JOIN discussion_requests r ON r.message_id=t.message_id "
                              "JOIN discussion_messages m ON m.id=t.message_id WHERE r.state='pending' AND t.message_id IN ({})",
                           message_ids):
            if c.execute("SELECT 1 FROM discussion_acknowledgements WHERE target_id=?", (row["id"],)).fetchone():
                continue
            if c.execute("SELECT 1 FROM discussion_seen WHERE target_id=? AND actor_id=?", (row["id"], actor["id"])).fetchone():
                continue
            if self._target(c, row, actor, row["author_id"])[1]:
                found.append(row["id"])
        return found

    @staticmethod
    def record_seen(c: sqlite3.Connection, actor: dict, target_ids: Iterable[str]) -> int:
        """Record that the caller has seen these slots, the first time only. A seen mark is delivery
        bookkeeping, not conversation: it writes no event and leaves the thread's revision alone."""
        stamp = now()
        return sum(c.execute("INSERT OR IGNORE INTO discussion_seen(target_id,actor_id,seen_at) VALUES(?,?,?)",
                             (target_id, actor["id"], stamp)).rowcount for target_id in target_ids)

    def _listed(self, c: sqlite3.Connection, actor: dict, message_ids: Iterable[str]) -> builtins.list[dict]:
        """Messages as request lists show them: each with its thread's id and title."""
        result = []
        for identity in message_ids:
            message = self.message(c, identity, actor)
            title = c.execute("SELECT title FROM discussions WHERE id=?", (message["discussionId"],)).fetchone()[0]
            result.append({"discussion": {"id": message["discussionId"], "title": title}, "message": message})
        return result

    @staticmethod
    def _managed(actor: dict, alias: str = "m") -> tuple[str, builtins.list[str]]:
        """Messages the caller manages: written in its own name, or in the name of a role it owns."""
        return (f"(({alias}.on_behalf_of_role_id IS NULL AND {alias}.author_id=?) OR EXISTS(SELECT 1 FROM discussion_roles sr "
                f"WHERE sr.id={alias}.on_behalf_of_role_id AND sr.owner_id=?))", [actor["id"], actor["id"]])

    @staticmethod
    def _received(actor: dict) -> tuple[str, builtins.list[str]]:
        """Requests with a slot for the caller: named, through a role it owns, or open to it. The
        person also receives every request that escalated."""
        if actor["kind"] == "human":
            return ("(r.escalated_at IS NOT NULL OR EXISTS(SELECT 1 FROM discussion_targets t WHERE t.message_id=m.id "
                    "AND t.kind IN('human','open_any')))", [])
        return ("m.author_id<>? AND EXISTS(SELECT 1 FROM discussion_targets t LEFT JOIN discussion_roles tr ON t.kind='role' "
                "AND tr.id=t.recipient_id WHERE t.message_id=m.id AND ((t.kind='agent' AND t.recipient_id=?) OR "
                "(t.kind='role' AND tr.owner_id=?) OR t.kind IN('open_agent','open_any')))", [actor["id"]] * 3)

    def requests(self, c: sqlite3.Connection, actor: dict, *, box: str = "received", state: str = "pending",
                 limit: int = 50, offset: int = 0) -> dict:
        """Acknowledgement requests, newest first: those the caller sent (`sent`: written in its name or
        its role's) or those with a slot for it (`received`), pending, ended or all. Each request says
        how far each slot got and whether its agents can be reached."""
        if box not in {"sent", "received"} or state not in {"pending", "ended", "all"}:
            raise AppError(422, "validation", "Choose sent or received, and pending, ended or all")
        clause, args = self._managed(actor) if box == "sent" else self._received(actor)
        if state != "all":
            clause += " AND r.state" + ("='pending'" if state == "pending" else "<>'pending'")
        query = " FROM discussion_messages m JOIN discussion_requests r ON r.message_id=m.id WHERE " + clause
        total = c.execute("SELECT count(*)" + query, args).fetchone()[0]
        ids = [row[0] for row in c.execute("SELECT m.id" + query + " ORDER BY m.created_at DESC,m.id DESC LIMIT ? OFFSET ?",
                                           [*args, limit, offset])]
        return {"items": self._listed(c, actor, ids), "total": total, "limit": limit, "offset": offset}

    def inbox(self, c: sqlite3.Connection, actor: dict, *, since: str | None = None) -> dict:
        """What waits on the caller and what came back to it, with a cursor to wait from.

        `received`: the pending requests with a slot the caller may acknowledge now, newest first;
        `unseen` counts those the caller had not seen. `answers`: messages others posted in the thread
        of a request the caller manages, after that request: since the cursor, or with none, the
        replies to its pending requests. `changes`: acknowledgements, cancels, releases and
        escalations of the requests it manages since the cursor (none without one). The cursor is the
        discussions feed's (`changes`), so either reads on from the other."""
        seq = self._decode_cursor(c, since, None) if since is not None else None
        head = c.execute("SELECT COALESCE(max(seq),0) FROM discussion_changes").fetchone()[0]
        clause, args = self._received(actor)
        pending = [row[0] for row in c.execute("SELECT m.id FROM discussion_messages m JOIN discussion_requests r ON r.message_id=m.id "
                                               f"WHERE r.state='pending' AND {clause} ORDER BY m.created_at DESC,m.id DESC", args)]
        received: builtins.list[dict] = []
        unseen = 0
        for item in self._listed(c, actor, pending):
            mine = item["message"]["request"]["actionableTargetIds"]
            if not mine:
                continue
            if len(received) < INBOX_LIMIT:
                received.append(item)
            if not any(c.execute("SELECT 1 FROM discussion_seen WHERE target_id=? AND actor_id=?", (slot, actor["id"])).fetchone()
                       for slot in mine):
                unseen += 1
        managed, managed_args = self._managed(actor, "sm")
        earlier = ("EXISTS(SELECT 1 FROM discussion_messages sm JOIN discussion_requests sr ON sr.message_id=sm.id WHERE "
                   f"sm.discussion_id=m.discussion_id AND sm.created_at<m.created_at AND {managed}"
                   + (")" if seq is not None else " AND sr.state='pending')"))
        if seq is not None:
            answers = [row[0] for row in c.execute(
                "SELECT m.id FROM discussion_changes ch JOIN discussion_messages m ON m.id=ch.message_id WHERE ch.seq>? "
                f"AND ch.kind IN('discussion.message','discussion.create') AND m.author_id<>? AND {earlier} ORDER BY ch.seq LIMIT ?",
                [seq, actor["id"], *managed_args, INBOX_LIMIT])]
            sent, sent_args = self._managed(actor)
            changes = c.execute(
                "SELECT ch.*,e.actor_id,e.actor_label,e.actor_kind FROM discussion_changes ch JOIN events e ON e.id=ch.event_id "
                f"JOIN discussion_messages m ON m.id=ch.message_id WHERE ch.seq>? AND ch.kind IN({','.join('?' * len(REQUEST_CHANGES))}) "
                f"AND {sent} ORDER BY ch.seq LIMIT ?", [seq, *REQUEST_CHANGES, *sent_args, INBOX_LIMIT]).fetchall()
        else:
            answers = [row[0] for row in c.execute(
                f"SELECT m.id FROM discussion_messages m WHERE m.author_id<>? AND {earlier} ORDER BY m.created_at DESC,m.id DESC LIMIT ?",
                [actor["id"], *managed_args, INBOX_LIMIT])]
            changes = []
        return {"received": received, "unseen": unseen, "answers": self._listed(c, actor, answers),
                "changes": [{"seq": r["seq"], "kind": r["kind"], "discussionId": r["discussion_id"], "messageId": r["message_id"],
                             "roleId": r["role_id"], "actorId": r["actor_id"], "actorLabel": r["actor_label"],
                             "actorKind": r["actor_kind"], "createdAt": r["created_at"]} for r in changes],
                "cursor": self._cursor(c, None, head)}

    @staticmethod
    def overdue(c: sqlite3.Connection, at: datetime | None = None) -> bool:
        """Whether a pending request's deadline has passed without it escalating: read first, so the
        watcher takes the write lock only when there may be something to escalate."""
        moment = at or datetime.now(UTC)
        return any(datetime.fromisoformat(row[0]) <= moment for row in c.execute(
            "SELECT deadline_at FROM discussion_requests WHERE state='pending' AND deadline_at IS NOT NULL AND escalated_at IS NULL"))

    def escalate_overdue(self, c: sqlite3.Connection, at: datetime | None = None) -> builtins.list[str]:
        """Escalate every pending request whose deadline has passed while some slot has neither
        acknowledged nor answered: mark it escalated and record the change, which reaches its sender's
        inbox and the person (Live counts it as theirs, Discussions marks it). The change is written
        as the request's sender, whose deadline it was. Returns the messages it escalated; the serve
        watcher calls it every ESCALATION_SWEEP seconds, when `overdue` finds one due."""
        moment = at or datetime.now(UTC)
        escalated = []
        for row in c.execute("SELECT r.message_id,r.deadline_at,m.author_id,m.author_label,m.author_kind,m.discussion_id FROM "
                             "discussion_requests r JOIN discussion_messages m ON m.id=r.message_id WHERE r.state='pending' "
                             "AND r.deadline_at IS NOT NULL AND r.escalated_at IS NULL ORDER BY r.deadline_at").fetchall():
            if datetime.fromisoformat(row["deadline_at"]) > moment:
                continue
            sender = {"id": row["author_id"], "label": row["author_label"], "kind": row["author_kind"]}
            before = self.message(c, row["message_id"], sender)
            if all(target["delivery"] in {"acknowledged", "answered"} for target in before["request"]["targets"]):
                continue
            c.execute("UPDATE discussion_requests SET escalated_at=? WHERE message_id=? AND escalated_at IS NULL",
                      (moment.isoformat(), row["message_id"]))
            self._touch(c, row["discussion_id"])
            self._emit(c, sender, "discussion.request.escalate", row["message_id"], before, self.message(c, row["message_id"], sender),
                       discussion_id=row["discussion_id"], message_id=row["message_id"])
            escalated.append(row["message_id"])
        return escalated

    def acknowledge(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        m = self._get(c, "discussion_messages", identity)
        request = c.execute("SELECT * FROM discussion_requests WHERE message_id=?", (identity,)).fetchone()
        if request is None:
            raise AppError(409, "no_request", "This message has no acknowledgement request")
        if m["author_id"] == actor["id"]:
            raise AppError(403, "self_acknowledgement", "The actual author cannot acknowledge their own message")
        targets = c.execute("SELECT * FROM discussion_targets WHERE message_id=? ORDER BY id", (identity,)).fetchall()
        selected = p.get("targetIds")
        if selected is None:
            if request["mode"] not in {"any_agent", "anyone"}:
                raise AppError(422, "validation", "Choose explicit acknowledgement target slots")
            selected = [targets[0]["id"]]
        if not selected or len(set(selected)) != len(selected):
            raise AppError(422, "validation", "Choose each target slot only once")
        target_map = {t["id"]: t for t in targets}
        prepared = []
        for target_id in selected:
            target = target_map.get(target_id)
            if target is None:
                raise AppError(422, "validation", "Target slot does not belong to this message")
            if c.execute("SELECT 1 FROM discussion_acknowledgements WHERE target_id=? AND actor_id=?", (target_id, actor["id"])).fetchone():
                continue
            view, eligible = self._target(c, target, actor, m["author_id"])
            if not eligible:
                raise AppError(403, "acknowledgement_authority", "You are not eligible to acknowledge that target slot")
            prepared.append((target, view))
        before = self.message(c, identity, actor)
        for target, view in prepared:
            c.execute("INSERT INTO discussion_acknowledgements(id,target_id,actor_id,actor_label,actor_kind,role_id,"
                      "ownership_generation,created_at,late) VALUES(?,?,?,?,?,?,?,?,?)", (uid(), target["id"], actor["id"], actor["label"], actor["kind"], target["recipient_id"] if target["kind"] == "role" else None, view["ownershipGeneration"], now(), int(request["state"] != "pending" or view["acknowledged"])))
        count = c.execute("SELECT count(*) FROM discussion_targets t WHERE message_id=? AND EXISTS(SELECT 1 FROM discussion_acknowledgements a WHERE a.target_id=t.id)", (identity,)).fetchone()[0]
        satisfied = count == len(targets) if request["mode"] == "all" else count > 0
        if request["state"] == "pending" and satisfied:
            c.execute("UPDATE discussion_requests SET state='satisfied',terminal_at=?,terminal_actor_id=?,terminal_actor_label=?,terminal_actor_kind=? WHERE message_id=? AND state='pending'", (now(), actor["id"], actor["label"], actor["kind"], identity))
        result = self.message(c, identity, actor)
        if prepared:
            self._touch(c, m["discussion_id"])
            self._emit(c, actor, "discussion.acknowledge", identity, before, result, discussion_id=m["discussion_id"], message_id=identity)
        return result

    def finish_request(self, c: sqlite3.Connection, actor: dict, identity: str,
                       action: str, p: dict) -> dict:
        m = self._get(c, "discussion_messages", identity)
        before = self.message(c, identity, actor)
        if before["request"] is None:
            raise AppError(409, "no_request", "This message has no acknowledgement request")
        role_id, generation = None, None
        reason = None
        if action == "release":
            self._human(actor)
            reason = text(p.get("reason"), "reason", required=True, limit=2000)
            state = "released_by_human"
        elif action == "cancel":
            state = "cancelled"
            role_id = m["on_behalf_of_role_id"]
            if role_id:
                role = self._get(c, "discussion_roles", role_id)
                if actor["kind"] != "agent" or role["owner_id"] != actor["id"]:
                    raise AppError(403, "request_authority", "Only the current sender-role owner may cancel this request")
                generation = role["generation"]
            elif m["author_id"] != actor["id"]:
                raise AppError(403, "request_authority", "Only the original author may cancel this personal request")
        else:
            raise AppError(422, "validation", "Unknown request action")
        if before["request"]["state"] != "pending":
            raise AppError(409, "request_terminal", "This request already has a permanent outcome")
        c.execute("UPDATE discussion_requests SET state=?,terminal_at=?,terminal_actor_id=?,terminal_actor_label=?,terminal_actor_kind=?,reason=?,authority_role_id=?,authority_generation=? WHERE message_id=? AND state='pending'", (state, now(), actor["id"], actor["label"], actor["kind"], reason, role_id, generation, identity))
        result = self.message(c, identity, actor)
        self._touch(c, m["discussion_id"])
        self._emit(c, actor, "discussion.request." + action, identity, before, result, discussion_id=m["discussion_id"], message_id=identity)
        return result

    @staticmethod
    def _touch(c: sqlite3.Connection, discussion_id: str) -> None:
        c.execute("UPDATE discussions SET updated_at=?,revision=revision+1 WHERE id=?", (now(), discussion_id))

    def archive(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        self._human(actor)
        row = self._get(c, "discussions", identity)
        require_revision(row, p["revision"])
        before = self.summary(c, identity)
        c.execute("UPDATE discussions SET archived=?,revision=revision+1,updated_at=? WHERE id=?", (int(p["archived"]), now(), identity))
        search.set_archived(c, ("discussion", "discussion_message"), identity, p["archived"])
        result = self.summary(c, identity)
        self._emit(c, actor, "discussion.archive" if p["archived"] else "discussion.reopen", identity, before, result, discussion_id=identity)
        return result

    def tag(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        """Tag a discussion with a record it is about. Anyone may, on any thread, archived ones too; a
        tag it already has changes nothing. A tag is not conversation, so the thread keeps its place
        in the list: its revision moves, and its updated_at does not."""
        self._get(c, "discussions", identity)
        kind = p["type"]
        context_id = self._context(c, kind, p["id"])
        if c.execute("SELECT 1 FROM discussion_contexts WHERE discussion_id=? AND context_type=? AND context_id=?",
                     (identity, kind, context_id)).fetchone():
            return self.summary(c, identity)
        held = c.execute("SELECT count(*) FROM discussion_contexts WHERE discussion_id=?", (identity,)).fetchone()[0]
        if held >= TAG_LIMIT:
            raise AppError(422, "validation", f"A discussion holds at most {TAG_LIMIT} tags; remove one first", {"contexts": "limit"})
        before = self.summary(c, identity)
        c.execute("INSERT INTO discussion_contexts(discussion_id,context_type,context_id,home,added_by,added_by_kind,created_at) "
                  "VALUES(?,?,?,0,?,?,?)", (identity, kind, context_id, actor["id"], actor["kind"], now()))
        c.execute("UPDATE discussions SET revision=revision+1 WHERE id=?", (identity,))
        result = self.summary(c, identity)
        self._emit(c, actor, "discussion.tag", identity, before, result, discussion_id=identity)
        return result

    def untag(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        """Take a tag off a discussion. An agent takes off only the tags it added, and the person any but
        a repository's tag on its own discussion, which only a merge moves. A tag the discussion
        doesn't have changes nothing."""
        self._get(c, "discussions", identity)
        kind = p["type"]
        if kind not in TAG_KINDS:
            raise AppError(422, "validation", "Choose a kind of record a discussion can be tagged with")
        context_id = self._tag_id(c, kind, p["id"])
        tag = c.execute("SELECT * FROM discussion_contexts WHERE discussion_id=? AND context_type=? AND context_id=?",
                        (identity, kind, context_id)).fetchone()
        if tag is None:
            return self.summary(c, identity)
        if tag["home"]:
            raise AppError(403, "forbidden", "This is the repository's own discussion, so it keeps that repository's tag",
                           {"contextId": "home"})
        if actor["kind"] == "agent" and tag["added_by"] != actor["id"]:
            raise AppError(403, "forbidden", "An agent takes off only the tags it added", {"contextId": "not_yours"})
        before = self.summary(c, identity)
        c.execute("DELETE FROM discussion_contexts WHERE discussion_id=? AND context_type=? AND context_id=?",
                  (identity, kind, context_id))
        c.execute("UPDATE discussions SET revision=revision+1 WHERE id=?", (identity,))
        result = self.summary(c, identity)
        # The feed of the record it no longer names sees it go.
        self._emit(c, actor, "discussion.untag", identity, before, result, discussion_id=identity, also=[(kind, context_id)])
        return result

    def pin(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        """The person pins a discussion, so it lists first wherever it is listed, or unpins it. Like a
        tag, a pin moves the revision and not updated_at."""
        self._human(actor)
        row = self._get(c, "discussions", identity)
        require_revision(row, p["revision"])
        if (row["pinned_at"] is not None) == p["pinned"]:
            return self.summary(c, identity)
        before = self.summary(c, identity)
        c.execute("UPDATE discussions SET pinned_at=?,revision=revision+1 WHERE id=?", (now() if p["pinned"] else None, identity))
        result = self.summary(c, identity)
        self._emit(c, actor, "discussion.pin" if p["pinned"] else "discussion.unpin", identity, before, result, discussion_id=identity)
        return result

    @staticmethod
    def home_discussions(c: sqlite3.Connection, repository_ids: Iterable[str]) -> dict[str, str]:
        """Each repository's own discussion, by repository; one with none is absent."""
        rows = rows_in(c, "SELECT context_id,discussion_id FROM discussion_contexts WHERE context_type='repository' "
                          "AND home=1 AND context_id IN ({})", repository_ids)
        return {row[0]: row[1] for row in rows}

    def open_repository_discussion(self, c: sqlite3.Connection, repository_id: str, name: str) -> dict:
        """Open a repository's own discussion, as the person, as proposal notices are written: one
        tagged with the repository, its tag marked home. A repository that has one keeps it."""
        held = self.home_discussions(c, [repository_id]).get(repository_id)
        if held is not None:
            return self.summary(c, held)
        p = {"title": REPOSITORY_TITLE.format(name=name), "text": REPOSITORY_TEXT.format(name=name, id=repository_id),
             "contexts": [{"type": "repository", "id": repository_id}]}
        return self.create(c, PERSON, p, home=("repository", repository_id))["discussion"]

    def retitle_repository_discussion(self, c: sqlite3.Connection, actor: dict, repository_id: str, name: str) -> None:
        """A renamed repository's own discussion takes its new name, in search too."""
        identity = self.home_discussions(c, [repository_id]).get(repository_id)
        title = REPOSITORY_TITLE.format(name=name)
        if identity is None or self._get(c, "discussions", identity)["title"] == title:
            return
        before = self.summary(c, identity)
        c.execute("UPDATE discussions SET title=?,revision=revision+1 WHERE id=?", (title, identity))
        search.retitle(c, "discussion", identity, title)
        search.retitle(c, "discussion_message", identity, title)
        self._emit(c, actor, "discussion.retitle", identity, before, self.summary(c, identity), discussion_id=identity)

    @staticmethod
    def move_repository_tags(c: sqlite3.Connection, source: str, survivor: str) -> int:
        """A merge moves a repository's tags to the one it was merged into, as it moves other links,
        without changing the revisions of the threads that hold them. The merged repository's own
        discussion becomes one tagged with the survivor, which keeps its own discussion. Returns how
        many tags the merged repository had."""
        moved = c.execute("SELECT count(*) FROM discussion_contexts WHERE context_type='repository' AND context_id=?",
                          (source,)).fetchone()[0]
        c.execute("UPDATE discussion_contexts SET home=0 WHERE context_type='repository' AND context_id=?", (source,))
        # A thread tagged with both keeps its tag of the survivor.
        c.execute("UPDATE OR IGNORE discussion_contexts SET context_id=? WHERE context_type='repository' AND context_id=?",
                  (survivor, source))
        c.execute("DELETE FROM discussion_contexts WHERE context_type='repository' AND context_id=?", (source,))
        return int(moved)

    def read(self, c: sqlite3.Connection, actor: dict, identity: str,
             limit: int = 50, offset: int = 0) -> dict:
        summary = self.summary(c, identity)
        ids = c.execute("SELECT id FROM discussion_messages WHERE discussion_id=? ORDER BY created_at,id LIMIT ? OFFSET ?", (identity, limit, offset)).fetchall()
        total = c.execute("SELECT count(*) FROM discussion_messages WHERE discussion_id=?", (identity,)).fetchone()[0]
        return {"discussion": summary, "messages": [self.message(c, r["id"], actor) for r in ids], "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def _discussion_scope(scope: tuple[str, str] | None) -> tuple[str, list]:
        if scope is None:
            return "1=1", []
        # A role-addressed or role-owned message belongs to its work context even
        # when the original discussion has no explicit context link.
        clause = """(EXISTS(SELECT 1 FROM discussion_contexts dc WHERE dc.discussion_id=d.id AND dc.context_type=? AND dc.context_id=?) OR
            EXISTS(SELECT 1 FROM discussion_messages m JOIN discussion_roles sr ON sr.id=m.on_behalf_of_role_id WHERE m.discussion_id=d.id AND sr.context_type=? AND sr.context_id=?) OR
            EXISTS(SELECT 1 FROM discussion_messages m JOIN discussion_targets t ON t.message_id=m.id JOIN discussion_roles tr ON t.kind='role' AND tr.id=t.recipient_id WHERE m.discussion_id=d.id AND tr.context_type=? AND tr.context_id=?))"""
        return clause, [*scope, *scope, *scope]

    def list(self, c: sqlite3.Connection, actor: dict, *, view: str = "all", q: str = "",
             context_type: str | None = None, context_id: str | None = None,
             repo: str | None = None, pinned: bool | None = None, limit: int = 50, offset: int = 0) -> dict:
        """Pinned threads first, the latest pinned first, then the most recently active. `repo` keeps
        the threads with that free-text label, which no new thread gets (K8)."""
        if view not in {"all", "awaiting", "archived"}:
            raise AppError(422, "validation", "Unknown discussion view")
        scope = self._scope(c, context_type, context_id)
        scope_clause, args = self._discussion_scope(scope)
        clauses = [scope_clause]
        if pinned is not None:
            clauses.append("d.pinned_at IS NOT NULL" if pinned else "d.pinned_at IS NULL")
        if view != "awaiting":
            clauses.append("d.archived=?")
            args.append(int(view == "archived"))
        else:
            awaiting, awaiting_args = self._awaiting_condition(actor)
            clauses.append(awaiting)
            args.extend(awaiting_args)
        if repo:
            clauses.append("EXISTS(SELECT 1 FROM discussion_repos dr WHERE dr.discussion_id=d.id AND dr.repo=?)")
            args.append(text(repo, "repo", required=True, limit=500))
        match = search.prefix_match(text(q, "q", limit=2000))
        if match:
            clauses.append("d.id IN(SELECT owner_id FROM search_index WHERE search_index MATCH ? AND type IN('discussion','discussion_message'))")
            args.append(match)
        clause = " AND ".join(clauses)
        total = c.execute("SELECT count(*) FROM discussions d WHERE " + clause, args).fetchone()[0]
        rows = c.execute("SELECT d.id FROM discussions d WHERE " + clause + " ORDER BY " + LISTED + " LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return {"items": [self.summary(c, r["id"]) for r in rows], "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def _awaiting_condition(actor: dict) -> tuple[str, builtins.list[Any]]:
        clause = "EXISTS(SELECT 1 FROM discussion_requests ar JOIN discussion_messages am ON am.id=ar.message_id LEFT JOIN discussion_roles sr ON sr.id=am.on_behalf_of_role_id WHERE am.discussion_id=d.id AND ar.state='pending'"
        if actor["kind"] == "human":
            return clause + ")", []
        clause += """ AND ((am.on_behalf_of_role_id IS NULL AND am.author_id=?) OR sr.owner_id=? OR
            (am.author_id<>? AND EXISTS(SELECT 1 FROM discussion_targets t LEFT JOIN discussion_roles tr ON t.kind='role' AND tr.id=t.recipient_id
                WHERE t.message_id=am.id AND NOT EXISTS(SELECT 1 FROM discussion_acknowledgements a WHERE a.target_id=t.id)
                AND ((t.kind='agent' AND t.recipient_id=?) OR (t.kind='role' AND tr.owner_id=?) OR t.kind IN('open_agent','open_any'))))))"""
        return clause, [actor["id"]] * 5

    def _emit(self, c: sqlite3.Connection, actor: dict, kind: str, identity: str,
              before: Any, after: Any, *, discussion_id: str | None = None,
              message_id: str | None = None, role_id: str | None = None,
              also: Iterable[tuple[str, str]] = ()) -> None:
        recorded = event(c, actor, kind, identity, before, after)
        change = c.execute("INSERT INTO discussion_changes(event_id,kind,discussion_id,message_id,role_id,created_at) VALUES(?,?,?,?,?,?)", (recorded["id"], kind, discussion_id, message_id, role_id, recorded["createdAt"]))
        scopes: set[tuple[str, str]] = set(also)
        if discussion_id:
            scopes.update((ctx["type"], ctx["id"]) for ctx in self._contexts(c, discussion_id))
        if role_id:
            role = self._get(c, "discussion_roles", role_id)
            scopes.add((role["context_type"], role["context_id"]))
            # A work-context feed must also observe a remote role's transfer when
            # that role addresses/manages a discussion linked to this context.
            related = c.execute("SELECT discussion_id FROM discussion_messages WHERE on_behalf_of_role_id=? UNION SELECT m.discussion_id FROM discussion_messages m JOIN discussion_targets t ON t.message_id=m.id WHERE t.kind='role' AND t.recipient_id=?", (role_id, role_id)).fetchall()
            for row in related:
                scopes.update((ctx["type"], ctx["id"]) for ctx in self._contexts(c, row[0]))
        c.executemany("INSERT INTO discussion_change_contexts(seq,context_type,context_id) VALUES(?,?,?)",
                      [(change.lastrowid, *scope) for scope in sorted(scopes)])

    @staticmethod
    def _cursor(c: sqlite3.Connection, scope: tuple[str, str] | None, seq: int) -> str:
        payload = {"dataId": setting(c, "dataId"), "scope": list(scope) if scope else None, "seq": seq}
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(c: sqlite3.Connection, value: str | None,
                       scope: tuple[str, str] | None) -> int:
        if value is None:
            return 0
        try:
            if len(value) > 2000 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError()
            decoded = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
            seq = decoded["seq"]
            maximum = c.execute("SELECT COALESCE(max(seq),0) FROM discussion_changes").fetchone()[0]
            if (set(decoded) != {"dataId", "scope", "seq"} or type(seq) is not int or seq < 0 or seq > maximum or
                    decoded["dataId"] != setting(c, "dataId") or decoded["scope"] != (list(scope) if scope else None)):
                raise ValueError()
            return seq
        except (ValueError, TypeError, KeyError, binascii.Error, UnicodeDecodeError):
            raise AppError(409, "cursor_reset", "The dataset or feed scope changed; resume from a fresh snapshot") from None

    def changes(self, c: sqlite3.Connection, *, cursor: str | None = None,
                context_type: str | None = None, context_id: str | None = None,
                limit: int = 50) -> dict:
        scope = self._scope(c, context_type, context_id)
        seq = self._decode_cursor(c, cursor, scope)
        clause = "ch.seq>?"
        args: list[Any] = [seq]
        if scope:
            clause += " AND EXISTS(SELECT 1 FROM discussion_change_contexts cc WHERE cc.seq=ch.seq AND cc.context_type=? AND cc.context_id=?)"
            args.extend(scope)
        rows = c.execute("SELECT ch.*,e.actor_id,e.actor_label,e.actor_kind FROM discussion_changes ch JOIN events e ON e.id=ch.event_id WHERE " + clause + " ORDER BY ch.seq LIMIT ?", [*args, limit + 1]).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        through = rows[-1]["seq"] if has_more else c.execute("SELECT COALESCE(max(seq),0) FROM discussion_changes").fetchone()[0]
        return {"items": [{"seq": r["seq"], "kind": r["kind"], "discussionId": r["discussion_id"], "messageId": r["message_id"], "roleId": r["role_id"],
                           "actorId": r["actor_id"], "actorLabel": r["actor_label"], "actorKind": r["actor_kind"], "createdAt": r["created_at"]} for r in rows],
                "cursor": self._cursor(c, scope, through), "hasMore": has_more}

    def resume(self, c: sqlite3.Connection, actor: dict, *, context_type: str | None = None,
               context_id: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        scope = self._scope(c, context_type, context_id)
        clause, args = self._discussion_scope(scope)
        discussion_count = c.execute("SELECT count(*) FROM discussions d WHERE " + clause, args).fetchone()[0]
        discussions = c.execute("SELECT d.id FROM discussions d WHERE " + clause + " ORDER BY " + LISTED + " LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        request_query = " FROM discussion_messages m JOIN discussion_requests r ON r.message_id=m.id JOIN discussions d ON d.id=m.discussion_id WHERE r.state='pending' AND " + clause
        request_count = c.execute("SELECT count(*)" + request_query, args).fetchone()[0]
        request_ids = c.execute("SELECT m.id" + request_query + " ORDER BY m.created_at,m.id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        roles = self.roles(c, context_type, context_id, limit, offset)
        seq = c.execute("SELECT COALESCE(max(seq),0) FROM discussion_changes").fetchone()[0]
        counts = {"roles": roles["total"], "discussions": discussion_count, "requests": request_count}
        return {"roles": roles["items"],
                "discussions": [self.summary(c, r["id"]) for r in discussions],
                "requests": [self.message(c, r["id"], actor) for r in request_ids],
                "cursor": self._cursor(c, scope, seq), "counts": counts, "limit": limit, "offset": offset,
                "hasMore": any(count > offset + limit for count in counts.values())}
