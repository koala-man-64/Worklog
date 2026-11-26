"""The calculated Active status: work items and roadmap steps an agent is working on now.

A record's stored status is the person's: open, doing, waiting, blocked or done. Agents change it
only by a proposal, so work an agent has taken up can read Open for hours. Worklog calculates one
more status beside it, Active, for a work item or step stored Open or Doing (a parent step's derived
from its leaves) that an agent reports work on: a participation that is not released, in a run that
has not ended, under a credential that is not revoked, that reported within STALE_AFTER. Waiting,
Blocked and Done keep their stored status, since the person set them.

A step is also Active while a work item on it is, and a parent step while a step under it is. Only
what shows Active counts toward the step above it, so a done item an agent never released leaves its
step as it is.

Nothing stores the status: each read works it out afresh as `agentActive`, so it never enters a
record's history, a proposal or a roadmap's saved versions.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .common import rows_in

# A participation that has not reported for this long is stale, as registry.STALE_AFTER counts it.
STALE_AFTER = timedelta(hours=2)
# The stored statuses Active stands in for.
SHOWN_AS_ACTIVE = frozenset({"open", "doing"})


@dataclass(frozen=True)
class ActiveWork:
    """What agents report working on now: work items, and steps named directly."""

    items: frozenset[str]
    steps: frozenset[str]


def _fresh(last_seen_at: Any, cutoff: datetime) -> bool:
    try:
        seen = datetime.fromisoformat(last_seen_at)
    except (TypeError, ValueError):
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return seen >= cutoff


def active_work(conn: sqlite3.Connection) -> ActiveWork:
    """The work items and steps that a fresh participation names. Open runs are few, so the read
    starts from them; their open participations follow by index."""
    cutoff = datetime.now(UTC) - STALE_AFTER
    items: set[str] = set()
    steps: set[str] = set()
    for row in conn.execute(
        "SELECT p.item_id,p.step_id,p.last_seen_at FROM agent_runs ar "
        "JOIN credentials cr ON cr.id=ar.credential_id "
        "JOIN participations p ON p.run_id=ar.id AND p.released_at IS NULL "
        "WHERE ar.ended_at IS NULL AND cr.revoked=0 AND (p.item_id IS NOT NULL OR p.step_id IS NOT NULL)"
    ):
        if not _fresh(row["last_seen_at"], cutoff):
            continue
        if row["item_id"] is not None:
            items.add(row["item_id"])
        if row["step_id"] is not None:
            steps.add(row["step_id"])
    return ActiveWork(frozenset(items), frozenset(steps))


def item_active(work: ActiveWork, item_id: str, status: str) -> bool:
    """Whether a work item shows Active: an agent works on it and the person left it open or doing."""
    return status in SHOWN_AS_ACTIVE and item_id in work.items


def active_item_ids(work: ActiveWork, conn: sqlite3.Connection) -> list[str]:
    """The work items that show Active, for a status filter."""
    return [r["id"] for r in rows_in(
        conn, "SELECT id FROM work_items WHERE status IN ('open','doing') AND id IN ({})", sorted(work.items))]


def _steps_of_active_items(conn: sqlite3.Connection, work: ActiveWork, step_ids: Iterable[str]) -> set[str]:
    """Of these steps, the ones a work item that shows Active is on. Archived work is off the plan."""
    wanted = set(step_ids)
    if not work.items or not wanted:
        return set()
    return {r["step_id"] for r in rows_in(
        conn,
        "SELECT step_id FROM work_items WHERE archived=0 AND status IN ('open','doing') AND step_id IS NOT NULL AND id IN ({})",
        sorted(work.items),
    ) if r["step_id"] in wanted}


def mark_steps(conn: sqlite3.Connection, steps: list[dict], work: ActiveWork | None = None) -> list[dict]:
    """Set each step's `agentActive`, in place, and return the steps. A step in the plan shows Active
    when its status (a parent's derived from its leaves) is open or doing and an agent works on it, on
    a work item on it, or on a step under it that shows Active. A retired step never does."""
    work = active_work(conn) if work is None else work
    plan = {s["id"]: s for s in steps if not s.get("retired")}
    worked = set(work.steps) | _steps_of_active_items(conn, work, plan)
    children: dict[str | None, list[dict]] = {}
    for step in plan.values():
        children.setdefault(step["parentId"], []).append(step)

    def walk(step: dict) -> bool:
        under = [walk(child) for child in children.get(step["id"], [])]
        step["agentActive"] = step["status"] in SHOWN_AS_ACTIVE and (step["id"] in worked or any(under))
        return step["agentActive"]

    for step in steps:
        step["agentActive"] = False
    # A step whose parent is retired or missing roots its own branch, as a top-level step does.
    for step in plan.values():
        if step["parentId"] is None or step["parentId"] not in plan:
            walk(step)
    return steps
