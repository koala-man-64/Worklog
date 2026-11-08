"""Read-only checks of what the schema does not enforce: `worklog doctor` (consolidation step 4.4, B-09).

Startup and restore check SQLite's integrity, foreign keys and the exact schema. Some references the
schema cannot state: a supporting record names its owner by type and id; ties, tags, reminders,
releases, person links and applied proposals name records of many kinds; a step names its parent;
and search_keys names the search_index row of each record, with nothing in the schema tying them
(from the review of #96).

The doctor reads a copy of the database, made as a backup is made, so it can run while Worklog does
and changes nothing. It reports each check with how many problems it found and the first EXAMPLES:

- integrity: SQLite's integrity_check and foreign_key_check;
- references: a record that names a type its table never takes, or a record that is gone;
- steps: a step whose parent is gone or on another roadmap;
- trackedChats: one chat tracked twice on the same record;
- searchIndex: FTS5's own integrity check;
- searchKeys: a key with no row, a key and its row that disagree, a row with no key, a key of a type
  the index never holds, and a key for a record that is gone.
"""

from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from .migrations import SCHEMA_VERSION
from .recovery import _readonly

EXAMPLES = 5
# The table of each kind of record that other records name by type and id.
TABLES = {"item": "work_items", "roadmap": "roadmaps", "step": "steps", "repository": "repositories",
          "person": "people", "resource": "resources", "discussion": "discussions", "proposal": "proposals",
          "ticket": "tickets", "agent": "communication_agents", "question": "roadmap_questions",
          "decision": "roadmap_decisions", "release": "releases", "github": "github_refs", "chat": "agent_chats"}
# What names a record by type and id, with the types each takes: its CHECK's, or for notes and
# attachments, which have none, those Worklog writes (records.SUPPORT_OWNERS and FILE_OWNERS).
REFERENCES = [
    ("notes", "owner_type", "owner_id", ("item", "roadmap", "step")),
    ("attachments", "owner_type", "owner_id", ("item", "roadmap", "step", "proposal", "repository")),
    ("web_links", "owner_type", "owner_id", ("item", "roadmap")),
    ("agent_chats", "owner_type", "owner_id", ("item", "roadmap")),
    ("github_refs", "owner_type", "owner_id", ("item", "roadmap")),
    ("proposal_targets", "target_type", "target_id", ("item", "roadmap")),
    ("person_links", "target_type", "target_id", ("item", "roadmap", "repository")),
    ("discussion_contexts", "context_type", "context_id",
     ("item", "roadmap", "step", "repository", "ticket", "release", "person", "resource", "question", "decision")),
    ("discussion_roles", "context_type", "context_id", ("item", "roadmap", "step")),
    ("agent_links", "target_type", "target_id",
     ("item", "roadmap", "step", "repository", "person", "resource", "discussion", "proposal", "ticket", "agent",
      "question", "decision")),
    ("release_contents", "target_type", "target_id", ("item", "roadmap", "step", "github")),
    ("reminders", "target_type", "target_id", ("item", "ticket", "roadmap", "person", "repository", "chat")),
]
# The table of each kind of record the search index holds (search.SearchType).
SEARCHED = {"item": "work_items", "step": "steps", "roadmap": "roadmaps", "note": "notes", "attachment": "attachments",
            "link": "web_links", "chat": "agent_chats", "github": "github_refs", "question": "roadmap_questions",
            "decision": "roadmap_decisions", "person": "people", "ticket": "tickets", "ticket_comment": "ticket_comments",
            "discussion": "discussions", "discussion_message": "discussion_messages", "resource": "resources",
            "release": "releases", "release_check": "release_checks", "reminder": "reminders"}


