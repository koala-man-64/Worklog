"""Tickets: issues the person and agents file, work on and close, then keep as history.

Agents write tickets directly, as they post in discussions, or by proposal, so a ticket can
come with the records the same proposal creates. Every change carries the author's stable
communication identity and lands in the ticket's history. Only the person archives or
recovers a ticket, and an archived ticket is read-only.
"""

from __future__ import annotations

import builtins
import re
import sqlite3
from typing import Any

from . import search
from .common import (
    LIVE_AGENT,
    AppError,
    event,
    now,
    read_events,
    require_revision,
    rows_in,
    text,
    uid,
)
from .common import status as valid_status
from .repository_links import linked_repositories, replace_repository_associations

KINDS = ("bug", "incident", "task")
PRIORITIES = ("low", "normal", "high", "urgent")
ACTIVE = ("open", "doing", "blocked")
# List items carry this much description and resolution, as the agent registry's activity does.
SNIPPET = 1000
MAX_LABELS = 20
_LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,49}")
_NUMBER = re.compile(r"(?:T-)?([1-9][0-9]{0,8})", re.IGNORECASE)
_PRIORITY_ORDER = "CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END"
_PERSON = {"id": "local-user", "label": "You", "kind": "human", "avatar": None, "active": True}


def key(number: int) -> str:
    return f"T-{number}"


