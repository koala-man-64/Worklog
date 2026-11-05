"""The commands proposals and the person's routes run, each sent to the module that owns its records.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import search, support
from .common import AppError
from .coordination import repository_apply
from .db import Database
from .items import Items
from .people import People


class WorkService:
    """Runs each command on the records it names: work items and their days, people, supporting
    records, repositories and roadmaps. Approved proposals reach every one of them through apply."""

    def __init__(self, db: Database, plans: Any, proposals: Any = None):
        self.db, self.plans, self.proposals = db, plans, proposals
        self.items, self.people = Items(db), People(db)

    def apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
        if action.startswith("repository."):
            return repository_apply(c, actor, action, p)
        if action.startswith("item."):
            return self.items.apply(c, actor, action, p)
        if action == "entry.upsert":
            return self.items.upsert_entry(c, actor, p)
        if action.startswith(("note.", "task.", "reference.", "attachment.", "link.", "chat.", "github.", "question.", "decision.")):
            return support.apply(c, actor, action, p)
        if action.startswith(("roadmap.", "progress.")):
            result = self.plans.apply(c, actor, action, p)
            # Moving changes no indexed text or archive state.
            if action not in {"progress.set", "roadmap.move"}:
                search.index_plans(c, result["id"])
            if self.proposals is not None and action != "roadmap.create":
                self.proposals.notify_stale_proposals(
                    c, result.get("roadmapId") if action == "progress.set" else result.get("id"))
            return result
        if action.startswith("person."):
            return self.people.apply(c, actor, action, p)
        raise AppError(422, "operation", "Unsupported operation")
