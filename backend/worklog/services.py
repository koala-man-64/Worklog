"""Work and supporting-record application services, independent of HTTP."""

from __future__ import annotations

import builtins
import hashlib
import json
import re
import sqlite3
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from .common import (
    FILE_LIMIT,
    AppError,
    event,
    now,
    read_events,
    require_revision,
    setting,
    status,
    text,
    today,
    uid,
    valid_date,
)
from .db import Database


def item_record(r: Any) -> dict:
    return {"id": r["id"], "title": r["title"], "description": r["description"],
            "status": r["status"], "adoUrl": r["ado_url"],
            "roadmapId": r["roadmap_id"] if "roadmap_id" in r.keys() else None,
            "stepId": r["step_id"],
            "backlogOrder": r["backlog_order"], "archived": bool(r["archived"]),
            "revision": r["revision"], "createdAt": r["created_at"], "updatedAt": r["updated_at"]}


def entry_record(r: Any) -> dict:
    return {"id": r["id"], "itemId": r["item_id"], "date": r["date"],
            "planned": bool(r["planned"]), "worked": bool(r["worked"]),
            "outcome": r["outcome"], "position": r["position"], "revision": r["revision"]}


def support_record(r: Any) -> dict:
    keys = {"owner_type": "ownerType", "owner_id": "ownerId", "item_id": "itemId",
            "chat_id": "chatId", "created_date": "createdDate", "chat_title": "chatTitle",
            "created_at": "createdAt", "updated_at": "updatedAt"}
    return {keys.get(k, k): bool(v) if k in {"archived", "done"} else v
            for k, v in dict(r).items() if k != "content"}


def valid_http_url(raw: Any, field: str = "url") -> str:
    link = text(raw, field, required=True, limit=2048).strip()
    url = urlsplit(link)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or re.search(r"[\x00-\x20]", link):
        raise AppError(422, "validation", f"{field} must be an absolute HTTP(S) URL")
    return link


def infer_github_reference(kind: str, link: str) -> str:
    path = urlsplit(link).path.strip("/")
    pr_match = re.search(r"(?:^|/)([^/]+/[^/]+)/pull/(\d+)(?:/|$)", path)
    if pr_match:
        return f"{pr_match.group(1)}#{pr_match.group(2)}"
    commit_match = re.search(r"(?:^|/)(?:([^/]+/[^/]+)/)?commit/([0-9a-fA-F]{7,40})(?:/|$)", path)
    if commit_match:
        repo, sha = commit_match.group(1), commit_match.group(2)[:7]
        return f"{repo}@{sha}" if repo else sha
    return ""


def person_record(r: Any) -> dict:
    keys = set(r.keys())
    res = {"id": r["id"], "name": r["name"], "role": r["role"], "notes": r["notes"],
           "archived": bool(r["archived"]), "revision": r["revision"],
           "createdAt": r["created_at"], "updatedAt": r["updated_at"]}
    if "work_item_count" in keys:
        res["workItemCount"] = r["work_item_count"]
    if "roadmap_count" in keys:
        res["roadmapCount"] = r["roadmap_count"]
    return res


def person_assignment_record(r: Any) -> dict:
    return {"id": r["id"], "personId": r["person_id"], "name": r["name"],
            "personRole": r["person_role"], "relationship": r["relationship"],
            "archived": bool(r["archived"]), "createdAt": r["created_at"]}


def boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise AppError(422, "validation", f"{field} must be a boolean")
    return value


