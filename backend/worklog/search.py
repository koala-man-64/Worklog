"""The search index: the one module that writes it, and global search, which reads all of it.

search_index is an FTS5 table whose entity_id, type, owner_id and archived columns are
UNINDEXED, so a statement that filters on them reads the whole index. search_keys (v11)
maps each record to its row's rowid, with the record's type and owner indexed, so every
write here finds its rows through an index and changes them by rowid. Keys and rows stay
in step only because every write goes through this module:
backend/tests/test_search_writes.py fails when another module writes either table.
The searches of one kind of record (work, people, tickets, resources, reminders, discussions) query
search_index with MATCH where they are, with the expression prefix_match builds from what
the person typed.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from typing import Literal

CHUNK = 500
# The most words one search matches; the rest of a long query is ignored.
MAX_TERMS = 30
# Every kind of record the index holds, which global search can be narrowed to.
SearchType = Literal["item", "step", "roadmap", "note", "attachment", "link", "chat", "github", "question",
                     "decision", "person", "ticket", "ticket_comment", "discussion", "discussion_message", "resource",
                     "release", "release_check", "reminder"]


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


def index_plans(c: sqlite3.Connection, roadmap_id: str | None = None) -> None:
    """Index one roadmap and its steps again, or every roadmap and step. A step counts as
    archived when it is retired or its roadmap is archived."""
    remove_plans(c, roadmap_id)
    clause = " WHERE id=?" if roadmap_id else ""
    args = (roadmap_id,) if roadmap_id else ()
    # The delete above removed every row rewritten below, so each insert skips its own scan.
    for r in c.execute("SELECT * FROM roadmaps" + clause, args).fetchall():
        keys = set(r.keys())
        put(c, r["id"], "roadmap", r["title"], r["overview"] if "overview" in keys else "", r["id"], bool(r["archived"]),
            replace=False)
    for r in c.execute("SELECT * FROM steps" + (" WHERE roadmap_id=?" if roadmap_id else ""), args).fetchall():
        keys = set(r.keys())
        parent = c.execute("SELECT archived FROM roadmaps WHERE id=?", (r["roadmap_id"],)).fetchone()
        put(c, r["id"], "step", r["title"], r["description"], r["roadmap_id"], bool(parent[0] or (r["retired"] if "retired" in keys else False)),
            replace=False)


def global_search(c: sqlite3.Connection, query: str, archived: bool = False, limit: int = 50, offset: int = 0,
                  types: Sequence[str] = ()) -> dict:
    """Every kind of record the header's search box finds, each with the page it opens on.

    A result is archived when it is, when the record it hangs off is, or when its step is retired.
    `types` narrows the results to those kinds of record, as a picker of one kind needs.
    """
    match = prefix_match(query)
    if match is None:
        return {"items": [], "total": 0}
    kinds = list(dict.fromkeys(types))
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
        LEFT JOIN releases rl ON s.type IN ('release','release_check') AND rl.id=s.owner_id
        WHERE search_index MATCH ? AND
         (CAST(s.archived AS INTEGER)=1 OR coalesce(w.archived,0)=1
          OR coalesce(st.retired,0)=1 OR coalesce(rm.archived,0)=1 OR coalesce(p.archived,0)=1 OR coalesce(ds.archived,0)=1
          OR coalesce(tk.archived,0)=1 OR coalesce(rp.archived,0)=1 OR coalesce(rl.archived,0)=1)=?"""
    if kinds:
        source += f" AND s.type IN ({','.join('?' * len(kinds))})"
    arguments = (match, int(archived), *kinds)
    # A resource or a reminder is its own page and carries its own archive flag in the index. A release's checks
    # open on the release, and are archived with it as well as on their own.
    rows = c.execute("""SELECT s.entity_id,s.type,s.title,s.body,s.owner_id,
                     CASE WHEN s.type='resource' THEN 'resources' WHEN s.type='reminder' THEN 'reminders' WHEN ds.id IS NOT NULL THEN 'discussions' WHEN tk.id IS NOT NULL THEN 'tickets' WHEN rl.id IS NOT NULL THEN 'releases' WHEN w.id IS NOT NULL THEN 'work' WHEN p.id IS NOT NULL THEN 'people' WHEN rp.id IS NOT NULL THEN 'repositories' ELSE 'roadmaps' END AS route_type,
                     CASE WHEN s.type IN ('resource','reminder') THEN s.entity_id ELSE coalesce(ds.id,tk.id,rl.id,w.id,p.id,rp.id,rm.id) END AS route_id """ + source + " ORDER BY rank LIMIT ? OFFSET ?", (*arguments, limit, offset)).fetchall()
    total = c.execute("SELECT count(*) " + source, arguments).fetchone()[0]
    return {"items": [{"id": r[0], "type": r[1], "title": r[2], "snippet": r[3][:220], "ownerId": r[4],
                       "routeType": r["route_type"], "routeId": r["route_id"]} for r in rows], "total": total}


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
