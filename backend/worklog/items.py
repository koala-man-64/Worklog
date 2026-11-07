"""Work items and their days: the backlog and its stack rank, each day's plan and reflection,
and the commands that change them.
"""

from __future__ import annotations

import builtins
import json
import re
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from . import search, support
from .common import (
    AppError,
    boolean,
    event,
    item_status,
    now,
    read_events,
    require_revision,
    setting,
    text,
    today,
    uid,
    valid_date,
)
from .coordination import release_item_participations
from .db import Database
from .records import entry_record, get_item, item_record, people_on
from .repository_links import linked_repositories, replace_repository_associations

# The stack rank orders unfinished, unarchived work, 1 first. It reads backlog_order, the one
# order every work item keeps, so done and archived items hold their place in it and rejoin
# the ranking there when reopened or recovered.
RANKED = "archived=0 AND status!='done'"
RANKS = f"SELECT id,ROW_NUMBER() OVER (ORDER BY backlog_order,id) AS stack_rank FROM work_items WHERE {RANKED}"


def stack_ranks(c: sqlite3.Connection, ids: list[str]) -> dict[str, int]:
    """The 1-based stack rank of each of these items that is ranked."""
    if not ids:
        return {}
    return dict(c.execute(f"SELECT id,stack_rank FROM ({RANKS}) WHERE id IN ({','.join('?' for _ in ids)})", ids).fetchall())


def ranked_count(c: sqlite3.Connection) -> int:
    return c.execute(f"SELECT count(*) FROM work_items WHERE {RANKED}").fetchone()[0]


def renumber(c: sqlite3.Connection, order: list[str]) -> None:
    """Give every work item its own backlog position in this order, writing only those that move."""
    current = dict(c.execute("SELECT id,backlog_order FROM work_items").fetchall())
    c.executemany("UPDATE work_items SET backlog_order=? WHERE id=?",
                  [(position, identity) for position, identity in enumerate(order, 1) if current[identity] != position])


def backlog_sequence(c: sqlite3.Connection) -> list[str]:
    """Every work item, archived and done ones included, in backlog order."""
    return [r[0] for r in c.execute("SELECT id FROM work_items ORDER BY backlog_order,id")]


