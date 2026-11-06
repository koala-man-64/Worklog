"""Agent ties (schema v17): the records each agent identity is tied to, and its role on each.

The person ties any agent to any record. An agent ties itself directly, as it reports its own
participation: it acts only on its own identity, and changes or removes only the ties it set. A tie
the person set, or last changed, is the person's, so an agent never undoes what the person decided;
the person changes any tie, and it becomes theirs.

Ties are informational. They reserve and lock nothing, grant no authority, and never deliver a chat's
messages, which reach only the agents bound to that chat (v13). A tie stays when its record is
archived or retired, or its agent is retired: Worklog deletes no record a tie names, and a repository
merge moves the ties to the survivor (coordination.repository_merge).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from .common import LIVE_AGENT, AppError, event, now, read_events, rows_in, text, uid
from .coordination import repository_takes_people
from .proposals import can_see
from .tickets import TicketService

# What an agent can be tied to, in the order a list shows them. A tracked chat is not among them: an
# agent is bound to a chat by registering with it, or by the person confirming it there (v13), which
# also brings it the chat's messages. Each type's summary query gives its
# id, title and archived state, and what a page needs to link to it: a step, question or decision
# opens on its roadmap, and a ticket shows its key. A step that is retired, a repository that is
# merged and an agent whose credentials are all revoked count as archived.
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
}
TYPES = tuple(SUMMARIES)
RELATIONSHIP_LIMIT = 200
# The ties an agent may hold that it set itself; the person's ties on it don't count.
SELF_SET_LIMIT = 500
# The most ties one read returns, with the total beside them.
READ_LIMIT = 500
# The most records one read asks about.
TARGET_IDS_LIMIT = 200
_HIDDEN = frozenset({"actor_id", "submitted_at"})


def _target_type(value: Any) -> str:
    if not isinstance(value, str) or value not in SUMMARIES:
        raise AppError(422, "validation", "targetType must be one of " + ", ".join(TYPES), {"targetType": "invalid"})
    return value


def _agent_id(actor: dict, value: Any) -> str:
    """The identity a write acts on: the person names one, an agent acts only on itself."""
    if actor["kind"] == "agent":
        if value not in (None, "", actor["id"]):
            raise AppError(403, "forbidden", "An agent ties only itself; leave agentId out or give your own",
                           {"agentId": "not_yours"})
        return str(actor["id"])
    if not isinstance(value, str) or not value.strip():
        raise AppError(422, "validation", "agentId is required", {"agentId": "required"})
    return value


def _target_id(c: sqlite3.Connection, target_type: str, value: Any) -> str:
    """The record's own id: a ticket may also be named by its key, T-12, or its number."""
    raw = text(value, "targetId", required=True, limit=200)
    return str(TicketService.resolve(c, raw)["id"]) if target_type == "ticket" else raw


def summaries(c: sqlite3.Connection, target_type: str, ids: Iterable[str], reader: dict | None) -> dict[str, dict]:
    """Each named record of one type that exists and the reader may see, by id.

    Agents see another agent's proposal only once it was submitted; the person sees every one.
    """
    found: dict[str, dict] = {}
    for row in rows_in(c, SUMMARIES[target_type], ids):
        if target_type == "proposal" and reader is not None and not can_see(c, row, reader):
            continue
        record = {key: row[key] for key in row.keys() if key not in _HIDDEN}
        record["archived"] = bool(record["archived"])
        found[row["id"]] = record
    return found


def _agents(c: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict]:
    return {r["id"]: {"id": r["id"], "label": r["label"], "avatar": r["avatar"], "active": bool(r["active"])}
            for r in rows_in(c, f"SELECT a.id,a.label,a.avatar,{LIVE_AGENT} AS active FROM communication_agents a WHERE a.id IN ({{}})", ids)}


def _agent(c: sqlite3.Connection, agent_id: str) -> dict:
    found = _agents(c, [agent_id])
    if agent_id not in found:
        raise AppError(404, "not_found", "Agent not found")
    return found[agent_id]


def _record(row: Any, agent: dict | None, target: dict | None) -> dict:
    return {"id": row["id"], "agentId": row["agent_id"], "agent": agent, "targetType": row["target_type"],
            "targetId": row["target_id"], "target": target, "relationship": row["relationship"],
            "setBy": row["set_by"], "createdAt": row["created_at"]}


