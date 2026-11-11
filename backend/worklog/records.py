"""How each kind of record reads and how it is found: the shapes and lookups that work items,
people and supporting records share, and the one map from each kind of owner to its table.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
from typing import Any

from .common import NOTE_LIMIT, AppError


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
            "roadmap_id": "roadmapId", "decided_date": "decidedDate",
            "created_at": "createdAt", "updated_at": "updatedAt"}
    # A file's bytes are read on their own.
    record = {keys.get(k, k): bool(v) if k == "archived" else v
              for k, v in dict(r).items() if k != "content"}
    if "repos" in record:
        record["repos"] = json.loads(r["repos"])
    return record


# A person's own fields, each named as its column, and their limits in UTF-8 bytes. The
# description is a paragraph and the notes Markdown; the others are single lines, trimmed.
PERSON_LIMITS = {"name": 500, "title": 500, "team": 500, "description": 10_000, "email": 320,
                 "phone": 100, "chat": 500, "location": 500, "notes": NOTE_LIMIT}
PERSON_FIELDS = tuple(PERSON_LIMITS)
# What a person can be tied to, by the table each lives in.
PERSON_TARGETS = {"item": "work_items", "roadmap": "roadmaps", "repository": "repositories"}


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


# The records that hold notes and files, by the table each lives in.
SUPPORT_OWNERS = {"item": "work_items", "roadmap": "roadmaps", "step": "steps", "proposal": "proposals"}
# A repository holds attached files and nothing else: no notes, links or chats of its own.
FILE_OWNERS = {**SUPPORT_OWNERS, "repository": "repositories"}


def check_owner(conn: sqlite3.Connection, owner_type: str, owner_id: str, *, files: bool = False) -> None:
    """Check that a supporting record's owner exists; `files` also admits repositories,
    which hold attached files only."""
    table = (FILE_OWNERS if files else SUPPORT_OWNERS).get(owner_type)
    if not table:
        raise AppError(422, "validation", "Invalid supporting-record owner")
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (owner_id,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Owner does not exist")


def get_item(conn: sqlite3.Connection, item_id: str, *, active: bool = False) -> Any:
    row = conn.execute("SELECT * FROM work_items WHERE id=?", (item_id,)).fetchone()
    if row is None or (active and row["archived"]):
        raise AppError(404, "not_found", "Work item does not exist or is archived")
    return row


def get_person(conn: sqlite3.Connection, person_id: str, *, active: bool = False) -> Any:
    row = conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
    if row is None or (active and row["archived"]):
        raise AppError(404, "not_found", "Person does not exist or is archived")
    return row
