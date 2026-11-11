"""The kinds of record other records name, and the query that summarizes each.

Discussion tags (discussions.py, v22) name records of many kinds by type and id, and roles and the
overlap check name some of the same. Each kind's query gives its id, title and archived state, and
what a page needs to link to it: a step, question or decision opens on its roadmap, and a ticket shows
its key. A step that is retired and a repository that is merged count as archived. Each user of these
lists its own kinds in its own order. Agent ties named three more kinds (discussions, proposals and
agents) until v29 dropped them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from .common import rows_in

SUMMARIES: dict[str, str] = {
    "item": "SELECT id,title,archived,status FROM work_items WHERE id IN ({})",
    "roadmap": "SELECT id,title,archived FROM roadmaps WHERE id IN ({})",
    "step": "SELECT s.id,s.title,s.retired AS archived,s.status,s.roadmap_id AS roadmapId,r.title AS roadmapTitle "
            "FROM steps s JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id IN ({})",
    "repository": "SELECT id,name AS title,(archived=1 OR merged_into IS NOT NULL) AS archived,merged_into AS mergedInto "
                  "FROM repositories WHERE id IN ({})",
    "person": "SELECT id,name AS title,archived FROM people WHERE id IN ({})",
    "resource": "SELECT id,title,archived FROM resources WHERE id IN ({})",
    "ticket": "SELECT id,title,archived,status,number FROM tickets WHERE id IN ({})",
    "question": "SELECT q.id,q.title,q.archived,q.status,q.roadmap_id AS roadmapId,r.title AS roadmapTitle "
                "FROM roadmap_questions q JOIN roadmaps r ON r.id=q.roadmap_id WHERE q.id IN ({})",
    "decision": "SELECT d.id,d.title,d.archived,d.roadmap_id AS roadmapId,r.title AS roadmapTitle "
                "FROM roadmap_decisions d JOIN roadmaps r ON r.id=d.roadmap_id WHERE d.id IN ({})",
}


def rows(c: sqlite3.Connection, kind: str, ids: Iterable[str]) -> list[sqlite3.Row]:
    """The rows of the named records of one kind that exist. The kind is one of SUMMARIES' keys,
    never a caller's text."""
    return rows_in(c, SUMMARIES[kind], ids)


def summary(row: sqlite3.Row) -> dict:
    """A summary row as a record shows it, archived a boolean."""
    record = {key: row[key] for key in row.keys()}
    record["archived"] = bool(record["archived"])
    return record
