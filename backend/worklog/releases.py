"""Releases: what ships together and when, what each holds, and what has to happen around it.

A release has a date, or none while it is not dated, and is planned until it is released or
cancelled. It holds work items, roadmaps, roadmap steps and GitHub references (PRs and commits)
by id. Its changes are the GitHub references it holds itself, together with the active ones on
the work items and roadmaps it holds, each with the tracked chat that made it, so a PR an agent
opens on a work item shows on every release that holds the item. Its checks say who needs to
know (notify), what to test (test) and what to prepare first (prepare), each with a work item's
statuses and an optional person and due date.

The person's routes run these commands through WorkService.apply, as approved proposals do.
Holding a record changes neither the release's revision nor the record's (K4): the release's
history records each one. An archived release is read-only until it is recovered.
"""

from __future__ import annotations

import builtins
import sqlite3
from collections.abc import Callable
from typing import Any

from . import search
from .common import (
    NOTE_LIMIT,
    AppError,
    event,
    item_status,
    now,
    require_revision,
    rows_in,
    setting,
    text,
    today,
    uid,
    valid_date,
)
from .plans import step_tree
from .records import get_person

STATUSES = ("planned", "released", "cancelled")
KINDS = ("notify", "test", "prepare")
# What a release can hold, by the table each lives in, and what each is called.
TARGETS = {"item": "work_items", "roadmap": "roadmaps", "step": "steps", "github": "github_refs"}
NOUNS = {"item": "work item", "roadmap": "roadmap", "step": "step", "github": "GitHub reference"}
TITLE_LIMIT = 1_000
CHECK_TEXT_LIMIT = 2_000
DETAILS_LIMIT = 100_000
HISTORY_LIMIT = 100
_CHECK = ("SELECT k.*,p.name AS person_name,p.archived AS person_archived FROM release_checks k "
          "LEFT JOIN people p ON p.id=k.person_id")
# Planned releases first, soonest first; then the others, latest first. Not dated ones end each group.
_ORDER = ("CASE r.status WHEN 'planned' THEN 0 ELSE 1 END,r.release_date IS NULL,"
          "CASE WHEN r.status='planned' THEN r.release_date END,"
          "CASE WHEN r.status<>'planned' THEN r.release_date END DESC,r.title COLLATE NOCASE,r.id")


def release_record(r: Any) -> dict:
    return {"id": r["id"], "title": r["title"], "description": r["description"],
            "releaseDate": r["release_date"], "status": r["status"], "archived": bool(r["archived"]),
            "revision": r["revision"], "createdAt": r["created_at"], "updatedAt": r["updated_at"]}


def check_record(r: Any) -> dict:
    """A check; read with its person, it also names them."""
    result = {"id": r["id"], "releaseId": r["release_id"], "kind": r["kind"], "text": r["text"],
              "details": r["details"], "personId": r["person_id"], "dueDate": r["due_date"],
              "status": r["status"], "archived": bool(r["archived"]), "revision": r["revision"],
              "createdAt": r["created_at"], "updatedAt": r["updated_at"]}
    if "person_name" in r.keys():
        result["personName"] = r["person_name"]
        result["personArchived"] = bool(r["person_archived"])
    return result


def _day(value: Any, field: str) -> str | None:
    """A YYYY-MM-DD calendar day, or None."""
    if value is None:
        return None
    try:
        return valid_date(value)
    except AppError:
        raise AppError(422, "validation", f"{field} must be a YYYY-MM-DD calendar date or null",
                       {field: "invalid"}) from None


def _choice(value: Any, field: str, options: tuple[str, ...], choices: str) -> str:
    if not isinstance(value, str) or value not in options:
        raise AppError(422, "validation", f"Choose {choices}", {field: "invalid"})
    return value


def _past_due(release: dict, current: str) -> dict:
    """A planned release whose date has passed is past due."""
    late = (release["status"] == "planned" and not release["archived"]
            and release["releaseDate"] is not None and release["releaseDate"] < current)
    return {**release, "pastDue": late}


def _check_past_due(check: dict, current: str) -> dict:
    """A check not done whose due date has passed is past due."""
    late = (check["status"] != "done" and not check["archived"]
            and check["dueDate"] is not None and check["dueDate"] < current)
    return {**check, "pastDue": late}