def doctor_database(db_path: Path | str) -> dict[str, Any]:
    """Check a copy of the database at db_path, as the module says; change nothing."""
    source = Path(db_path)
    if not source.is_file():
        raise ValueError("Database file does not exist")
    with tempfile.TemporaryDirectory(prefix="worklog-doctor-") as directory:
        copy = Path(directory) / "copy.sqlite3"
        # The backup API reads one consistent snapshot, as a backup does. Unlike a backup, nothing
        # validates the copy: the doctor reports what a validation would refuse.
        with closing(_readonly(source)) as src, closing(sqlite3.connect(copy)) as dest:
            src.backup(dest)
        with closing(sqlite3.connect(copy, isolation_level=None)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise ValueError(f"The database is at schema version {version}: start Worklog once to upgrade it "
                                 f"to version {SCHEMA_VERSION}, then check it")
            checks = {"integrity": _integrity(conn), "references": _references(conn), "steps": _steps(conn),
                      "trackedChats": _tracked_chats(conn), "searchIndex": _search_index(conn),
                      "searchKeys": _search_keys(conn)}
    return {"ok": not any(check["problems"] for check in checks.values()), "checks": checks}


class _Found:
    """A check's problems: how many, and the first EXAMPLES of them."""

    def __init__(self) -> None:
        self.count = 0
        self.examples: list[Any] = []

    def add(self, example: Any, count: int = 1) -> None:
        self.count += count
        if len(self.examples) < EXAMPLES:
            self.examples.append(example)

    def report(self) -> dict[str, Any]:
        return {"problems": self.count, "examples": self.examples}


def _integrity(conn: sqlite3.Connection) -> dict[str, Any]:
    found = _Found()
    messages = [row[0] for row in conn.execute("PRAGMA integrity_check")]
    if messages != ["ok"]:
        for message in messages:
            found.add(message)
    for table, rowid, parent, _ in conn.execute("PRAGMA foreign_key_check"):
        found.add({"table": table, "rowid": rowid, "references": parent})
    return found.report()


def _references(conn: sqlite3.Connection) -> dict[str, Any]:
    found = _Found()
    for table, kind_column, id_column, kinds in REFERENCES:
        marks = ",".join("?" for _ in kinds)
        for kind, count in conn.execute(
                f"SELECT {kind_column},count(*) FROM {table} WHERE {kind_column} IS NOT NULL "
                f"AND {kind_column} NOT IN ({marks}) GROUP BY {kind_column} ORDER BY {kind_column}", kinds):
            found.add({"table": table, "type": kind, "rows": count, "problem": "a type this table never takes"}, count)
        for kind in kinds:
            for (identity,) in conn.execute(
                    f"SELECT {id_column} FROM {table} r WHERE r.{kind_column}=? AND r.{id_column} IS NOT NULL "
                    f"AND NOT EXISTS (SELECT 1 FROM {TABLES[kind]} o WHERE o.id=r.{id_column}) ORDER BY {id_column}",
                    (kind,)):
                found.add({"table": table, "type": kind, "id": identity, "problem": "the record it names is gone"})
    return found.report()


def _steps(conn: sqlite3.Connection) -> dict[str, Any]:
    found = _Found()
    for step, parent in conn.execute(
            "SELECT s.id,s.parent_id FROM steps s LEFT JOIN steps p ON p.id=s.parent_id "
            "WHERE s.parent_id IS NOT NULL AND (p.id IS NULL OR p.roadmap_id<>s.roadmap_id) ORDER BY s.id"):
        found.add({"step": step, "parent": parent, "problem": "its parent is gone or on another roadmap"})
    return found.report()


def _tracked_chats(conn: sqlite3.Connection) -> dict[str, Any]:
    found = _Found()
    for kind, owner, chat, times in conn.execute(
            "SELECT owner_type,owner_id,chat_id,count(*) FROM agent_chats GROUP BY owner_type,owner_id,chat_id "
            "HAVING count(*)>1 ORDER BY owner_type,owner_id,chat_id"):
        found.add({"ownerType": kind, "ownerId": owner, "chatId": chat, "times": times})
    return found.report()


def _search_index(conn: sqlite3.Connection) -> dict[str, Any]:
    found = _Found()
    try:
        # Written as an INSERT, FTS5's own check needs a writable connection: the copy's.
        conn.execute("INSERT INTO search_index(search_index) VALUES('integrity-check')")
    except sqlite3.DatabaseError as exc:
        found.add(str(exc))
    return found.report()


def _search_keys(conn: sqlite3.Connection) -> dict[str, Any]:
    found = _Found()
    for (entity,) in conn.execute("SELECT k.entity_id FROM search_keys k LEFT JOIN search_index s ON s.rowid=k.doc "
                                  "WHERE s.rowid IS NULL ORDER BY k.entity_id"):
        found.add({"entityId": entity, "problem": "its row is gone"})
    for (entity,) in conn.execute("SELECT k.entity_id FROM search_keys k JOIN search_index s ON s.rowid=k.doc "
                                  "WHERE s.entity_id IS NOT k.entity_id OR s.type IS NOT k.type "
                                  "OR s.owner_id IS NOT k.owner_id ORDER BY k.entity_id"):
        found.add({"entityId": entity, "problem": "its row names another record, type or owner"})
    for doc, entity in conn.execute("SELECT s.rowid,s.entity_id FROM search_index s LEFT JOIN search_keys k "
                                    "ON k.doc=s.rowid WHERE k.doc IS NULL ORDER BY s.rowid"):
        found.add({"row": doc, "entityId": entity, "problem": "a row with no key"})
    marks = ",".join("?" for _ in SEARCHED)
    for entity, kind in conn.execute(f"SELECT entity_id,type FROM search_keys WHERE type NOT IN ({marks}) "
                                     "ORDER BY entity_id", tuple(SEARCHED)):
        found.add({"entityId": entity, "type": kind, "problem": "a type the index never holds"})
    for kind, table in SEARCHED.items():
        for (entity,) in conn.execute(f"SELECT k.entity_id FROM search_keys k WHERE k.type=? AND NOT EXISTS "
                                      f"(SELECT 1 FROM {table} o WHERE o.id=k.entity_id) ORDER BY k.entity_id", (kind,)):
            found.add({"entityId": entity, "type": kind, "problem": "its record is gone"})
    return found.report()
