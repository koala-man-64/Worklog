"""Reminders: what the person wants to be told on a day, or at a time on it.

A reminder is due on its date, from the start of the day, or at its time on that date, by the
clock of the Worklog timezone setting, and it stays due until it is marked done. Snoozing moves
its date and time; nothing repeats. A reminder may name one record: a work item, ticket, roadmap,
person, repository or tracked chat, whose page lists it.

Agents set, change, snooze and complete reminders directly, as they file tickets: each change
carries the author's stable communication identity and lands in the reminder's history. Only the
person archives or recovers a reminder, and an archived reminder is read-only.
"""

from __future__ import annotations

import builtins
import re
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import search
from .common import (
    AppError,
    event,
    now,
    read_events,
    require_revision,
    rows_in,
    setting,
    text,
    uid,
    valid_date,
)
from .tickets import TicketService
from .tickets import key as ticket_key

TITLE_LIMIT = 1_000
NOTES_LIMIT = 100_000
# What a list shows: open reminders, those of them due now or still to come, done ones, or all.
VIEWS = ("open", "due", "upcoming", "done", "all")
_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
# Each kind of record a reminder may name: how to read it, and its name in messages.
_TARGETS = {
    "item": ("SELECT id,title,archived FROM work_items WHERE id IN ({})", "Work item"),
    "ticket": ("SELECT id,number,title,archived FROM tickets WHERE id IN ({})", "Ticket"),
    "roadmap": ("SELECT id,title,archived FROM roadmaps WHERE id IN ({})", "Roadmap"),
    "person": ("SELECT id,name AS title,archived FROM people WHERE id IN ({})", "Person"),
    "repository": ("SELECT id,name AS title,archived FROM repositories WHERE id IN ({})", "Repository"),
    "chat": ("SELECT id,title,archived FROM agent_chats WHERE id IN ({})", "Chat"),
}
# Due: dated before today, or today and its time has come. An all-day reminder is due from midnight.
_DUE = "(r.due_date<? OR (r.due_date=? AND coalesce(r.due_time,'00:00')<=?))"
# Open reminders first, by when they come due, all-day ones first on a date; then done ones, the
# latest done first.
_ORDER = ("r.state='done',CASE WHEN r.state='open' THEN r.due_date END,"
          "CASE WHEN r.state='open' THEN r.due_time IS NOT NULL END,CASE WHEN r.state='open' THEN r.due_time END,"
          "r.done_at DESC,r.created_at,r.id")
# Every column but the notes, which only a reminder's own page shows.
_LISTED = ("r.id,r.title,r.due_date,r.due_time,r.state,r.target_type,r.target_id,r.author_id,r.author_label,"
           "r.author_kind,r.archived,r.revision,r.created_at,r.updated_at,r.done_at")


def local_now(tz: str) -> datetime:
    """Now, by the clock of the Worklog timezone. Tests put their own clock here."""
    return datetime.now(ZoneInfo(tz))


def _clock(c: sqlite3.Connection) -> tuple[str, str]:
    """Today's date and the time of day, HH:MM, by the clock of the Worklog timezone."""
    current = local_now(setting(c, "timezone", "America/Chicago"))
    return current.date().isoformat(), current.strftime("%H:%M")


def _date(value: Any, field: str) -> str:
    try:
        return valid_date(value)
    except AppError:
        raise AppError(422, "validation", "Use a valid YYYY-MM-DD calendar date", {field: "invalid"}) from None