class ReleaseService:
    """The commands that change releases, their contents and their checks, and their reads."""

    def apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        commands: dict[str, Callable[[sqlite3.Connection, dict, dict], dict]] = {
            "release.create": self.create, "release.update": self.update,
            "release.add": self.add, "release.remove": self.remove,
            "check.create": self.create_check, "check.update": self.update_check,
        }
        if action in commands:
            return commands[action](c, actor, p)
        if action in {"release.archive", "release.recover"}:
            return self.set_archived(c, actor, p, action == "release.archive")
        if action in {"check.archive", "check.recover"}:
            return self.set_check_archived(c, actor, p, action == "check.archive")
        raise AppError(422, "operation", "Unsupported operation")

    @staticmethod
    def row(c: sqlite3.Connection, identity: Any) -> sqlite3.Row:
        row = (c.execute("SELECT * FROM releases WHERE id=?", (identity,)).fetchone()
               if isinstance(identity, str) else None)
        if row is None:
            raise AppError(404, "not_found", "Release not found")
        return row

    @staticmethod
    def check_row(c: sqlite3.Connection, identity: Any) -> sqlite3.Row:
        row = c.execute(_CHECK + " WHERE k.id=?", (identity,)).fetchone() if isinstance(identity, str) else None
        if row is None:
            raise AppError(404, "not_found", "Release check not found")
        return row

    @staticmethod
    def _open(release: sqlite3.Row) -> None:
        if release["archived"]:
            raise AppError(409, "archived", "Recover the release before changing it")

    @staticmethod
    def _index(c: sqlite3.Connection, release: dict) -> None:
        search.put(c, release["id"], "release", release["title"], release["description"],
                   release["id"], release["archived"])

    @staticmethod
    def _index_check(c: sqlite3.Connection, check: dict) -> None:
        # Each check keeps its own archive flag. Global search hides a release's checks with it at
        # query time, so recovering the release never recovers a check archived on its own.
        search.put(c, check["id"], "release_check", check["text"], check["details"],
                   check["releaseId"], check["archived"])

    # Releases.

    @staticmethod
    def _fields(current: dict, p: dict) -> dict:
        """Supplied fields over the current ones. A null date makes the release not dated."""
        values = {"title": current["title"], "description": current["description"],
                  "releaseDate": current["releaseDate"], "status": current["status"]}
        values.update({k: p[k] for k in ("title", "description", "status") if p.get(k) is not None})
        if "releaseDate" in p:
            values["releaseDate"] = p["releaseDate"]
        values["title"] = text(values["title"], "title", required=True, limit=TITLE_LIMIT)
        values["description"] = text(values["description"], "description", limit=NOTE_LIMIT)
        values["releaseDate"] = _day(values["releaseDate"], "releaseDate")
        values["status"] = _choice(values["status"], "status", STATUSES, "Planned, Released or Cancelled")
        return values

    def create(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        values = self._fields({"title": "", "description": "", "releaseDate": None, "status": "planned"}, p)
        identity, stamp = uid(), now()
        c.execute("INSERT INTO releases(id,title,description,release_date,status,archived,revision,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,0,1,?,?)",
                  (identity, values["title"], values["description"], values["releaseDate"], values["status"],
                   stamp, stamp))
        after = release_record(self.row(c, identity))
        self._index(c, after)
        event(c, actor, "release.create", identity, None, after)
        return after

    def update(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        row = self.row(c, p.get("id"))
        require_revision(row, p.get("revision"))
        self._open(row)
        before = release_record(row)
        values = self._fields(before, p)
        c.execute("UPDATE releases SET title=?,description=?,release_date=?,status=?,revision=revision+1,updated_at=? "
                  "WHERE id=?",
                  (values["title"], values["description"], values["releaseDate"], values["status"], now(), row["id"]))
        after = release_record(self.row(c, row["id"]))
        self._index(c, after)
        event(c, actor, "release.update", row["id"], before, after)
        return after

    def set_archived(self, c: sqlite3.Connection, actor: dict, p: dict, archived: bool) -> dict:
        row = self.row(c, p.get("id"))
        require_revision(row, p.get("revision"))
        before = release_record(row)
        c.execute("UPDATE releases SET archived=?,revision=revision+1,updated_at=? WHERE id=?",
                  (int(archived), now(), row["id"]))
        after = release_record(self.row(c, row["id"]))
        self._index(c, after)
        event(c, actor, "release.archive" if archived else "release.recover", row["id"], before, after)
        return after

    # What a release holds.

    @staticmethod
    def _target(p: dict) -> tuple[str, str]:
        target_type = p.get("targetType")
        if not isinstance(target_type, str) or target_type not in TARGETS:
            raise AppError(422, "validation", "Choose a work item, roadmap, step or GitHub reference",
                           {"targetType": "invalid"})
        return target_type, text(p.get("targetId"), "targetId", required=True, limit=200)

    @staticmethod
    def _holdable(c: sqlite3.Connection, target_type: str, target_id: str) -> None:
        """Refuse what a release can't take on: a record that doesn't exist or is archived, a retired
        step, or a step or GitHub reference whose roadmap or work item is archived."""
        if target_type == "step":
            row = c.execute("SELECT s.retired,0 AS archived,r.archived AS owner_archived FROM steps s "
                            "JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id=?", (target_id,)).fetchone()
        elif target_type == "github":
            row = c.execute("SELECT g.archived,coalesce(w.archived,rm.archived,0) AS owner_archived FROM github_refs g "
                            "LEFT JOIN work_items w ON g.owner_type='item' AND w.id=g.owner_id "
                            "LEFT JOIN roadmaps rm ON g.owner_type='roadmap' AND rm.id=g.owner_id "
                            "WHERE g.id=?", (target_id,)).fetchone()
        else:
            row = c.execute(f"SELECT archived FROM {TARGETS[target_type]} WHERE id=?", (target_id,)).fetchone()
        noun = NOUNS[target_type]
        if row is None:
            raise AppError(404, "not_found", f"That {noun} does not exist")
        if target_type == "step" and row["retired"]:
            raise AppError(409, "conflict", "Retired steps cannot be added to a release")
        if row["archived"]:
            raise AppError(409, "archived", f"Recover the {noun} before adding it to a release")
        if target_type == "step" and row["owner_archived"]:
            raise AppError(409, "archived", "Recover the step's roadmap before adding the step to a release")
        if target_type == "github" and row["owner_archived"]:
            raise AppError(409, "archived", "Recover the work item or roadmap the GitHub reference is on "
                                            "before adding it to a release")

    def add(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        """Hold a record. Holding one already held changes nothing and records nothing."""
        release = self.row(c, p.get("releaseId"))
        self._open(release)
        target_type, target_id = self._target(p)
        self._holdable(c, target_type, target_id)
        held = {"releaseId": release["id"], "targetType": target_type, "targetId": target_id}
        added = c.execute("INSERT INTO release_contents(release_id,target_type,target_id,created_at) VALUES(?,?,?,?) "
                          "ON CONFLICT(release_id,target_type,target_id) DO NOTHING",
                          (release["id"], target_type, target_id, now())).rowcount == 1
        if added:
            event(c, actor, "release.add", release["id"], None, held)
        (created,) = c.execute("SELECT created_at FROM release_contents WHERE release_id=? AND target_type=? "
                               "AND target_id=?", (release["id"], target_type, target_id)).fetchone()
        return {**held, "createdAt": created, "changed": added}

    def remove(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        """Stop holding a record. Removing one not held changes nothing and records nothing."""
        release = self.row(c, p.get("releaseId"))
        self._open(release)
        target_type, target_id = self._target(p)
        held = {"releaseId": release["id"], "targetType": target_type, "targetId": target_id}
        removed = c.execute("DELETE FROM release_contents WHERE release_id=? AND target_type=? AND target_id=?",
                            (release["id"], target_type, target_id)).rowcount == 1
        if removed:
            event(c, actor, "release.remove", release["id"], held, None)
        return {**held, "changed": removed}

    # Checks.

    @staticmethod
    def _check_fields(c: sqlite3.Connection, current: dict, p: dict) -> dict:
        """Supplied fields over the current ones. A null person or due date clears it."""
        values = {k: current[k] for k in ("kind", "text", "details", "personId", "dueDate", "status")}
        values.update({k: p[k] for k in ("kind", "text", "details", "status") if p.get(k) is not None})
        for key in ("personId", "dueDate"):
            if key in p:
                values[key] = p[key]
        values["kind"] = _choice(values["kind"], "kind", KINDS,
                                 "who needs to know (notify), what to test (test) or what to prepare (prepare)")
        values["text"] = text(values["text"], "text", required=True, limit=CHECK_TEXT_LIMIT)
        values["details"] = text(values["details"], "details", limit=DETAILS_LIMIT)
        values["status"] = item_status(values["status"])
        values["dueDate"] = _day(values["dueDate"], "dueDate")
        person = values["personId"]
        if person is not None:
            if not isinstance(person, str):
                raise AppError(422, "validation", "personId must be a person's id or null", {"personId": "invalid"})
            # A person newly named must be active, as for a new tie; one named before keeps their place.
            if person != current["personId"]:
                get_person(c, person, active=True)
        return values

    def create_check(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        release = self.row(c, p.get("releaseId"))
        self._open(release)
        values = self._check_fields(c, {"kind": None, "text": "", "details": "", "personId": None,
                                        "dueDate": None, "status": "open"}, p)
        identity, stamp = uid(), now()
        c.execute("INSERT INTO release_checks(id,release_id,kind,text,details,person_id,due_date,status,archived,"
                  "revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,0,1,?,?)",
                  (identity, release["id"], values["kind"], values["text"], values["details"], values["personId"],
                   values["dueDate"], values["status"], stamp, stamp))
        after = check_record(self.check_row(c, identity))
        self._index_check(c, after)
        event(c, actor, "check.create", identity, None, after)
        return after

    def update_check(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        row = self.check_row(c, p.get("id"))
        require_revision(row, p.get("revision"))
        self._open(self.row(c, row["release_id"]))
        if row["archived"]:
            raise AppError(409, "archived", "Recover the check before changing it")
        before = check_record(row)
        values = self._check_fields(c, before, p)
        c.execute("UPDATE release_checks SET kind=?,text=?,details=?,person_id=?,due_date=?,status=?,"
                  "revision=revision+1,updated_at=? WHERE id=?",
                  (values["kind"], values["text"], values["details"], values["personId"], values["dueDate"],
                   values["status"], now(), row["id"]))
        after = check_record(self.check_row(c, row["id"]))
        self._index_check(c, after)
        event(c, actor, "check.update", row["id"], before, after)
        return after

    def set_check_archived(self, c: sqlite3.Connection, actor: dict, p: dict, archived: bool) -> dict:
        row = self.check_row(c, p.get("id"))
        require_revision(row, p.get("revision"))
        self._open(self.row(c, row["release_id"]))
        before = check_record(row)
        c.execute("UPDATE release_checks SET archived=?,revision=revision+1,updated_at=? WHERE id=?",
                  (int(archived), now(), row["id"]))
        after = check_record(self.check_row(c, row["id"]))
        self._index_check(c, after)
        event(c, actor, "check.archive" if archived else "check.recover", row["id"], before, after)
        return after

    # Reads.

    def list(self, c: sqlite3.Connection, *, status: str = "planned", archived: bool = False, q: str = "",
             target_type: str | None = None, target_id: str | None = None,
             limit: int = 50, offset: int = 0) -> dict:
        """Releases in the list's order, each with its counts. `status` is planned, released,
        cancelled or all; q matches a release's title or description or one of its checks; a
        target names a record, for the releases that hold it."""
        clauses: builtins.list[str] = ["r.archived=?"]
        args: builtins.list[Any] = [int(archived)]
        if status != "all":
            clauses.append("r.status=?")
            args.append(_choice(status, "status", STATUSES, "planned, released, cancelled or all"))
        match = search.prefix_match(text(q, "q", limit=2000))
        if match:
            # A release matches on its own words, archived or not, as the list asked, and on its
            # checks' words, apart from those archived on their own.
            clauses.append("r.id IN(SELECT owner_id FROM search_index WHERE search_index MATCH ? "
                           "AND (type='release' OR (type='release_check' AND CAST(archived AS INTEGER)=0)))")
            args.append(match)
        if target_type is not None or target_id is not None:
            held_type, held_id = self._target({"targetType": target_type, "targetId": target_id})
            clauses.append("r.id IN(SELECT release_id FROM release_contents WHERE target_type=? AND target_id=?)")
            args += [held_type, held_id]
        where = " AND ".join(clauses)
        total = c.execute("SELECT count(*) FROM releases r WHERE " + where, args).fetchone()[0]
        rows = c.execute(f"SELECT r.* FROM releases r WHERE {where} ORDER BY {_ORDER} LIMIT ? OFFSET ?",
                         [*args, limit, offset]).fetchall()
        current = today(setting(c, "timezone", "America/Chicago"))
        counts = self._counts(c, [r["id"] for r in rows], current)
        items = [{**_past_due(release_record(r), current), "counts": counts[r["id"]]} for r in rows]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def _counts(c: sqlite3.Connection, ids: builtins.list[str], current: str) -> dict[str, dict]:
        """Per release: its active work items and how many are done, its open checks of each kind,
        and how many open checks are past due."""
        counts = {identity: {"items": 0, "itemsDone": 0, "notify": 0, "test": 0, "prepare": 0, "pastDue": 0}
                  for identity in ids}
        for r in rows_in(c, "SELECT rc.release_id,count(*) AS total,sum(w.status='done') AS done "
                            "FROM release_contents rc JOIN work_items w ON w.id=rc.target_id "
                            "WHERE rc.target_type='item' AND w.archived=0 AND rc.release_id IN ({}) "
                            "GROUP BY rc.release_id", ids):
            counts[r["release_id"]].update(items=r["total"], itemsDone=r["done"])
        for r in rows_in(c, "SELECT release_id,kind,due_date FROM release_checks "
                            "WHERE archived=0 AND status<>'done' AND release_id IN ({})", ids):
            entry = counts[r["release_id"]]
            entry[r["kind"]] += 1
            if r["due_date"] is not None and r["due_date"] < current:
                entry["pastDue"] += 1
        return counts

    def detail(self, c: sqlite3.Connection, identity: str) -> dict:
        """A release with what it holds, its changes, every check and who changed them when.

        History leaves out each change's before and after records, as a ticket's does: a long
        description edited often would repeat it in every read. /history?entityId= has them.
        """
        row = self.row(c, identity)
        current = today(setting(c, "timezone", "America/Chicago"))
        held: dict[str, builtins.list[tuple[str, str]]] = {kind: [] for kind in TARGETS}
        for h in c.execute("SELECT target_type,target_id,created_at FROM release_contents WHERE release_id=? "
                           "ORDER BY created_at,target_type,target_id", (row["id"],)):
            held[h["target_type"]].append((h["target_id"], h["created_at"]))
        checks = [_check_past_due(check_record(r), current) for r in c.execute(
            _CHECK + " WHERE k.release_id=? ORDER BY k.due_date IS NULL,k.due_date,k.created_at,k.id", (row["id"],))]
        return {**_past_due(release_record(row), current),
                "items": self._items(c, held["item"]),
                "roadmaps": self._roadmaps(c, held["roadmap"]),
                "steps": self._steps(c, held["step"]),
                "changes": self._changes(c, row["id"]),
                "checks": checks,
                "history": self._history(c, [row["id"], *(check["id"] for check in checks)])}

    def check(self, c: sqlite3.Connection, identity: str) -> dict:
        return _check_past_due(check_record(self.check_row(c, identity)),
                               today(setting(c, "timezone", "America/Chicago")))

    @staticmethod
    def _items(c: sqlite3.Connection, held: builtins.list[tuple[str, str]]) -> builtins.list[dict]:
        found = {r["id"]: r for r in rows_in(c, "SELECT id,title,status,archived FROM work_items WHERE id IN ({})",
                                             [identity for identity, _ in held])}
        return [{"id": identity, "title": found[identity]["title"], "status": found[identity]["status"],
                 "archived": bool(found[identity]["archived"]), "addedAt": added}
                for identity, added in held if identity in found]

    @staticmethod
    def _roadmaps(c: sqlite3.Connection, held: builtins.list[tuple[str, str]]) -> builtins.list[dict]:
        """Each roadmap with its finished leaf steps out of all, as its progress bar counts them."""
        found = {r["id"]: r for r in rows_in(c, "SELECT id,title,archived FROM roadmaps WHERE id IN ({})",
                                             [identity for identity, _ in held])}
        result = []
        for identity, added in held:
            if identity not in found:
                continue
            active = [s for s in step_tree(c, identity) if not s["retired"]]
            parents = {s["parentId"] for s in active}
            leaves = [s for s in active if s["id"] not in parents]
            result.append({"id": identity, "title": found[identity]["title"],
                           "archived": bool(found[identity]["archived"]), "addedAt": added,
                           "steps": len(leaves), "stepsDone": sum(s["status"] == "done" for s in leaves)})
        return result

    @staticmethod
    def _steps(c: sqlite3.Connection, held: builtins.list[tuple[str, str]]) -> builtins.list[dict]:
        """Each step with its progress as its roadmap shows it: a parent's follows its leaves."""
        found = {r["id"]: r for r in rows_in(c, "SELECT s.id,s.roadmap_id,r.title AS roadmap_title,"
                                                "r.archived AS roadmap_archived FROM steps s "
                                                "JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id IN ({})",
                                             [identity for identity, _ in held])}
        trees: dict[str, dict[str, dict]] = {}
        result = []
        for identity, added in held:
            row = found.get(identity)
            if row is None:
                continue
            if row["roadmap_id"] not in trees:
                trees[row["roadmap_id"]] = {s["id"]: s for s in step_tree(c, row["roadmap_id"])}
            step = trees[row["roadmap_id"]][identity]
            result.append({"id": identity, "title": step["title"], "status": step["status"],
                           "retired": step["retired"], "roadmapId": row["roadmap_id"],
                           "roadmapTitle": row["roadmap_title"], "roadmapArchived": bool(row["roadmap_archived"]),
                           "addedAt": added})
        return result

    @staticmethod
    def _changes(c: sqlite3.Connection, release_id: str) -> builtins.list[dict]:
        """The GitHub references the release holds, archived ones included, and the active ones on
        the active work items and roadmaps it holds, once each, with the tracked chat that made each.
        A reference counts as archived when it is or the work item or roadmap it is on is, as search
        counts it."""
        rows = c.execute(
            """SELECT g.id,g.kind,g.url,g.reference,g.title,g.owner_type,g.owner_id,g.chat_id,g.created_at,
                      g.archived OR coalesce(w.archived,rm.archived,0) AS archived,
                      coalesce(w.title,rm.title) AS owner_title,ac.title AS chat_title,ac.provider,ac.model,
                      EXISTS(SELECT 1 FROM release_contents d WHERE d.release_id=? AND d.target_type='github'
                             AND d.target_id=g.id) AS direct
               FROM github_refs g
               LEFT JOIN work_items w ON g.owner_type='item' AND w.id=g.owner_id
               LEFT JOIN roadmaps rm ON g.owner_type='roadmap' AND rm.id=g.owner_id
               LEFT JOIN agent_chats ac ON ac.id=g.chat_id
               WHERE g.id IN(SELECT target_id FROM release_contents WHERE release_id=? AND target_type='github'
                             UNION SELECT h.id FROM github_refs h JOIN release_contents rc
                                   ON rc.target_type=h.owner_type AND rc.target_id=h.owner_id
                                   LEFT JOIN work_items hw ON h.owner_type='item' AND hw.id=h.owner_id
                                   LEFT JOIN roadmaps hr ON h.owner_type='roadmap' AND hr.id=h.owner_id
                                   WHERE rc.release_id=? AND rc.target_type IN('item','roadmap') AND h.archived=0
                                         AND coalesce(hw.archived,hr.archived,0)=0)
               ORDER BY g.created_at,g.id""",
            (release_id, release_id, release_id)).fetchall()
        return [{"id": r["id"], "kind": r["kind"], "url": r["url"], "reference": r["reference"], "title": r["title"],
                 "ownerType": r["owner_type"], "ownerId": r["owner_id"], "ownerTitle": r["owner_title"],
                 "chatId": r["chat_id"], "chatTitle": r["chat_title"], "provider": r["provider"], "model": r["model"],
                 "direct": bool(r["direct"]), "archived": bool(r["archived"]), "createdAt": r["created_at"]}
                for r in rows]

    @staticmethod
    def _history(c: sqlite3.Connection, ids: builtins.list[str]) -> builtins.list[dict]:
        """The latest changes to the release and its checks, newest first, in the order they were
        written: by rowid, which a coarse clock can't tie, since Worklog never deletes events."""
        rows = rows_in(c, "SELECT rowid,id,entity_id,kind,actor_id,actor_label,actor_kind,created_at FROM events "
                          "WHERE entity_id IN ({}) ORDER BY rowid DESC LIMIT " + str(HISTORY_LIMIT), ids)
        latest = sorted(rows, key=lambda r: r["rowid"], reverse=True)[:HISTORY_LIMIT]
        return [{"id": r["id"], "entityId": r["entity_id"], "kind": r["kind"], "actorId": r["actor_id"],
                 "actorLabel": r["actor_label"], "actorKind": r["actor_kind"], "createdAt": r["created_at"]}
                for r in latest]