def _history(record: dict) -> dict:
    """What a tie's history keeps: who was tied to what, by name as they stood then."""
    return {"id": record["id"], "agentId": record["agentId"], "agentLabel": (record["agent"] or {}).get("label"),
            "targetType": record["targetType"], "targetId": record["targetId"],
            "targetTitle": (record["target"] or {}).get("title"), "relationship": record["relationship"],
            "setBy": record["setBy"], "createdAt": record["createdAt"]}


def _takes_new_tie(c: sqlite3.Connection, target_type: str, target: dict) -> None:
    """A new tie needs a record that takes changes, as a new person tie or a chat's new step does."""
    if target_type == "repository":
        repository_takes_people(c, target["id"], new=True, who="agents")
    elif target_type == "step" and target["archived"]:
        raise AppError(409, "conflict", "Retired steps cannot take on agents", {"targetId": "retired"})


def set_tie(c: sqlite3.Connection, actor: dict, p: dict, *, reader: dict) -> dict:
    """Tie an agent to a record, or change its role there.

    `actor` is the person, or the calling agent's identity; `reader` is the caller as the request
    named it, the credential for an agent, which deciding who may see a proposal needs.
    """
    agent_id = _agent_id(actor, p.get("agentId"))
    target_type = _target_type(p.get("targetType"))
    target_id = _target_id(c, target_type, p.get("targetId"))
    relationship = text(p.get("relationship") or "", "relationship", limit=RELATIONSHIP_LIMIT).strip()
    agent = _agent(c, agent_id)
    if target_type == "agent" and target_id == agent_id:
        raise AppError(422, "validation", "An agent cannot be tied to itself", {"targetId": "self"})
    target = summaries(c, target_type, [target_id], reader).get(target_id)
    if target is None:
        raise AppError(404, "not_found", "The record to tie does not exist", {"targetId": "not_found"})
    existing = c.execute("SELECT * FROM agent_links WHERE agent_id=? AND target_type=? AND target_id=?",
                         (agent_id, target_type, target_id)).fetchone()
    set_by = "human" if actor["kind"] == "human" else "agent"
    if existing is None:
        _takes_new_tie(c, target_type, target)
        if set_by == "agent":
            (held,) = c.execute("SELECT count(*) FROM agent_links WHERE agent_id=? AND set_by='agent'", (agent_id,)).fetchone()
            if held >= SELF_SET_LIMIT:
                raise AppError(429, "agent_limit", f"An agent holds at most {SELF_SET_LIMIT} ties it set itself; remove one first")
    elif set_by == "agent" and existing["set_by"] == "human":
        raise AppError(403, "forbidden", "The person set this tie; only they change or remove it", {"tieId": existing["id"]})
    before = _record(existing, agent, target) if existing else None
    row = {"id": existing["id"] if existing else uid(), "agent_id": agent_id, "target_type": target_type,
           "target_id": target_id, "relationship": relationship, "set_by": set_by,
           "created_at": existing["created_at"] if existing else now()}
    after = _record(row, agent, target)
    if before is not None and (before["relationship"], before["setBy"]) == (relationship, set_by):
        return after  # nothing changed, so nothing is written
    c.execute("INSERT INTO agent_links(id,agent_id,target_type,target_id,relationship,set_by,created_at) VALUES(?,?,?,?,?,?,?) "
              "ON CONFLICT(agent_id,target_type,target_id) DO UPDATE SET relationship=excluded.relationship,set_by=excluded.set_by",
              (row["id"], agent_id, target_type, target_id, relationship, set_by, row["created_at"]))
    # Both records keep it in their history, as a person's tie does.
    for entity in (agent_id, target_id):
        event(c, actor, "agent.link", entity, _history(before) if before else None, _history(after))
    return after


def remove_tie(c: sqlite3.Connection, actor: dict, p: dict, *, reader: dict) -> dict:
    """Untie an agent from a record. An agent removes only the ties it set, even to a record it can no
    longer see; a tie the person set to a draft it may not see answers as if there were none."""
    agent_id = _agent_id(actor, p.get("agentId"))
    target_type = _target_type(p.get("targetType"))
    target_id = _target_id(c, target_type, p.get("targetId"))
    existing = c.execute("SELECT * FROM agent_links WHERE agent_id=? AND target_type=? AND target_id=?",
                         (agent_id, target_type, target_id)).fetchone()
    hidden = (existing is not None and actor["kind"] == "agent" and existing["set_by"] == "human"
              and target_type == "proposal" and not summaries(c, "proposal", [target_id], reader))
    if existing is None or hidden:
        raise AppError(404, "not_found", "That agent is not tied to that record")
    if actor["kind"] == "agent" and existing["set_by"] == "human":
        raise AppError(403, "forbidden", "The person set this tie; only they change or remove it", {"tieId": existing["id"]})
    before = _record(existing, _agents(c, [agent_id]).get(agent_id),
                     summaries(c, target_type, [target_id], None).get(target_id))
    c.execute("DELETE FROM agent_links WHERE id=?", (existing["id"],))
    for entity in (agent_id, target_id):
        event(c, actor, "agent.unlink", entity, _history(before), None)
    return {"ok": True, "id": existing["id"], "agentId": agent_id, "targetType": target_type, "targetId": target_id}


