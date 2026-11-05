"""The one module that writes the search index.

search_index is an FTS5 table whose entity_id, type, owner_id and archived columns are
UNINDEXED, so a statement that filters on them reads the whole index. search_keys (v11)
maps each record to its row's rowid, with the record's type and owner indexed, so every
write here finds its rows through an index and changes them by rowid. Keys and rows stay
in step only because every write goes through this module:
backend/tests/test_search_writes.py fails when another module writes either table.
Readers still query search_index with MATCH where they are, with the expression prefix_match
builds from what the person typed.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence

CHUNK = 500
# The most words one search matches; the rest of a long query is ignored.
MAX_TERMS = 30


def prefix_match(query: str) -> str | None:
    """The FTS5 MATCH expression for a search box: every word, each as a prefix, all required.

    Punctuation only separates words, so no FTS5 syntax a person types gets through, and None
    means nothing searchable is left. Each caller keeps its own limit on the query's length.
    """
    words = re.findall(r"\w+", query, re.UNICODE)[:MAX_TERMS]
    if not words:
        return None
    return " AND ".join('"' + word.replace('"', '""') + '"*' for word in words)


def put(c: sqlite3.Connection, identity: str, kind: str, title: str, body: str,
        owner: str, archived: bool = False, *, replace: bool = True) -> None:
    """Index one record. Pass replace=False only when the record has no row: it is new,
    or the caller has just removed the rows it rewrites."""
    if replace:
        remove(c, identity)
    doc = c.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,?,?,?,?,?)",
                    (identity, kind, title, body, owner, int(archived))).lastrowid
    c.execute("INSERT INTO search_keys(entity_id,doc,type,owner_id) VALUES(?,?,?,?)", (identity, doc, kind, owner))


def remove(c: sqlite3.Connection, identity: str) -> None:
    _delete(c, _docs(c, "entity_id=?", (identity,)))


def remove_plans(c: sqlite3.Connection, roadmap_id: str | None = None) -> None:
    """The rows of one roadmap and its steps, or of every roadmap and step."""
    if roadmap_id:
        _delete(c, _docs(c, "entity_id=? OR (owner_id=? AND type='step')", (roadmap_id, roadmap_id)))
    else:
        _delete(c, _docs(c, "type IN ('roadmap','step')", ()))


def set_archived(c: sqlite3.Connection, kinds: Sequence[str], owner: str, archived: bool) -> None:
    """Archive or recover every row of these kinds under one owner."""
    docs = _docs(c, f"owner_id=? AND type IN ({','.join('?' * len(kinds))})", (owner, *kinds))
    _update(c, "archived=?", (int(archived),), docs)


def retitle(c: sqlite3.Connection, kind: str, owner: str, title: str) -> None:
    """Give every row of this kind under one owner a new title."""
    _update(c, "title=?", (title,), _docs(c, "owner_id=? AND type=?", (owner, kind)))


def move_repository_files(c: sqlite3.Connection, source_id: str, survivor_id: str) -> None:
    """A merged repository's files, archived ones included, are found under the survivor."""
    docs = _docs(c, "type='attachment' AND entity_id IN "
                    "(SELECT id FROM attachments WHERE owner_type='repository' AND owner_id=?)", (source_id,))
    _update(c, "owner_id=?", (survivor_id,), docs)
    for chunk in _chunks(docs):
        c.execute(f"UPDATE search_keys SET owner_id=? WHERE doc IN ({_marks(chunk)})", (survivor_id, *chunk))


def _docs(c: sqlite3.Connection, where: str, args: Sequence[object]) -> list[int]:
    return [row[0] for row in c.execute("SELECT doc FROM search_keys WHERE " + where, tuple(args))]


def _delete(c: sqlite3.Connection, docs: list[int]) -> None:
    for chunk in _chunks(docs):
        c.execute(f"DELETE FROM search_index WHERE rowid IN ({_marks(chunk)})", chunk)
        c.execute(f"DELETE FROM search_keys WHERE doc IN ({_marks(chunk)})", chunk)


def _update(c: sqlite3.Connection, assignment: str, values: tuple[object, ...], docs: list[int]) -> None:
    for chunk in _chunks(docs):
        c.execute(f"UPDATE search_index SET {assignment} WHERE rowid IN ({_marks(chunk)})", (*values, *chunk))


def _chunks(docs: list[int]) -> list[list[int]]:
    return [docs[start:start + CHUNK] for start in range(0, len(docs), CHUNK)]


def _marks(chunk: list[int]) -> str:
    return ",".join("?" * len(chunk))
