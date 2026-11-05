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
from typing import Any

from . import search
from .avatars import AVATARS, choose, is_avatar
from .common import (
    LIVE_AGENT,
    AppError,
    event,
    now,
    repository_labels,
    require_revision,
    rows_in,
    setting,
    text,
    uid,
)

# Added in schema v3. It sits exactly where ALTER TABLE ADD COLUMN places it, so
# created and migrated databases share one definition for restore validation.
AGENT_AVATAR_COLUMN = ", avatar TEXT"

SCHEMA = """
CREATE TABLE communication_agents(id TEXT PRIMARY KEY,label TEXT NOT NULL,created_at TEXT NOT NULL""" + AGENT_AVATAR_COLUMN + """);
CREATE TABLE communication_credentials(credential_id TEXT PRIMARY KEY REFERENCES credentials(id),agent_id TEXT NOT NULL REFERENCES communication_agents(id));
CREATE INDEX communication_credentials_agent ON communication_credentials(agent_id);
CREATE TABLE discussions(id TEXT PRIMARY KEY,title TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX discussions_updated ON discussions(archived,updated_at,id);
CREATE TABLE discussion_contexts(discussion_id TEXT NOT NULL REFERENCES discussions(id),context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step')),context_id TEXT NOT NULL,PRIMARY KEY(discussion_id,context_type,context_id));
CREATE INDEX discussion_contexts_context ON discussion_contexts(context_type,context_id,discussion_id);
CREATE TABLE discussion_repos(discussion_id TEXT NOT NULL REFERENCES discussions(id),repo TEXT NOT NULL,PRIMARY KEY(discussion_id,repo));
CREATE INDEX discussion_repos_repo ON discussion_repos(repo,discussion_id);
CREATE TABLE discussion_roles(id TEXT PRIMARY KEY,context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step')),context_id TEXT NOT NULL,label TEXT NOT NULL,label_key TEXT NOT NULL,owner_id TEXT REFERENCES communication_agents(id),revision INTEGER NOT NULL DEFAULT 1,generation INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(context_type,context_id,label_key));
CREATE INDEX discussion_roles_owner ON discussion_roles(owner_id,context_type,context_id);
CREATE TABLE discussion_role_offers(id TEXT PRIMARY KEY,role_id TEXT NOT NULL REFERENCES discussion_roles(id),from_id TEXT NOT NULL REFERENCES communication_agents(id),to_id TEXT NOT NULL REFERENCES communication_agents(id),generation INTEGER NOT NULL,note TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN('pending','accepted','declined','withdrawn','invalidated')),created_at TEXT NOT NULL,ended_at TEXT);
CREATE UNIQUE INDEX discussion_role_pending_offer ON discussion_role_offers(role_id) WHERE state='pending';
CREATE TABLE discussion_messages(id TEXT PRIMARY KEY,discussion_id TEXT NOT NULL REFERENCES discussions(id),text TEXT NOT NULL,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),created_at TEXT NOT NULL,chat_id TEXT,on_behalf_of_role_id TEXT REFERENCES discussion_roles(id),ownership_generation INTEGER);
CREATE INDEX discussion_messages_discussion ON discussion_messages(discussion_id,created_at,id);
CREATE TABLE discussion_requests(message_id TEXT PRIMARY KEY REFERENCES discussion_messages(id),mode TEXT NOT NULL CHECK(mode IN('all','any','any_agent','anyone')),state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN('pending','satisfied','released_by_human','cancelled')),terminal_at TEXT,terminal_actor_id TEXT,terminal_actor_label TEXT,terminal_actor_kind TEXT,reason TEXT,authority_role_id TEXT REFERENCES discussion_roles(id),authority_generation INTEGER);
CREATE TABLE discussion_targets(id TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES discussion_requests(message_id),kind TEXT NOT NULL CHECK(kind IN('agent','human','role','open_agent','open_any')),recipient_id TEXT NOT NULL,UNIQUE(message_id,kind,recipient_id));
CREATE INDEX discussion_targets_recipient ON discussion_targets(kind,recipient_id,message_id);
CREATE TABLE discussion_acknowledgements(id TEXT PRIMARY KEY,target_id TEXT NOT NULL REFERENCES discussion_targets(id),actor_id TEXT NOT NULL,actor_label TEXT NOT NULL,actor_kind TEXT NOT NULL CHECK(actor_kind IN('agent','human')),role_id TEXT REFERENCES discussion_roles(id),ownership_generation INTEGER,created_at TEXT NOT NULL,late INTEGER NOT NULL CHECK(late IN(0,1)),UNIQUE(target_id,actor_id));
CREATE TABLE discussion_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE REFERENCES events(id),kind TEXT NOT NULL,discussion_id TEXT REFERENCES discussions(id),message_id TEXT REFERENCES discussion_messages(id),role_id TEXT REFERENCES discussion_roles(id),created_at TEXT NOT NULL);
CREATE TABLE discussion_change_contexts(seq INTEGER NOT NULL REFERENCES discussion_changes(seq),context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step')),context_id TEXT NOT NULL,PRIMARY KEY(seq,context_type,context_id));
CREATE INDEX discussion_change_contexts_context ON discussion_change_contexts(context_type,context_id,seq);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Add the whole schema atomically inside the caller's migration transaction."""
    statements = list(filter(str.strip, SCHEMA.split(";")))
    expected = {}
    for statement in statements:
        match = re.match(r"\s*CREATE (?:UNIQUE )?(?:TABLE|INDEX) (\w+)", statement)
        assert match is not None
        expected[match.group(1)] = " ".join(statement.split())
    existing = {row[0]: " ".join((row[1] or "").split()) for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'discussion%' OR name LIKE 'communication%'")}
    if existing and existing != expected:
        raise AppError(503, "database_invalid", "Communication schema is incomplete or changed; restore a verified backup")
    # Each statement is executed separately: executescript would commit the caller.
    if not existing:
        for statement in statements:
            conn.execute(statement)
    for row in conn.execute("SELECT id,label FROM credentials WHERE id NOT IN(SELECT credential_id FROM communication_credentials)").fetchall():
        bind_credential(conn, row[0], row[1])


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
    """The chat an identity registered with, whichever of its credentials registered it:
    rotating the credential in Settings keeps the identity, and so its chat."""
    credentials = [r[0] for r in c.execute("SELECT credential_id FROM communication_credentials WHERE agent_id=?", (agent_id,))]
    return next(iter(registered_chats(c, credentials).values()), None)


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
    def _scope(context_type: str | None, context_id: str | None) -> tuple[str, str] | None:
        if context_type is None and context_id is None:
            return None
        if context_type not in {"item", "roadmap", "step"} or not context_id:
            raise AppError(422, "validation", "Choose both a context type and ID")
        return context_type, text(context_id, "contextId", required=True, limit=500)

    @staticmethod
    def _context(c: sqlite3.Connection, context_type: str, context_id: str) -> None:
        table = {"item": "work_items", "roadmap": "roadmaps", "step": "steps"}.get(context_type)
        if table is None or not c.execute(f"SELECT 1 FROM {table} WHERE id=?", (context_id,)).fetchone():
            raise AppError(422, "validation", "Work context does not exist")

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
        scope = self._scope(context_type, context_id)
        clause, args = (" WHERE context_type=? AND context_id=?", list(scope)) if scope else ("", [])
        total = c.execute("SELECT count(*) FROM discussion_roles" + clause, args).fetchone()[0]
        ids = c.execute("SELECT id FROM discussion_roles" + clause + " ORDER BY context_type,context_id,label_key,id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return {"items": [self.role(c, r["id"]) for r in ids], "total": total, "limit": limit, "offset": offset}

    def create_role(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
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

    def summary(self, c: sqlite3.Connection, identity: str) -> dict:
        r = self._get(c, "discussions", identity)
        contexts = self._contexts(c, identity)
        repos = [row[0] for row in c.execute("SELECT repo FROM discussion_repos WHERE discussion_id=? ORDER BY repo", (identity,))]
        pending = c.execute("SELECT count(*) FROM discussion_requests r JOIN discussion_messages m ON m.id=r.message_id WHERE m.discussion_id=? AND r.state='pending'", (identity,)).fetchone()[0]
        return {"id": r["id"], "title": r["title"], "archived": bool(r["archived"]), "revision": r["revision"],
                "createdAt": r["created_at"], "updatedAt": r["updated_at"], "contexts": contexts, "repos": repos, "pendingCount": pending}

    def create(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        body = text(p["text"], "text", required=True)
        title = text(p.get("title") or body.splitlines()[0][:120], "title", required=True, limit=1000)
        contexts = {(ctx["type"], ctx["id"]) for ctx in p.get("contexts", [])}
        for context_type, context_id in contexts:
            self._context(c, context_type, context_id)
        repos = repository_labels(p.get("repos", []))
        identity, at = uid(), now()
        c.execute("INSERT INTO discussions(id,title,created_at,updated_at) VALUES(?,?,?,?)", (identity, title, at, at))
        c.executemany("INSERT INTO discussion_contexts(discussion_id,context_type,context_id) VALUES(?,?,?)",
                      [(identity, *scope) for scope in sorted(contexts)])
        c.executemany("INSERT INTO discussion_repos(discussion_id,repo) VALUES(?,?)", [(identity, repo) for repo in repos])
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
            self._create_request(c, identity, ack)
        if not first:
            c.execute("UPDATE discussions SET revision=revision+1,updated_at=? WHERE id=?", (at, discussion_id))
        search.put(c, identity, "discussion_message", discussion["title"], body, discussion_id, bool(discussion["archived"]),
                   replace=False)
        result = self.message(c, identity, actor)
        self._emit(c, actor, "discussion.create" if first else "discussion.message", identity, None, result, discussion_id=discussion_id, message_id=identity)
        return result

    def _create_request(self, c: sqlite3.Connection, message_id: str, ack: dict) -> None:
        mode = ack.get("mode", "all")
        targets = ack.get("targets", [])
        if mode not in {"all", "any", "any_agent", "anyone"} or ((mode in {"all", "any"}) != bool(targets)):
            raise AppError(422, "validation", "Choose named targets or an open audience")
        seen = set()
        normalized = []
        if mode in {"any_agent", "anyone"}:
            normalized.append(("open_agent" if mode == "any_agent" else "open_any", ""))
        for target in targets:
            kind, recipient = target["kind"], target["id"]
            if (kind, recipient) in seen:
                raise AppError(422, "validation", "Choose each target only once")
            seen.add((kind, recipient))
            if kind == "agent":
                self._agent(c, recipient)
            elif kind == "role":
                self._get(c, "discussion_roles", recipient)
            elif kind != "human" or recipient != "local-user":
                raise AppError(422, "validation", "Unknown acknowledgement target")
            normalized.append((kind, recipient))
        c.execute("INSERT INTO discussion_requests(message_id,mode) VALUES(?,?)", (message_id, mode))
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
                             "authorityRoleId": r["authority_role_id"], "authorityGeneration": r["authority_generation"]}
        return result

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
             repo: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        if view not in {"all", "awaiting", "archived"}:
            raise AppError(422, "validation", "Unknown discussion view")
        scope = self._scope(context_type, context_id)
        scope_clause, args = self._discussion_scope(scope)
        clauses = [scope_clause]
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
        rows = c.execute("SELECT d.id FROM discussions d WHERE " + clause + " ORDER BY d.updated_at DESC,d.id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
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
              message_id: str | None = None, role_id: str | None = None) -> None:
        recorded = event(c, actor, kind, identity, before, after)
        change = c.execute("INSERT INTO discussion_changes(event_id,kind,discussion_id,message_id,role_id,created_at) VALUES(?,?,?,?,?,?)", (recorded["id"], kind, discussion_id, message_id, role_id, recorded["createdAt"]))
        scopes: set[tuple[str, str]] = set()
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
        scope = self._scope(context_type, context_id)
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
        scope = self._scope(context_type, context_id)
        clause, args = self._discussion_scope(scope)
        discussion_count = c.execute("SELECT count(*) FROM discussions d WHERE " + clause, args).fetchone()[0]
        discussions = c.execute("SELECT d.id FROM discussions d WHERE " + clause + " ORDER BY d.updated_at DESC,d.id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
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
