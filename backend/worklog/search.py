"""The one module that writes the search index.

search_index is an FTS5 table whose entity_id, type, owner_id and archived columns are
UNINDEXED, so any statement that filters on them reads the whole index. Every write goes
through here, so the index can change how it keys rows in one place.
backend/tests/test_search_writes.py fails when another module writes the table directly.
Readers still query it with MATCH where they are.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence


def put(c: sqlite3.Connection, identity: str, kind: str, title: str, body: str,
        owner: str, archived: bool = False, *, replace: bool = True) -> None:
    """Index one record. Pass replace=False only when the record has no row: it is new,
    or the caller has just deleted the rows it rewrites."""
    if replace:
        remove(c, identity)
    c.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,?,?,?,?,?)",
              (identity, kind, title, body, owner, int(archived)))


def remove(c: sqlite3.Connection, identity: str) -> None:
    c.execute("DELETE FROM search_index WHERE entity_id=?", (identity,))


def remove_plans(c: sqlite3.Connection, roadmap_id: str | None = None) -> None:
    """The rows of one roadmap and its steps, or of every roadmap and step."""
    if roadmap_id:
        c.execute("DELETE FROM search_index WHERE entity_id=? OR (type='step' AND owner_id=?)", (roadmap_id, roadmap_id))
    else:
        c.execute("DELETE FROM search_index WHERE type IN ('roadmap','step')")


def set_archived(c: sqlite3.Connection, kinds: Sequence[str], owner: str, archived: bool) -> None:
    """Archive or recover every row of these kinds under one owner."""
    c.execute(f"UPDATE search_index SET archived=? WHERE type IN ({','.join('?' * len(kinds))}) AND owner_id=?",
              (int(archived), *kinds, owner))


def retitle(c: sqlite3.Connection, kind: str, owner: str, title: str) -> None:
    """Give every row of this kind under one owner a new title."""
    c.execute("UPDATE search_index SET title=? WHERE type=? AND owner_id=?", (title, kind, owner))


def move_repository_files(c: sqlite3.Connection, source_id: str, survivor_id: str) -> None:
    """A merged repository's files, archived ones included, are found under the survivor."""
    c.execute("UPDATE search_index SET owner_id=? WHERE type='attachment' AND entity_id IN "
              "(SELECT id FROM attachments WHERE owner_type='repository' AND owner_id=?)", (survivor_id, source_id))
