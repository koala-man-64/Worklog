"""The work items and roadmaps each change of an applied proposal was made to (schema v16).

Approval records them in proposal_targets, and the v16 upgrade fills them in for the proposals
approved before it by the same rules, so a record's proposal history reads the same either way.
A change counts toward:
- the work item or roadmap it creates, changes, archives or recovers;
- the work item or roadmap that holds the record it creates or changes: a work item's tasks,
  scratchpad, links, files, agent chats, GitHub references, day entries and people; a roadmap's
  steps and their progress, notes and files, its questions, decisions, notes, links, files, agent
  chats, GitHub references and people;
- the roadmap it puts a work item or a sub-roadmap on.
A person or a repository is neither, so a change to one alone counts toward nothing here.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# The supporting records operations name by id, by the prefix of their actions: each one's table,
# and the columns that name its owner's type and id ('item' and 'roadmap' are fixed owner types).
_HELD = {
    "task": ("tasks", "'item'", "item_id"),
    "reference": ("file_references", "'item'", "item_id"),
    "note": ("notes", "owner_type", "owner_id"),
    "attachment": ("attachments", "owner_type", "owner_id"),
    "link": ("web_links", "owner_type", "owner_id"),
    "chat": ("agent_chats", "owner_type", "owner_id"),
    "github": ("github_refs", "owner_type", "owner_id"),
    "question": ("roadmap_questions", "'roadmap'", "roadmap_id"),
    "decision": ("roadmap_decisions", "'roadmap'", "roadmap_id"),
}
INSERT = ("INSERT OR IGNORE INTO proposal_targets(proposal_id,operation,action,target_type,target_id) "
          "VALUES(?,?,?,?,?)")


def _named(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _step_roadmap(conn: sqlite3.Connection, step_id: Any) -> str | None:
    step = _named(step_id)
    row = conn.execute("SELECT roadmap_id FROM steps WHERE id=?", (step,)).fetchone() if step else None
    return row[0] if row else None


def _holder(conn: sqlite3.Connection, owner_type: Any, owner_id: Any) -> tuple[str, str] | None:
    """The work item or roadmap a supporting record's owner is: a step's roadmap for a step."""
    identity = _named(owner_id)
    if identity is None:
        return None
    if owner_type in {"item", "roadmap"}:
        return owner_type, identity
    if owner_type == "step":
        roadmap = _step_roadmap(conn, identity)
        return ("roadmap", roadmap) if roadmap else None
    return None  # a repository's file, or a proposal's own evidence


def targets(conn: sqlite3.Connection, action: Any, payload: Any, created: str | None = None) -> set[tuple[str, str]]:
    """The ("item" or "roadmap", id) of each record one applied change was made to.

    `created` is the id Worklog gave the work item or roadmap an item.create or roadmap.create
    made, which its payload cannot name. Anything malformed counts toward nothing: approval
    refused such a change, so it was never applied.
    """
    if not isinstance(action, str) or not isinstance(payload, dict):
        return set()
    kind, _, verb = action.partition(".")
    found: set[tuple[str, str]] = set()

    def add(target_type: str, identity: Any) -> None:
        if _named(identity):
            found.add((target_type, identity))

    if kind == "item":
        add("item", created if verb == "create" else payload.get("id"))
        add("roadmap", payload.get("roadmapId"))
        add("roadmap", _step_roadmap(conn, payload.get("stepId")))
    elif kind == "roadmap":
        add("roadmap", created if verb == "create" else payload.get("id"))
        if verb in {"create", "move"}:
            add("roadmap", payload.get("parentId"))
    elif action == "progress.set":
        add("roadmap", _step_roadmap(conn, payload.get("id") or payload.get("stepId")))
    elif action == "entry.upsert":
        add("item", payload.get("itemId"))
    elif action in {"person.link", "person.unlink"}:
        holder = _holder(conn, payload.get("targetType"), payload.get("targetId"))
        if holder:
            add(*holder)
    elif kind in _HELD:
        table, type_column, id_column = _HELD[kind]
        if verb == "create":
            # A new record names its owner; tasks and paths name a work item, questions and
            # decisions a roadmap, and the rest an owner type and id.
            owner = (("item", payload.get("itemId")) if kind in {"task", "reference"}
                     else ("roadmap", payload.get("roadmapId")) if kind in {"question", "decision"}
                     else (payload.get("ownerType"), payload.get("ownerId")))
        else:
            identity = _named(payload.get("id"))
            row = conn.execute(f"SELECT {type_column},{id_column} FROM {table} WHERE id=?",
                               (identity,)).fetchone() if identity else None
            owner = (row[0], row[1]) if row else (None, None)
        holder = _holder(conn, *owner)
        if holder:
            add(*holder)
    return found


def record(conn: sqlite3.Connection, proposal_id: str, number: int, action: Any, payload: Any,
           created: str | None = None) -> None:
    """Record what change `number` of an applied proposal was made to."""
    conn.executemany(INSERT, [(proposal_id, number, action, target_type, identity)
                              for target_type, identity in sorted(targets(conn, action, payload, created))])
