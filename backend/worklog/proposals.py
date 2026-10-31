"""Reviewed agent changes: typed operations and an atomic, attributable approval."""

from __future__ import annotations

import json
from contextlib import nullcontext

from .common import AppError, event, now, require_revision, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, rationale TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL,
 actor_id TEXT NOT NULL, actor_label TEXT NOT NULL, actor_kind TEXT NOT NULL,
 operations TEXT NOT NULL, supersedes_id TEXT REFERENCES proposals(id),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS proposals_status ON proposals(status,created_at);
"""

# Explicit schemas prevent a proposal becoming an arbitrary operation transport.
FIELDS = {
    "item.create": ({"title"}, {"title", "description", "adoUrl", "roadmapId", "stepId", "day", "worked"}),
    "item.update": ({"id", "revision"}, {"id", "revision", "title", "description", "status", "adoUrl", "roadmapId", "stepId"}),
    "item.archive": ({"id", "revision"}, {"id", "revision"}),
    "item.recover": ({"id", "revision"}, {"id", "revision"}),
    "entry.upsert": ({"date", "itemId", "revision"}, {"date", "itemId", "revision", "planned", "worked", "outcome"}),
    "note.create": ({"ownerType", "ownerId", "text"}, {"ownerType", "ownerId", "title", "text"}),
    "note.update": ({"id", "revision"}, {"id", "revision", "title", "text"}),
    "task.create": ({"itemId", "text"}, {"itemId", "text", "done"}),
    "task.update": ({"id", "revision"}, {"id", "revision", "text", "done"}),
    "roadmap.create": ({"title"}, {"title", "overview", "steps", "parentId"}),
    "roadmap.revise": ({"id", "revision", "title", "steps"}, {"id", "revision", "title", "overview", "steps", "note"}),
    "roadmap.move": ({"id", "revision", "parentId"}, {"id", "revision", "parentId"}),
    "progress.set": ({"id", "revision", "status"}, {"id", "revision", "status"}),
    "person.create": ({"name"}, {"name", "role", "notes"}),
    "person.update": ({"id", "revision"}, {"id", "revision", "name", "role", "notes"}),
    "person.archive": ({"id", "revision"}, {"id", "revision"}),
    "person.recover": ({"id", "revision"}, {"id", "revision"}),
    "person.link": ({"personId", "targetType", "targetId"}, {"personId", "targetType", "targetId", "relationship"}),
    "person.unlink": ({"personId", "targetType", "targetId"}, {"personId", "targetType", "targetId"}),
    "link.create": ({"ownerType", "ownerId", "url"}, {"ownerType", "ownerId", "url", "title", "description"}),
    "link.update": ({"id", "revision"}, {"id", "revision", "url", "title", "description"}),
    "link.archive": ({"id", "revision"}, {"id", "revision"}),
    "link.recover": ({"id", "revision"}, {"id", "revision"}),
    "chat.create": ({"ownerType", "ownerId", "chatId", "title"}, {"ownerType", "ownerId", "chatId", "title", "createdDate"}),
    "chat.update": ({"id", "revision"}, {"id", "revision", "chatId", "title", "createdDate"}),
    "chat.archive": ({"id", "revision"}, {"id", "revision"}),
    "chat.recover": ({"id", "revision"}, {"id", "revision"}),
    "github.create": ({"ownerType", "ownerId", "kind", "url"}, {"ownerType", "ownerId", "kind", "url", "reference", "title", "description", "chatId"}),
    "github.update": ({"id", "revision"}, {"id", "revision", "kind", "url", "reference", "title", "description", "chatId"}),
    "github.archive": ({"id", "revision"}, {"id", "revision"}),
    "github.recover": ({"id", "revision"}, {"id", "revision"}),
}


def _string(value, name, max_bytes=1_000_000, required=False):
    if not isinstance(value, str) or len(value.encode("utf-8")) > max_bytes or (required and not value.strip()):
        raise AppError(422, "validation", f"Invalid {name}")
    return value.strip() if required else value


def _operations(value):
    if not isinstance(value, list) or len(value) > 500:
        raise AppError(422, "validation", "Operations must be a list of at most 500 operations")
    for operation in value:
        if not isinstance(operation, dict) or set(operation) != {"action", "payload"}:
            raise AppError(422, "validation", "Each operation requires action and payload")
        action, payload = operation["action"], operation["payload"]
        if not isinstance(action, str) or action not in FIELDS or not isinstance(payload, dict):
            raise AppError(422, "validation", "Unsupported proposal action")
        required, allowed = FIELDS[action]
        if not required <= payload.keys() or not payload.keys() <= allowed:
            raise AppError(422, "validation", f"Invalid fields for {action}")
        if "revision" in required and (type(payload["revision"]) is not int or payload["revision"] < 0):
            raise AppError(422, "validation", "An existing target requires an integer revision")
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (ValueError, TypeError):
        raise AppError(422, "validation", "Operations must contain JSON values") from None
    if len(encoded.encode("utf-8")) > 5_000_000:
        raise AppError(413, "too_large", "Proposal operations exceed 5,000,000 bytes")
    return encoded


def _record(row):
    return {"id": row["id"], "title": row["title"], "rationale": row["rationale"],
            "status": row["status"], "revision": row["revision"], "actorId": row["actor_id"],
            "actorLabel": row["actor_label"], "operations": json.loads(row["operations"]),
            "supersedesId": row["supersedes_id"], "createdAt": row["created_at"],
            "updatedAt": row["updated_at"], "attachments": []}


def _row(conn, identifier):
    row = conn.execute("SELECT * FROM proposals WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Proposal not found")
    return row


def _can_see(conn, row, actor):
    # Agents see other agents' proposals only once submitted; withdrawing an unsubmitted draft keeps it private.
    if actor["kind"] != "agent" or row["actor_id"] == actor["id"] or row["status"] in {"submitted", "approved", "rejected"}:
        return True
    return row["status"] == "withdrawn" and conn.execute(
        "SELECT 1 FROM events WHERE entity_id=? AND kind='proposal.submit'", (row["id"],)).fetchone() is not None


def _visible(conn, row, actor):
    if not _can_see(conn, row, actor):
        raise AppError(404, "not_found", "Proposal not found")


def _owner(row, actor):
    if row["actor_id"] != actor["id"]:
        raise AppError(403, "forbidden", "Only the originating actor may edit or submit this proposal")


class ProposalService:
    def __init__(self, db, dispatch):
        self.db = db
        self.dispatch = dispatch

    def list(self, actor, status=None):
        if status is not None and status not in {"draft", "submitted", "approved", "rejected", "withdrawn"}:
            raise AppError(422, "validation", "Invalid proposal status")
        with self.db.read() as conn:
            rows = conn.execute("SELECT * FROM proposals ORDER BY created_at,id").fetchall()
            items = [_record(r) for r in rows if (not status or r["status"] == status)
                     and _can_see(conn, r, actor)]
            return {"items": items, "total": len(items)}

    def detail(self, identifier, actor):
        with self.db.read() as conn:
            row = _row(conn, identifier)
            _visible(conn, row, actor)
            return _record(row)

    def create(self, payload, actor, conn=None):
        if actor.get("kind") not in {"human", "agent"}:
            raise AppError(403, "forbidden", "Invalid actor")
        title = _string(payload.get("title"), "title", 1000, required=True)
        rationale = _string(payload.get("rationale", ""), "rationale")
        operations = _operations(payload.get("operations", []))
        with self.db.transaction() if conn is None else nullcontext(conn) as connection:
            supersedes = payload.get("supersedesId")
            if supersedes:
                prior = _row(connection, supersedes)
                _visible(connection, prior, actor)
                _owner(prior, actor)
                if prior["status"] not in {"rejected", "withdrawn"}:
                    raise AppError(409, "conflict", "Only rejected or withdrawn proposals may be superseded")
            identifier, stamp = uid(), now()
            connection.execute("INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                               (identifier, title, rationale, "draft", 1, actor["id"], actor["label"],
                                actor["kind"], operations, supersedes, stamp, stamp))
            result = _record(_row(connection, identifier))
            event(connection, actor, "proposal.create", identifier, None, result)
            return result

    def update(self, identifier, payload, actor):
        if not set(payload) <= {"revision", "title", "rationale", "operations"}:
            raise AppError(422, "validation", "Unknown proposal fields")
        with self.db.transaction() as conn:
            row = _row(conn, identifier)
            _visible(conn, row, actor)
            _owner(row, actor)
            require_revision(row, payload.get("revision"))
            if row["status"] != "draft":
                raise AppError(409, "conflict", "Only drafts can be edited")
            before = _record(row)
            title = _string(payload.get("title", row["title"]), "title", 1000, required=True)
            rationale = _string(payload.get("rationale", row["rationale"]), "rationale")
            operations = _operations(payload.get("operations", json.loads(row["operations"])))
            conn.execute("UPDATE proposals SET title=?,rationale=?,operations=?,revision=revision+1,updated_at=? WHERE id=?",
                         (title, rationale, operations, now(), identifier))
            result = _record(_row(conn, identifier))
            event(conn, actor, "proposal.update", identifier, before, result)
            return result

    def transition(self, identifier, verb, payload, actor, conn=None):
        if verb not in {"submit", "withdraw", "approve", "reject"}:
            raise AppError(422, "validation", "Unsupported proposal transition")
        with self.db.transaction() if conn is None else nullcontext(conn) as connection:
            row = _row(connection, identifier)
            _visible(connection, row, actor)
            require_revision(row, payload.get("revision"))
            if verb in {"approve", "reject"}:
                if actor["kind"] != "human":
                    raise AppError(403, "forbidden", "Only a human may approve or reject proposals")
                allowed = {"submitted"}
            else:
                _owner(row, actor)
                allowed = {"draft"} if verb == "submit" else {"draft", "submitted"}
            if row["status"] not in allowed:
                raise AppError(409, "conflict", f"Cannot {verb} a {row['status']} proposal")
            before = _record(row)
            if verb == "submit" and not before["operations"]:
                raise AppError(422, "validation", "A submitted proposal needs at least one operation")
            if verb == "approve":
                operations = before["operations"]
                _operations(operations)
                proposer = {"id": row["actor_id"], "label": row["actor_label"], "kind": row["actor_kind"]}
                # Dispatch uses this same transaction. Any stale revision, validation
                # error, or injected failure rolls back all operations and approval.
                for operation in operations:
                    self.dispatch(connection, proposer, operation["action"], operation["payload"])
            state = {"submit": "submitted", "withdraw": "withdrawn", "approve": "approved", "reject": "rejected"}[verb]
            connection.execute("UPDATE proposals SET status=?,revision=revision+1,updated_at=? WHERE id=?",
                               (state, now(), identifier))
            result = _record(_row(connection, identifier))
            event(connection, actor, f"proposal.{verb}", identifier, before, result)
            return result

    def check_upload(self, conn, identifier, actor):
        row = _row(conn, identifier)
        _visible(conn, row, actor)
        if actor["kind"] == "agent":
            _owner(row, actor)
            if row["status"] != "draft":
                raise AppError(409, "conflict", "Agent evidence uploads require an owned draft")
        return _record(row)
