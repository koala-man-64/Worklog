"""Work and supporting-record application services, independent of HTTP."""

from __future__ import annotations

import builtins
import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from . import scratchpad, search
from .common import (
    FILE_LIMIT,
    NOTE_LIMIT,
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
from .repository_links import linked_repositories, replace_repository_associations


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


def support_record(r: Any) -> dict:
    keys = {"owner_type": "ownerType", "owner_id": "ownerId", "item_id": "itemId",
            "chat_id": "chatId", "created_date": "createdDate", "chat_title": "chatTitle",
            "roadmap_id": "roadmapId", "decided_date": "decidedDate",
            "created_at": "createdAt", "updated_at": "updatedAt"}
    record = {keys.get(k, k): bool(v) if k in {"archived", "done"} else v
              for k, v in dict(r).items() if k != "content"}
    if "repos" in record:
        record["repos"] = json.loads(r["repos"])
    return record


def repositories(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 20:
        raise AppError(422, "validation", "repos must be a list of at most 20 repositories")
    names = [text(name, "repository", required=True, limit=500) for name in value]
    return list(dict.fromkeys(names))


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


# A person's own fields, each named as its column, and their limits in UTF-8 bytes. The
# description is a paragraph and the notes Markdown; the others are single lines, trimmed.
PERSON_LIMITS = {"name": 500, "title": 500, "team": 500, "description": 10_000, "email": 320,
                 "phone": 100, "chat": 500, "location": 500, "notes": NOTE_LIMIT}
PERSON_FIELDS = tuple(PERSON_LIMITS)
# What a person can be tied to, by the table each lives in.
PERSON_TARGETS = {"item": "work_items", "roadmap": "roadmaps", "repository": "repositories"}
# Besides whitespace and control characters: what a mailto: link would read as more than an address.
EMAIL_FORBIDDEN = frozenset('<>()[]\\,;:"?#/%')
# The single-line person fields, and what breaks a line: control characters and line or paragraph separators.
PERSON_LINES = ("name", "title", "team", "chat", "location")
LINE_BREAKS = frozenset({"Cc", "Zl", "Zp"})


def person_record(r: Any) -> dict:
    keys = set(r.keys())
    res = {"id": r["id"], **{field: r[field] for field in PERSON_FIELDS},
           "archived": bool(r["archived"]), "revision": r["revision"],
           "createdAt": r["created_at"], "updatedAt": r["updated_at"]}
    for column, field in (("work_item_count", "workItemCount"), ("roadmap_count", "roadmapCount"),
                          ("repository_count", "repositoryCount")):
        if column in keys:
            res[field] = r[column]
    return res


def person_assignment_record(r: Any) -> dict:
    return {"id": r["id"], "personId": r["person_id"], "name": r["name"],
            "personTitle": r["person_title"], "personTeam": r["person_team"], "relationship": r["relationship"],
            "archived": bool(r["archived"]), "createdAt": r["created_at"]}


def people_on(c: sqlite3.Connection, target_type: str, target_ids: builtins.list[str]) -> dict[str, builtins.list[dict]]:
    """The people tied to each of these work items, roadmaps or repositories, by name."""
    if not target_ids:
        return {}
    placeholders = ",".join("?" for _ in target_ids)
    rows = c.execute(
        f"""SELECT pl.id, pl.person_id, pl.target_id, pl.relationship, pl.created_at,
                   p.name, p.title AS person_title, p.team AS person_team, p.archived
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


def person_email(raw: Any) -> str:
    """Empty, or an address mail can be sent to: one @ before a dotted domain."""
    value = text(raw, "email", limit=PERSON_LIMITS["email"]).strip()
    if not value:
        return ""
    local, _, domain = value.rpartition("@")
    labels = domain.split(".")
    if (not local or "@" in local or len(labels) < 2 or not all(labels)
            or any(ch.isspace() or not ch.isprintable() or ch in EMAIL_FORBIDDEN for ch in value)):
        raise AppError(422, "validation", "Email must be an address such as name@example.com.", {"email": "invalid"})
    return value


def person_phone(raw: Any) -> str:
    """Empty, or a number as people write it, such as +1 (312) 555-0100 ext. 12."""
    value = text(raw, "phone", limit=PERSON_LIMITS["phone"]).strip()
    # A digit in any script counts, such as the full-width ones some keyboards type.
    if value and (not any(unicodedata.category(ch) == "Nd" for ch in value) or not value.isprintable()):
        raise AppError(422, "validation", "Phone must be a number such as +1 312 555 0100.", {"phone": "invalid"})
    return value


def boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise AppError(422, "validation", f"{field} must be a boolean")
    return value


# The records that hold notes and files, by the table each lives in.
SUPPORT_OWNERS = {"item": "work_items", "roadmap": "roadmaps", "step": "steps", "proposal": "proposals"}
# A repository holds attached files and nothing else: no notes, links or chats of its own.
FILE_OWNERS = {**SUPPORT_OWNERS, "repository": "repositories"}


class WorkService:
    def __init__(self, db: Database, plans: Any, proposals: Any = None):
        self.db, self.plans, self.proposals = db, plans, proposals

    def owner(self, conn: sqlite3.Connection, owner_type: str, owner_id: str,
              actor: dict | None = None, *, files: bool = False) -> None:
        """Check that a supporting record's owner exists; `files` also admits repositories,
        which hold attached files only."""
        table = (FILE_OWNERS if files else SUPPORT_OWNERS).get(owner_type)
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
        return people_on(c, target_type, target_ids)

    def list(self, *, view: str = "all", archived: bool = False, states: builtins.list[str] | None = None,
             roadmap_id: str | None = None, person_id: str | None = None, repository_id: str | None = None,
             limit: int = 100, offset: int = 0, q: str = "", item_id: str | None = None) -> dict:
        # Any of the given states matches; none, or only empty values, means every state.
        chosen = sorted({status(value) for value in states or [] if value != ""})
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
            people_map = self.target_people(c, "item", [r["id"] for r in rows])
            repos = linked_repositories(c, "item", [r["id"] for r in rows])
            return {"items": [{**item_record(r), "rank": r["stack_rank"], "repositoryIds": repos.get(r["id"], []), "isBacklog": bool(r["is_backlog"]), "people": people_map.get(r["id"], [])} for r in rows], "total": total,
                    "rankedCount": ranked_count(c), "collectionRevision": setting(c, "backlogRevision", 1)}

    @staticmethod
    def chat_steps(c: sqlite3.Connection, chat_ids: builtins.list[str]) -> dict[str, builtins.list[str]]:
        if not chat_ids:
            return {}
        placeholders = ",".join("?" for _ in chat_ids)
        grouped: dict[str, builtins.list[str]] = {cid: [] for cid in chat_ids}
        for r in c.execute(f"SELECT chat_id,step_id FROM chat_steps WHERE chat_id IN ({placeholders}) ORDER BY created_at,step_id", chat_ids):
            grouped[r["chat_id"]].append(r["step_id"])
        return grouped

    def chats(self, c: sqlite3.Connection, rows: builtins.list[Any]) -> builtins.list[dict]:
        steps = self.chat_steps(c, [r["id"] for r in rows])
        repos = linked_repositories(c, "chat", [r["id"] for r in rows])
        return [{**support_record(r), "stepIds": steps[r["id"]], "repositoryIds": repos.get(r["id"], [])} for r in rows]

    def chat_overview(self, c: sqlite3.Connection, archived: bool = False) -> builtins.list[dict]:
        """Every agent chat with the record it belongs to, the roadmaps that record sits within, and the steps it works on."""
        chats = self.chats(c, c.execute("SELECT * FROM agent_chats WHERE archived=? ORDER BY created_date DESC,created_at DESC,id", (int(archived),)).fetchall())
        owners: dict[tuple[str, str], dict] = {}
        # The roadmap each owner sits directly in: a roadmap is its own, a work item takes its step's roadmap first.
        within: dict[tuple[str, str], str | None] = {}
        roadmaps = {r["id"]: r for r in c.execute("SELECT id,title,archived,parent_id FROM roadmaps")} if chats else {}
        for ch in chats:
            r = roadmaps.get(ch["ownerId"]) if ch["ownerType"] == "roadmap" else None
            if r is not None:
                owners[("roadmap", r["id"])] = {"type": "roadmap", "id": r["id"], "title": r["title"], "archived": bool(r["archived"])}
                within[("roadmap", r["id"])] = r["id"]
        ids = list({ch["ownerId"] for ch in chats if ch["ownerType"] == "item"})
        if ids:
            for r in c.execute(
                f"SELECT id,title,archived,COALESCE((SELECT roadmap_id FROM steps WHERE steps.id=work_items.step_id),roadmap_id) AS roadmap FROM work_items WHERE id IN ({','.join('?' for _ in ids)})",
                ids,
            ):
                owners[("item", r["id"])] = {"type": "item", "id": r["id"], "title": r["title"], "archived": bool(r["archived"])}
                within[("item", r["id"])] = r["roadmap"]

        def path(roadmap_id: str | None) -> builtins.list[dict]:
            """The roadmaps from the top level down to this one; a broken parent chain stops where it breaks."""
            chain: builtins.list[dict] = []
            while roadmap_id is not None and roadmap_id in roadmaps and all(r["id"] != roadmap_id for r in chain):
                row = roadmaps[roadmap_id]
                chain.append({"id": row["id"], "title": row["title"], "archived": bool(row["archived"])})
                roadmap_id = row["parent_id"]
            return chain[::-1]
        steps: dict[str, builtins.list[dict]] = {ch["id"]: [] for ch in chats}
        if chats:
            for r in c.execute(
                f"SELECT cs.chat_id,s.id,s.title,s.status,s.retired FROM chat_steps cs JOIN steps s ON s.id=cs.step_id WHERE cs.chat_id IN ({','.join('?' for _ in chats)}) ORDER BY cs.created_at,s.position,s.id",
                [ch["id"] for ch in chats],
            ):
                steps[r["chat_id"]].append({"id": r["id"], "title": r["title"], "status": r["status"], "retired": bool(r["retired"])})
        return [{**ch, "owner": owners.get((ch["ownerType"], ch["ownerId"]), {"type": ch["ownerType"], "id": ch["ownerId"], "title": "", "archived": False}),
                 "roadmaps": path(within.get((ch["ownerType"], ch["ownerId"]))), "steps": steps[ch["id"]]} for ch in chats]

    def roadmap_items(self, c: sqlite3.Connection, roadmap_id: str) -> builtins.list[dict]:
        rows = c.execute(
            "SELECT * FROM work_items WHERE archived=0 AND (roadmap_id=? OR step_id IN (SELECT id FROM steps WHERE roadmap_id=?)) ORDER BY backlog_order,id",
            (roadmap_id, roadmap_id),
        ).fetchall()
        people_map = self.target_people(c, "item", [r["id"] for r in rows])
        repos = linked_repositories(c, "item", [r["id"] for r in rows])
        return [
            {**item_record(r), "repositoryIds": repos.get(r["id"], []), "roadmapId": r["roadmap_id"] or roadmap_id, "people": people_map.get(r["id"], [])}
            for r in rows
        ]

    def detail(self, item_id: str) -> dict:
        with self.db.read() as c:
            result = item_record(self.item(c, item_id))
            result["rank"] = stack_ranks(c, [item_id]).get(item_id)
            result["rankedCount"] = ranked_count(c)
            result["repositoryIds"] = linked_repositories(c, "item", [item_id]).get(item_id, [])
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
            match = search.prefix_match(q)
            if match:
                where.append("p.id IN(SELECT entity_id FROM search_index WHERE search_index MATCH ? AND type='person')")
                args.append(match)
            clause = " AND ".join(where)
            rows = c.execute(
                f"""SELECT p.*,
                    (SELECT count(*) FROM person_links pl JOIN work_items w ON w.id=pl.target_id WHERE pl.person_id=p.id AND pl.target_type='item' AND w.archived=0) AS work_item_count,
                    (SELECT count(*) FROM person_links pl JOIN roadmaps r ON r.id=pl.target_id WHERE pl.person_id=p.id AND pl.target_type='roadmap' AND r.archived=0) AS roadmap_count,
                    (SELECT count(*) FROM person_links pl JOIN repositories rp ON rp.id=pl.target_id WHERE pl.person_id=p.id AND pl.target_type='repository' AND rp.archived=0) AS repository_count
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
            repos = linked_repositories(c, "item", [link["itemId"] for link in work_items])
            for link in work_items:
                link["item"]["repositoryIds"] = repos.get(link["itemId"], [])
            from .coordination import repository_record

            repositories = [
                {"linkId": r["link_id"], "personId": r["person_id"], "repositoryId": r["repository_id"],
                 "relationship": r["relationship"], "createdAt": r["link_created_at"], "repository": repository_record(r)}
                for r in c.execute(
                    """SELECT pl.id AS link_id, pl.person_id, pl.target_id AS repository_id, pl.relationship,
                              pl.created_at AS link_created_at, rp.*
                       FROM person_links pl
                       JOIN repositories rp ON rp.id = pl.target_id
                       WHERE pl.person_id = ? AND pl.target_type = 'repository'
                       ORDER BY rp.archived, rp.name COLLATE NOCASE, rp.id""",
                    (person_id,),
                )
            ]
            result["roadmaps"] = roadmaps
            result["workItems"] = work_items
            result["repositories"] = repositories
            result["history"] = read_events(c, person_id)
            return result

    def support(self, c: sqlite3.Connection, owner_type: str, owner_id: str) -> dict:
        result = {"notes": [support_record(r) for r in c.execute("SELECT * FROM notes WHERE owner_type=? AND owner_id=? ORDER BY created_at", (owner_type, owner_id))],
                  "attachments": [support_record(r) for r in c.execute("SELECT id,owner_type,owner_id,name,mime,size,sha256,archived,revision,created_at FROM attachments WHERE owner_type=? AND owner_id=? ORDER BY created_at", (owner_type, owner_id))]}
        if owner_type in {"item", "roadmap"}:
            result["links"] = [support_record(r) for r in c.execute("SELECT * FROM web_links WHERE owner_type=? AND owner_id=? ORDER BY created_at,id", (owner_type, owner_id))]
            result["chats"] = self.chats(c, c.execute("SELECT * FROM agent_chats WHERE owner_type=? AND owner_id=? ORDER BY created_date DESC,created_at DESC,id", (owner_type, owner_id)).fetchall())
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
        if owner_type == "roadmap":
            result["questions"] = [support_record(r) for r in c.execute("SELECT * FROM roadmap_questions WHERE roadmap_id=? ORDER BY created_at,id", (owner_id,))]
            result["decisions"] = [support_record(r) for r in c.execute("SELECT * FROM roadmap_decisions WHERE roadmap_id=? ORDER BY decided_date DESC,created_at DESC,id", (owner_id,))]
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
            ids = [entry["itemId"] for entry in entries]
            repos = linked_repositories(c, "item", ids)
            ranked = stack_ranks(c, ids)
            counts = self.tab_counts(c, ids)
            for entry in entries:
                entry["item"]["repositoryIds"] = repos.get(entry["itemId"], [])
                entry["item"]["rank"] = ranked.get(entry["itemId"])
                entry["counts"] = counts[entry["itemId"]]
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
        if action.startswith("repository."):
            from .coordination import repository_apply

            return repository_apply(c, actor, action, p)
        if action.startswith("item."):
            return self._item_apply(c, actor, action, p)
        if action == "entry.upsert":
            return self._entry_apply(c, actor, p)
        if action.startswith(("note.", "task.", "reference.", "attachment.", "link.", "chat.", "github.", "question.", "decision.")):
            return self._support_apply(c, actor, action, p)
        if action.startswith(("roadmap.", "progress.")):
            result = self.plans.apply(c, actor, action, p)
            # Moving changes no indexed text or archive state.
            if action not in {"progress.set", "roadmap.move"}:
                self.refresh_plans(c, result["id"])
            if self.proposals is not None and action != "roadmap.create":
                self.proposals.notify_stale_proposals(
                    c, result.get("roadmapId") if action == "progress.set" else result.get("id"))
            return result
        if action.startswith("person."):
            return self._person_apply(c, actor, action, p)
        raise AppError(422, "operation", "Unsupported operation")

    @staticmethod
    def _person_target(p: dict) -> tuple[str, str, str]:
        person_id = text(p.get("personId", ""), "personId", required=True, limit=200)
        target_type = p.get("targetType", "")
        if not isinstance(target_type, str) or target_type not in PERSON_TARGETS:
            raise AppError(422, "validation", "Invalid targetType for person link")
        return person_id, target_type, text(p.get("targetId", ""), "targetId", required=True, limit=200)

    def _person_apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        if action == "person.link":
            person_id, target_type, target_id = self._person_target(p)
            relationship = text(p.get("relationship", ""), "relationship", limit=200).strip()
            person_row = self.person(c, person_id, active=True)
            existing = c.execute(
                "SELECT * FROM person_links WHERE person_id=? AND target_type=? AND target_id=?",
                (person_id, target_type, target_id),
            ).fetchone()
            if target_type == "repository":
                from .coordination import repository_takes_people

                # A new tie needs an active record, as other links do; an existing one keeps its role editable.
                repository_takes_people(c, target_id, new=existing is None)
            else:
                self.owner(c, target_type, target_id)
            before = (
                {"id": existing["id"], "personId": person_id, "targetType": target_type,
                 "targetId": target_id, "relationship": existing["relationship"], "createdAt": existing["created_at"]}
                if existing
                else None
            )
            link_id = existing["id"] if existing else uid()
            created_at = existing["created_at"] if existing else now()
            c.execute(
                "INSERT INTO person_links(id,person_id,target_type,target_id,relationship,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(person_id,target_type,target_id) DO UPDATE SET relationship=excluded.relationship",
                (link_id, person_id, target_type, target_id, relationship, created_at),
            )
            after = {
                "id": link_id,
                "personId": person_id,
                "name": person_row["name"],
                "personTitle": person_row["title"],
                "personTeam": person_row["team"],
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
            person_id, target_type, target_id = self._person_target(p)
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
            else {"id": uid(), **dict.fromkeys(PERSON_FIELDS, ""), "archived": False, "revision": 0, "createdAt": now()}
        )
        if action in {"person.archive", "person.recover"}:
            val["archived"] = action == "person.archive"
        elif action in {"person.create", "person.update"}:
            for k in PERSON_FIELDS:
                if k in p and p[k] is not None:
                    val[k] = p[k]
            val["name"] = text(val["name"], "name", required=True, limit=PERSON_LIMITS["name"])
            for k in ("title", "team", "chat", "location"):
                val[k] = text(val[k], k, limit=PERSON_LIMITS[k]).strip()
            # Refused only as it is set: a break kept from before (v9 never refused one) blocks no other change.
            for k in PERSON_LINES:
                if p.get(k) is not None and any(unicodedata.category(ch) in LINE_BREAKS for ch in val[k]):
                    raise AppError(422, "validation", f"{k.capitalize()} must be a single line.", {k: "invalid"})
            val["email"] = person_email(val["email"])
            val["phone"] = person_phone(val["phone"])
            val["description"] = text(val["description"], "description", limit=PERSON_LIMITS["description"])
            val["notes"] = text(val["notes"], "notes", limit=PERSON_LIMITS["notes"])
        else:
            raise AppError(422, "operation", "Unsupported person operation")
        val["revision"] += 1
        val["updatedAt"] = now()
        columns = ",".join(PERSON_FIELDS)
        changes = ",".join(f"{field}=excluded.{field}" for field in PERSON_FIELDS)
        c.execute(
            f"INSERT INTO people(id,{columns},archived,revision,created_at,updated_at) VALUES({','.join('?' * (len(PERSON_FIELDS) + 5))}) "
            f"ON CONFLICT(id) DO UPDATE SET {changes},archived=excluded.archived,revision=excluded.revision,updated_at=excluded.updated_at",
            (val["id"], *(val[field] for field in PERSON_FIELDS), int(val["archived"]), val["revision"], val["createdAt"], val["updatedAt"]),
        )
        event(c, actor, action, val["id"], before, val)
        # Search finds a person by any of their fields: a title, a team, an address or a word in their notes.
        body = "\n".join(val[field] for field in PERSON_FIELDS[1:] if val[field].strip())
        self.index(c, val["id"], "person", val["name"], body, val["id"], val["archived"])
        return val

    def _item_apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        creating = action == "item.create"
        row = None if creating else self.item(c, p.get("id", ""))
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
        c.execute("""INSERT INTO work_items(id,title,description,status,ado_url,step_id,backlog_order,archived,revision,created_at,updated_at,roadmap_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,description=excluded.description,status=excluded.status,ado_url=excluded.ado_url,step_id=excluded.step_id,archived=excluded.archived,revision=excluded.revision,updated_at=excluded.updated_at,roadmap_id=excluded.roadmap_id""",
                  (val["id"], val["title"], val["description"], val["status"], val["adoUrl"], val["stepId"], val["backlogOrder"], int(val["archived"]), val["revision"], val["createdAt"], val["updatedAt"], val["roadmapId"]))
        if "repositoryIds" in p:
            val["repositoryIds"] = replace_repository_associations(c, "item", val["id"], p["repositoryIds"])
        else:
            val.setdefault("repositoryIds", [])
        if action == "item.archive":
            from .coordination import release_item_participations

            release_item_participations(c, actor, val["id"])
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
        c.execute("""INSERT INTO daily_entries(id,item_id,date,planned,worked,outcome,position,revision) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(item_id,date) DO UPDATE SET planned=excluded.planned,worked=excluded.worked,outcome=excluded.outcome,revision=excluded.revision""",
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
        if date:
            for pos, identity in enumerate(ids):
                c.execute("UPDATE daily_entries SET position=?,revision=revision+1 WHERE id=?", (pos, identity))
            c.execute("UPDATE days SET revision=revision+1 WHERE date=?", (date,))
        else:
            # The backlog trades the positions it already holds, so planned work keeps its stack
            # rank and done work its place.
            renumber(c, backlog_sequence(c))
            held = dict(c.execute("SELECT id,backlog_order FROM work_items").fetchall())
            for slot, identity in zip(sorted(held[i] for i in actual), ids, strict=True):
                c.execute("UPDATE work_items SET backlog_order=?,revision=revision+1 WHERE id=?", (slot, identity))
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
        neighbor = rows[target]["id"]
        # Older orderings left tied positions, where swapping two items' values would let a
        # third item tied with either fall on the other side of it. Every item gets its own
        # position first (nothing is written once they have), so only the two trade places.
        renumber(c, backlog_sequence(c))
        held = dict(c.execute("SELECT id,backlog_order FROM work_items WHERE id IN (?,?)", (identity, neighbor)).fetchall())
        positions = (held[neighbor], held[identity])
        c.execute("UPDATE work_items SET backlog_order=?,revision=revision+1 WHERE id=?", (positions[0], identity))
        c.execute("UPDATE work_items SET backlog_order=?,revision=revision+1 WHERE id=?", (positions[1], neighbor))
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
        row = self.item(c, identity, active=True)
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
            "question": "roadmap_questions",
            "decision": "roadmap_decisions",
        }[kind]
        creating = verb == "create"
        row = None if creating else c.execute(f"SELECT * FROM {table} WHERE id=?", (p.get("id", ""),)).fetchone()
        if not creating:
            require_revision(row, p.get("revision"))
        before = support_record(row) if row else None
        if kind == "chat" and before is not None:
            before["repositoryIds"] = linked_repositories(c, "chat", [before["id"]]).get(before["id"], [])
            before["stepIds"] = self.chat_steps(c, [before["id"]])[before["id"]]
        val: dict[str, Any]
        if verb in {"archive", "recover"}:
            assert row is not None
            if kind == "note" and verb == "recover" and row["owner_type"] == "item":
                pad = scratchpad.find(c, row["owner_id"])
                if pad is not None and pad[0] != row["id"]:
                    raise AppError(409, "conflict", "This work item already has a scratchpad; copy what you need from this note into it")
            c.execute(f"UPDATE {table} SET archived=?,revision=revision+1 WHERE id=?", (int(verb == "archive"), row["id"]))
            if kind == "chat" and verb == "archive":
                from .chat_messages import expire_pending

                # Recovering the chat later never revives these deliveries.
                expire_pending(c, "chat_archived", chat_id=row["id"])
        elif kind == "note" and verb in {"create", "update"}:
            val = before.copy() if before else {"id": uid(), "ownerType": p.get("ownerType"), "ownerId": p.get("ownerId"), "title": "", "text": "", "createdAt": now(), "revision": 0}
            self.owner(c, val["ownerType"], val["ownerId"])
            if val["ownerType"] == "proposal":
                raise AppError(422, "validation", "Use proposal rationale for proposed notes")
            for k in ("title", "text"):
                if k in p:
                    val[k] = text(p[k], k, limit=2000 if k == "title" else 1_000_000)
            # A scratchpad may be emptied; other notes need something in them.
            if val["ownerType"] != "item" and not val["title"].strip() and not val["text"].strip():
                raise AppError(422, "validation", "A note needs a title or text")
            # A work item has one scratchpad, its only active note (v8).
            if creating and val["ownerType"] == "item" and scratchpad.find(c, val["ownerId"]):
                raise AppError(409, "conflict", "This work item already has a scratchpad; edit it instead")
            c.execute("INSERT INTO notes(id,owner_type,owner_id,title,text,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,text=excluded.text,updated_at=excluded.updated_at,revision=excluded.revision",
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
                "INSERT INTO web_links(id,owner_type,owner_id,url,title,description,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET url=excluded.url,title=excluded.title,description=excluded.description,revision=excluded.revision,updated_at=excluded.updated_at",
                (val["id"], val["ownerType"], val["ownerId"], val["url"], val["title"], val["description"], 0, val["revision"] + 1, val["createdAt"], now()),
            )
            p = {**p, "id": val["id"]}
        elif kind == "chat" and verb in {"create", "update"}:
            default_date = today(setting(c, "timezone", "America/Chicago"))
            val = before.copy() if before else {"id": uid(), "ownerType": p.get("ownerType"), "ownerId": p.get("ownerId"), "chatId": "", "title": "", "createdDate": default_date,
                                                "provider": "", "model": "", "repos": [], "stepIds": [], "createdAt": now(), "revision": 0}
            if val["ownerType"] not in {"item", "roadmap"}:
                raise AppError(422, "validation", "Agent chats must belong to a work item or roadmap")
            self.owner(c, val["ownerType"], val["ownerId"])
            for k in ("chatId", "title", "createdDate", "provider", "model", "repos", "stepIds"):
                if k in p and p[k] is not None:
                    val[k] = p[k]
            val["chatId"] = text(val["chatId"], "chatId", required=True, limit=500)
            val["title"] = text(val["title"], "title", required=True, limit=1000)
            raw_date = str(val.get("createdDate") or "").strip()
            val["createdDate"] = valid_date(raw_date) if raw_date else default_date
            val["provider"] = text(val["provider"], "provider", limit=200).strip()
            val["model"] = text(val["model"], "model", limit=200).strip()
            val["repos"] = repositories(val["repos"])
            previous = before["stepIds"] if before else []
            assigned = self._chat_step_ids(c, val, previous)
            c.execute(
                "INSERT INTO agent_chats(id,owner_type,owner_id,chat_id,title,created_date,archived,revision,created_at,updated_at,provider,model,repos) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET chat_id=excluded.chat_id,title=excluded.title,created_date=excluded.created_date,provider=excluded.provider,model=excluded.model,repos=excluded.repos,revision=excluded.revision,updated_at=excluded.updated_at",
                (val["id"], val["ownerType"], val["ownerId"], val["chatId"], val["title"], val["createdDate"], 0, val["revision"] + 1, val["createdAt"], now(),
                 val["provider"], val["model"], json.dumps(val["repos"])),
            )
            self._assign_steps(c, actor, val, previous, assigned)
            if before is not None and before["chatId"] != val["chatId"]:
                from .chat_messages import expire_pending

                # Runs that reported the old ID are no longer tied to this chat.
                expire_pending(c, "chat_changed", chat_id=val["id"])
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
                val["chatId"] = self._github_chat_id(c, val["ownerType"], val["ownerId"], val["chatId"])
            else:
                val["chatId"] = None
            c.execute(
                "INSERT INTO github_refs(id,owner_type,owner_id,kind,url,reference,title,description,chat_id,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,url=excluded.url,reference=excluded.reference,title=excluded.title,description=excluded.description,chat_id=excluded.chat_id,revision=excluded.revision,updated_at=excluded.updated_at",
                (val["id"], val["ownerType"], val["ownerId"], val["kind"], val["url"], val["reference"], val["title"], val["description"], val["chatId"], 0, val["revision"] + 1, val["createdAt"], now()),
            )
            p = {**p, "id": val["id"]}
        elif kind == "question" and verb in {"create", "update"}:
            val = before.copy() if before else {"id": uid(), "roadmapId": p.get("roadmapId"), "title": "", "details": "", "answer": "", "status": "open", "createdAt": now(), "revision": 0}
            self.owner(c, "roadmap", val["roadmapId"])
            for k in ("title", "details", "answer", "status"):
                if k in p and p[k] is not None:
                    val[k] = p[k]
            val["title"] = text(val["title"], "title", required=True, limit=1000)
            val["details"] = text(val["details"], "details", limit=100_000)
            val["answer"] = text(val["answer"], "answer", limit=100_000)
            if val["status"] not in {"open", "answered"}:
                raise AppError(422, "validation", "Question status must be open or answered", {"status": "invalid"})
            # Reopening keeps the earlier answer; only an answered question needs one.
            if val["status"] == "answered" and not val["answer"].strip():
                raise AppError(422, "validation", "Write an answer before marking the question answered", {"answer": "required"})
            c.execute(
                "INSERT INTO roadmap_questions(id,roadmap_id,title,details,answer,status,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,details=excluded.details,answer=excluded.answer,status=excluded.status,revision=excluded.revision,updated_at=excluded.updated_at",
                (val["id"], val["roadmapId"], val["title"], val["details"], val["answer"], val["status"], 0, val["revision"] + 1, val["createdAt"], now()),
            )
            p = {**p, "id": val["id"]}
        elif kind == "decision" and verb in {"create", "update"}:
            default_date = today(setting(c, "timezone", "America/Chicago"))
            val = before.copy() if before else {"id": uid(), "roadmapId": p.get("roadmapId"), "title": "", "rationale": "", "decidedDate": default_date, "createdAt": now(), "revision": 0}
            self.owner(c, "roadmap", val["roadmapId"])
            for k in ("title", "rationale", "decidedDate"):
                if k in p and p[k] is not None:
                    val[k] = p[k]
            val["title"] = text(val["title"], "title", required=True, limit=1000)
            val["rationale"] = text(val["rationale"], "rationale", limit=100_000)
            raw_date = str(val.get("decidedDate") or "").strip()
            if raw_date:
                val["decidedDate"] = valid_date(raw_date)
            elif before is None:
                val["decidedDate"] = default_date
            else:
                # A blank date must not quietly redate a decision recorded earlier.
                raise AppError(422, "validation", "Give the date the decision was made", {"decidedDate": "required"})
            c.execute(
                "INSERT INTO roadmap_decisions(id,roadmap_id,title,rationale,decided_date,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,rationale=excluded.rationale,decided_date=excluded.decided_date,revision=excluded.revision,updated_at=excluded.updated_at",
                (val["id"], val["roadmapId"], val["title"], val["rationale"], val["decidedDate"], 0, val["revision"] + 1, val["createdAt"], now()),
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
            c.execute("INSERT INTO tasks(id,item_id,text,done,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text,done=excluded.done,revision=excluded.revision,updated_at=excluded.updated_at",
                      (val["id"], val["itemId"], val["text"], int(val["done"]), 0, val["revision"] + 1, val["createdAt"], now()))
            p = {**p, "id": val["id"]}
        elif kind == "attachment" and creating:
            from .proposals import evidence_file

            # Only an applied proposal attaches a file this way: a copy of evidence uploaded to it,
            # so the proposal keeps the file it was reviewed with. The person uploads directly.
            owner_type, owner_id = p.get("ownerType"), p.get("ownerId")
            if not (isinstance(owner_type, str) and owner_type in FILE_OWNERS.keys() - {"proposal"} and isinstance(owner_id, str)):
                raise AppError(422, "validation", "Files belong to a work item, roadmap, step or repository")
            source = evidence_file(c, p.get("proposalId"), p.get("attachmentId"))
            self.owner(c, owner_type, owner_id, files=True)
            if owner_type == "repository":
                from .coordination import repository_takes_files

                repository_takes_files(c, owner_id)
            identity = uid()
            c.execute("INSERT INTO attachments(id,owner_type,owner_id,name,mime,size,sha256,content,archived,revision,created_at) "
                      "SELECT ?,?,?,name,mime,size,sha256,content,0,1,? FROM attachments WHERE id=?",
                      (identity, owner_type, owner_id, now(), source["id"]))
            p = {**p, "id": identity}
        elif kind == "reference" and creating:
            self.item(c, p.get("itemId", ""))
            identity = uid()
            value = text(p.get("path"), "path", required=True, limit=32768)
            if any(ord(ch) < 32 for ch in value):
                raise AppError(422, "validation", "Path contains control characters")
            c.execute("INSERT INTO file_references(id,item_id,path,archived,revision,created_at) VALUES(?,?,?,?,?,?)", (identity, p["itemId"], value, 0, 1, now()))
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
        elif kind == "chat":
            if "repositoryIds" in p:
                replace_repository_associations(c, "chat", p["id"], p["repositoryIds"])
            after = self.chats(c, [c.execute("SELECT * FROM agent_chats WHERE id=?", (p["id"],)).fetchone()])[0]
        else:
            after = support_record(c.execute(f"SELECT * FROM {table} WHERE id=?", (p["id"],)).fetchone())
        if kind in {"note", "attachment"} and after.get("ownerType") != "proposal":
            self.index(c, after["id"], kind, after.get("title", after.get("name", "")), after.get("text", ""), after["ownerId"], after["archived"])
        elif kind == "link":
            self.index(c, after["id"], "link", after.get("title") or after["url"], (after["url"] + "\n" + after.get("description", "")).strip(), after["ownerId"], after["archived"])
        elif kind == "chat":
            self.index(c, after["id"], "chat", after["title"], " ".join([after["chatId"], after["createdDate"], after["provider"], after["model"], *after["repos"]]).strip(), after["ownerId"], after["archived"])
        elif kind == "github":
            self.index(c, after["id"], "github", after.get("title") or after.get("reference") or after["url"], f"{after['kind']} {after.get('reference', '')} {after['url']}\n{after.get('description', '')}".strip(), after["ownerId"], after["archived"])
        elif kind == "question":
            self.index(c, after["id"], "question", after["title"], (after["details"] + "\n" + after["answer"]).strip(), after["roadmapId"], after["archived"])
        elif kind == "decision":
            self.index(c, after["id"], "decision", after["title"], after["rationale"], after["roadmapId"], after["archived"])
        elif kind == "attachment":
            # Proposal evidence lands here: unapproved files never appear in global search.
            search.remove(c, after["id"])
        event(c, actor, action, p["id"], before, after)
        return after

    @staticmethod
    def _github_chat_id(c: sqlite3.Connection, owner_type: str, owner_id: str, chat_ref: str) -> str:
        """Resolve a GitHub reference's chat by record ID or external chatId on this record or its linked roadmap/items."""
        ref = chat_ref.strip()
        if not ref:
            raise AppError(422, "validation", "Agent chat does not belong to this record")
        row = c.execute(
            """SELECT id FROM agent_chats
               WHERE owner_type=? AND owner_id=? AND (id=? OR chat_id=?)
               ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, archived ASC, created_at DESC, id DESC""",
            (owner_type, owner_id, ref, ref, ref),
        ).fetchone()
        if row is not None:
            return row["id"]
        if owner_type == "item":
            row = c.execute(
                """SELECT ac.id FROM agent_chats ac
                   JOIN work_items w ON w.id=?
                   LEFT JOIN steps s ON s.id=w.step_id
                   WHERE ac.owner_type='roadmap'
                     AND ac.owner_id=COALESCE(s.roadmap_id, w.roadmap_id)
                     AND (ac.id=? OR ac.chat_id=?)
                   ORDER BY CASE WHEN ac.id=? THEN 0 ELSE 1 END, ac.archived ASC, ac.created_at DESC, ac.id DESC""",
                (owner_id, ref, ref, ref),
            ).fetchone()
        elif owner_type == "roadmap":
            row = c.execute(
                """SELECT ac.id FROM agent_chats ac
                   JOIN work_items w ON ac.owner_type='item' AND ac.owner_id=w.id
                   LEFT JOIN steps s ON s.id=w.step_id
                   WHERE COALESCE(s.roadmap_id, w.roadmap_id)=?
                     AND (ac.id=? OR ac.chat_id=?)
                   ORDER BY CASE WHEN ac.id=? THEN 0 ELSE 1 END, ac.archived ASC, ac.created_at DESC, ac.id DESC""",
                (owner_id, ref, ref, ref),
            ).fetchone()
        if row is not None:
            return row["id"]
        raise AppError(422, "validation", "Agent chat does not belong to this record")

    @staticmethod
    def _chat_step_ids(c: sqlite3.Connection, chat: dict, kept: builtins.list[str]) -> builtins.list[str]:
        """Validate a chat's complete step assignment; a retired step may stay but not be added."""
        step_ids = chat["stepIds"]
        if not isinstance(step_ids, list) or len(step_ids) > 500 or not all(isinstance(s, str) for s in step_ids):
            raise AppError(422, "validation", "stepIds must be a list of at most 500 step ids")
        step_ids = list(dict.fromkeys(step_ids))
        if step_ids and chat["ownerType"] != "roadmap":
            raise AppError(422, "validation", "Only a roadmap's agent chats can be assigned to steps")
        for step_id in step_ids:
            row = c.execute("SELECT roadmap_id,retired FROM steps WHERE id=?", (step_id,)).fetchone()
            if row is None or row["roadmap_id"] != chat["ownerId"]:
                raise AppError(422, "validation", "Assigned steps must belong to the chat's roadmap")
            if row["retired"] and step_id not in kept:
                raise AppError(409, "conflict", "Retired steps cannot take on agent chats")
        return step_ids

    @staticmethod
    def _assign_steps(c: sqlite3.Connection, actor: dict, chat: dict,
                      previous: builtins.list[str], assigned: builtins.list[str]) -> None:
        # Each step's own history records which chats took it on or let it go.
        summary = {k: chat[k] for k in ("id", "chatId", "title", "provider", "model")}
        for step_id in previous:
            if step_id not in assigned:
                c.execute("DELETE FROM chat_steps WHERE chat_id=? AND step_id=?", (chat["id"], step_id))
                event(c, actor, "chat.unassign", step_id, summary, None)
        stamp = now()
        for step_id in assigned:
            if step_id not in previous:
                c.execute("INSERT INTO chat_steps(chat_id,step_id,created_at) VALUES(?,?,?)", (chat["id"], step_id, stamp))
                event(c, actor, "chat.assign", step_id, None, summary)

    def upload(self, c: sqlite3.Connection, actor: dict, owner_type: str, owner_id: str,
               name: str, mime: str, content: bytes) -> dict:
        self.owner(c, owner_type, owner_id, actor, files=True)
        if owner_type == "repository":
            from .coordination import repository_takes_files

            repository_takes_files(c, owner_id)
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
        c.execute("INSERT INTO attachments(id,owner_type,owner_id,name,mime,size,sha256,content,archived,revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (identity, owner_type, owner_id, name, mime, len(content), hashlib.sha256(content).hexdigest(), content, 0, 1, now()))
        after = support_record(c.execute("SELECT * FROM attachments WHERE id=?", (identity,)).fetchone())
        # Unapproved evidence must never appear in canonical global search.
        if owner_type != "proposal":
            self.index(c, identity, "attachment", name, "", owner_id, False)
        event(c, actor, "attachment.create", identity, None, after)
        return after

    # Every write to the search index goes through search.py; this name serves this module's callers.
    index = staticmethod(search.put)

    def refresh_plans(self, c: sqlite3.Connection, roadmap_id: str | None = None) -> None:
        search.remove_plans(c, roadmap_id)
        clause = " WHERE id=?" if roadmap_id else ""
        args = (roadmap_id,) if roadmap_id else ()
        # The delete above removed every row rewritten below, so each insert skips its own scan.
        for r in c.execute("SELECT * FROM roadmaps" + clause, args).fetchall():
            keys = set(r.keys())
            self.index(c, r["id"], "roadmap", r["title"], r["overview"] if "overview" in keys else "", r["id"], bool(r["archived"]),
                       replace=False)
        for r in c.execute("SELECT * FROM steps" + (" WHERE roadmap_id=?" if roadmap_id else ""), args).fetchall():
            keys = set(r.keys())
            parent = c.execute("SELECT archived FROM roadmaps WHERE id=?", (r["roadmap_id"],)).fetchone()
            self.index(c, r["id"], "step", r["title"], r["description"], r["roadmap_id"], bool(parent[0] or (r["retired"] if "retired" in keys else False)),
                       replace=False)

    def search(self, query: str, archived: bool = False, limit: int = 50, offset: int = 0) -> dict:
        match = search.prefix_match(query)
        if match is None:
            return {"items": [], "total": 0}
        # Resolve ownership at query time so archiving a parent also hides its notes/files
        # without destroying each supporting record's independent archive flag.
        source = """FROM search_index s
            LEFT JOIN notes n ON s.type='note' AND n.id=s.entity_id
            LEFT JOIN attachments a ON s.type='attachment' AND a.id=s.entity_id
            LEFT JOIN web_links wl ON s.type='link' AND wl.id=s.entity_id
            LEFT JOIN agent_chats ac ON s.type='chat' AND ac.id=s.entity_id
            LEFT JOIN github_refs gr ON s.type='github' AND gr.id=s.entity_id
            LEFT JOIN roadmap_questions rq ON s.type='question' AND rq.id=s.entity_id
            LEFT JOIN roadmap_decisions rd ON s.type='decision' AND rd.id=s.entity_id
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
                WHEN rq.id IS NOT NULL THEN rq.roadmap_id WHEN rd.id IS NOT NULL THEN rd.roadmap_id
                ELSE st.roadmap_id END
            LEFT JOIN people p ON p.id=CASE WHEN s.type='person' THEN s.entity_id END
            LEFT JOIN repositories rp ON a.owner_type='repository' AND rp.id=a.owner_id
            LEFT JOIN discussions ds ON s.type IN ('discussion','discussion_message') AND ds.id=s.owner_id
            LEFT JOIN tickets tk ON s.type IN ('ticket','ticket_comment') AND tk.id=s.owner_id
            WHERE search_index MATCH ? AND
             (CAST(s.archived AS INTEGER)=1 OR coalesce(w.archived,0)=1
              OR coalesce(st.retired,0)=1 OR coalesce(rm.archived,0)=1 OR coalesce(p.archived,0)=1 OR coalesce(ds.archived,0)=1
              OR coalesce(tk.archived,0)=1 OR coalesce(rp.archived,0)=1)=?"""
        with self.db.read() as c:
            # A resource is its own page and carries its own archive flag in the index.
            rows = c.execute("""SELECT s.entity_id,s.type,s.title,s.body,s.owner_id,
                             CASE WHEN s.type='resource' THEN 'resources' WHEN ds.id IS NOT NULL THEN 'discussions' WHEN tk.id IS NOT NULL THEN 'tickets' WHEN w.id IS NOT NULL THEN 'work' WHEN p.id IS NOT NULL THEN 'people' WHEN rp.id IS NOT NULL THEN 'repositories' ELSE 'roadmaps' END AS route_type,
                             CASE WHEN s.type='resource' THEN s.entity_id ELSE coalesce(ds.id,tk.id,w.id,p.id,rp.id,rm.id) END AS route_id """ + source + " ORDER BY rank LIMIT ? OFFSET ?", (match, int(archived), limit, offset)).fetchall()
            total = c.execute("SELECT count(*) " + source, (match, int(archived))).fetchone()[0]
            return {"items": [{"id": r[0], "type": r[1], "title": r[2], "snippet": r[3][:220], "ownerId": r[4],
                               "routeType": r["route_type"], "routeId": r["route_id"]} for r in rows], "total": total}
