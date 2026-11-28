"""A snapshot of declared prerequisites and task-scoped agent waits."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import record_kinds
from .common import LIVE_AGENT, AppError, event, now, require_revision, rows_in, text, uid
from .repository_links import repository_dependencies

Key = tuple[str, str]
DONE_TYPES = frozenset({"step", "item", "ticket"})
OPEN_STATES = frozenset({"waiting", "unavailable"})

# Parent progress follows its non-retired leaves. Resolve only the requested subtrees, in bulk,
# so a prerequisite in another roadmap does not require reading that whole roadmap.
_STEP_NODES = """
WITH RECURSIVE wanted AS (
    SELECT s.id,s.title,s.status,s.roadmap_id,s.retired,r.archived AS roadmap_archived
    FROM steps s JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id IN ({})
), subtree(root,id,status,roadmap_id) AS (
    SELECT id,id,status,roadmap_id FROM wanted WHERE retired=0
    UNION
    SELECT p.root,s.id,s.status,s.roadmap_id FROM subtree p
    JOIN steps s ON s.parent_id=p.id AND s.roadmap_id=p.roadmap_id AND s.retired=0
), completed AS (
    SELECT p.root FROM subtree p WHERE NOT EXISTS (
        SELECT 1 FROM steps s WHERE s.parent_id=p.id AND s.roadmap_id=p.roadmap_id AND s.retired=0
    ) GROUP BY p.root HAVING min(p.status='done')=1
)
SELECT w.id,w.title,w.roadmap_id AS roadmapId,(w.retired=1 OR w.roadmap_archived=1) AS archived,
       EXISTS(SELECT 1 FROM completed c WHERE c.root=w.id) AS done