class WorkService:
    def __init__(self, db: Database, plans: Any, proposals: Any = None):
        self.db, self.plans, self.proposals = db, plans, proposals

    def owner(self, conn: sqlite3.Connection, owner_type: str, owner_id: str,
              actor: dict | None = None) -> None:
        table = {"item": "work_items", "roadmap": "roadmaps", "step": "steps", "proposal": "proposals"}.get(owner_type)
        if not table:
            raise AppError(422, "validation", "Invalid supporting-record owner")
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (owner_id,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Owner does not exist")
        if owner_type == "proposal" and actor is not None:
            self.proposals.check_upload(conn, owner_id, actor)
        elif actor is not None and actor["kind"] == "agent":
            # Agents can reach canonical supporting records only through approval dispatch.
            pass

    def item(self, conn: sqlite3.Connection, item_id: str, *, active: bool = False) -> Any:
        row = conn.execute("SELECT * FROM work_items WHERE id=?", (item_id,)).fetchone()
        if row is None or (active and row["archived"]):
            raise AppError(404, "not_found", "Work item does not exist or is archived")
        return row

    def person(self, conn: sqlite3.Connection, person_id: str, *, active: bool = False) -> Any:
        row = conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
        if row is None or (active and row["archived"]):
            raise AppError(404, "not_found", "Person does not exist or is archived")
        return row

    def target_people(self, c: sqlite3.Connection, target_type: str, target_ids: builtins.list[str]) -> dict[str, builtins.list[dict]]:
        if not target_ids:
            return {}
        placeholders = ",".join("?" for _ in target_ids)
        rows = c.execute(
            f"""SELECT pl.id, pl.person_id, pl.target_id, pl.relationship, pl.created_at,
                       p.name, p.role AS person_role, p.archived
                FROM person_links pl
                JOIN people p ON p.id = pl.person_id
                WHERE pl.target_type = ? AND pl.target_id IN ({placeholders})
                ORDER BY p.name COLLATE NOCASE, pl.created_at, pl.id""",
            [target_type, *target_ids],
        ).fetchall()
        grouped: dict[str, builtins.list[dict]] = {tid: [] for tid in target_ids}
        for r in rows:
            grouped.setdefault(r["target_id"], []).append(person_assignment_record(r))
        return grouped

    def list(self, *, view: str = "all", archived: bool = False, state: str | None = None,
             roadmap_id: str | None = None, person_id: str | None = None,
             limit: int = 100, offset: int = 0, q: str = "") -> dict:
        with self.db.read() as c:
            where = ["w.archived=?"]
            args: builtins.list[Any] = [int(archived)]
            if state:
                where.append("w.status=?")
                args.append(status(state))
            if roadmap_id:
                where.append("(w.roadmap_id=? OR w.step_id IN(SELECT id FROM steps WHERE roadmap_id=?))")
                args.extend([roadmap_id, roadmap_id])
            if person_id:
                where.append("w.id IN(SELECT target_id FROM person_links WHERE target_type='item' AND person_id=?)")
                args.append(person_id)
            if view == "backlog":
                where.extend(["w.status!='done'", "NOT EXISTS(SELECT 1 FROM daily_entries e WHERE e.item_id=w.id AND e.planned=1 AND e.date>=?)"])
                args.append(today(setting(c, "timezone", "America/Chicago")))
            elif view != "all":
                raise AppError(422, "validation", "Unknown work view")
            terms = re.findall(r"\w+", q, re.UNICODE)
            if terms:
                where.append("w.id IN(SELECT entity_id FROM search_index WHERE search_index MATCH ? AND type='item')")
                args.append(" AND ".join('"' + word + '"*' for word in terms[:30]))
            clause = " AND ".join(where)
            total = c.execute(f"SELECT count(*) FROM work_items w WHERE {clause}", args).fetchone()[0]
            current = today(setting(c, "timezone", "America/Chicago"))
            rows = c.execute(f"SELECT w.*, (w.archived=0 AND w.status!='done' AND NOT EXISTS(SELECT 1 FROM daily_entries de WHERE de.item_id=w.id AND de.planned=1 AND de.date>=?)) AS is_backlog FROM work_items w WHERE {clause} ORDER BY backlog_order,id LIMIT ? OFFSET ?", [current, *args, limit, offset]).fetchall()
            people_map = self.target_people(c, "item", [r["id"] for r in rows])
            return {"items": [{**item_record(r), "isBacklog": bool(r["is_backlog"]), "people": people_map.get(r["id"], [])} for r in rows], "total": total,
                    "collectionRevision": setting(c, "backlogRevision", 1)}

    def roadmap_items(self, c: sqlite3.Connection, roadmap_id: str) -> builtins.list[dict]:
        rows = c.execute(
            "SELECT * FROM work_items WHERE archived=0 AND (roadmap_id=? OR step_id IN (SELECT id FROM steps WHERE roadmap_id=?)) ORDER BY backlog_order,id",
            (roadmap_id, roadmap_id),
        ).fetchall()
        people_map = self.target_people(c, "item", [r["id"] for r in rows])
        return [
            {**item_record(r), "roadmapId": r["roadmap_id"] or roadmap_id, "people": people_map.get(r["id"], [])}
            for r in rows
        ]

    def detail(self, item_id: str) -> dict:
        with self.db.read() as c:
            result = item_record(self.item(c, item_id))
            result.update(self.support(c, "item", item_id))
            result["people"] = self.target_people(c, "item", [item_id]).get(item_id, [])
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

    def people_list(self, *, archived: bool = False, q: str = "") -> dict:
        with self.db.read() as c:
            where = ["p.archived=?"]
            args: builtins.list[Any] = [int(archived)]
            terms = re.findall(r"\w+", q, re.UNICODE)
            if terms:
                where.append("p.id IN(SELECT entity_id FROM search_index WHERE search_index MATCH ? AND type='person')")
                args.append(" AND ".join('"' + word + '"*' for word in terms[:30]))
            clause = " AND ".join(where)
            rows = c.execute(
                f"""SELECT p.*,
                    (SELECT count(*) FROM person_links pl JOIN work_items w ON w.id=pl.target_id WHERE pl.person_id=p.id AND pl.target_type='item' AND w.archived=0) AS work_item_count,
                    (SELECT count(*) FROM person_links pl JOIN roadmaps r ON r.id=pl.target_id WHERE pl.person_id=p.id AND pl.target_type='roadmap' AND r.archived=0) AS roadmap_count
                    FROM people p WHERE {clause} ORDER BY p.name COLLATE NOCASE, p.id""",
                args,
            ).fetchall()
            items = [person_record(r) for r in rows]
            return {"items": items, "total": len(items)}

    def person_detail(self, person_id: str) -> dict:
        with self.db.read() as c:
            result = person_record(self.person(c, person_id))
            roadmaps = []
            for r in c.execute(
                """SELECT pl.id AS link_id, pl.person_id, pl.target_id AS roadmap_id, pl.relationship, pl.created_at AS link_created_at,
                          rm.id, rm.title, rm.overview, rm.archived, rm.revision, rm.created_at, rm.updated_at
                   FROM person_links pl
                   JOIN roadmaps rm ON rm.id = pl.target_id
                   WHERE pl.person_id = ? AND pl.target_type = 'roadmap'
                   ORDER BY rm.archived, rm.created_at, rm.id""",
                (person_id,),
            ):
                roadmaps.append({
                    "linkId": r["link_id"],
                    "personId": r["person_id"],
                    "roadmapId": r["roadmap_id"],
                    "relationship": r["relationship"],
                    "createdAt": r["link_created_at"],
                    "roadmap": {
                        "id": r["id"],
                        "title": r["title"],
                        "overview": r["overview"],
                        "archived": bool(r["archived"]),
                        "revision": r["revision"],
                        "createdAt": r["created_at"],
                        "updatedAt": r["updated_at"],
                    },
                })
            work_items = []
            for r in c.execute(
                """SELECT pl.id AS link_id, pl.person_id, pl.target_id AS item_id, pl.relationship, pl.created_at AS link_created_at,
                          w.*
                   FROM person_links pl
                   JOIN work_items w ON w.id = pl.target_id
                   WHERE pl.person_id = ? AND pl.target_type = 'item'
                   ORDER BY w.archived, w.backlog_order, w.id""",
                (person_id,),
            ):
                work_items.append({
                    "linkId": r["link_id"],
                    "personId": r["person_id"],
                    "itemId": r["item_id"],
                    "relationship": r["relationship"],
                    "createdAt": r["link_created_at"],
                    "item": item_record(r),
                })
            result["roadmaps"] = roadmaps
            result["workItems"] = work_items
            result["history"] = read_events(c, person_id)
            return result

    def support(self, c: sqlite3.Connection, owner_type: str, owner_id: str) -> dict:
        result = {"notes": [support_record(r) for r in c.execute("SELECT * FROM notes WHERE owner_type=? AND owner_id=? ORDER BY created_at", (owner_type, owner_id))],
                  "attachments": [support_record(r) for r in c.execute("SELECT id,owner_type,owner_id,name,mime,size,sha256,archived,revision,created_at FROM attachments WHERE owner_type=? AND owner_id=? ORDER BY created_at", (owner_type, owner_id))]}
        if owner_type in {"item", "roadmap"}:
            result["links"] = [support_record(r) for r in c.execute("SELECT * FROM web_links WHERE owner_type=? AND owner_id=? ORDER BY created_at,id", (owner_type, owner_id))]
            result["chats"] = [support_record(r) for r in c.execute("SELECT * FROM agent_chats WHERE owner_type=? AND owner_id=? ORDER BY created_date DESC,created_at DESC,id", (owner_type, owner_id))]
            result["github"] = [
                support_record(r)
                for r in c.execute(
                    """SELECT g.*, ac.title AS chat_title
                       FROM github_refs g
                       LEFT JOIN agent_chats ac ON ac.id = g.chat_id
                       WHERE g.owner_type=? AND g.owner_id=?
                       ORDER BY g.created_at, g.id""",
                    (owner_type, owner_id),
                )
            ]
        if owner_type == "item":
            result["tasks"] = [support_record(r) for r in c.execute("SELECT * FROM tasks WHERE item_id=? ORDER BY created_at", (owner_id,))]
            result["references"] = [support_record(r) for r in c.execute("SELECT * FROM file_references WHERE item_id=? ORDER BY created_at", (owner_id,))]
        return result

    def day(self, date: str) -> dict:
        valid_date(date)
        with self.db.read() as c:
            day = c.execute("SELECT * FROM days WHERE date=?", (date,)).fetchone()
            entries = []
            for r in c.execute("SELECT e.* FROM daily_entries e JOIN work_items w ON w.id=e.item_id WHERE e.date=? AND (w.archived=0 OR e.worked=1 OR e.outcome!='') ORDER BY e.position,e.id", (date,)):
                record = entry_record(r)
                record["item"] = item_record(self.item(c, r["item_id"]))
                entries.append(record)
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
                                "blocked": self._blocked_as_of(c, date)},
                    "nextItemId": next((e["itemId"] for e in entries if e["planned"] and not e["item"]["archived"] and e["item"]["status"] in {"open", "doing"}), None)}

    @staticmethod
    def _blocked_as_of(c: sqlite3.Connection, date: str) -> builtins.list[dict]:
        # Fold status events up to the selected day; later changes cannot alter old summaries.
        states: dict[str, dict] = {}
        for r in c.execute("""SELECT e.entity_id,e.after_json FROM events e
                            JOIN daily_entries d ON d.item_id=e.entity_id AND d.date=?
                            WHERE e.local_date<=? AND e.kind IN ('item.create','item.update')
                            ORDER BY e.created_at,e.id""", (date, date)):
            value = json.loads(r["after_json"])
            states[r["entity_id"]] = value
        planned = {r[0] for r in c.execute("SELECT item_id FROM daily_entries WHERE date=?", (date,))}
        return [{"id": k, "title": v["title"]} for k, v in states.items() if k in planned and v["status"] == "blocked"]

    @staticmethod
    def ensure_day(c: sqlite3.Connection, date: str) -> None:
        valid_date(date)
        c.execute("INSERT OR IGNORE INTO days(date) VALUES(?)", (date,))

    @staticmethod
    def bump_backlog(c: sqlite3.Connection) -> None:
        value = setting(c, "backlogRevision", 1) + 1
        c.execute("UPDATE settings SET value=? WHERE key='backlogRevision'", (json.dumps(value),))

    def apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        if action.startswith("item."):
            return self._item_apply(c, actor, action, p)
        if action == "entry.upsert":
            return self._entry_apply(c, actor, p)
        if action.startswith(("note.", "task.", "reference.", "attachment.", "link.", "chat.", "github.")):
            return self._support_apply(c, actor, action, p)
        if action.startswith(("roadmap.", "progress.")):
            result = self.plans.apply(c, actor, action, p)
            if not action.startswith("progress."):
                self.refresh_plans(c, result["id"])
            return result
        if action.startswith("person."):
            return self._person_apply(c, actor, action, p)
        raise AppError(422, "operation", "Unsupported operation")

    def _person_apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        if action == "person.link":
            person_id = text(p.get("personId", ""), "personId", required=True, limit=200)
            target_type = p.get("targetType", "")
            if target_type not in {"item", "roadmap"}:
                raise AppError(422, "validation", "Invalid targetType for person link")
            target_id = text(p.get("targetId", ""), "targetId", required=True, limit=200)
            relationship = text(p.get("relationship", ""), "relationship", limit=200).strip()
            person_row = self.person(c, person_id, active=True)
            self.owner(c, target_type, target_id)
            existing = c.execute(
                "SELECT * FROM person_links WHERE person_id=? AND target_type=? AND target_id=?",
                (person_id, target_type, target_id),
            ).fetchone()
            before = (
                {"id": existing["id"], "personId": person_id, "targetType": target_type,
                 "targetId": target_id, "relationship": existing["relationship"], "createdAt": existing["created_at"]}
                if existing
                else None
            )
            link_id = existing["id"] if existing else uid()
            created_at = existing["created_at"] if existing else now()
            c.execute(
                "INSERT INTO person_links VALUES(?,?,?,?,?,?) ON CONFLICT(person_id,target_type,target_id) DO UPDATE SET relationship=excluded.relationship",
                (link_id, person_id, target_type, target_id, relationship, created_at),
            )
            after = {
                "id": link_id,
                "personId": person_id,
                "name": person_row["name"],
                "personRole": person_row["role"],
                "targetType": target_type,
                "targetId": target_id,
                "relationship": relationship,
                "archived": bool(person_row["archived"]),
                "createdAt": created_at,
            }
            event(c, actor, "person.link", person_id, before, after)
            event(c, actor, "person.link", target_id, before, after)
            return after
        if action == "person.unlink":
            person_id = text(p.get("personId", ""), "personId", required=True, limit=200)
            target_type = p.get("targetType", "")
            if target_type not in {"item", "roadmap"}:
                raise AppError(422, "validation", "Invalid targetType for person link")
            target_id = text(p.get("targetId", ""), "targetId", required=True, limit=200)
            existing = c.execute(
                "SELECT * FROM person_links WHERE person_id=? AND target_type=? AND target_id=?",
                (person_id, target_type, target_id),
            ).fetchone()
            if existing is None:
                raise AppError(404, "not_found", "Person link does not exist")
            before = {
                "id": existing["id"],
                "personId": person_id,
                "targetType": target_type,
                "targetId": target_id,
                "relationship": existing["relationship"],
                "createdAt": existing["created_at"],
            }
            c.execute("DELETE FROM person_links WHERE id=?", (existing["id"],))
            event(c, actor, "person.unlink", person_id, before, None)
            event(c, actor, "person.unlink", target_id, before, None)
            return {"ok": True, "id": existing["id"], "personId": person_id, "targetType": target_type, "targetId": target_id}
        creating = action == "person.create"
        row = None if creating else self.person(c, p.get("id", ""))
        if row is not None:
            require_revision(row, p.get("revision"))
        before = person_record(row) if row else None
        val: dict[str, Any] = (
            before.copy()
            if before
            else {
                "id": uid(),
                "name": "",
                "role": "",
                "notes": "",
                "archived": False,
                "revision": 0,
                "createdAt": now(),
            }
        )
        if action in {"person.archive", "person.recover"}:
            val["archived"] = action == "person.archive"
        elif action in {"person.create", "person.update"}:
            for k in ("name", "role", "notes"):
                if k in p and p[k] is not None:
                    val[k] = p[k]
            val["name"] = text(val["name"], "name", required=True, limit=500)
            val["role"] = text(val["role"], "role", limit=500).strip()
            val["notes"] = text(val["notes"], "notes")
        else:
            raise AppError(422, "operation", "Unsupported person operation")
        val["revision"] += 1
        val["updatedAt"] = now()
        c.execute(
            """INSERT INTO people VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,role=excluded.role,notes=excluded.notes,archived=excluded.archived,revision=excluded.revision,updated_at=excluded.updated_at""",
            (val["id"], val["name"], val["role"], val["notes"], int(val["archived"]), val["revision"], val["createdAt"], val["updatedAt"]),
        )
        event(c, actor, action, val["id"], before, val)
        self.index(c, val["id"], "person", val["name"], (val["role"] + "\n" + val["notes"]).strip(), val["id"], val["archived"])
        return val

    def _item_apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        creating = action == "item.create"
        row = None if creating else self.item(c, p.get("id", ""))
        if row is not None:
            require_revision(row, p.get("revision"))
        before = item_record(row) if row else None
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
            val["status"] = status(val["status"])
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
        c.execute("""INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,description=excluded.description,status=excluded.status,ado_url=excluded.ado_url,step_id=excluded.step_id,archived=excluded.archived,revision=excluded.revision,updated_at=excluded.updated_at,roadmap_id=excluded.roadmap_id""",
                  (val["id"], val["title"], val["description"], val["status"], val["adoUrl"], val["stepId"], val["backlogOrder"], int(val["archived"]), val["revision"], val["createdAt"], val["updatedAt"], val["roadmapId"]))
        audit = {**val, "_statusChanged": bool(before and before["status"] != val["status"])}
        event(c, actor, action, val["id"], before, audit)
        self.index(c, val["id"], "item", val["title"], val["description"], val["id"], val["archived"])
        self.bump_backlog(c)
        if creating and p.get("day"):
            self._entry_apply(c, actor, {"date": p["day"], "itemId": val["id"], "revision": 0,
                                        "planned": not p.get("worked", False), "worked": p.get("worked", False)})
        return val

    def _entry_apply(self, c: sqlite3.Connection, actor: dict, p: dict) -> dict:
        date, item_id = valid_date(p.get("date", "")), p.get("itemId", "")
        self.item(c, item_id, active=True)
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
        c.execute("""INSERT INTO daily_entries VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(item_id,date) DO UPDATE SET planned=excluded.planned,worked=excluded.worked,outcome=excluded.outcome,revision=excluded.revision""",
                  (val["id"], item_id, date, int(val["planned"]), int(val["worked"]), val["outcome"], val["position"], val["revision"]))
        c.execute("UPDATE days SET revision=revision+1 WHERE date=?", (date,))
        self.bump_backlog(c)
        event(c, actor, "entry.upsert", item_id, before, val)
        return val

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
        for pos, identity in enumerate(ids):
            if date:
                c.execute("UPDATE daily_entries SET position=?,revision=revision+1 WHERE id=?", (pos, identity))
            else:
                c.execute("UPDATE work_items SET backlog_order=?,revision=revision+1 WHERE id=?", (pos, identity))
        if date:
            c.execute("UPDATE days SET revision=revision+1 WHERE date=?", (date,))
        self.bump_backlog(c)
        event(c, actor, "day.reorder" if date else "backlog.reorder", date or "backlog", actual, ids)
        return {"ok": True}

    def reschedule(self, c: sqlite3.Connection, actor: dict, date: str, p: dict) -> dict:
        target = valid_date(p.get("targetDate", ""))
        if target == date:
            raise AppError(422, "validation", "Choose a different date")
        self._entry_apply(c, actor, {"date": date, "itemId": p.get("itemId"), "revision": p.get("revision"), "planned": False})
        return self._entry_apply(c, actor, {"date": target, "itemId": p.get("itemId"), "revision": p.get("targetRevision", 0), "planned": True})

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
        source, neighbor = rows[index], rows[target]
        if source["backlog_order"] == neighbor["backlog_order"]:
            # A previously scheduled item can rejoin with a tied position. Normalize
            # only lightweight order columns before the two-row swap.
            for position, row in enumerate(rows):
                c.execute("UPDATE work_items SET backlog_order=? WHERE id=?", (position, row["id"]))
            positions = (target, index)
        else:
            positions = (neighbor["backlog_order"], source["backlog_order"])
        c.execute("UPDATE work_items SET backlog_order=?,revision=revision+1 WHERE id=?", (positions[0], identity))
        c.execute("UPDATE work_items SET backlog_order=?,revision=revision+1 WHERE id=?", (positions[1], neighbor["id"]))
        self.bump_backlog(c)
        event(c, actor, "backlog.move", "backlog", {"id": identity, "position": index}, {"id": identity, "position": target})
        return {"ok": True}

    def _support_apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        kind, verb = action.split(".", 1)
        table = {
            "note": "notes",
            "task": "tasks",
            "reference": "file_references",
            "attachment": "attachments",
            "link": "web_links",
            "chat": "agent_chats",
            "github": "github_refs",
        }[kind]
        creating = verb == "create"
        row = None if creating else c.execute(f"SELECT * FROM {table} WHERE id=?", (p.get("id", ""),)).fetchone()
        if not creating:
            require_revision(row, p.get("revision"))
        before = support_record(row) if row else None
        val: dict[str, Any]
        if verb in {"archive", "recover"}:
            assert row is not None
            c.execute(f"UPDATE {table} SET archived=?,revision=revision+1 WHERE id=?", (int(verb == "archive"), row["id"]))
        elif kind == "note" and verb in {"create", "update"}:
            val = before.copy() if before else {"id": uid(), "ownerType": p.get("ownerType"), "ownerId": p.get("ownerId"), "title": "", "text": "", "createdAt": now(), "revision": 0}
            self.owner(c, val["ownerType"], val["ownerId"])
            if val["ownerType"] == "proposal":
                raise AppError(422, "validation", "Use proposal rationale for proposed notes")
            for k in ("title", "text"):
                if k in p:
                    val[k] = text(p[k], k, limit=2000 if k == "title" else 1_000_000)
            if not val["title"].strip() and not val["text"].strip():
                raise AppError(422, "validation", "A note needs a title or text")
            c.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,text=excluded.text,updated_at=excluded.updated_at,revision=excluded.revision",
                      (val["id"], val["ownerType"], val["ownerId"], val["title"], val["text"], 0, val["revision"] + 1, val["createdAt"], now()))
            p = {**p, "id": val["id"]}
        elif kind == "link" and verb in {"create", "update"}:
            val = before.copy() if before else {"id": uid(), "ownerType": p.get("ownerType"), "ownerId": p.get("ownerId"), "url": "", "title": "", "description": "", "createdAt": now(), "revision": 0}
            if val["ownerType"] not in {"item", "roadmap"}:
                raise AppError(422, "validation", "Links must belong to a work item or roadmap")
            self.owner(c, val["ownerType"], val["ownerId"])
            for k in ("url", "title", "description"):
                if k in p and p[k] is not None:
                    val[k] = p[k]
            val["url"] = valid_http_url(val["url"], "url")
            val["title"] = text(val["title"], "title", limit=1000).strip()
            val["description"] = text(val["description"], "description", limit=100_000).strip()
            c.execute(
                "INSERT INTO web_links VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET url=excluded.url,title=excluded.title,description=excluded.description,revision=excluded.revision,updated_at=excluded.updated_at",
                (val["id"], val["ownerType"], val["ownerId"], val["url"], val["title"], val["description"], 0, val["revision"] + 1, val["createdAt"], now()),
            )
            p = {**p, "id": val["id"]}
        elif kind == "chat" and verb in {"create", "update"}:
            default_date = today(setting(c, "timezone", "America/Chicago"))
            val = before.copy() if before else {"id": uid(), "ownerType": p.get("ownerType"), "ownerId": p.get("ownerId"), "chatId": "", "title": "", "createdDate": default_date, "createdAt": now(), "revision": 0}
            if val["ownerType"] not in {"item", "roadmap"}:
                raise AppError(422, "validation", "Agent chats must belong to a work item or roadmap")
            self.owner(c, val["ownerType"], val["ownerId"])
            for k in ("chatId", "title", "createdDate"):
                if k in p and p[k] is not None:
                    val[k] = p[k]
            val["chatId"] = text(val["chatId"], "chatId", required=True, limit=500)
            val["title"] = text(val["title"], "title", required=True, limit=1000)
            raw_date = str(val.get("createdDate") or "").strip()
            val["createdDate"] = valid_date(raw_date) if raw_date else default_date
            c.execute(
                "INSERT INTO agent_chats VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET chat_id=excluded.chat_id,title=excluded.title,created_date=excluded.created_date,revision=excluded.revision,updated_at=excluded.updated_at",
                (val["id"], val["ownerType"], val["ownerId"], val["chatId"], val["title"], val["createdDate"], 0, val["revision"] + 1, val["createdAt"], now()),
            )
            p = {**p, "id": val["id"]}
        elif kind == "github" and verb in {"create", "update"}:
            val = before.copy() if before else {"id": uid(), "ownerType": p.get("ownerType"), "ownerId": p.get("ownerId"), "kind": "pr", "url": "", "reference": "", "title": "", "description": "", "chatId": None, "createdAt": now(), "revision": 0}
            if val["ownerType"] not in {"item", "roadmap"}:
                raise AppError(422, "validation", "GitHub references must belong to a work item or roadmap")
            self.owner(c, val["ownerType"], val["ownerId"])
            for k in ("kind", "url", "reference", "title", "description", "chatId"):
                if k in p and (k == "chatId" or p[k] is not None):
                    val[k] = p[k]
            if val["kind"] not in {"commit", "pr"}:
                raise AppError(422, "validation", "GitHub reference kind must be commit or pr")
            val["url"] = valid_http_url(val["url"], "url")
            val["reference"] = text(val["reference"], "reference", limit=300).strip() or infer_github_reference(val["kind"], val["url"])
            val["title"] = text(val["title"], "title", limit=1000).strip()
            val["description"] = text(val["description"], "description", limit=100_000).strip()
            if val["chatId"]:
                if not isinstance(val["chatId"], str):
                    raise AppError(422, "validation", "Invalid chatId")
                chat_row = c.execute("SELECT id,owner_type,owner_id FROM agent_chats WHERE id=?", (val["chatId"],)).fetchone()
                if not chat_row or chat_row["owner_type"] != val["ownerType"] or chat_row["owner_id"] != val["ownerId"]:
                    raise AppError(422, "validation", "Agent chat does not belong to this record")
            else:
                val["chatId"] = None
            c.execute(
                "INSERT INTO github_refs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,url=excluded.url,reference=excluded.reference,title=excluded.title,description=excluded.description,chat_id=excluded.chat_id,revision=excluded.revision,updated_at=excluded.updated_at",
                (val["id"], val["ownerType"], val["ownerId"], val["kind"], val["url"], val["reference"], val["title"], val["description"], val["chatId"], 0, val["revision"] + 1, val["createdAt"], now()),
            )
            p = {**p, "id": val["id"]}
        elif kind == "task" and verb in {"create", "update"}:
            val = before.copy() if before else {"id": uid(), "itemId": p.get("itemId"), "text": "", "done": False, "createdAt": now(), "revision": 0}
            self.item(c, val["itemId"])
            if "text" in p:
                val["text"] = text(p["text"], "text", required=True)
            if "done" in p:
                val["done"] = boolean(p["done"], "done")
            if not val["text"]:
                raise AppError(422, "validation", "Checklist text is required")
            c.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text,done=excluded.done,revision=excluded.revision,updated_at=excluded.updated_at",
                      (val["id"], val["itemId"], val["text"], int(val["done"]), 0, val["revision"] + 1, val["createdAt"], now()))
            p = {**p, "id": val["id"]}
        elif kind == "reference" and creating:
            self.item(c, p.get("itemId", ""))
            identity = uid()
            value = text(p.get("path"), "path", required=True, limit=32768)
            if any(ord(ch) < 32 for ch in value):
                raise AppError(422, "validation", "Path contains control characters")
            c.execute("INSERT INTO file_references VALUES(?,?,?,?,?,?)", (identity, p["itemId"], value, 0, 1, now()))
            p = {**p, "id": identity}
        else:
            raise AppError(422, "operation", "Unsupported supporting-record operation")
        if kind == "github":
            after = support_record(
                c.execute(
                    "SELECT g.*, ac.title AS chat_title FROM github_refs g LEFT JOIN agent_chats ac ON ac.id=g.chat_id WHERE g.id=?",
                    (p["id"],),
                ).fetchone()
            )
        else:
            after = support_record(c.execute(f"SELECT * FROM {table} WHERE id=?", (p["id"],)).fetchone())
        if kind in {"note", "attachment"} and after.get("ownerType") != "proposal":
            self.index(c, after["id"], kind, after.get("title", after.get("name", "")), after.get("text", ""), after["ownerId"], after["archived"])
        elif kind == "link":
            self.index(c, after["id"], "link", after.get("title") or after["url"], (after["url"] + "\n" + after.get("description", "")).strip(), after["ownerId"], after["archived"])
        elif kind == "chat":
            self.index(c, after["id"], "chat", after["title"], f"{after['chatId']} {after['createdDate']}", after["ownerId"], after["archived"])
        elif kind == "github":
            self.index(c, after["id"], "github", after.get("title") or after.get("reference") or after["url"], f"{after['kind']} {after.get('reference', '')} {after['url']}\n{after.get('description', '')}".strip(), after["ownerId"], after["archived"])
        elif kind == "attachment":
            c.execute("DELETE FROM search_index WHERE entity_id=?", (after["id"],))
        event(c, actor, action, p["id"], before, after)
        return after

    def upload(self, c: sqlite3.Connection, actor: dict, owner_type: str, owner_id: str,
               name: str, mime: str, content: bytes) -> dict:
        self.owner(c, owner_type, owner_id, actor)
        if not content or len(content) > FILE_LIMIT:
            raise AppError(413 if content else 422, "file_size", "Attach a nonempty file up to 25,000,000 bytes")
        name = PureWindowsPath(name.replace("/", "\\")).name
        name = "".join(ch for ch in name if ord(ch) >= 32 and ord(ch) != 127)[:200]
        if name in {"", ".", ".."}:
            raise AppError(422, "validation", "A filename is required")
        mime = mime.split(";", 1)[0].lower().strip()
        if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", mime):
            mime = "application/octet-stream"
        identity = uid()
        c.execute("INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?,?,?)", (identity, owner_type, owner_id, name, mime, len(content), hashlib.sha256(content).hexdigest(), content, 0, 1, now()))
        after = support_record(c.execute("SELECT * FROM attachments WHERE id=?", (identity,)).fetchone())
        # Unapproved evidence must never appear in canonical global search.
        if owner_type != "proposal":
            self.index(c, identity, "attachment", name, "", owner_id, False)
        event(c, actor, "attachment.create", identity, None, after)
        return after

    @staticmethod
    def index(c: sqlite3.Connection, identity: str, kind: str, title: str, body: str,
              owner: str, archived: bool = False) -> None:
        c.execute("DELETE FROM search_index WHERE entity_id=?", (identity,))
        c.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,?,?,?,?,?)", (identity, kind, title, body, owner, int(archived)))

    def refresh_plans(self, c: sqlite3.Connection, roadmap_id: str | None = None) -> None:
        if roadmap_id:
            c.execute("DELETE FROM search_index WHERE entity_id=? OR (type='step' AND owner_id=?)", (roadmap_id, roadmap_id))
        else:
            c.execute("DELETE FROM search_index WHERE type IN ('roadmap','step')")
        clause = " WHERE id=?" if roadmap_id else ""
        args = (roadmap_id,) if roadmap_id else ()
        for r in c.execute("SELECT * FROM roadmaps" + clause, args).fetchall():
            keys = set(r.keys())
            self.index(c, r["id"], "roadmap", r["title"], r["overview"] if "overview" in keys else "", r["id"], bool(r["archived"]))
        for r in c.execute("SELECT * FROM steps" + (" WHERE roadmap_id=?" if roadmap_id else ""), args).fetchall():
            keys = set(r.keys())
            parent = c.execute("SELECT archived FROM roadmaps WHERE id=?", (r["roadmap_id"],)).fetchone()
            self.index(c, r["id"], "step", r["title"], r["description"], r["roadmap_id"], bool(parent[0] or (r["retired"] if "retired" in keys else False)))

    def search(self, query: str, archived: bool = False, limit: int = 50, offset: int = 0) -> dict:
        terms = re.findall(r"\w+", query, re.UNICODE)
        if not terms:
            return {"items": [], "total": 0}
        match = " AND ".join('"' + term.replace('"', '""') + '"*' for term in terms[:30])
        # Resolve ownership at query time so archiving a parent also hides its notes/files
        # without destroying each supporting record's independent archive flag.
        source = """FROM search_index s
            LEFT JOIN notes n ON s.type='note' AND n.id=s.entity_id
            LEFT JOIN attachments a ON s.type='attachment' AND a.id=s.entity_id
            LEFT JOIN web_links wl ON s.type='link' AND wl.id=s.entity_id
            LEFT JOIN agent_chats ac ON s.type='chat' AND ac.id=s.entity_id
            LEFT JOIN github_refs gr ON s.type='github' AND gr.id=s.entity_id
            LEFT JOIN work_items w ON w.id=CASE WHEN s.type='item' THEN s.entity_id
                WHEN n.owner_type='item' THEN n.owner_id WHEN a.owner_type='item' THEN a.owner_id
                WHEN wl.owner_type='item' THEN wl.owner_id WHEN ac.owner_type='item' THEN ac.owner_id
                WHEN gr.owner_type='item' THEN gr.owner_id END
            LEFT JOIN steps st ON st.id=CASE WHEN s.type='step' THEN s.entity_id
                WHEN n.owner_type='step' THEN n.owner_id WHEN a.owner_type='step' THEN a.owner_id END
            LEFT JOIN roadmaps rm ON rm.id=CASE WHEN s.type='roadmap' THEN s.entity_id
                WHEN n.owner_type='roadmap' THEN n.owner_id WHEN a.owner_type='roadmap' THEN a.owner_id
                WHEN wl.owner_type='roadmap' THEN wl.owner_id WHEN ac.owner_type='roadmap' THEN ac.owner_id
                WHEN gr.owner_type='roadmap' THEN gr.owner_id
                ELSE st.roadmap_id END
            LEFT JOIN people p ON p.id=CASE WHEN s.type='person' THEN s.entity_id END
            WHERE search_index MATCH ? AND
             (CAST(s.archived AS INTEGER)=1 OR coalesce(w.archived,0)=1
              OR coalesce(st.retired,0)=1 OR coalesce(rm.archived,0)=1 OR coalesce(p.archived,0)=1)=?"""
        with self.db.read() as c:
            rows = c.execute("""SELECT s.entity_id,s.type,s.title,s.body,s.owner_id,
                             CASE WHEN w.id IS NOT NULL THEN 'work' WHEN p.id IS NOT NULL THEN 'people' ELSE 'roadmaps' END AS route_type,
                             coalesce(w.id,p.id,rm.id) AS route_id """ + source + " ORDER BY rank LIMIT ? OFFSET ?", (match, int(archived), limit, offset)).fetchall()
            total = c.execute("SELECT count(*) " + source, (match, int(archived))).fetchone()[0]
            return {"items": [{"id": r[0], "type": r[1], "title": r[2], "snippet": r[3][:220], "ownerId": r[4],
                               "routeType": r["route_type"], "routeId": r["route_id"]} for r in rows], "total": total}
