"""Resources: reference pages the person writes for themselves and their agents to read.

A resource is one Markdown page with a title and a one-line summary, so a list can say what
each page is for without its text. Only the person creates, edits, archives and recovers
resources; agents read them, as the person does, and never change them, not even by proposal.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import search
from .common import NOTE_LIMIT, AppError, event, now, read_events, require_revision, text, uid

TITLE_LIMIT = 1_000
SUMMARY_LIMIT = 2_000
# As much as a scratchpad holds.
BODY_LIMIT = NOTE_LIMIT
_LISTED = "r.id,r.title,r.summary,r.archived,r.revision,r.created_at,r.updated_at"


def record(r: sqlite3.Row, *, full: bool = False) -> dict:
    """A resource as the API returns it; only a full record carries the page's text."""
    result: dict[str, Any] = {"id": r["id"], "title": r["title"], "summary": r["summary"],
                              "archived": bool(r["archived"]), "revision": r["revision"],
                              "createdAt": r["created_at"], "updatedAt": r["updated_at"]}
    if full:
        result["body"] = r["body"]
    return result


class ResourceService:
    @staticmethod
    def row(c: sqlite3.Connection, identity: str) -> sqlite3.Row:
        row = c.execute("SELECT * FROM resources WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Resource not found")
        return row

    @staticmethod
    def _person(actor: dict) -> None:
        # The HTTP layer already refuses agents' writes; this keeps any other caller to the rule.
        if actor["kind"] != "human":
            raise AppError(403, "agent_scope", "Only the person writes resources; agents read them")

    @staticmethod
    def _fields(current: dict, p: dict) -> dict:
        """Supplied fields over the current ones, validated together as the page would read."""
        values = {k: current[k] for k in ("title", "summary", "body")}
        values.update({k: v for k, v in p.items() if k in values and v is not None})
        values["title"] = text(values["title"], "title", required=True, limit=TITLE_LIMIT)
        # Kept as typed: trimming a saved summary would take the space being typed after a word.
        values["summary"] = text(values["summary"], "summary", limit=SUMMARY_LIMIT)
        values["body"] = text(values["body"], "body", limit=BODY_LIMIT)
        return values

    @staticmethod
    def _index(c: sqlite3.Connection, resource: dict) -> None:
        body = "\n\n".join(part for part in (resource["summary"].strip(), resource["body"]) if part.strip())
        search.put(c, resource["id"], "resource", resource["title"], body, resource["id"], resource["archived"])

    def create(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        self._person(actor)
        values = self._fields({"title": "", "summary": "", "body": ""}, p)
        identity, stamp = uid(), now()
        c.execute("INSERT INTO resources(id,title,summary,archived,revision,created_at,updated_at,body) VALUES(?,?,?,0,1,?,?,?)",
                  (identity, values["title"], values["summary"], stamp, stamp, values["body"]))
        after = record(self.row(c, identity), full=True)
        self._index(c, after)
        event(c, actor, "resource.create", identity, None, after)
        return after

    def update(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        self._person(actor)
        row = self.row(c, identity)
        require_revision(row, p.get("revision"))
        if row["archived"]:
            raise AppError(409, "archived", "Recover the resource before changing it")
        before = record(row, full=True)
        values = self._fields(before, p)
        c.execute("UPDATE resources SET title=?,summary=?,body=?,revision=revision+1,updated_at=? WHERE id=?",
                  (values["title"], values["summary"], values["body"], now(), row["id"]))
        after = record(self.row(c, row["id"]), full=True)
        self._index(c, after)
        event(c, actor, "resource.update", row["id"], before, after)
        return after

    def set_archived(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict, archived: bool) -> dict:
        self._person(actor)
        row = self.row(c, identity)
        require_revision(row, p.get("revision"))
        before = record(row, full=True)
        c.execute("UPDATE resources SET archived=?,revision=revision+1,updated_at=? WHERE id=?", (int(archived), now(), row["id"]))
        after = record(self.row(c, row["id"]), full=True)
        # Search reads the archive flag from the index, so an archived page leaves ordinary results.
        self._index(c, after)
        event(c, actor, "resource.archive" if archived else "resource.recover", row["id"], before, after)
        return after

    def list(self, c: sqlite3.Connection, *, q: str = "", archived: bool = False,
             limit: int = 50, offset: int = 0) -> dict:
        """Titles and summaries by title, without the pages' text; q matches title, summary or text."""
        clauses: list[str] = ["r.archived=?"]
        args: list[Any] = [int(archived)]
        match = search.prefix_match(text(q, "q", limit=2000))
        if match:
            clauses.append("r.id IN(SELECT entity_id FROM search_index WHERE search_index MATCH ? AND type='resource')")
            args.append(match)
        where = " AND ".join(clauses)
        total = c.execute("SELECT count(*) FROM resources r WHERE " + where, args).fetchone()[0]
        rows = c.execute(f"SELECT {_LISTED} FROM resources r WHERE {where} ORDER BY r.title COLLATE NOCASE,r.id LIMIT ? OFFSET ?",
                         [*args, limit, offset]).fetchall()
        return {"items": [record(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def detail(self, c: sqlite3.Connection, identity: str) -> dict:
        """The whole page and who changed it when.

        History leaves out each change's before and after records: they repeat the whole page,
        so a long page edited often would run to megabytes in every read. /history?entityId=
        has them in full.
        """
        row = self.row(c, identity)
        history = [{key: e[key] for key in ("id", "kind", "actorId", "actorLabel", "actorKind", "createdAt")}
                   for e in read_events(c, row["id"])]
        return {**record(row, full=True), "history": history}