def _render(c: sqlite3.Connection, rows: list[sqlite3.Row], reader: dict | None) -> dict:
    """The ties with their agents and records, in type order, live agents before retired ones, then by name.

    A tie to a record the reader may not see is left out, as the record would be.
    """
    agents = _agents(c, {r["agent_id"] for r in rows})
    targets: dict[str, dict[str, dict]] = {}
    for target_type in TYPES:
        ids = [r["target_id"] for r in rows if r["target_type"] == target_type]
        if ids:
            targets[target_type] = summaries(c, target_type, ids, reader)
    shown = []
    for r in rows:
        target = targets.get(r["target_type"], {}).get(r["target_id"])
        if target is None and r["target_type"] == "proposal":
            continue  # a draft the reader may not see
        shown.append(_record(r, agents.get(r["agent_id"]), target))
    order = {target_type: position for position, target_type in enumerate(TYPES)}
    shown.sort(key=lambda t: (order[t["targetType"]], not (t["agent"] or {}).get("active", False),
                              ((t["agent"] or {}).get("label") or "").casefold(),
                              ((t["target"] or {}).get("title") or "").casefold(), t["id"]))
    return {"items": shown[:READ_LIMIT], "total": len(shown)}


def visible_events(c: sqlite3.Connection, events: list[dict], reader: dict) -> list[dict]:
    """History as the reader may see it. A tie's history sits on its agent too, so an agent reading
    another agent's history would otherwise learn of a draft proposal it may not see: such entries
    are left out for agents, as the draft's own history is (proposals.can_see)."""
    if reader["kind"] != "agent":
        return events

    def proposal(entry: dict) -> str | None:
        if entry.get("kind") not in {"agent.link", "agent.unlink"}:
            return None
        state = entry.get("after") or entry.get("before")
        if not isinstance(state, dict) or state.get("targetType") != "proposal":
            return None
        return str(state.get("targetId"))

    named = {identity for identity in map(proposal, events) if identity}
    seen = set(summaries(c, "proposal", named, reader)) if named else set()
    return [entry for entry in events if (identity := proposal(entry)) is None or identity in seen]


def visible_history(c: sqlite3.Connection, entity_id: str, limit: int, reader: dict) -> list[dict]:
    """A record's latest history entries the reader may see, `limit` of them where it has that many: the
    page fills up past entries left out, so its length says nothing about them."""
    window = limit
    while True:
        entries = read_events(c, entity_id, window)
        shown = visible_events(c, entries, reader)
        if len(shown) >= limit or len(entries) < window:
            return shown[:limit]
        window *= 2


def ties_of_agent(c: sqlite3.Connection, agent_id: str, reader: dict | None) -> dict:
    """Everything one agent is tied to."""
    _agent(c, agent_id)
    return _render(c, c.execute("SELECT * FROM agent_links WHERE agent_id=?", (agent_id,)).fetchall(), reader)


def ties_on(c: sqlite3.Connection, target_type: str, target_ids: list[str], reader: dict | None) -> dict:
    """The agents tied to each of these records of one type, such as a roadmap's questions."""
    target_type = _target_type(target_type)
    if not target_ids or len(target_ids) > TARGET_IDS_LIMIT:
        raise AppError(422, "validation", f"Give 1 to {TARGET_IDS_LIMIT} targetId values", {"targetId": "invalid"})
    ids = [_target_id(c, target_type, value) for value in target_ids]
    # The type is one of SUMMARIES' keys, never the caller's text, so it can sit in the statement.
    rows = rows_in(c, f"SELECT * FROM agent_links WHERE target_type='{target_type}' AND target_id IN ({{}})", ids)
    return _render(c, rows, reader)
