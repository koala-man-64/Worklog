"""Pre-completion gates on roadmap steps, hybrid auto-evaluation, and completion blocking."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .common import AppError, event, now, require_revision, rows_in, text, uid

GATE_KINDS = frozenset({"manual", "approval", "verification", "prerequisite"})
TARGET_TYPES = frozenset({"step", "deliverable", "ticket", "question"})

_STEP_SUBTREE = """
WITH RECURSIVE wanted AS (
    SELECT s.id,s.title,s.status,s.parent_id,s.roadmap_id,s.retired,r.archived AS roadmap_archived
    FROM steps s JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id IN ({})
), subtree(id,title,status,parent_id,roadmap_id,retired,roadmap_archived) AS (
    SELECT id,title,status,parent_id,roadmap_id,retired,roadmap_archived FROM wanted
    UNION
    SELECT s.id,s.title,s.status,s.parent_id,s.roadmap_id,s.retired,w.roadmap_archived
    FROM subtree w JOIN steps s ON s.parent_id=w.id AND s.roadmap_id=w.roadmap_id AND s.retired=0
    WHERE w.retired=0
)
SELECT DISTINCT id,title,status,parent_id,roadmap_id,retired,roadmap_archived FROM subtree
"""


def empty_summary() -> dict[str, int]:
    return {
        "total": 0,
        "open": 0,
        "passed": 0,
        "waived": 0,
        "unavailable": 0,
        "blocking": 0,
        "humanOnlyBlocking": 0,
    }


@dataclass
class _EvalContext:
    step_meta: dict[str, dict[str, Any]]
    children: dict[str, list[str]]
    active_gates_by_step: dict[str, list[sqlite3.Row]]
    deliverables: dict[str, dict[str, Any]]
    tickets: dict[str, dict[str, Any]]
    questions: dict[str, dict[str, Any]]
    done_cache: dict[str, bool]
    visiting: set[str]
    allow_archived_roadmap: bool = False

    def target_info(self, target_type: str, target_id: str) -> tuple[dict[str, Any], bool]:
        """Return (GateTarget view, target_satisfied) for a record-linked gate."""
        if target_type == "step":
            meta = self.step_meta.get(target_id)
            if meta is None:
                return {
                    "type": "step", "id": target_id, "title": "Unavailable step",
                    "status": "unavailable", "available": False, "roadmapId": None, "href": None,
                }, False
            available = not meta["retired"] and not meta["roadmapArchived"]
            done = available and self.step_done(target_id)
            status = self.step_status(target_id) if available else "unavailable"
            return {
                "type": "step", "id": target_id, "title": meta["title"], "status": status,
                "available": available, "roadmapId": meta["roadmapId"],
                "href": f"#roadmaps/{meta['roadmapId']}/{target_id}" if available else None,
            }, done
        if target_type == "deliverable":
            meta = self.deliverables.get(target_id)
            if meta is None:
                return {
                    "type": "deliverable", "id": target_id, "title": "Unavailable deliverable",
                    "status": "unavailable", "available": False, "roadmapId": None, "href": None,
                }, False
            available = not meta["unavailable"]
            return {
                "type": "deliverable", "id": target_id, "title": meta["title"],
                "status": meta["status"] if available else "unavailable",
                "available": available, "roadmapId": meta["roadmapId"],
                "href": f"#roadmaps/{meta['roadmapId']}/deliverables" if available else None,
            }, available and meta["status"] == "accepted"
        if target_type == "ticket":
            meta = self.tickets.get(target_id)
            if meta is None:
                return {
                    "type": "ticket", "id": target_id, "title": "Unavailable ticket",
                    "status": "unavailable", "available": False, "roadmapId": None, "href": None,
                }, False
            available = not meta["unavailable"]
            return {
                "type": "ticket", "id": target_id, "title": meta["title"],
                "status": meta["status"] if available else "unavailable",
                "available": available, "roadmapId": meta["roadmapId"],
                "href": f"#tickets/{target_id}" if available else None,
            }, available and meta["status"] == "done"
        meta = self.questions.get(target_id)
        if meta is None:
            return {
                "type": "question", "id": target_id, "title": "Unavailable question",
                "status": "unavailable", "available": False, "roadmapId": None, "href": None,
            }, False
        available = not meta["unavailable"]
        return {
            "type": "question", "id": target_id, "title": meta["title"],
            "status": meta["status"] if available else "unavailable",
            "available": available, "roadmapId": meta["roadmapId"],
            "href": f"#roadmaps/{meta['roadmapId']}/questions" if available else None,
        }, available and meta["status"] == "answered"

    def gate_state(self, row: sqlite3.Row) -> tuple[str, dict[str, Any] | None]:
        """Compute (effective_state, target_view) for one step_gates row."""
        target_type, target_id = row["target_type"], row["target_id"]
        target_view: dict[str, Any] | None = None
        target_satisfied = False
        if target_type and target_id:
            target_view, target_satisfied = self.target_info(target_type, target_id)
        if row["status"] == "waived":
            return "waived", target_view
        if target_type is None:
            return row["status"], None
        if target_view is None or not target_view["available"]:
            return "unavailable", target_view
        return ("passed" if target_satisfied else "open"), target_view

    def step_done(self, step_id: str) -> bool:
        cached = self.done_cache.get(step_id)
        if cached is not None:
            return cached
        meta = self.step_meta.get(step_id)
        if meta is None or meta["retired"] or (meta["roadmapArchived"] and not self.allow_archived_roadmap):
            self.done_cache[step_id] = False
            return False
        if step_id in self.visiting:
            return False
        self.visiting.add(step_id)
        try:
            kids = self.children.get(step_id, [])
            base_done = all(self.step_done(kid) for kid in kids) if kids else meta["status"] == "done"
            if not base_done:
                result = False
            else:
                result = all(
                    self.gate_state(g)[0] in {"passed", "waived"}
                    for g in self.active_gates_by_step.get(step_id, ())
                )
        finally:
            self.visiting.discard(step_id)
        self.done_cache[step_id] = result
        return result

    def step_status(self, step_id: str) -> str:
        meta = self.step_meta.get(step_id)
        if meta is None or meta["retired"] or meta["roadmapArchived"]:
            return "unavailable"
        if step_id in self.visiting:
            return "doing"
        kids = self.children.get(step_id, [])
        if not kids:
            return str(meta["status"])
        self.visiting.add(step_id)
        try:
            child_states = [self.step_status(kid) for kid in kids]
        finally:
            self.visiting.discard(step_id)
        if self.step_done(step_id):
            return "done"
        if "blocked" in child_states:
            return "blocked"
        if any(s in {"doing", "done"} for s in child_states):
            return "doing"
        return "open"


def _build_eval_context(
    conn: sqlite3.Connection,
    seed_step_ids: Iterable[str],
    extra_rows: Iterable[sqlite3.Row] = (),
    *,
    allow_archived_roadmap: bool = False,
) -> _EvalContext:
    """Bulk-load all steps, subtrees, active step gates, and linked targets reachable from seeds."""
    step_meta: dict[str, dict[str, Any]] = {}
    children: dict[str, list[str]] = {}
    active_gates_by_step: dict[str, list[sqlite3.Row]] = {}
    loaded_step_gates: set[str] = set()

    extra = list(extra_rows)
    frontier: set[str] = set(seed_step_ids)
    for row in extra:
        frontier.add(row["step_id"])
        if row["target_type"] == "step" and row["target_id"]:
            frontier.add(row["target_id"])

    target_ids: dict[str, set[str]] = {"deliverable": set(), "ticket": set(), "question": set()}
    for row in extra:
        if row["target_type"] in target_ids and row["target_id"]:
            target_ids[row["target_type"]].add(row["target_id"])

    while frontier:
        to_load = [sid for sid in frontier if sid not in step_meta]
        frontier = set()
        if not to_load:
            break
        newly_loaded: list[str] = []
        for r in rows_in(conn, _STEP_SUBTREE, to_load):
            sid = r["id"]
            if sid in step_meta:
                continue
            step_meta[sid] = {
                "id": sid,
                "title": r["title"],
                "status": r["status"],
                "parentId": r["parent_id"],
                "roadmapId": r["roadmap_id"],
                "retired": bool(r["retired"]),
                "roadmapArchived": bool(r["roadmap_archived"]),
            }
            newly_loaded.append(sid)
        for sid in newly_loaded:
            meta = step_meta[sid]
            pid = meta["parentId"]
            if not meta["retired"] and pid and pid in step_meta and not step_meta[pid]["retired"]:
                children.setdefault(pid, []).append(sid)
        # Also attach children loaded earlier whose parent just arrived in step_meta.
        new_set = set(newly_loaded)
        for sid, meta in step_meta.items():
            pid = meta["parentId"]
            if sid not in new_set and not meta["retired"] and pid in new_set and not step_meta[pid]["retired"]:
                kids = children.setdefault(pid, [])
                if sid not in kids:
                    kids.append(sid)

        gate_lookup = [sid for sid in newly_loaded if sid not in loaded_step_gates and not step_meta[sid]["retired"]]
        if gate_lookup:
            loaded_step_gates.update(gate_lookup)
            for g in rows_in(
                conn,
                "SELECT * FROM step_gates WHERE archived=0 AND step_id IN ({}) "
                "ORDER BY position,created_at,id",
                gate_lookup,
            ):
                active_gates_by_step.setdefault(g["step_id"], []).append(g)
                tt, tid = g["target_type"], g["target_id"]
                if tt == "step" and tid and tid not in step_meta:
                    frontier.add(tid)
                elif tt in target_ids and tid:
                    target_ids[tt].add(tid)

    deliverables: dict[str, dict[str, Any]] = {}
    if target_ids["deliverable"]:
        for r in rows_in(
            conn,
            "SELECT d.id,d.title,d.status,d.roadmap_id,(d.archived=1 OR r.archived=1) AS unavailable "
            "FROM deliverables d JOIN roadmaps r ON r.id=d.roadmap_id WHERE d.id IN ({})",
            target_ids["deliverable"],
        ):
            deliverables[r["id"]] = {
                "title": r["title"], "status": r["status"], "roadmapId": r["roadmap_id"],
                "unavailable": bool(r["unavailable"]),
            }

    tickets: dict[str, dict[str, Any]] = {}
    if target_ids["ticket"]:
        for r in rows_in(
            conn,
            "SELECT t.id,t.title,t.status,coalesce(s.roadmap_id,w.roadmap_id) AS roadmap_id,"
            "t.archived AS unavailable FROM tickets t "
            "LEFT JOIN work_items w ON w.id=t.item_id LEFT JOIN steps s ON s.id=w.step_id WHERE t.id IN ({})",
            target_ids["ticket"],
        ):
            tickets[r["id"]] = {
                "title": r["title"], "status": r["status"], "roadmapId": r["roadmap_id"],
                "unavailable": bool(r["unavailable"]),
            }

    questions: dict[str, dict[str, Any]] = {}
    if target_ids["question"]:
        for r in rows_in(
            conn,
            "SELECT q.id,q.title,q.status,q.roadmap_id,(q.archived=1 OR r.archived=1) AS unavailable "
            "FROM roadmap_questions q JOIN roadmaps r ON r.id=q.roadmap_id WHERE q.id IN ({})",
            target_ids["question"],
        ):
            questions[r["id"]] = {
                "title": r["title"], "status": r["status"], "roadmapId": r["roadmap_id"],
                "unavailable": bool(r["unavailable"]),
            }

    return _EvalContext(
        step_meta=step_meta,
        children=children,
        active_gates_by_step=active_gates_by_step,
        deliverables=deliverables,
        tickets=tickets,
        questions=questions,
        done_cache={},
        visiting=set(),
        allow_archived_roadmap=allow_archived_roadmap,
    )


def _render_gate(row: sqlite3.Row, ctx: _EvalContext) -> dict[str, Any]:
    state, target_view = ctx.gate_state(row)
    step_meta = ctx.step_meta.get(row["step_id"])
    resolution = None
    if row["resolved_at"] is not None:
        resolution = {
            "resolvedAt": row["resolved_at"],
            "actorId": row["resolved_by_id"],
            "actorLabel": row["resolved_by_label"],
            "actorKind": row["resolved_by_kind"],
            "proposalId": row["proposal_id"],
            "note": row["resolution_note"],
        }
    return {
        "id": row["id"],
        "stepId": row["step_id"],
        "roadmapId": step_meta["roadmapId"] if step_meta else "",
        "stepTitle": step_meta["title"] if step_meta else "",
        "title": row["title"],
        "criteria": row["criteria"],
        "kind": row["kind"],
        "humanOnly": bool(row["human_only"]),
        "target": target_view,
        "storedStatus": row["status"],
        "state": state,
        "satisfied": state in {"passed", "waived"},
        "autoEvaluated": row["target_type"] is not None and row["status"] != "waived",
        "resolution": resolution,
        "position": row["position"],
        "archived": bool(row["archived"]),
        "revision": row["revision"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def summaries_by_step(conn: sqlite3.Connection, step_ids: Iterable[str]) -> dict[str, dict[str, int]]:
    """Bulk gate summaries for a set of steps. Returns empty summaries when no gates exist."""
    ids = list(dict.fromkeys(step_ids))
    if not ids:
        return {}
    result = {sid: empty_summary() for sid in ids}
    direct_rows = list(rows_in(
        conn,
        "SELECT * FROM step_gates WHERE archived=0 AND step_id IN ({}) ORDER BY position,created_at,id",
        ids,
    ))
    if not direct_rows:
        return result
    ctx = _build_eval_context(conn, (r["step_id"] for r in direct_rows), direct_rows)
    for row in direct_rows:
        summary = result[row["step_id"]]
        state, _ = ctx.gate_state(row)
        summary["total"] += 1
        summary[state] += 1
        if state in {"open", "unavailable"}:
            summary["blocking"] += 1
            if row["human_only"]:
                summary["humanOnlyBlocking"] += 1
    return result


def steps_effectively_done(
    conn: sqlite3.Connection,
    step_ids: Iterable[str],
    *,
    include_archived_roadmaps: bool = False,
) -> dict[str, bool]:
    """Whether each step is effectively done (all non-retired leaves done AND no open gates in subtree)."""
    ids = list(dict.fromkeys(step_ids))
    if not ids:
        return {}
    ctx = _build_eval_context(conn, ids, allow_archived_roadmap=include_archived_roadmaps)
    return {sid: ctx.step_done(sid) for sid in ids}


def blocking_for_step(conn: sqlite3.Connection, step_id: str) -> list[dict[str, Any]]:
    """Unsatisfied active gates currently blocking completion of step_id."""
    rows = conn.execute(
        "SELECT * FROM step_gates WHERE step_id=? AND archived=0 ORDER BY position,created_at,id",
        (step_id,),
    ).fetchall()
    if not rows:
        return []
    ctx = _build_eval_context(conn, [step_id], rows)
    rendered = [_render_gate(row, ctx) for row in rows]
    return [g for g in rendered if not g["satisfied"]]


def _require_step(conn: sqlite3.Connection, step_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT s.id,s.roadmap_id,s.title,s.parent_id,s.position,s.status,s.revision,s.retired,"
        "r.archived AS roadmap_archived FROM steps s JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id=?",
        (step_id,),
    ).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Step not found")
    return row


def _require_gate(conn: sqlite3.Connection, gate_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM step_gates WHERE id=?", (gate_id,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Gate not found")
    return row


def get_gate(conn: sqlite3.Connection, gate_id: str) -> dict[str, Any]:
    row = _require_gate(conn, gate_id)
    ctx = _build_eval_context(conn, [row["step_id"]], [row])
    return _render_gate(row, ctx)


def list_for_step(conn: sqlite3.Connection, step_id: str, *, archived: bool = False) -> dict[str, Any]:
    _require_step(conn, step_id)
    rows = conn.execute(
        "SELECT * FROM step_gates WHERE step_id=? AND archived=? ORDER BY position,created_at,id",
        (step_id, int(archived)),
    ).fetchall()
    ctx = _build_eval_context(conn, [step_id], rows)
    items = [_render_gate(row, ctx) for row in rows]
    summary = empty_summary()
    for item in items:
        if not item["archived"]:
            summary["total"] += 1
            summary[item["state"]] += 1
            if not item["satisfied"]:
                summary["blocking"] += 1
                if item["humanOnly"]:
                    summary["humanOnlyBlocking"] += 1
    return {"items": items, "total": len(items), "summary": summary}


def list_for_roadmap(
    conn: sqlite3.Connection, roadmap_id: str, *, archived: bool = False, only_open: bool = False,
) -> dict[str, Any]:
    rm = conn.execute("SELECT id FROM roadmaps WHERE id=?", (roadmap_id,)).fetchone()
    if rm is None:
        raise AppError(404, "not_found", "Roadmap not found")
    step_rows = conn.execute(
        "SELECT id,title,parent_id,position,status,retired FROM steps WHERE roadmap_id=? AND retired=0 ORDER BY position,id",
        (roadmap_id,),
    ).fetchall()
    if not step_rows:
        return {"items": [], "total": 0, "summary": empty_summary()}
    by_id = {r["id"]: r for r in step_rows}
    has_children = {r["parent_id"] for r in step_rows if r["parent_id"] in by_id}

    def phase_of(sid: str) -> sqlite3.Row:
        cur = by_id[sid]
        seen = {sid}
        while cur["parent_id"] in by_id and cur["parent_id"] not in seen:
            seen.add(cur["parent_id"])
            cur = by_id[cur["parent_id"]]
        return cur

    gate_rows = conn.execute(
        "SELECT g.* FROM step_gates g JOIN steps s ON s.id=g.step_id "
        "WHERE s.roadmap_id=? AND s.retired=0 AND g.archived=? "
        "ORDER BY s.position,s.id,g.position,g.created_at,g.id",
        (roadmap_id, int(archived)),
    ).fetchall()
    if not gate_rows:
        return {"items": [], "total": 0, "summary": empty_summary()}
    ctx = _build_eval_context(conn, list(by_id), gate_rows)
    items: list[dict[str, Any]] = []
    summary = empty_summary()
    for row in gate_rows:
        rendered = _render_gate(row, ctx)
        if not rendered["archived"]:
            summary["total"] += 1
            summary[rendered["state"]] += 1
            if not rendered["satisfied"]:
                summary["blocking"] += 1
                if rendered["humanOnly"]:
                    summary["humanOnlyBlocking"] += 1
        if only_open and rendered["satisfied"]:
            continue
        phase = phase_of(row["step_id"])
        items.append({
            **rendered,
            "stepStatus": ctx.step_status(row["step_id"]),
            "isParent": row["step_id"] in has_children,
            "phaseId": phase["id"],
            "phaseTitle": phase["title"],
        })
    return {"items": items, "total": len(items), "summary": summary}


def history(conn: sqlite3.Connection, gate_id: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    import json

    _require_gate(conn, gate_id)
    rows = conn.execute(
        "SELECT * FROM events WHERE entity_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
        (gate_id, limit, offset),
    ).fetchall()
    events = [
        {
            "id": r["id"],
            "entityId": r["entity_id"],
            "kind": r["kind"],
            "actorId": r["actor_id"],
            "actorLabel": r["actor_label"],
            "actorKind": r["actor_kind"],
            "createdAt": r["created_at"],
            "localDate": r["local_date"],
            "before": json.loads(r["before_json"]),
            "after": json.loads(r["after_json"]),
        }
        for r in rows
    ]
    total = int(conn.execute("SELECT count(*) FROM events WHERE entity_id=?", (gate_id,)).fetchone()[0])
    return {"items": events, "total": total}


def _reaches(conn: sqlite3.Connection, start_step_id: str, goal_step_id: str, *, ignore_gate_id: str | None = None) -> bool:
    """Whether start_step_id reaches goal_step_id through non-retired parent->child and active unwaived step gates."""
    if start_step_id == goal_step_id:
        return True
    visited = {start_step_id}
    frontier = [start_step_id]
    while frontier:
        next_frontier: list[str] = []
        for (child_id,) in rows_in(
            conn,
            "SELECT id FROM steps WHERE retired=0 AND parent_id IN ({})",
            frontier,
        ):
            if child_id == goal_step_id:
                return True
            if child_id not in visited:
                visited.add(child_id)
                next_frontier.append(child_id)
        for gate_id, target_id in rows_in(
            conn,
            "SELECT id,target_id FROM step_gates WHERE archived=0 AND status<>'waived' "
            "AND target_type='step' AND target_id IS NOT NULL AND step_id IN ({})",
            frontier,
        ):
            if ignore_gate_id is not None and gate_id == ignore_gate_id:
                continue
            if target_id == goal_step_id:
                return True
            if target_id not in visited:
                visited.add(target_id)
                next_frontier.append(target_id)
        frontier = next_frontier
    return False


def validate_step_hierarchy_cycles(conn: sqlite3.Connection, roadmap_id: str) -> None:
    """Ensure active step gates touching this roadmap have no cycle after a step tree change."""
    rows = conn.execute(
        "SELECT g.id,g.step_id,g.target_id FROM step_gates g "
        "JOIN steps s ON s.id=g.step_id "
        "WHERE g.archived=0 AND g.status<>'waived' AND g.target_type='step' AND g.target_id IS NOT NULL "
        "AND (s.roadmap_id=? OR g.target_id IN (SELECT id FROM steps WHERE roadmap_id=?))",
        (roadmap_id, roadmap_id),
    ).fetchall()
    for row in rows:
        if _reaches(conn, row["target_id"], row["step_id"], ignore_gate_id=row["id"]):
            raise AppError(
                422,
                "validation",
                "This step change would create a completion cycle with an active step gate",
                {"gateId": row["id"], "stepId": row["step_id"], "targetStepId": row["target_id"]},
            )


def _validate_target(
    conn: sqlite3.Connection, step_id: str, target: dict[str, Any] | None, *, ignore_gate_id: str | None = None,
) -> tuple[str | None, str | None]:
    if target is None:
        return None, None
    if not isinstance(target, dict):
        raise AppError(422, "validation", "Invalid gate target")
    target_type = target.get("type")
    target_id = text(target.get("id"), "target.id", required=True, limit=200).strip()
    if target_type not in TARGET_TYPES:
        raise AppError(422, "validation", "Invalid gate target type", {"target": "invalid_type"})
    if target_type == "step":
        if target_id == step_id:
            raise AppError(422, "validation", "A step cannot gate on itself", {"target": "self"})
        row = conn.execute(
            "SELECT s.id,s.retired,r.archived AS roadmap_archived "
            "FROM steps s JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id=?",
            (target_id,),
        ).fetchone()
        if row is None:
            raise AppError(422, "validation", "Linked step does not exist", {"target": "missing"})
        if row["retired"] or row["roadmap_archived"]:
            raise AppError(409, "conflict", "Linked step is retired or on an archived roadmap", {"target": "unavailable"})
        if _reaches(conn, target_id, step_id, ignore_gate_id=ignore_gate_id):
            raise AppError(
                422,
                "validation",
                "Step gates cannot form a cycle or wait on an ancestor step",
                {"target": "cycle"},
            )
    elif target_type == "deliverable":
        row = conn.execute(
            "SELECT d.id,d.archived,r.archived AS roadmap_archived "
            "FROM deliverables d JOIN roadmaps r ON r.id=d.roadmap_id WHERE d.id=?",
            (target_id,),
        ).fetchone()
        if row is None:
            raise AppError(422, "validation", "Linked deliverable does not exist", {"target": "missing"})
        if row["archived"] or row["roadmap_archived"]:
            raise AppError(409, "conflict", "Linked deliverable is archived", {"target": "unavailable"})
    elif target_type == "ticket":
        row = conn.execute("SELECT id,archived FROM tickets WHERE id=?", (target_id,)).fetchone()
        if row is None:
            raise AppError(422, "validation", "Linked ticket does not exist", {"target": "missing"})
        if row["archived"]:
            raise AppError(409, "conflict", "Linked ticket is archived", {"target": "unavailable"})
    elif target_type == "question":
        row = conn.execute(
            "SELECT q.id,q.archived,r.archived AS roadmap_archived "
            "FROM roadmap_questions q JOIN roadmaps r ON r.id=q.roadmap_id WHERE q.id=?",
            (target_id,),
        ).fetchone()
        if row is None:
            raise AppError(422, "validation", "Linked question does not exist", {"target": "missing"})
        if row["archived"] or row["roadmap_archived"]:
            raise AppError(409, "conflict", "Linked question is archived", {"target": "unavailable"})

    duplicate = conn.execute(
        "SELECT id FROM step_gates WHERE step_id=? AND target_type=? AND target_id=? AND archived=0",
        (step_id, target_type, target_id),
    ).fetchone()
    if duplicate is not None and duplicate["id"] != ignore_gate_id:
        raise AppError(409, "conflict", "This step already has an active gate for that record", {"target": "duplicate"})
    return target_type, target_id


def check_proposal_operation(conn: sqlite3.Connection, action: str, payload: dict[str, Any]) -> None:
    """Fail-fast guard for agent proposal submission on human-only or waived gates."""
    if action not in {"gate.update", "gate.pass", "gate.reopen"}:
        return
    gate_id = payload.get("id")
    if not isinstance(gate_id, str) or gate_id.startswith("@"):
        return
    row = conn.execute("SELECT human_only,status,target_type FROM step_gates WHERE id=?", (gate_id,)).fetchone()
    if row is None:
        return
    if row["human_only"]:
        raise AppError(403, "agent_scope", "Only the person can update, pass, or reopen a human-only gate")
    if action == "gate.pass" and row["target_type"] is not None:
        raise AppError(
            422, "validation",
            "This gate follows its linked record; complete the linked record or ask the person to waive it",
        )
    if action == "gate.reopen" and row["status"] == "waived":
        raise AppError(403, "agent_scope", "Only the person can reopen a waived gate")


def _position(conn: sqlite3.Connection, step_id: str, value: Any) -> int:
    if value is None:
        return int(conn.execute(
            "SELECT COALESCE(MAX(position),-1)+1 FROM step_gates WHERE step_id=? AND archived=0",
            (step_id,),
        ).fetchone()[0])
    if type(value) is not int or value < 0:
        raise AppError(422, "validation", "Invalid gate position")
    return value


def _kind(value: Any, *, has_target: bool, fallback: str | None = None) -> str:
    if value is None:
        if fallback is not None:
            return fallback
        return "prerequisite" if has_target else "manual"
    if not isinstance(value, str) or value not in GATE_KINDS:
        raise AppError(422, "validation", "Invalid gate kind")
    return value


def apply(
    conn: sqlite3.Connection,
    actor: dict[str, Any],
    action: str,
    payload: dict[str, Any],
    *,
    proposed: bool = False,
    proposal_id: str | None = None,
) -> dict[str, Any]:
    if actor.get("kind") not in {"human", "agent"}:
        raise AppError(403, "forbidden", "Invalid actor")
    if actor.get("kind") == "agent" and not proposed:
        raise AppError(403, "agent_scope", "Agents change gates through proposals")
    verb = action.removeprefix("gate.")
    stamp = now()

    if verb == "create":
        step = _require_step(conn, payload["stepId"])
        if step["roadmap_archived"] or step["retired"]:
            raise AppError(409, "conflict", "Recover this roadmap and step before adding gates")
        title = text(payload.get("title"), "title", required=True, limit=1000).strip()
        criteria = text(payload.get("criteria", ""), "criteria", limit=100_000)
        human_only = bool(payload.get("humanOnly", False))
        target_type, target_id = _validate_target(conn, step["id"], payload.get("target"))
        kind = _kind(payload.get("kind"), has_target=target_type is not None)
        position = _position(conn, step["id"], payload.get("position"))
        identity = uid()
        conn.execute(
            "INSERT INTO step_gates(id,step_id,title,criteria,kind,human_only,target_type,target_id,"
            "status,resolution_note,position,archived,revision,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,'open','',?,0,1,?,?)",
            (identity, step["id"], title, criteria, kind, int(human_only), target_type, target_id,
             position, stamp, stamp),
        )
        after = get_gate(conn, identity)
        event(conn, actor, "gate.create", identity, None, after, at=stamp)
        return after

    identity = payload["id"]
    row = _require_gate(conn, identity)
    require_revision(row, payload.get("revision"))
    step = _require_step(conn, row["step_id"])
    before = get_gate(conn, identity)

    if verb in {"archive", "recover"}:
        if proposed or actor["kind"] != "human":
            raise AppError(403, "agent_scope", "Only the person archives or recovers gates")
        if step["roadmap_archived"] or step["retired"]:
            raise AppError(409, "conflict", "Recover this roadmap and step before changing its gates")
        archived = verb == "archive"
        if archived == bool(row["archived"]):
            raise AppError(409, "conflict", "This gate is already in the requested archive state")
        if not archived and row["target_type"] is not None:
            duplicate = conn.execute(
                "SELECT id FROM step_gates WHERE step_id=? AND target_type=? AND target_id=? AND archived=0",
                (row["step_id"], row["target_type"], row["target_id"]),
            ).fetchone()
            if duplicate is not None:
                raise AppError(409, "conflict", "This step already has an active gate for that record")
            if row["target_type"] == "step" and row["status"] != "waived":
                if _reaches(conn, row["target_id"], row["step_id"], ignore_gate_id=identity):
                    raise AppError(422, "validation", "Recovering this gate would create a step completion cycle")
        conn.execute(
            "UPDATE step_gates SET archived=?,revision=revision+1,updated_at=? WHERE id=?",
            (int(archived), stamp, identity),
        )
        after = get_gate(conn, identity)
        event(conn, actor, action, identity, before, after, at=stamp)
        return after

    if row["archived"]:
        raise AppError(409, "conflict", "Recover this gate before changing it")
    if step["roadmap_archived"] or step["retired"]:
        raise AppError(409, "conflict", "Recover this roadmap and step before changing its gates")

    if verb == "waive":
        if proposed or actor["kind"] != "human":
            raise AppError(403, "agent_scope", "Only the person waives gates")
        reason = text(payload.get("reason"), "reason", required=True, limit=8000).strip()
        if before["satisfied"]:
            raise AppError(409, "conflict", "This gate is already satisfied")
        conn.execute(
            "UPDATE step_gates SET status='waived',resolution_note=?,resolved_at=?,resolved_by_id=?,"
            "resolved_by_label=?,resolved_by_kind=?,proposal_id=NULL,revision=revision+1,updated_at=? WHERE id=?",
            (reason, stamp, actor["id"], actor["label"], actor["kind"], stamp, identity),
        )
        after = get_gate(conn, identity)
        event(conn, actor, "gate.waive", identity, before, after, at=stamp)
        return after

    if proposed and row["human_only"]:
        raise AppError(403, "agent_scope", "Only the person can update, pass, or reopen a human-only gate")

    if verb == "update":
        title = row["title"] if payload.get("title") is None else text(payload["title"], "title", required=True, limit=1000).strip()
        criteria = row["criteria"] if payload.get("criteria") is None else text(payload["criteria"], "criteria", limit=100_000)
        human_only = bool(row["human_only"]) if payload.get("humanOnly") is None else bool(payload["humanOnly"])
        position = row["position"] if payload.get("position") is None else _position(conn, row["step_id"], payload["position"])
        target_type, target_id = row["target_type"], row["target_id"]
        if "target" in payload:
            incoming = payload["target"]
            same_target = (
                (incoming is None and target_type is None)
                or (isinstance(incoming, dict) and incoming.get("type") == target_type and incoming.get("id") == target_id)
            )
            if not same_target:
                if row["status"] in {"passed", "waived"}:
                    raise AppError(409, "conflict", "Reopen this gate before changing its linked record")
                target_type, target_id = _validate_target(conn, row["step_id"], incoming, ignore_gate_id=identity)
        kind = _kind(payload.get("kind"), has_target=target_type is not None, fallback=row["kind"])
        conn.execute(
            "UPDATE step_gates SET title=?,criteria=?,kind=?,human_only=?,target_type=?,target_id=?,"
            "position=?,revision=revision+1,updated_at=? WHERE id=?",
            (title, criteria, kind, int(human_only), target_type, target_id, position, stamp, identity),
        )
        after = get_gate(conn, identity)
        event(conn, actor, "gate.update", identity, before, after, at=stamp)
        return after

    if verb == "pass":
        if row["target_type"] is not None:
            raise AppError(
                422, "validation",
                "This gate follows its linked record; complete the linked record or waive the gate instead",
            )
        if row["status"] != "open":
            raise AppError(409, "conflict", "Only an open manual gate can be passed")
        note = text(payload.get("note", ""), "note", limit=8000).strip()
        conn.execute(
            "UPDATE step_gates SET status='passed',resolution_note=?,resolved_at=?,resolved_by_id=?,"
            "resolved_by_label=?,resolved_by_kind=?,proposal_id=?,revision=revision+1,updated_at=? WHERE id=?",
            (note, stamp, actor["id"], actor["label"], actor["kind"], proposal_id, stamp, identity),
        )
        after = get_gate(conn, identity)
        event(conn, actor, "gate.pass", identity, before, after, at=stamp)
        return after

    if verb == "reopen":
        if row["status"] not in {"passed", "waived"}:
            raise AppError(409, "conflict", "This gate is already open")
        if proposed and row["status"] == "waived":
            raise AppError(403, "agent_scope", "Only the person can reopen a waived gate")
        reason = text(payload.get("reason"), "reason", required=True, limit=8000).strip()
        if row["status"] == "waived" and row["target_type"] == "step":
            if _reaches(conn, row["target_id"], row["step_id"], ignore_gate_id=identity):
                raise AppError(422, "validation", "Reopening this gate would create a step completion cycle")
        conn.execute(
            "UPDATE step_gates SET status='open',resolution_note='',resolved_at=NULL,resolved_by_id=NULL,"
            "resolved_by_label=NULL,resolved_by_kind=NULL,proposal_id=NULL,revision=revision+1,updated_at=? WHERE id=?",
            (stamp, identity),
        )
        after = get_gate(conn, identity)
        event(conn, actor, "gate.reopen", identity, before, {**after, "reason": reason}, at=stamp)
        return after

    raise AppError(422, "validation", "Unsupported gate action")
