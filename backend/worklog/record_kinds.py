"""The kinds of record other records name, and the query that summarizes each.

Agent ties (agent_links.py, v17) and discussion tags (discussions.py, v21) both name records of many
kinds by type and id. Each kind's query gives its id, title and archived state, and what a page needs
to link to it: a step, question or decision opens on its roadmap, and a ticket shows its key. A step
that is retired, a repository that is merged and an agent whose credentials are all revoked count as
archived. Each user of these lists its own kinds in its own order.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from .common import LIVE_AGENT, rows_in

SUMMARIES: dict[str, str] = {
    "item": "SELECT id,title,archived,status FROM work_items WHERE id IN ({})",
    "roadmap": "SELECT id,title,archived FROM roadmaps WHERE id IN ({})",
    "step": "SELECT s.id,s.title,s.retired AS archived,s.status,s.roadmap_id AS roadmapId,r.title AS roadmapTitle "
            "FROM steps s JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id IN ({})",
    "repository": "SELECT id,name AS title,(archived=1 OR merged_into IS NOT NULL) AS archived,merged_into AS mergedInto "
                  "FROM repositories WHERE id IN ({})",
    "person": "SELECT id,name AS title,archived FROM people WHERE id IN ({})",
    "resource": "SELECT id,title,archived FROM resources WHERE id IN ({})",
    "discussion": "SELECT id,title,archived FROM discussions WHERE id IN ({})",
    # A proposal is never archived; its status says where it got to. actor_id and submitted_at decide
    # who may see it, and are left out of the summary.
    "proposal": "SELECT id,title,0 AS archived,status,actor_id,submitted_at FROM proposals WHERE id IN ({})",
    "ticket": "SELECT id,title,archived,status,number FROM tickets WHERE id IN ({})",
    "agent": f"SELECT a.id,a.label AS title,NOT {LIVE_AGENT} AS archived,a.avatar FROM communication_agents a WHERE a.id IN ({{}})",
    "question": "SELECT q.id,q.title,q.archived,q.status,q.roadmap_id AS roadmapId,r.title AS roadmapTitle "
                "FROM roadmap_questions q JOIN roadmaps r ON r.id=q.roadmap_id WHERE q.id IN ({})",
    "decision": "SELECT d.id,d.title,d.archived,d.roadmap_id AS roadmapId,r.title AS roadmapTitle "
                "FROM roadmap_decisions d JOIN roadmaps r ON r.id=d.roadmap_id WHERE d.id IN ({})",
    "release": "SELECT id,title,archived,status,release_date AS releaseDate FROM releases WHERE id IN ({})",
}
# Columns a summary query reads but never shows.
HIDDEN = frozenset({"actor_id", "submitted_at"})


def rows(c: sqlite3.Connection, kind: str, ids: Iterable[str]) -> list[sqlite3.Row]:
    """The rows of the named records of one kind that exist. The kind is one of SUMMARIES' keys,
    never a caller's text."""
    return rows_in(c, SUMMARIES[kind], ids)


def summary(row: sqlite3.Row) -> dict:
    """A summary row as a record shows it: its hidden columns left out, and archived a boolean."""
    record = {key: row[key] for key in row.keys() if key not in HIDDEN}
    record["archived"] = bool(record["archived"])
    return record