def _choice(value: Any, field: str, options: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in options:
        raise AppError(422, "validation", f"Choose a {field}: " + ", ".join(options), {field: "invalid"})
    return value


def _labels(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LABELS:
        raise AppError(422, "validation", f"Use at most {MAX_LABELS} labels", {"labels": "invalid"})
    labels: set[str] = set()
    for label in value:
        normalized = label.strip().lower() if isinstance(label, str) else ""
        if not _LABEL.fullmatch(normalized):
            raise AppError(422, "validation", "A label is up to 50 letters, digits, '.', '_' or '-', starting with a letter or digit", {"labels": "invalid"})
        labels.add(normalized)
    return sorted(labels)


class TicketService:
    @staticmethod
    def resolve(c: sqlite3.Connection, ref: str) -> sqlite3.Row:
        """A ticket by its UUID, its key (T-12) or its number."""
        number = _NUMBER.fullmatch(ref.strip())
        row = (c.execute("SELECT * FROM tickets WHERE number=?", (int(number.group(1)),)) if number
               else c.execute("SELECT * FROM tickets WHERE id=?", (ref,))).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Ticket not found")
        return row

    @staticmethod
    def _people(c: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
        """The person and the agent identities these ids name, as they stand now."""
        people = {"local-user": _PERSON}
        agents = sorted({i for i in ids if i and i != "local-user"})
        for r in rows_in(c, f"SELECT a.id,a.label,a.avatar,{LIVE_AGENT} AS active FROM communication_agents a WHERE a.id IN ({{}})", agents):
            people[r["id"]] = {"id": r["id"], "label": r["label"], "kind": "agent", "avatar": r["avatar"], "active": bool(r["active"])}
        return people

    def records(self, c: sqlite3.Connection, rows: list[sqlite3.Row], *, full: bool = False) -> list[dict]:
        ids = [r["id"] for r in rows]
        labels: dict[str, list[str]] = {}
        for r in rows_in(c, "SELECT ticket_id,label FROM ticket_labels WHERE ticket_id IN ({}) ORDER BY label", ids):
            labels.setdefault(r["ticket_id"], []).append(r["label"])
        counts = {r[0]: r[1] for r in rows_in(c, "SELECT ticket_id,count(*) FROM ticket_comments WHERE ticket_id IN ({}) GROUP BY ticket_id", ids)}
        items = {r["id"]: {"id": r["id"], "title": r["title"], "status": r["status"], "archived": bool(r["archived"])}
                 for r in rows_in(c, "SELECT id,title,status,archived FROM work_items WHERE id IN ({})", sorted({r["item_id"] for r in rows if r["item_id"]}))}
        repositories = linked_repositories(c, "ticket", ids)
        people = self._people(c, [r["author_id"] for r in rows] + [r["assignee_id"] for r in rows])
        result = []
        for r in rows:
            description, resolution = r["description"], r["resolution"]
            truncated = not full and (len(description) > SNIPPET or len(resolution) > SNIPPET)
            if not full:
                description, resolution = description[:SNIPPET], resolution[:SNIPPET]
            assignee = r["assignee_id"] and people.get(r["assignee_id"], {
                "id": r["assignee_id"], "label": "Unknown agent", "kind": "agent", "avatar": None, "active": False})
            result.append({
                "id": r["id"], "number": r["number"], "key": key(r["number"]), "title": r["title"],
                "description": description, "resolution": resolution, "truncated": truncated,
                "kind": r["kind"], "priority": r["priority"], "status": r["status"],
                "labels": labels.get(r["id"], []), "repositoryIds": repositories.get(r["id"], []),
                "itemId": r["item_id"], "item": items.get(r["item_id"]) if r["item_id"] else None,
                "assigneeId": r["assignee_id"], "assignee": assignee or None,
                "author": {"id": r["author_id"], "label": r["author_label"], "kind": r["author_kind"],
                           "avatar": people.get(r["author_id"], {}).get("avatar")},
                "commentCount": counts.get(r["id"], 0), "archived": bool(r["archived"]), "revision": r["revision"],
                "createdAt": r["created_at"], "updatedAt": r["updated_at"], "closedAt": r["closed_at"],
                "lastActivityAt": r["last_activity_at"]})
        return result

    def record(self, c: sqlite3.Connection, identity: str) -> dict:
        return self.records(c, [self.resolve(c, identity)], full=True)[0]

    def _comments(self, c: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
        people = self._people(c, [r["author_id"] for r in rows])
        return [{"id": r["id"], "ticketId": r["ticket_id"], "text": r["text"], "createdAt": r["created_at"],
                 "author": {"id": r["author_id"], "label": r["author_label"], "kind": r["author_kind"],
                            "avatar": people.get(r["author_id"], {}).get("avatar")}} for r in rows]

    @staticmethod
    def _fields(current: dict, p: dict) -> dict:
        """Supplied fields over the current ones, validated together as the ticket would read."""
        values = {k: current[k] for k in ("title", "description", "kind", "priority", "status", "resolution")}
        values.update({k: v for k, v in p.items() if k in values and v is not None})
        values["title"] = text(values["title"], "title", required=True, limit=1000)
        values["description"] = text(values["description"], "description", limit=100_000)
        values["resolution"] = text(values["resolution"], "resolution", limit=100_000)
        values["kind"] = _choice(values["kind"], "kind", KINDS)
        values["priority"] = _choice(values["priority"], "priority", PRIORITIES)
        values["status"] = valid_status(values["status"])
        # As with answered questions: reopening keeps the resolution, only Done needs one.
        if values["status"] == "done" and not values["resolution"].strip():
            raise AppError(422, "validation", "Write a resolution before marking the ticket done", {"resolution": "required"})
        return values

    @staticmethod
    def _item(c: sqlite3.Connection, item_id: Any) -> str | None:
        if item_id is None:
            return None
        item_id = text(item_id, "itemId", required=True, limit=200)
        row = c.execute("SELECT archived FROM work_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Work item not found")
        if row["archived"]:
            raise AppError(409, "archived", "Link an active work item; this one is archived")
        return item_id

    @staticmethod
    def _assignee(c: sqlite3.Connection, assignee_id: Any) -> str | None:
        """Assignment is advisory, but it must name the person or an agent that can still act."""
        if assignee_id is None:
            return None
        assignee_id = text(assignee_id, "assigneeId", required=True, limit=200)
        if assignee_id != "local-user" and not c.execute(
                f"SELECT 1 FROM communication_agents a WHERE a.id=? AND {LIVE_AGENT}", (assignee_id,)).fetchone():
            raise AppError(422, "assignee_unavailable", "Assign the person or an agent with a live credential", {"assigneeId": "invalid"})
        return assignee_id

    @staticmethod
    def _set_labels(c: sqlite3.Connection, identity: str, labels: list[str]) -> None:
        c.execute("DELETE FROM ticket_labels WHERE ticket_id=?", (identity,))
        c.executemany("INSERT INTO ticket_labels(ticket_id,label) VALUES(?,?)", [(identity, label) for label in labels])

    @staticmethod
    def _index(c: sqlite3.Connection, ticket: dict, *, retitle: bool = False) -> None:
        title = ticket["key"] + " " + ticket["title"]
        body = "\n\n".join(part for part in (ticket["description"], ticket["resolution"],
                                             " ".join([ticket["kind"], *ticket["labels"]])) if part)
        search.put(c, ticket["id"], "ticket", title, body, ticket["id"])
        if retitle:
            # Comments show their ticket's key and title in search results.
            search.retitle(c, "ticket_comment", ticket["id"], title)

    def apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        """An applied proposal's change, made as the identity that proposed it (WorkService.apply)."""
        if action == "ticket.create":
            return self.create(c, actor, p)
        if action == "ticket.update":
            return self.update(c, actor, p["id"], {k: v for k, v in p.items() if k != "id"})
        raise AppError(422, "operation", "Unsupported ticket operation")

    def create(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        values = self._fields({"title": "", "description": "", "kind": "bug", "priority": "normal",
                               "status": "open", "resolution": ""}, p)
        labels = _labels(p.get("labels") or [])
        item_id = self._item(c, p.get("itemId"))
        assignee_id = self._assignee(c, p.get("assigneeId"))
        identity, stamp = uid(), now()
        # The write transaction serializes creation, and tickets are never deleted, so a number
        # is never taken twice or reused. A delete path would need a counter that only grows.
        number = c.execute("SELECT coalesce(max(number),0)+1 FROM tickets").fetchone()[0]
        c.execute("INSERT INTO tickets(id,number,title,description,kind,priority,status,resolution,item_id,assignee_id,"
                  "author_id,author_label,author_kind,revision,created_at,updated_at,closed_at,last_activity_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
                  (identity, number, values["title"], values["description"], values["kind"], values["priority"],
                   values["status"], values["resolution"], item_id, assignee_id, actor["id"], actor["label"],
                   actor["kind"], stamp, stamp, stamp if values["status"] == "done" else None, stamp))
        self._set_labels(c, identity, labels)
        replace_repository_associations(c, "ticket", identity, p.get("repositoryIds") or [])
        after = self.record(c, identity)
        self._index(c, after)
        event(c, actor, "ticket.create", identity, None, after)
        return after

    def update(self, c: sqlite3.Connection, actor: dict, ref: str, p: dict) -> dict:
        row = self.resolve(c, ref)
        require_revision(row, p.get("revision"))
        if row["archived"]:
            raise AppError(409, "ticket_archived", "Recover the ticket before changing it")
        before = self.records(c, [row], full=True)[0]
        values = self._fields(dict(row), p)
        # An existing link stays valid after its target is archived; only a new one is checked.
        item_id = self._item(c, p["itemId"]) if "itemId" in p and p["itemId"] != row["item_id"] else row["item_id"]
        assignee_id = (self._assignee(c, p["assigneeId"]) if "assigneeId" in p and p["assigneeId"] != row["assignee_id"]
                       else row["assignee_id"])
        stamp = now()
        closed_at = (row["closed_at"] or stamp) if values["status"] == "done" else None
        c.execute("UPDATE tickets SET title=?,description=?,kind=?,priority=?,status=?,resolution=?,item_id=?,assignee_id=?,"
                  "closed_at=?,revision=revision+1,updated_at=?,last_activity_at=? WHERE id=?",
                  (values["title"], values["description"], values["kind"], values["priority"], values["status"],
                   values["resolution"], item_id, assignee_id, closed_at, stamp, stamp, row["id"]))
        if p.get("labels") is not None:
            self._set_labels(c, row["id"], _labels(p["labels"]))
        if p.get("repositoryIds") is not None:
            replace_repository_associations(c, "ticket", row["id"], p["repositoryIds"])
        after = self.record(c, row["id"])
        self._index(c, after, retitle=after["title"] != before["title"])
        kind = ("ticket.close" if after["status"] == "done" and before["status"] != "done"
                else "ticket.reopen" if before["status"] == "done" and after["status"] != "done" else "ticket.update")
        event(c, actor, kind, row["id"], before, after)
        return after

    def comment(self, c: sqlite3.Connection, actor: dict, ref: str, p: dict) -> dict:
        row = self.resolve(c, ref)
        if row["archived"]:
            raise AppError(409, "ticket_archived", "Recover the ticket before commenting")
        body = text(p.get("text"), "text", required=True, limit=100_000)
        identity, stamp = uid(), now()
        c.execute("INSERT INTO ticket_comments(id,ticket_id,text,author_id,author_label,author_kind,created_at) VALUES(?,?,?,?,?,?,?)",
                  (identity, row["id"], body, actor["id"], actor["label"], actor["kind"], stamp))
        # A comment changes none of the ticket's fields, so it can't make an edit conflict.
        c.execute("UPDATE tickets SET last_activity_at=? WHERE id=?", (stamp, row["id"]))
        comment = self._comments(c, c.execute("SELECT * FROM ticket_comments WHERE id=?", (identity,)).fetchall())[0]
        search.put(c, identity, "ticket_comment", key(row["number"]) + " " + row["title"], body, row["id"])
        event(c, actor, "ticket.comment", row["id"], None, comment)
        return comment

    def set_archived(self, c: sqlite3.Connection, actor: dict, ref: str, p: dict, archived: bool) -> dict:
        if actor["kind"] != "human":
            raise AppError(403, "agent_scope", "Only the person archives or recovers tickets")
        row = self.resolve(c, ref)
        require_revision(row, p.get("revision"))
        before = self.records(c, [row], full=True)[0]
        c.execute("UPDATE tickets SET archived=?,revision=revision+1,updated_at=? WHERE id=?", (int(archived), now(), row["id"]))
        after = self.record(c, row["id"])
        # Search reads the archive flag from the ticket itself, for its comments as well.
        event(c, actor, "ticket.archive" if archived else "ticket.recover", row["id"], before, after)
        return after

    def list(self, c: sqlite3.Connection, *, status: str = "active", q: str = "", kind: str | None = None,
             priority: str | None = None, label: str | None = None, repository_id: str | None = None,
             item_id: str | None = None, assignee_id: str | None = None, archived: bool = False,
             sort: str = "priority", limit: int = 50, offset: int = 0) -> dict:
        clauses = ["t.archived=?"]
        args: builtins.list[Any] = [int(archived)]
        if status == "active":
            clauses.append("t.status IN ('open','doing','blocked')")
        elif status != "all":
            clauses.append("t.status=?")
            args.append(_choice(status, "status", ("active", "open", "doing", "blocked", "done", "all")))
        for column, value, options in (("kind", kind, KINDS), ("priority", priority, PRIORITIES)):
            if value is not None:
                clauses.append(f"t.{column}=?")
                args.append(_choice(value, column, options))
        if label:
            clauses.append("EXISTS(SELECT 1 FROM ticket_labels tl WHERE tl.ticket_id=t.id AND tl.label=?)")
            args.append(text(label, "label", limit=200).strip().lower())
        if repository_id:
            clauses.append("EXISTS(SELECT 1 FROM ticket_repositories tr WHERE tr.ticket_id=t.id AND tr.repository_id=?)")
            args.append(repository_id)
        if item_id:
            clauses.append("t.item_id=?")
            args.append(item_id)
        if assignee_id == "none":
            clauses.append("t.assignee_id IS NULL")
        elif assignee_id:
            clauses.append("t.assignee_id=?")
            args.append(assignee_id)
        match = search.prefix_match(text(q, "q", limit=2000))
        if match:
            clauses.append("t.id IN(SELECT owner_id FROM search_index WHERE search_index MATCH ? AND type IN('ticket','ticket_comment'))")
            args.append(match)
        order = {"priority": _PRIORITY_ORDER + ",t.last_activity_at DESC,t.id",
                 "recent": "t.last_activity_at DESC,t.id"}.get(sort)
        if order is None:
            raise AppError(422, "validation", "Sort by priority or recent", {"sort": "invalid"})
        where = " AND ".join(clauses)
        total = c.execute("SELECT count(*) FROM tickets t WHERE " + where, args).fetchone()[0]
        rows = c.execute("SELECT t.* FROM tickets t WHERE " + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                         [*args, limit, offset]).fetchall()
        return {"items": self.records(c, rows), "total": total, "limit": limit, "offset": offset}

    def detail(self, c: sqlite3.Connection, ref: str, comments: int = 200) -> dict:
        """The whole ticket with its latest comments, oldest first, and who changed it when.

        History leaves out each change's before and after records: they repeat the whole
        ticket, so a long description edited often would run to megabytes in every read.
        /history?entityId= has them in full.
        """
        row = self.resolve(c, ref)
        total = c.execute("SELECT count(*) FROM ticket_comments WHERE ticket_id=?", (row["id"],)).fetchone()[0]
        latest = c.execute("SELECT * FROM (SELECT * FROM ticket_comments WHERE ticket_id=? ORDER BY created_at DESC,id DESC LIMIT ?) "
                           "ORDER BY created_at,id", (row["id"], comments)).fetchall()
        history = [{key: e[key] for key in ("id", "kind", "actorId", "actorLabel", "actorKind", "createdAt")}
                   for e in read_events(c, row["id"])]
        return {**self.records(c, [row], full=True)[0], "comments": {"items": self._comments(c, latest), "total": total},
                "history": history}