class Items:
    def __init__(self, db: Database):
        self.db = db

    def list(self, *, view: str = "all", archived: bool = False, states: builtins.list[str] | None = None,
             roadmap_id: str | None = None, person_id: str | None = None, repository_id: str | None = None,
             limit: int = 100, offset: int = 0, q: str = "", item_id: str | None = None) -> dict:
        # Any of the given states matches; none, or only empty values, means every state.
        chosen = sorted({item_status(value) for value in states or [] if value != ""})
        with self.db.read() as c:
            where = ["w.archived=?"]
            args: builtins.list[Any] = [int(archived)]
            if item_id is not None:
                where.append("w.id=?")
                args.append(item_id)
            if chosen:
                where.append("w.status IN(" + ",".join("?" for _ in chosen) + ")")
                args.extend(chosen)
            if roadmap_id:
                where.append("(w.roadmap_id=? OR w.step_id IN(SELECT id FROM steps WHERE roadmap_id=?))")
                args.extend([roadmap_id, roadmap_id])
            if repository_id:
                where.append("w.id IN(SELECT item_id FROM item_repositories WHERE repository_id=?)")
                args.append(repository_id)
            if person_id:
                where.append("w.id IN(SELECT target_id FROM person_links WHERE target_type='item' AND person_id=?)")
                args.append(person_id)
            if view == "backlog":
                where.extend(["w.status!='done'", "NOT EXISTS(SELECT 1 FROM daily_entries e WHERE e.item_id=w.id AND e.planned=1 AND e.date>=?)"])
                args.append(today(setting(c, "timezone", "America/Chicago")))
            elif view != "all":
                raise AppError(422, "validation", "Unknown work view")
            match = search.prefix_match(q)
            if match:
                where.append("w.id IN(SELECT entity_id FROM search_index WHERE search_index MATCH ? AND type='item')")
                args.append(match)
            clause = " AND ".join(where)
            total = c.execute(f"SELECT count(*) FROM work_items w WHERE {clause}", args).fetchone()[0]
            current = today(setting(c, "timezone", "America/Chicago"))
            # Ranked work comes first, in stack rank order; done and archived work follows it.
            rows = c.execute(f"SELECT w.*, r.stack_rank, (w.archived=0 AND w.status!='done' AND NOT EXISTS(SELECT 1 FROM daily_entries de WHERE de.item_id=w.id AND de.planned=1 AND de.date>=?)) AS is_backlog FROM work_items w LEFT JOIN ({RANKS}) r ON r.id=w.id WHERE {clause} ORDER BY r.stack_rank IS NULL,w.backlog_order,w.id LIMIT ? OFFSET ?", [current, *args, limit, offset]).fetchall()
            people_map = people_on(c, "item", [r["id"] for r in rows])
            repos = linked_repositories(c, "item", [r["id"] for r in rows])
            return {"items": [{**item_record(r), "rank": r["stack_rank"], "repositoryIds": repos.get(r["id"], []), "isBacklog": bool(r["is_backlog"]), "people": people_map.get(r["id"], [])} for r in rows], "total": total,
                    "rankedCount": ranked_count(c), "collectionRevision": setting(c, "backlogRevision", 1)}

    def roadmap_items(self, c: sqlite3.Connection, roadmap_id: str) -> builtins.list[dict]:
        rows = c.execute(
            "SELECT * FROM work_items WHERE archived=0 AND (roadmap_id=? OR step_id IN (SELECT id FROM steps WHERE roadmap_id=?)) ORDER BY backlog_order,id",
            (roadmap_id, roadmap_id),
        ).fetchall()
        people_map = people_on(c, "item", [r["id"] for r in rows])
        repos = linked_repositories(c, "item", [r["id"] for r in rows])
        return [
            {**item_record(r), "repositoryIds": repos.get(r["id"], []), "roadmapId": r["roadmap_id"] or roadmap_id, "people": people_map.get(r["id"], [])}
            for r in rows
        ]

    def detail(self, item_id: str) -> dict:
        with self.db.read() as c:
            result = item_record(get_item(c, item_id))
            result["rank"] = stack_ranks(c, [item_id]).get(item_id)
            result["rankedCount"] = ranked_count(c)
            result["repositoryIds"] = linked_repositories(c, "item", [item_id]).get(item_id, [])
            result.update(support.for_owner(c, "item", item_id))
            result["people"] = people_on(c, "item", [item_id]).get(item_id, [])
            result["dailyEntries"] = [entry_record(r) for r in c.execute("SELECT * FROM daily_entries WHERE item_id=? ORDER BY date DESC", (item_id,))]
            result["history"] = read_events(c, item_id)
            result["linkedRoadmap"] = None
            result["linkedStep"] = None
            if result["stepId"]:
                linked = c.execute("SELECT s.id,s.title,s.roadmap_id,s.retired,r.title AS roadmap_title,r.archived AS roadmap_archived FROM steps s JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id=?", (result["stepId"],)).fetchone()
                if linked:
                    result["roadmapId"] = linked["roadmap_id"]
                    result["linkedRoadmap"] = {"id": linked["roadmap_id"], "title": linked["roadmap_title"], "archived": bool(linked["roadmap_archived"])}
                    result["linkedStep"] = {"id": linked["id"], "title": linked["title"], "roadmapId": linked["roadmap_id"], "roadmapTitle": linked["roadmap_title"], "retired": bool(linked["retired"])}
            elif result["roadmapId"]:
                roadmap = c.execute("SELECT id,title,archived FROM roadmaps WHERE id=?", (result["roadmapId"],)).fetchone()
                if roadmap:
                    result["linkedRoadmap"] = {"id": roadmap["id"], "title": roadmap["title"], "archived": bool(roadmap["archived"])}
            return result

    def day(self, date: str) -> dict:
        valid_date(date)
        with self.db.read() as c:
            day = c.execute("SELECT * FROM days WHERE date=?", (date,)).fetchone()
            entries = []
            for r in c.execute("SELECT e.* FROM daily_entries e JOIN work_items w ON w.id=e.item_id WHERE e.date=? AND (w.archived=0 OR e.worked=1 OR e.outcome!='') ORDER BY e.position,e.id", (date,)):
                record = entry_record(r)
                record["item"] = item_record(get_item(c, r["item_id"]))
                entries.append(record)
            ids = [entry["itemId"] for entry in entries]
            repos = linked_repositories(c, "item", ids)
            ranked = stack_ranks(c, ids)
            counts = self.tab_counts(c, ids)
            states = self._states_as_of(c, date)
            for entry in entries:
                entry["item"]["repositoryIds"] = repos.get(entry["itemId"], [])
                entry["item"]["rank"] = ranked.get(entry["itemId"])
                entry["counts"] = counts[entry["itemId"]]
                # The status the item ended the day with, which groups the day's summary. Work
                # logged after its day has no history by then, so it shows its status now.
                entry["statusOnDay"] = states.get(entry["itemId"], entry["item"])["status"]
            # Completed titles are preserved from the actual completion event, not current item state.
            completed = []
            for r in c.execute("SELECT after_json FROM events WHERE local_date=? AND kind='item.update' ORDER BY created_at", (date,)):
                after = json.loads(r[0])
                if after.get("status") == "done" and after.get("_statusChanged"):
                    completed.append({"id": after["id"], "title": after["title"]})
            return {"date": date, "revision": day["revision"] if day else 0,
                    "reflection": day["reflection"] if day else "", "entries": entries,
                    "summary": {"worked": [{"id": e["itemId"], "title": e["item"]["title"], "outcome": e["outcome"]} for e in entries if e["worked"]],
                                "completed": completed,
                                "blocked": [{"id": k, "title": v["title"]} for k, v in states.items() if v["status"] == "blocked"]},
                    "nextItemId": next((e["itemId"] for e in entries if e["planned"] and not e["item"]["archived"] and e["item"]["status"] in {"open", "doing"}), None)}

    @staticmethod
    def tab_counts(c: sqlite3.Connection, ids: builtins.list[str]) -> dict[str, dict]:
        """What each item's tabs on Today hold, so they say so before the item is read: its active
        tasks and how many are done, its links and files, and whether its scratchpad has text."""
        counts = {i: {"tasks": 0, "tasksDone": 0, "links": 0, "files": 0, "scratchpad": False} for i in ids}
        if not ids:
            return counts
        marks = ",".join("?" for _ in ids)
        for owner, total, done in c.execute(f"SELECT item_id,count(*),total(done) FROM tasks WHERE archived=0 AND item_id IN ({marks}) GROUP BY item_id", ids):
            counts[owner].update(tasks=total, tasksDone=int(done))
        for owner, total in c.execute(f"SELECT owner_id,count(*) FROM web_links WHERE owner_type='item' AND archived=0 AND owner_id IN ({marks}) GROUP BY owner_id", ids):
            counts[owner]["links"] = total
        # The Files tab lists attached copies and local paths together.
        files = (f"SELECT owner_id AS owner FROM attachments WHERE owner_type='item' AND archived=0 AND owner_id IN ({marks}) "
                 f"UNION ALL SELECT item_id FROM file_references WHERE archived=0 AND item_id IN ({marks})")
        for owner, total in c.execute(f"SELECT owner,count(*) FROM ({files}) GROUP BY owner", [*ids, *ids]):
            counts[owner]["files"] = total
        # Blank counts as empty: whitespace alone, as str.strip() sees it (ASCII whitespace,
        # no-break spaces and Unicode line breaks), holds nothing to read.
        blank = "char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)"
        for (owner,) in c.execute(f"SELECT owner_id FROM notes WHERE owner_type='item' AND archived=0 AND trim(text,{blank})<>'' AND owner_id IN ({marks})", ids):
            counts[owner]["scratchpad"] = True
        return counts

    @staticmethod
    def _states_as_of(c: sqlite3.Connection, date: str) -> dict[str, dict]:
        """Each of the day's work items as its last change on or before that day left it, folded
        from its events, so later changes cannot alter an old day's summary. An item with no
        event by then is missing."""
        states: dict[str, dict] = {}
        for r in c.execute("""SELECT e.entity_id,e.after_json FROM events e
                            JOIN daily_entries d ON d.item_id=e.entity_id AND d.date=?
                            WHERE e.local_date<=? AND e.kind IN ('item.create','item.update')
                            ORDER BY e.created_at,e.id""", (date, date)):
            states[r["entity_id"]] = json.loads(r["after_json"])
        return states

    @staticmethod
    def ensure_day(c: sqlite3.Connection, date: str) -> None:
        valid_date(date)
        c.execute("INSERT OR IGNORE INTO days(date) VALUES(?)", (date,))

    @staticmethod
    def bump_backlog(c: sqlite3.Connection) -> None:
        value = setting(c, "backlogRevision", 1) + 1
        c.execute("UPDATE settings SET value=? WHERE key='backlogRevision'", (json.dumps(value),))

    def apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        creating = action == "item.create"
        row = None if creating else get_item(c, p.get("id", ""))
        if row is not None:
            require_revision(row, p.get("revision"))
        before = item_record(row) if row else None
        if before:
            before["repositoryIds"] = linked_repositories(c, "item", [before["id"]]).get(before["id"], [])
        val = before.copy() if before else {"id": uid(), "title": "", "description": "", "status": "open", "adoUrl": None, "roadmapId": None, "stepId": None, "archived": False,
                                            "backlogOrder": c.execute("SELECT coalesce(max(backlog_order),0)+1 FROM work_items").fetchone()[0],
                                            "revision": 0, "createdAt": now()}
        if action in {"item.archive", "item.recover"}:
            val["archived"] = action == "item.archive"
        elif action in {"item.create", "item.update"}:
            if "roadmapId" in p and "stepId" not in p and p["roadmapId"] != val["roadmapId"]:
                val["stepId"] = None
            for k in ("title", "description", "status", "adoUrl", "roadmapId", "stepId"):
                if k in p:
                    val[k] = p[k]
            val["title"] = text(val["title"], "title", required=True, limit=2000)
            val["description"] = text(val["description"], "description")
            val["status"] = item_status(val["status"])
            if val["adoUrl"]:
                link = text(val["adoUrl"], "adoUrl", limit=2048).strip()
                url = urlsplit(link)
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or re.search(r"[\x00-\x20]", link):
                    raise AppError(422, "validation", "ADO link must be an absolute HTTP(S) URL")
                val["adoUrl"] = link
            else:
                val["adoUrl"] = None
            if val["stepId"] is not None and not isinstance(val["stepId"], str):
                raise AppError(422, "validation", "Invalid stepId")
            if val["roadmapId"] is not None and not isinstance(val["roadmapId"], str):
                raise AppError(422, "validation", "Invalid roadmapId")
            if val["stepId"]:
                step = c.execute("SELECT id,roadmap_id FROM steps WHERE id=?", (val["stepId"],)).fetchone()
                if not step:
                    raise AppError(422, "validation", "Roadmap step does not exist")
                if p.get("roadmapId") and p["roadmapId"] != step["roadmap_id"]:
                    raise AppError(422, "validation", "Roadmap step does not belong to the selected roadmap")
                val["roadmapId"] = step["roadmap_id"]
            else:
                val["stepId"] = None
                if val["roadmapId"]:
                    roadmap = c.execute("SELECT id FROM roadmaps WHERE id=?", (val["roadmapId"],)).fetchone()
                    if not roadmap:
                        raise AppError(422, "validation", "Roadmap does not exist")
                else:
                    val["roadmapId"] = None
        else:
            raise AppError(422, "operation", "Unsupported work item operation")
        val["revision"] += 1
        val["updatedAt"] = now()
        c.execute("""INSERT INTO work_items(id,title,description,status,ado_url,step_id,backlog_order,archived,revision,created_at,updated_at,roadmap_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,description=excluded.description,status=excluded.status,ado_url=excluded.ado_url,step_id=excluded.step_id,archived=excluded.archived,revision=excluded.revision,updated_at=excluded.updated_at,roadmap_id=excluded.roadmap_id""",
                  (val["id"], val["title"], val["description"], val["status"], val["adoUrl"], val["stepId"], val["backlogOrder"], int(val["archived"]), val["revision"], val["createdAt"], val["updatedAt"], val["roadmapId"]))
        if "repositoryIds" in p:
            val["repositoryIds"] = replace_repository_associations(c, "item", val["id"], p["repositoryIds"])
        else:
            val.setdefault("repositoryIds", [])
        if action == "item.archive":
            release_item_participations(c, actor, val["id"])
        audit = {**val, "_statusChanged": bool(before and before["status"] != val["status"])}
        event(c, actor, action, val["id"], before, audit)
        search.put(c, val["id"], "item", val["title"], val["description"], val["id"], val["archived"])
        self.bump_backlog(c)
        if creating and p.get("day"):
            self.upsert_entry(c, actor, {"date": p["day"], "itemId": val["id"], "revision": 0,
                                        "planned": not p.get("worked", False), "worked": p.get("worked", False)})
        return val

    def upsert_entry(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        date, item_id = valid_date(p.get("date", "")), p.get("itemId", "")
        get_item(c, item_id, active=True)
        row = c.execute("SELECT * FROM daily_entries WHERE date=? AND item_id=?", (date, item_id)).fetchone()
        if row:
            require_revision(row, p.get("revision"))
        elif p.get("revision") != 0 or isinstance(p.get("revision"), bool):
            raise AppError(409, "conflict", "Entry does not exist; reload the day")
        before = entry_record(row) if row else None
        val = before.copy() if before else {"id": uid(), "date": date, "itemId": item_id, "planned": True, "worked": False, "outcome": "", "position": c.execute("SELECT coalesce(max(position),0)+1 FROM daily_entries WHERE date=?", (date,)).fetchone()[0], "revision": 0}
        for k in ("planned", "worked"):
            if k in p:
                val[k] = boolean(p[k], k)
        if "outcome" in p:
            val["outcome"] = text(p["outcome"], "outcome")
        val["revision"] += 1
        self.ensure_day(c, date)
        c.execute("""INSERT INTO daily_entries(id,item_id,date,planned,worked,outcome,position,revision) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(item_id,date) DO UPDATE SET planned=excluded.planned,worked=excluded.worked,outcome=excluded.outcome,revision=excluded.revision""",
                  (val["id"], item_id, date, int(val["planned"]), int(val["worked"]), val["outcome"], val["position"], val["revision"]))
        if row is None:
            # The day's revision guards which entries it holds and their order (K4): a new entry
            # changes them, an entry's own fields only its revision.
            c.execute("UPDATE days SET revision=revision+1 WHERE date=?", (date,))
        self.bump_backlog(c)
        event(c, actor, "entry.upsert", item_id, before, val)
        return val

    @staticmethod
    def _day_entry(c: sqlite3.Connection, date: str, item_id: str, revision: Any) -> Any:
        """An active work item's entry on a day, as of the revision the caller read it at.
        An archived item's entries are read-only history."""
        valid_date(date)
        get_item(c, item_id, active=True)
        row = c.execute("SELECT * FROM daily_entries WHERE date=? AND item_id=?", (date, item_id)).fetchone()
        if row is None:
            raise AppError(409, "conflict", "Entry does not exist; reload the day")
        require_revision(row, revision)
        return row

    def _drop_entry(self, c: sqlite3.Connection, actor: dict, row: Any) -> None:
        """Delete a day's entry, so neither the day nor the item's daily record lists it;
        history keeps the entry as it was."""
        c.execute("DELETE FROM daily_entries WHERE id=?", (row["id"],))
        c.execute("UPDATE days SET revision=revision+1 WHERE date=?", (row["date"],))
        # Planned work leaves the backlog; taken off its day, it may rejoin it.
        self.bump_backlog(c)
        event(c, actor, "entry.remove", row["item_id"], entry_record(row), None)

    def remove_entry(self, c: sqlite3.Connection, actor: dict, date: str, item_id: str, p: dict) -> dict:
        """Take a work item off a day: work planned but not reached, or put there by mistake.

        The entry goes with what the day recorded for it (planned, worked on, its outcome); Today
        asks the person first when that record holds work. The item and its other days stay as
        they are.
        """
        self._drop_entry(c, actor, self._day_entry(c, date, item_id, p.get("revision")))
        return {"ok": True}

    def reflection(self, c: sqlite3.Connection, actor: dict, date: str, p: dict) -> dict:
        valid_date(date)
        row = c.execute("SELECT * FROM days WHERE date=?", (date,)).fetchone()
        require_revision(row or {"revision": 0}, p.get("revision"))
        value = text(p.get("text", ""), "reflection")
        self.ensure_day(c, date)
        c.execute("UPDATE days SET reflection=?,revision=revision+1 WHERE date=?", (value, date))
        event(c, actor, "day.reflection", date, row["reflection"] if row else "", value)
        return {"date": date, "reflection": value, "revision": c.execute("SELECT revision FROM days WHERE date=?", (date,)).fetchone()[0]}

    def reorder(self, c: sqlite3.Connection, actor: dict, p: dict, date: str | None = None) -> dict:
        if date:
            row = c.execute("SELECT * FROM days WHERE date=?", (valid_date(date),)).fetchone()
            require_revision(row or {"revision": 0}, p.get("revision"))
            actual = [r[0] for r in c.execute("SELECT e.id FROM daily_entries e JOIN work_items w ON e.item_id=w.id WHERE e.date=? AND (w.archived=0 OR e.worked=1 OR e.outcome!='') ORDER BY e.position", (date,))]
        else:
            require_revision({"revision": setting(c, "backlogRevision", 1)}, p.get("revision"))
            day = today(setting(c, "timezone", "America/Chicago"))
            actual = [r[0] for r in c.execute("SELECT id FROM work_items w WHERE archived=0 AND status!='done' AND NOT EXISTS(SELECT 1 FROM daily_entries e WHERE e.item_id=w.id AND e.planned=1 AND e.date>=?) ORDER BY backlog_order", (day,))]
        ids = p.get("ids")
        if not isinstance(ids, list) or len(ids) != len(set(ids)) or set(ids) != set(actual):
            raise AppError(409, "conflict", "Order must include exactly the current records")
        # Order is the day's or the backlog's, guarded by their revisions: the entries and work items
        # moved keep their own (K4), so an edit open on any of them still saves.
        if date:
            for pos, identity in enumerate(ids):
                c.execute("UPDATE daily_entries SET position=? WHERE id=?", (pos, identity))
            c.execute("UPDATE days SET revision=revision+1 WHERE date=?", (date,))
        else:
            # The backlog trades the positions it already holds, so planned work keeps its stack
            # rank and done work its place.
            renumber(c, backlog_sequence(c))
            held = dict(c.execute("SELECT id,backlog_order FROM work_items").fetchall())
            for slot, identity in zip(sorted(held[i] for i in actual), ids, strict=True):
                c.execute("UPDATE work_items SET backlog_order=? WHERE id=?", (slot, identity))
        self.bump_backlog(c)
        event(c, actor, "day.reorder" if date else "backlog.reorder", date or "backlog", actual, ids)
        return {"ok": True}

    def reschedule(self, c: sqlite3.Connection, actor: dict, date: str, p: dict) -> dict:
        """Move a work item's plan from this day to another.

        What this day recorded on the item, "Worked on" or an outcome, stays here, no longer
        planned. An entry that only planned the item goes, so this day doesn't list work it never
        got. The target day plans the item, in the entry it already has or a new one.
        """
        target = valid_date(p.get("targetDate", ""))
        if target == date:
            raise AppError(422, "validation", "Choose a different date")
        item_id = p.get("itemId", "")
        row = self._day_entry(c, date, item_id, p.get("revision"))
        if row["worked"] or row["outcome"].strip():
            self.upsert_entry(c, actor, {"date": date, "itemId": item_id, "revision": row["revision"], "planned": False})
        else:
            self._drop_entry(c, actor, row)
        return self.upsert_entry(c, actor, {"date": target, "itemId": item_id, "revision": p.get("targetRevision", 0), "planned": True})

    def move_backlog(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        require_revision({"revision": setting(c, "backlogRevision", 1)}, p.get("revision"))
        current = today(setting(c, "timezone", "America/Chicago"))
        rows = c.execute("""SELECT id,backlog_order FROM work_items w WHERE archived=0 AND status!='done'
                            AND NOT EXISTS(SELECT 1 FROM daily_entries e WHERE e.item_id=w.id AND e.planned=1 AND e.date>=?)
                            ORDER BY backlog_order,id""", (current,)).fetchall()
        ids = [r["id"] for r in rows]
        if identity not in ids:
            raise AppError(409, "conflict", "This item is no longer in the backlog")
        index = ids.index(identity)
        target = index + (-1 if p["direction"] == "up" else 1)
        if not 0 <= target < len(rows):
            return {"ok": True}
        neighbor = rows[target]["id"]
        # Older orderings left tied positions, where swapping two items' values would let a
        # third item tied with either fall on the other side of it. Every item gets its own
        # position first (nothing is written once they have), so only the two trade places.
        renumber(c, backlog_sequence(c))
        held = dict(c.execute("SELECT id,backlog_order FROM work_items WHERE id IN (?,?)", (identity, neighbor)).fetchall())
        positions = (held[neighbor], held[identity])
        # Checked against the backlog's revision, the move leaves both items theirs (K4).
        c.execute("UPDATE work_items SET backlog_order=? WHERE id=?", (positions[0], identity))
        c.execute("UPDATE work_items SET backlog_order=? WHERE id=?", (positions[1], neighbor))
        self.bump_backlog(c)
        event(c, actor, "backlog.move", "backlog", {"id": identity, "position": index}, {"id": identity, "position": target})
        return {"ok": True}

    def rank(self, c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
        """Move unfinished, unarchived work to a stack rank; a rank past the last moves it last.

        The move is checked against the item's own revision, not the collection's, which every
        change to any work or day bumps: a stale view can only place the item at the rank it
        showed. Moving changes no item's revision, so a draft open on any of them still saves.
        """
        wanted = p.get("rank")
        if not isinstance(wanted, int) or isinstance(wanted, bool) or wanted < 1:
            raise AppError(422, "validation", "rank must be a whole number from 1")
        row = get_item(c, identity, active=True)
        require_revision(row, p.get("revision"))
        if row["status"] == "done":
            raise AppError(409, "conflict", "Done work has no stack rank; reopen it to rank it")
        ranked = [r[0] for r in c.execute(f"SELECT id FROM work_items WHERE {RANKED} ORDER BY backlog_order,id")]
        before, after = ranked.index(identity) + 1, min(wanted, len(ranked))
        if after != before:
            others = [i for i in ranked if i != identity]
            order = [i for i in backlog_sequence(c) if i != identity]
            # Taken out, the item goes back in just before the one that then holds its new rank,
            # or just after the last. Done and archived work keeps its place among the rest.
            order.insert(order.index(others[after - 1]) if after <= len(others) else order.index(others[-1]) + 1, identity)
            renumber(c, order)
            self.bump_backlog(c)
            event(c, actor, "item.rank", identity, {"rank": before}, {"rank": after})
        return {"id": identity, "rank": after, "rankedCount": len(ranked)}