def _time(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise AppError(422, "validation", "Use a time of day from 00:00 to 23:59, as HH:MM", {"time": "invalid"})
    return value


def _target(c: sqlite3.Connection, kind: Any, ref: Any, current: tuple[str, str] | None = None) -> tuple[str, str] | None:
    """The record a reminder names: it must exist and, unless the reminder already names it, be active.
    A ticket can be named by its key (T-12) or number as well as its ID. Neither part names none."""
    if kind is None and ref is None:
        return None
    if kind not in _TARGETS or not isinstance(ref, str) or not ref.strip():
        raise AppError(422, "validation", "Name a record with both targetType and targetId, or neither",
                       {"targetType" if kind not in _TARGETS else "targetId": "invalid"})
    sql, name = _TARGETS[kind]
    row = TicketService.resolve(c, ref) if kind == "ticket" else c.execute(sql.format("?"), (ref,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", f"{name} not found")
    # A reminder keeps a record it already names after that record is archived; only a new one is checked.
    if row["archived"] and (kind, row["id"]) != current:
        raise AppError(409, "archived", f"Name an active {name.lower()}; this one is archived")
    return kind, row["id"]


def _records(c: sqlite3.Connection, rows: list[sqlite3.Row], *, full: bool = False) -> list[dict]:
    """Reminders as the API returns them, each record they name read as it stands now. Only a full
    record carries the notes."""
    today, time = _clock(c)
    wanted: dict[str, set[str]] = {}
    for r in rows:
        if r["target_type"]:
            wanted.setdefault(r["target_type"], set()).add(r["target_id"])
    targets: dict[tuple[str, str], dict] = {}
    for kind, ids in wanted.items():
        for t in rows_in(c, _TARGETS[kind][0], sorted(ids)):
            title = ticket_key(t["number"]) + " " + t["title"] if kind == "ticket" else t["title"]
            targets[(kind, t["id"])] = {"type": kind, "id": t["id"], "title": title, "archived": bool(t["archived"])}
    agents = sorted({r["author_id"] for r in rows if r["author_kind"] == "agent"})
    avatars = {a["id"]: a["avatar"] for a in rows_in(c, "SELECT id,avatar FROM communication_agents WHERE id IN ({})", agents)}
    result = []
    for r in rows:
        waiting = r["state"] == "open"
        target = None
        if r["target_type"]:
            target = targets.get((r["target_type"], r["target_id"]),
                                 {"type": r["target_type"], "id": r["target_id"], "title": "", "archived": False})
        reminder: dict[str, Any] = {
            "id": r["id"], "title": r["title"], "date": r["due_date"], "time": r["due_time"], "state": r["state"],
            "due": waiting and (r["due_date"] < today or (r["due_date"] == today and (r["due_time"] or "00:00") <= time)),
            "overdue": waiting and r["due_date"] < today, "target": target,
            "author": {"id": r["author_id"], "label": r["author_label"], "kind": r["author_kind"],
                       "avatar": avatars.get(r["author_id"])},
            "archived": bool(r["archived"]), "revision": r["revision"], "createdAt": r["created_at"],
            "updatedAt": r["updated_at"], "doneAt": r["done_at"]}
        if full:
            reminder["notes"] = r["notes"]
        result.append(reminder)
    return result


class ReminderService:
    @staticmethod
    def row(c: sqlite3.Connection, identity: str) -> sqlite3.Row:
        row = c.execute("SELECT * FROM reminders WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Reminder not found")
        return row

    def record(self, c: sqlite3.Connection, identity: str) -> dict:
        return _records(c, [self.row(c, identity)], full=True)[0]

    @staticmethod
    def _fields(current: dict, p: dict) -> dict:
        """Supplied fields over the current ones, validated together as the reminder would read. A
        supplied null time makes it an all-day reminder."""
        values = {k: current[k] for k in ("title", "notes", "date")}
        values.update({k: v for k, v in p.items() if k in values and v is not None})
        values["title"] = text(values["title"], "title", required=True, limit=TITLE_LIMIT)
        values["notes"] = text(values["notes"], "notes", limit=NOTES_LIMIT)
        values["date"] = _date(values["date"], "date")
        values["time"] = _time(p["time"]) if "time" in p else current["time"]
        return values

    @staticmethod
    def _index(c: sqlite3.Connection, reminder: dict) -> None:
        search.put(c, reminder["id"], "reminder", reminder["title"], reminder["notes"], reminder["id"], reminder["archived"])

    def create(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        values = self._fields({"title": "", "notes": "", "date": "", "time": None}, p)
        target_type, target_id = _target(c, p.get("targetType"), p.get("targetId")) or (None, None)
        identity, stamp = uid(), now()
        c.execute("INSERT INTO reminders(id,title,due_date,due_time,state,target_type,target_id,author_id,author_label,"
                  "author_kind,archived,revision,created_at,updated_at,done_at,notes) "
                  "VALUES(?,?,?,?,'open',?,?,?,?,?,0,1,?,?,NULL,?)",
                  (identity, values["title"], values["date"], values["time"], target_type, target_id,
                   actor["id"], actor["label"], actor["kind"], stamp, stamp, values["notes"]))
        after = self.record(c, identity)
        self._index(c, after)
        event(c, actor, "reminder.create", identity, None, after)
        return after

    def update(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        row = self.row(c, identity)
        require_revision(row, p.get("revision"))
        if row["archived"]:
            raise AppError(409, "archived", "Recover the reminder before changing it")
        before = _records(c, [row], full=True)[0]
        values = self._fields(before, p)
        current = (row["target_type"], row["target_id"]) if row["target_type"] else None
        target = (_target(c, p.get("targetType"), p.get("targetId"), current)
                  if "targetType" in p or "targetId" in p else current)
        state = p.get("state") or row["state"]
        if state not in ("open", "done"):
            raise AppError(422, "validation", "Choose open or done", {"state": "invalid"})
        stamp = now()
        c.execute("UPDATE reminders SET title=?,notes=?,due_date=?,due_time=?,state=?,target_type=?,target_id=?,done_at=?,"
                  "revision=revision+1,updated_at=? WHERE id=?",
                  (values["title"], values["notes"], values["date"], values["time"], state,
                   *(target or (None, None)), (row["done_at"] or stamp) if state == "done" else None, stamp, row["id"]))
        after = self.record(c, row["id"])
        self._index(c, after)
        kind = ("reminder.done" if after["state"] == "done" and before["state"] != "done"
                else "reminder.reopen" if before["state"] == "done" and after["state"] != "done" else "reminder.update")
        event(c, actor, kind, row["id"], before, after)
        return after

    def set_archived(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict, archived: bool) -> dict:
        # The HTTP layer already refuses agents here; this keeps any other caller to the rule.
        if actor["kind"] != "human":
            raise AppError(403, "agent_scope", "Only the person archives or recovers reminders")
        row = self.row(c, identity)
        require_revision(row, p.get("revision"))
        before = _records(c, [row], full=True)[0]
        c.execute("UPDATE reminders SET archived=?,revision=revision+1,updated_at=? WHERE id=?", (int(archived), now(), row["id"]))
        after = self.record(c, row["id"])
        # Search reads the archive flag from the index, so an archived reminder leaves ordinary results.
        self._index(c, after)
        event(c, actor, "reminder.archive" if archived else "reminder.recover", row["id"], before, after)
        return after

    def list(self, c: sqlite3.Connection, *, state: str = "open", date: str | None = None, until: str | None = None,
             target_type: str | None = None, target_id: str | None = None, q: str = "", archived: bool = False,
             limit: int = 50, offset: int = 0) -> dict:
        """Reminders without their notes. `date` keeps those due on that day and `until` those due on or
        before it; a target keeps those that name it; q matches title or notes."""
        if state not in VIEWS:
            raise AppError(422, "validation", "Show open, due, upcoming, done or all reminders", {"state": "invalid"})
        clauses = ["r.archived=?"]
        args: builtins.list[Any] = [int(archived)]
        if state != "all":
            clauses.append("r.state=?")
            args.append("done" if state == "done" else "open")
        if state in ("due", "upcoming"):
            today, time = _clock(c)
            clauses.append(_DUE if state == "due" else "NOT " + _DUE)
            args += [today, today, time]
        if date is not None:
            clauses.append("r.due_date=?")
            args.append(_date(date, "date"))
        if until is not None:
            clauses.append("r.due_date<=?")
            args.append(_date(until, "until"))
        if target_type is not None or target_id is not None:
            if target_type not in _TARGETS or not target_id:
                raise AppError(422, "validation", "Filter by a record with both targetType and targetId",
                               {"targetType" if target_type not in _TARGETS else "targetId": "invalid"})
            clauses.append("r.target_type=? AND r.target_id=?")
            args += [target_type, TicketService.resolve(c, target_id)["id"] if target_type == "ticket" else target_id]
        match = search.prefix_match(text(q, "q", limit=2000))
        if match:
            clauses.append("r.id IN(SELECT entity_id FROM search_index WHERE search_index MATCH ? AND type='reminder')")
            args.append(match)
        where = " AND ".join(clauses)
        total = c.execute("SELECT count(*) FROM reminders r WHERE " + where, args).fetchone()[0]
        rows = c.execute(f"SELECT {_LISTED} FROM reminders r WHERE {where} ORDER BY {_ORDER} LIMIT ? OFFSET ?",
                         [*args, limit, offset]).fetchall()
        return {"items": _records(c, rows), "total": total, "limit": limit, "offset": offset}

    def detail(self, c: sqlite3.Connection, identity: str) -> dict:
        """The whole reminder and who changed it when. History leaves out each change's before and
        after records, as a resource's does; /history?entityId= has them in full."""
        row = self.row(c, identity)
        history = [{key: e[key] for key in ("id", "kind", "actorId", "actorLabel", "actorKind", "createdAt")}
                   for e in read_events(c, row["id"])]
        return {**_records(c, [row], full=True)[0], "history": history}