FROM wanted w
"""
_RECORD_NODES = {
    "step": _STEP_NODES,
    "item": "SELECT w.id,w.title,w.archived,w.status='done' AS done,"
            "coalesce(s.roadmap_id,w.roadmap_id) AS roadmapId FROM work_items w "
            "LEFT JOIN steps s ON s.id=w.step_id WHERE w.id IN ({})",
    "ticket": "SELECT t.id,t.title,t.archived,t.status='done' AS done,"
              "coalesce(s.roadmap_id,w.roadmap_id) AS roadmapId FROM tickets t "
              "LEFT JOIN work_items w ON w.id=t.item_id LEFT JOIN steps s ON s.id=w.step_id WHERE t.id IN ({})",
    "agent": f"SELECT a.id,a.label AS title,NOT {LIVE_AGENT} AS archived FROM communication_agents a WHERE a.id IN ({{}})",
    "repository": record_kinds.SUMMARIES["repository"],
}


@dataclass(frozen=True)
class _Record:
    summary: dict[str, Any]
    done: bool


def _key(ref: dict) -> Key:
    return ref["type"], ref["id"]


def _records(conn: sqlite3.Connection, keys: Iterable[Key]) -> dict[Key, _Record]:
    by_type: dict[str, set[str]] = defaultdict(set)
    for kind, identity in keys:
        by_type[kind].add(identity)
    result: dict[Key, _Record] = {}
    for kind, ids in by_type.items():
        for row in rows_in(conn, _RECORD_NODES[kind], ids):
            info = dict(row)
            identity = info["id"]
            result[(kind, identity)] = _Record({
                "type": kind, "id": identity, "title": info["title"], "available": not info["archived"],
                "roadmapId": info.get("roadmapId"), "mergedInto": info.get("mergedInto"),
            }, bool(info.get("done")))
    return result


def _endpoint(records: dict[Key, _Record], key: Key) -> dict:
    if key in records:
        return records[key].summary
    return {"type": key[0], "id": key[1], "title": "Unavailable record", "available": False,
            "roadmapId": None, "mergedInto": None}


def _references(row: sqlite3.Row) -> tuple[Key, Key, Key | None]:
    dependent = row["dependent_type"], row["dependent_id"]
    prerequisite = row["prerequisite_type"], row["prerequisite_id"]
    context = (row["context_type"], row["context_id"]) if row["context_type"] else None
    return dependent, prerequisite, context


def _load_rows(conn: sqlite3.Connection, rows: Iterable[sqlite3.Row]) -> dict[Key, _Record]:
    return _records(conn, (key for row in rows for key in _references(row) if key is not None))


def _state(row: sqlite3.Row, records: dict[Key, _Record]) -> str:
    dependent, prerequisite, context = _references(row)
    if row["confirmed_at"]:
        return "satisfied"
    target = records.get(prerequisite)
    if dependent[0] != "agent" and target and target.done:
        return "satisfied"
    if any(not _endpoint(records, key)["available"] for key in (dependent, prerequisite, context) if key is not None):
        return "unavailable"
    return "waiting"


def _link(row: sqlite3.Row, records: dict[Key, _Record]) -> dict:
    dependent, prerequisite, context = _references(row)
    confirmation = None
    if row["confirmed_at"]:
        confirmation = {"confirmedAt": row["confirmed_at"], "confirmedBy": row["confirmed_by_label"],
                        "note": row["confirmation_note"]}
    return {
        "id": row["id"], "kind": "wait" if row["reported_by_agent_id"] else "shared",
        "dependent": _endpoint(records, dependent), "prerequisite": _endpoint(records, prerequisite),
        "context": _endpoint(records, context) if context else None,
        "condition": row["condition"], "state": _state(row, records), "confirmation": confirmation,
        "reportedByAgentId": row["reported_by_agent_id"], "revision": row["revision"], "archived": bool(row["archived"]),
    }


def _catalog_link(dependent: str, prerequisite: str, records: dict[Key, _Record]) -> dict:
    return {
        "id": f"catalog:{dependent}:{prerequisite}", "kind": "catalog",
        "dependent": _endpoint(records, ("repository", dependent)),
        "prerequisite": _endpoint(records, ("repository", prerequisite)),
        "context": None, "condition": "", "state": "linked", "confirmation": None,
        "reportedByAgentId": None, "revision": None, "archived": False,
    }


def list_links(conn: sqlite3.Connection, key: Key) -> dict:
    outgoing = conn.execute(
        "SELECT * FROM dependencies WHERE dependent_type=? AND dependent_id=? AND archived=0 ORDER BY created_at,id", key,
    ).fetchall()
    incoming = conn.execute(
        "SELECT * FROM dependencies WHERE prerequisite_type=? AND prerequisite_id=? AND archived=0 ORDER BY created_at,id", key,
    ).fetchall()
    upstream: list[str] = []
    downstream: list[str] = []
    if key[0] == "repository":
        catalog = repository_dependencies(conn, [key[1]])[key[1]]
        upstream, downstream = catalog["upstreamIds"], catalog["downstreamIds"]
    refs = {ref for row in outgoing + incoming for ref in _references(row) if ref is not None}
    refs.add(key)
    refs.update(("repository", identity) for identity in upstream + downstream)
    records = _records(conn, refs)
    if key not in records:
        raise AppError(404, "not_found", "The dependency record was not found")
    return {
        "prerequisites": [_catalog_link(key[1], identity, records) for identity in upstream]
                         + [_link(row, records) for row in outgoing],
        "dependents": [_catalog_link(identity, key[1], records) for identity in downstream]
                      + [_link(row, records) for row in incoming],
    }


def get_link(conn: sqlite3.Connection, identity: str) -> dict:
    row = conn.execute("SELECT * FROM dependencies WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Dependency not found")
    return _link(row, _load_rows(conn, [row]))


def open_by_step(conn: sqlite3.Connection, roadmap_id: str) -> dict[str, int]:
    roadmap = conn.execute("SELECT archived FROM roadmaps WHERE id=?", (roadmap_id,)).fetchone()
    if roadmap is None:
        raise AppError(404, "not_found", "Roadmap not found")
    if roadmap["archived"]:
        return {}
    steps = [row[0] for row in conn.execute("SELECT id FROM steps WHERE roadmap_id=? AND retired=0", (roadmap_id,))]
    edges = rows_in(
        conn, "SELECT * FROM dependencies WHERE dependent_type='step' AND dependent_id IN ({}) "
              "AND archived=0 AND confirmed_at IS NULL", steps,
    )
    records = _load_rows(conn, edges)
    counts: dict[str, int] = {}
    for edge in edges:
        identity = edge["dependent_id"]
        if not records[("step", identity)].done and _state(edge, records) in OPEN_STATES:
            counts[identity] = counts.get(identity, 0) + 1
    return counts


def pending_waits(
    conn: sqlite3.Connection,
    roadmap_id: str | None = None,
    roadmap_ids: Iterable[str] | None = None,
) -> list[dict]:
    edges = conn.execute(
        "SELECT * FROM dependencies WHERE dependent_type='agent' AND reported_by_agent_id IS NOT NULL "
        "AND archived=0 AND confirmed_at IS NULL ORDER BY created_at,id",
    ).fetchall()
    records = _load_rows(conn, edges)
    roots: list[str] = []
    if roadmap_id is not None:
        roots.append(roadmap_id)
    if roadmap_ids:
        for rid in roadmap_ids:
            if rid and rid not in roots:
                roots.append(rid)
    if not roots:
        return [_link(edge, records) for edge in edges]
    placeholders = ",".join("?" for _ in roots)
    family = {row[0] for row in conn.execute(
        f"WITH RECURSIVE family(id) AS (SELECT id FROM roadmaps WHERE id IN ({placeholders}) UNION "
        "SELECT r.id FROM roadmaps r JOIN family f ON r.parent_id=f.id) SELECT id FROM family",
        tuple(roots),
    )}
    if not family:
        return []
    repository_ids = {row[0] for row in rows_in(
        conn, "WITH scope(id) AS (SELECT id FROM roadmaps WHERE id IN ({})) "
              "SELECT sr.repository_id FROM step_repositories sr JOIN steps s ON s.id=sr.step_id "
              "WHERE s.roadmap_id IN (SELECT id FROM scope) AND s.retired=0 "
              "UNION SELECT ir.repository_id FROM item_repositories ir JOIN work_items w ON w.id=ir.item_id "
              "LEFT JOIN steps s ON s.id=w.step_id "
              "WHERE coalesce(s.roadmap_id,w.roadmap_id) IN (SELECT id FROM scope) AND w.archived=0 "
              "UNION SELECT wr.repository_id FROM wave_repositories wr JOIN waves w ON w.id=wr.wave_id "
              "WHERE w.roadmap_id IN (SELECT id FROM scope) AND w.archived=0 AND w.status IN ('planning','active')", family,
    )}
    waits = []
    for edge in edges:
        context = edge["context_type"], edge["context_id"]
        info = _endpoint(records, context)
        if info["roadmapId"] in family or (context[0] == "repository" and context[1] in repository_ids):
            waits.append(_link(edge, records))
    return waits


def _require_available(records: dict[Key, _Record], key: Key, field: str) -> None:
    if not _endpoint(records, key)["available"]:
        raise AppError(422, "validation", f"Choose an available {field}", {field: "unavailable"})


def _would_cycle(conn: sqlite3.Connection, dependent: Key, prerequisite: Key) -> bool:
    return conn.execute(
        "WITH RECURSIVE upstream(type,id) AS (VALUES (?,?) UNION "
        "SELECT d.prerequisite_type,d.prerequisite_id FROM dependencies d "
        "JOIN upstream u ON d.dependent_type=u.type AND d.dependent_id=u.id "
        "WHERE d.archived=0 AND d.dependent_type<>'agent' AND d.confirmed_at IS NULL) "
        "SELECT 1 FROM upstream WHERE type=? AND id=?", (*prerequisite, *dependent),
    ).fetchone() is not None


def _requires_confirmation(dependent: Key, prerequisite: Key) -> bool:
    return dependent[0] == "agent" or prerequisite[0] not in DONE_TYPES


def _create(
    conn: sqlite3.Connection, actor: dict, payload: dict, reporter: str | None = None,
) -> dict:
    dependent = _key(payload["dependent"])
    prerequisite = _key(payload["prerequisite"])
    context = _key(payload["context"]) if payload.get("context") else None
    if dependent == prerequisite:
        raise AppError(422, "validation", "A record cannot depend on itself", {"prerequisite": "self"})
    if (dependent[0] == "agent") != (context is not None):
        raise AppError(422, "validation", "Provide a work context for an agent dependency",
                       {"context": "required" if context is None else "unexpected"})
    condition = text(payload.get("condition", ""), "condition", limit=2000,
                     required=_requires_confirmation(dependent, prerequisite)).strip()
    records = _records(conn, [dependent, prerequisite, *([context] if context else [])])
    _require_available(records, dependent, "dependent")
    _require_available(records, prerequisite, "prerequisite")
    if context:
        _require_available(records, context, "context")
        duplicate = conn.execute(
            "SELECT 1 FROM dependencies WHERE dependent_type=? AND dependent_id=? AND prerequisite_type=? "
            "AND prerequisite_id=? AND context_type=? AND context_id=? AND condition=? "
            "AND archived=0 AND confirmed_at IS NULL", (*dependent, *prerequisite, *context, condition),
        ).fetchone()
    else:
        duplicate = conn.execute(
            "SELECT 1 FROM dependencies WHERE dependent_type=? AND dependent_id=? AND prerequisite_type=? "
            "AND prerequisite_id=? AND archived=0", (*dependent, *prerequisite),
        ).fetchone()
        if _would_cycle(conn, dependent, prerequisite):
            raise AppError(422, "validation", "Shared prerequisites cannot form a cycle", {"prerequisite": "cycle"})
    if duplicate:
        raise AppError(409, "conflict", "This dependency is already recorded", {"prerequisite": "duplicate"})
    identity, stamp = uid(), now()
    conn.execute(
        "INSERT INTO dependencies(id,dependent_type,dependent_id,prerequisite_type,prerequisite_id,"
        "context_type,context_id,condition,reported_by_agent_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (identity, *dependent, *prerequisite, *(context or (None, None)), condition, reporter, stamp, stamp),
    )
    row = conn.execute("SELECT * FROM dependencies WHERE id=?", (identity,)).fetchone()
    outcome = _link(row, records)
    event(conn, actor, "dependency.report" if reporter else "dependency.create", identity, None, outcome, at=stamp)
    return outcome


def create(conn: sqlite3.Connection, actor: dict, payload: dict, *, proposed: bool = False) -> dict:
    if actor["kind"] != "human" and not proposed:
        raise AppError(403, "agent_scope", "Use a proposal to create a shared dependency")
    return _create(conn, actor, payload)


def report(conn: sqlite3.Connection, actor: dict, payload: dict) -> dict:
    if actor["kind"] != "agent":
        raise AppError(403, "agent_scope", "Only the reporting agent can record its own wait")
    return _create(conn, actor, {**payload, "dependent": {"type": "agent", "id": actor["id"]}}, actor["id"])


def transition(
    conn: sqlite3.Connection, actor: dict, verb: str, payload: dict, *, proposed: bool = False,
) -> dict:
    if verb not in {"confirm", "archive"}:
        raise AppError(422, "validation", "Unknown dependency command")
    identity = payload["id"]
    row = conn.execute("SELECT * FROM dependencies WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Dependency not found")
    if actor["kind"] == "agent" and not proposed and row["reported_by_agent_id"] != actor["id"]:
        raise AppError(403, "agent_scope", "Only the reporting agent can change this wait directly")
    require_revision(row, payload.get("revision"))
    records = _load_rows(conn, [row])
    before = _link(row, records)
    if row["archived"]:
        if verb == "archive":
            return before
        raise AppError(409, "archived", "This dependency was archived")
    stamp = now()
    if verb == "confirm":
        dependent, prerequisite, _ = _references(row)
        if not _requires_confirmation(dependent, prerequisite):
            raise AppError(422, "validation", "This prerequisite follows the linked record's Done status")
        if row["confirmed_at"]:
            return before
        note = text(payload.get("note", ""), "note", limit=2000)
        conn.execute(
            "UPDATE dependencies SET confirmed_at=?,confirmed_by_id=?,confirmed_by_label=?,confirmation_note=?,"
            "revision=revision+1,updated_at=? WHERE id=?", (stamp, actor["id"], actor["label"], note, stamp, identity),
        )
    else:
        conn.execute(
            "UPDATE dependencies SET archived=1,revision=revision+1,updated_at=? WHERE id=?", (stamp, identity),
        )
    current = conn.execute("SELECT * FROM dependencies WHERE id=?", (identity,)).fetchone()
    after = _link(current, records)
    event(conn, actor, f"dependency.{verb}", identity, before, after, at=stamp)
    return after
