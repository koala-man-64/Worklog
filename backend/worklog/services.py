"""The commands proposals and the person's routes run, each sent to the module that owns its records.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import search, support
from .common import AppError
from .coordination import repository_apply
from .db import Database
from .discussions import proposer_identity
from .items import Items
from .people import People
from .resources import ResourceService
from .tickets import TicketService


class WorkService:
    """Runs each command on the records it names: work items and their days, people, supporting
    records, repositories and roadmaps. Approved proposals reach every one of them through apply, and
    resources and tickets as well, which the person's own routes write directly."""

    def __init__(self, db: Database, plans: Any, proposals: Any = None):
        self.db, self.plans, self.proposals = db, plans, proposals
        self.items, self.people = Items(db), People(db)
        self.resources, self.tickets = ResourceService(), TicketService()

    def apply(self, c: sqlite3.Connection, actor: dict, action: str, p: dict, *, proposed: bool = False) -> dict:
        """`proposed` is true only for a change an approved proposal makes (app.dispatch)."""
        if action.startswith(("resource.", "ticket.")):
            # Only the person writes resources directly; an agent's change to one must come
            # through a proposal they decided. A ticket keeps its author's stable identity.
            if not proposed:
                raise AppError(422, "operation", "Unsupported operation")
            if action.startswith("resource."):
                return self.resources.apply(c, actor, action, p)
            return self.tickets.apply(c, proposer_identity(c, actor), action, p)
        if action.startswith("repository."):
            return repository_apply(c, actor, action, p)
        if action.startswith("item."):
            return self.items.apply(c, actor, action, p)
        if action == "entry.upsert":
            return self.items.upsert_entry(c, actor, p)
        if action.startswith(("note.", "reference.", "attachment.", "link.", "chat.", "github.", "question.", "decision.")):
            return support.apply(c, actor, action, p)
        if action.startswith(("roadmap.", "progress.", "step.")):
            result = self.plans.apply(c, actor, action, p)
            if action == "step.update":
                # One step's text: index that step alone. Only an active step of an active roadmap changes.
                search.put(c, result["id"], "step", result["title"], result["description"], result["roadmapId"])
            # Moving changes no indexed text or archive state.
            elif action not in {"progress.set", "roadmap.move"}:
                search.index_plans(c, result["id"])
            if self.proposals is not None and action != "roadmap.create":
                self.proposals.notify_stale_proposals(
                    c, result.get("roadmapId") if action in {"progress.set", "step.update"} else result.get("id"))
            return result
        if action.startswith("person."):
            return self.people.apply(c, actor, action, p)
        raise AppError(422, "operation", "Unsupported operation")
