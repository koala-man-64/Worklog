"""People: who the work involves, what each is tied to, and the commands that change them.
"""

from __future__ import annotations

import builtins
import sqlite3
import unicodedata
from typing import Any

from . import search
from .common import AppError, event, now, read_events, require_revision, text, uid
from .coordination import repository_record, repository_takes_people
from .db import Database
from .records import (
    PERSON_FIELDS,
    PERSON_LIMITS,
    PERSON_TARGETS,
    check_owner,
    get_person,
    item_record,
    person_record,
)
from .repository_links import linked_repositories

# Besides whitespace and control characters: what a mailto: link would read as more than an address.
EMAIL_FORBIDDEN = frozenset('<>()[]\\,;:"?#/%')
# The single-line person fields, and what breaks a line: control characters and line or paragraph separators.
PERSON_LINES = ("name", "title", "team", "chat", "location")
LINE_BREAKS = frozenset({"Cc", "Zl", "Zp"})


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


class People:
    def __init__(self, db: Database):
        self.db = db

    def list(self, *, archived: bool = False, q: str = "") -> dict:
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

    def detail(self, person_id: str) -> dict:
        with self.db.read() as c:
            result = person_record(get_person(c, person_id))
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

    @staticmethod
    def _person_target(p: dict) -> tuple[str, str, str]:
        person_id = text(p.get("personId", ""), "personId", required=True, limit=200)
        target_type = p.get("targetType", "")
        if not isinstance(target_type, str) or target_type not in PERSON_TARGETS:
            raise AppError(422, "validation", "Invalid targetType for person link")
        return person_id, target_type, text(p.get("targetId", ""), "targetId", required=True, limit=200)

    def apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        if action == "person.link":
            person_id, target_type, target_id = self._person_target(p)
            relationship = text(p.get("relationship", ""), "relationship", limit=200).strip()
            person_row = get_person(c, person_id, active=True)
            existing = c.execute(
                "SELECT * FROM person_links WHERE person_id=? AND target_type=? AND target_id=?",
                (person_id, target_type, target_id),
            ).fetchone()
            if target_type == "repository":
                # A new tie needs an active record, as other links do; an existing one keeps its role editable.
                repository_takes_people(c, target_id, new=existing is None)
            else:
                check_owner(c, target_type, target_id)
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
        row = None if creating else get_person(c, p.get("id", ""))
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
        search.put(c, val["id"], "person", val["name"], body, val["id"], val["archived"])
        return val
