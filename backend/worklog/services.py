"""The commands proposals and the person's routes run, each sent to the module that owns its records.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import dependencies, search, support
from .common import AppError
from .coordination import repository_apply
from .db import Database
from .deliverables import DeliverableService
from .discussions import proposer_identity
from .items import Items
from .people import People
from .resources import ResourceService
from .tickets import TicketService
from .waves import WaveService


class WorkService:
    """Runs each command on the records it names: work items and their days, people, supporting
    records, repositories, roadmaps and waves. Approved proposals reach every one of them through
    apply, and resources and tickets as well, which the person's own routes write directly."""

    def __init__(self, db: Database, plans: Any, proposals: Any = None):
        self.db, self.plans, self.proposals = db, plans, proposals
        self.items, self.people = Items(db), People(db)
        self.deliverables = DeliverableService(db)
        self.resources, self.tickets, self.waves = ResourceService(), TicketService(), WaveService(db)

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
        if action.startswith("dependency."):
            author = proposer_identity(c, actor) if proposed else actor
            if action == "dependency.create":
                return dependencies.create(c, author, p, proposed=proposed)
            return dependencies.transition(c, author, action.removeprefix("dependency."), p, proposed=proposed)
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
            if action in {"step.update", "step.create"}:
                # One step's text: index that step alone. Only an active step of an active roadmap changes.
                search.put(c, result["id"], "step", result["title"], search.step_body(result["description"], result["outcome"]),
                           result["roadmapId"])
            elif action == "step.split":
                search.index_plans(c, result["roadmapId"])
            # Moving changes no indexed text or archive state.
            elif action not in {"progress.set", "roadmap.move", "step.move"}:
                search.index_plans(c, result["id"])
            if self.proposals is not None and action != "roadmap.create":
                self.proposals.notify_stale_proposals(
                    c, result.get("roadmapId") if action in {"progress.set", "step.update", "step.create", "step.split", "step.move"} else result.get("id"))
            return result
        if action.startswith("deliverable."):
            author = proposer_identity(c, actor) if proposed else actor
            proposal_id = self.proposals.applying_id if proposed and self.proposals is not None else None
            result = self.deliverables.apply(c, author, action, p, proposal_id=proposal_id)
            if self.proposals is not None and not proposed and action != "deliverable.create":
                self.proposals.notify_stale_proposals(c, result["roadmapId"])
            return result
        if action.startswith("wave."):
            return self.waves.apply(c, actor, action, p)
        if action.startswith("person."):
            return self.people.apply(c, actor, action, p)
        raise AppError(422, "operation", "Unsupported operation")
