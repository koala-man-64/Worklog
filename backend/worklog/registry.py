"""Who works on what, gathered from the records agents already write.

The registry writes nothing. Runs, participation, status updates, discussion
messages, responsibility roles and tracked chats each remain the one record of
their fact; this module only collects them per agent identity, so it cannot drift
from them. Reported chat IDs, paths and branches stay provenance, never proof.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from . import record_kinds
from .common import LIVE_AGENT, AppError, rows_in, text
from .coordination import CoordinationService
from .discussions import LISTED, DiscussionService, registered_by, registered_chats
from .tickets import TicketService

STALE_AFTER = timedelta(hours=2)
TIE_LIMIT = 200
SNIPPET = 1000
# An overlap check names at most this many records of each kind, and lists at most OVERLAP_LIMIT of
# each kind of finding (per repository for announcements).
OVERLAP_IDS = 20
OVERLAP_LIMIT = 50
# How an agent came to be tied to a record, in display order.
VIA = ("participation", "role", "update", "message", "run", "confirmed", "chat")


def _latest(*values: str | None) -> str | None:
    present = [value for value in values if value]
    return max(present) if present else None


def _stale(last_seen_at: str) -> bool:
    return datetime.fromisoformat(last_seen_at) < datetime.now(UTC) - STALE_AFTER


def _why(entry: dict[str, Any]) -> dict[str, Any]:
    """A tie's reasons in display order, whether something still open holds it, and its latest time."""
    return {"via": [v for v in VIA if v in entry["via"]], "current": entry["current"], "lastAt": entry["lastAt"]}


def _first(records: list[dict]) -> list[dict]:
    """Current ties first, then the most recent."""
    records.sort(key=lambda r: r["lastAt"] or "", reverse=True)
    records.sort(key=lambda r: not r["current"])
    return records


class _Ties:
    """Distinct records with every reason that ties them and the latest time it happened."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {}

    def add(self, identity: str | None, via: str, at: str | None, current: bool = False) -> None:
        if not identity:
            return
        entry = self.entries.setdefault(identity, {"via": set(), "lastAt": None, "current": False})
        entry["via"].add(via)
        entry["lastAt"] = _latest(entry["lastAt"], at)
        entry["current"] = entry["current"] or current

    def render(self, records: dict[str, dict]) -> dict:
        """Current ties first, then the most recent; unresolvable IDs are left out."""
        items = _first([{**records[identity], **_why(entry)} for identity, entry in self.entries.items() if identity in records])
        return {"items": items[:TIE_LIMIT], "total": len(items)}


class _Tied:
    """What one agent's work ties it to, by kind of record, before any record is named."""

    def __init__(self) -> None:
        self.items, self.roadmaps, self.repositories, self.chats = _Ties(), _Ties(), _Ties(), _Ties()
        # A step ties its roadmap once the step's roadmap is known.
        self.steps: list[tuple[str, str, str | None, bool]] = []
        # Each tracked chat it is tied to: the latest time, and whether something still open ties it.
        self.chat_ties: dict[str, tuple[str | None, bool]] = {}


class AgentRegistry:
    def _agent(self, c: sqlite3.Connection, identity: str) -> sqlite3.Row:
        row = c.execute(f"SELECT a.*,{LIVE_AGENT} AS active FROM communication_agents a WHERE a.id=?", (identity,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Agent not found")
        return row

    @staticmethod
    def _work_titles(c: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict]:
        # A step's roadmap wins over the item's own roadmap, as on the Chats page.
        rows = rows_in(c, "SELECT w.id,w.title,w.status,w.archived,COALESCE(s.roadmap_id,w.roadmap_id) AS roadmap_id FROM work_items w LEFT JOIN steps s ON s.id=w.step_id WHERE w.id IN ({})", ids)
        return {r["id"]: {"id": r["id"], "title": r["title"], "status": r["status"],
                          "archived": bool(r["archived"]), "roadmapId": r["roadmap_id"]} for r in rows}

    @staticmethod
    def _roadmap_titles(c: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict]:
        rows = rows_in(c, "SELECT id,title,archived FROM roadmaps WHERE id IN ({})", ids)
        return {r["id"]: {"id": r["id"], "title": r["title"], "archived": bool(r["archived"])} for r in rows}

    @staticmethod
    def _step_roadmaps(c: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict]:
        rows = rows_in(c, "SELECT id,title,roadmap_id FROM steps WHERE id IN ({})", ids)
        return {r["id"]: {"title": r["title"], "roadmapId": r["roadmap_id"]} for r in rows}

    def _roles(self, c: sqlite3.Connection, owner_id: str | None) -> dict[str, list[dict]]:
        """Owned responsibility roles per agent, with the roadmap each one sits within."""
        clause, args = ("=?", [owner_id]) if owner_id else (" IS NOT NULL", [])
        rows = c.execute("SELECT * FROM discussion_roles WHERE owner_id" + clause + " ORDER BY updated_at DESC,id", args).fetchall()
        items = self._work_titles(c, [r["context_id"] for r in rows if r["context_type"] == "item"])
        steps = self._step_roadmaps(c, [r["context_id"] for r in rows if r["context_type"] == "step"])
        roadmap_ids = {r["context_id"] for r in rows if r["context_type"] == "roadmap"}
        roadmap_ids |= {v["roadmapId"] for v in [*items.values(), *steps.values()] if v["roadmapId"]}
        roadmaps = self._roadmap_titles(c, roadmap_ids)
        result: dict[str, list[dict]] = {}
        for r in rows:
            kind, context = r["context_type"], r["context_id"]
            if kind == "item":
                title, roadmap_id = items.get(context, {}).get("title"), items.get(context, {}).get("roadmapId")
            elif kind == "step":
                title, roadmap_id = steps.get(context, {}).get("title"), steps.get(context, {}).get("roadmapId")
            else:
                title, roadmap_id = roadmaps.get(context, {}).get("title"), context
            result.setdefault(r["owner_id"], []).append({
                "id": r["id"], "label": r["label"], "contextType": kind, "contextId": context,
                "contextTitle": title or context, "roadmap": roadmaps.get(roadmap_id) if roadmap_id else None,
                "updatedAt": r["updated_at"]})
        return result

    def _working(self, c: sqlite3.Connection, agent_id: str | None) -> dict[str, list[dict]]:
        """Open participation per agent: the work and repositories it currently reports."""
        clause, args = (" AND ar.agent_id=?", [agent_id]) if agent_id else ("", [])
        rows = c.execute("SELECT p.*,ar.agent_id,ar.display_name FROM participations p JOIN agent_runs ar ON ar.id=p.run_id WHERE p.released_at IS NULL" + clause + " ORDER BY p.last_seen_at DESC,p.id DESC", args).fetchall()
        items = self._work_titles(c, [r["item_id"] for r in rows if r["item_id"]])
        roadmaps = self._roadmap_titles(c, [v["roadmapId"] for v in items.values() if v["roadmapId"]])
        result: dict[str, list[dict]] = {}
        for r in rows:
            item = items.get(r["item_id"]) if r["item_id"] else None
            result.setdefault(r["agent_id"], []).append({
                "participationId": r["id"], "runId": r["run_id"], "runName": r["display_name"],
                "description": r["description"], "lastSeenAt": r["last_seen_at"], "stale": _stale(r["last_seen_at"]),
                "item": item, "roadmap": roadmaps.get(item["roadmapId"]) if item and item["roadmapId"] else None,
                "contexts": CoordinationService._contexts(c, r["id"])})
        return result

    def _summaries(self, c: sqlite3.Connection, agents: list[sqlite3.Row]) -> list[dict]:
        """Last activity counts only what the agent itself did, never a human closing its runs."""
        one = agents[0]["id"] if len(agents) == 1 else None
        where, args = (" WHERE agent_id=?", [one]) if one else ("", [])
        runs = {r["agent_id"]: r for r in c.execute("SELECT agent_id,COUNT(*) AS total,SUM(ended_at IS NULL) AS open,MAX(created_at) AS last FROM agent_runs" + where + " GROUP BY agent_id", args)}
        providers: dict[str, list[str]] = {}
        for r in c.execute("SELECT DISTINCT agent_id,provider FROM agent_runs" + (where + " AND" if one else " WHERE") + " provider<>'' ORDER BY provider", args):
            providers.setdefault(r["agent_id"], []).append(r["provider"])
        seen = {r[0]: r[1] for r in c.execute("SELECT ar.agent_id,MAX(p.last_seen_at) FROM participations p JOIN agent_runs ar ON ar.id=p.run_id" + (" WHERE ar.agent_id=?" if one else "") + " GROUP BY ar.agent_id", args)}
        updates = {r[0]: r[1] for r in c.execute("SELECT agent_id,MAX(created_at) FROM coordination_updates" + where + " GROUP BY agent_id", args)}
        messages = {r["author_id"]: r for r in c.execute("SELECT author_id,COUNT(*) AS total,MAX(created_at) AS last FROM discussion_messages WHERE author_kind='agent'" + (" AND author_id=?" if one else "") + " GROUP BY author_id", args)}
        working, roles = self._working(c, one), self._roles(c, one)
        result = []
        for a in agents:
            run, message = runs.get(a["id"]), messages.get(a["id"])
            result.append({
                "id": a["id"], "label": a["label"], "active": bool(a["active"]), "createdAt": a["created_at"],
                "lastActivityAt": _latest(run["last"] if run else None, seen.get(a["id"]), updates.get(a["id"]), message["last"] if message else None),
                "providers": providers.get(a["id"], []),
                "openRuns": run["open"] if run else 0, "runCount": run["total"] if run else 0,
                "messageCount": message["total"] if message else 0,
                "working": working.get(a["id"], []), "roles": roles.get(a["id"], [])})
        return result

    def agents(self, c: sqlite3.Connection, limit: int = 50, offset: int = 0, q: str = "") -> dict:
        """Agents with an active credential first, then the most recently active; `q` keeps those
        whose label holds it, ignoring case. Each comes as its card on the Agents page shows it: with
        its avatar, and the roadmaps and repositories its work ties it to, as its own page lists them."""
        rows = c.execute(f"SELECT a.*,{LIVE_AGENT} AS active FROM communication_agents a").fetchall()
        if q.strip():
            rows = [r for r in rows if q.strip().casefold() in r["label"].casefold()]
        summaries = self._summaries(c, rows) if rows else []
        summaries.sort(key=lambda s: (s["lastActivityAt"] or s["createdAt"], s["id"]), reverse=True)
        summaries.sort(key=lambda s: not s["active"])
        page = summaries[offset:offset + limit]
        avatars = {r["id"]: r["avatar"] for r in rows}
        ties, _ = self._ties(c, {s["id"]: s["roles"] for s in page})
        items = [{**s, "avatar": avatars[s["id"]], "roadmaps": ties[s["id"]]["roadmaps"],
                  "repositories": ties[s["id"]]["repositories"]} for s in page]
        return {"items": items, "total": len(summaries), "limit": limit, "offset": offset}

    @staticmethod
    def _chats(c: sqlite3.Connection, chat_ids: Iterable[str]) -> list[sqlite3.Row]:
        return rows_in(c, "SELECT * FROM agent_chats WHERE chat_id IN ({})", chat_ids)

    def _gather(self, c: sqlite3.Connection, roles: dict[str, list[dict]]) -> tuple[
            dict[str, _Tied], list[sqlite3.Row], dict[str, list[sqlite3.Row]], dict[str, dict]]:
        """What each agent's runs, participation, updates, messages, roles and confirmed chats tie it
        to, before any record is named: its work items, roadmaps, repositories and tracked chats, each
        with every reason and the latest time. A tie to a step or to a work item is a tie to its
        roadmap too. `roles` holds the roles each agent owns, keyed by the agents to gather for, so a
        page of agents costs the same few queries as one agent.

        Also gives the tracked chats those agents' runs, messages and confirmations name, those
        chats by chat ID, and each work item tied, with its roadmap."""
        tied = {identity: _Tied() for identity in roles}
        for r in rows_in(c, "SELECT ar.agent_id,p.item_id,p.last_seen_at,p.released_at,pc.repository_id FROM participations p "
                            "JOIN agent_runs ar ON ar.id=p.run_id LEFT JOIN participation_contexts pc ON pc.participation_id=p.id "
                            "WHERE ar.agent_id IN ({})", tied):
            agent, current = tied[r["agent_id"]], r["released_at"] is None
            agent.items.add(r["item_id"], "participation", r["last_seen_at"], current)
            agent.repositories.add(r["repository_id"], "participation", r["last_seen_at"], current)
        for r in rows_in(c, "SELECT u.agent_id,u.item_id,u.created_at,ur.repository_id FROM coordination_updates u "
                            "LEFT JOIN coordination_update_repositories ur ON ur.update_id=u.id WHERE u.agent_id IN ({})", tied):
            agent = tied[r["agent_id"]]
            agent.items.add(r["item_id"], "update", r["created_at"])
            agent.repositories.add(r["repository_id"], "update", r["created_at"])
        for identity, owned in roles.items():
            agent = tied[identity]
            for role in owned:
                target = {"item": agent.items, "roadmap": agent.roadmaps}.get(role["contextType"])
                if target is not None:
                    target.add(role["contextId"], "role", role["updatedAt"], True)
                else:
                    agent.steps.append((role["contextId"], "role", role["updatedAt"], True))

        # Messages tie each agent to their discussions' work contexts and reported chats.
        posted: dict[str, dict[str, str]] = {}
        chat_refs: dict[str, list[tuple[str, str, str | None, bool]]] = {identity: [] for identity in tied}
        for r in rows_in(c, "SELECT author_id,discussion_id,chat_id,created_at FROM discussion_messages "
                            "WHERE author_kind='agent' AND author_id IN ({})", tied):
            latest = posted.setdefault(r["discussion_id"], {})
            latest[r["author_id"]] = _latest(latest.get(r["author_id"]), r["created_at"]) or r["created_at"]
            if r["chat_id"]:
                chat_refs[r["author_id"]].append((r["chat_id"], "message", r["created_at"], False))
        # A thread's work contexts and repositories tie them; its other tags (v22) tie nothing here.
        for r in rows_in(c, "SELECT * FROM discussion_contexts WHERE discussion_id IN ({})", posted):
            for identity, last in posted[r["discussion_id"]].items():
                agent = tied[identity]
                if r["context_type"] == "step":
                    agent.steps.append((r["context_id"], "message", last, False))
                elif r["context_type"] in {"item", "roadmap", "repository"}:
                    {"item": agent.items, "roadmap": agent.roadmaps,
                     "repository": agent.repositories}[r["context_type"]].add(r["context_id"], "message", last)
        for r in rows_in(c, "SELECT agent_id,external_chat_id,created_at,ended_at FROM agent_runs WHERE agent_id IN ({})", tied):
            if r["external_chat_id"]:
                chat_refs[r["agent_id"]].append((r["external_chat_id"], "run", r["created_at"], r["ended_at"] is None))
        # The person's word that the agent works in a chat ties it too, though not to current work.
        for r in rows_in(c, "SELECT agent_id,chat_id,created_at FROM chat_confirmations WHERE agent_id IN ({})", tied):
            chat_refs[r["agent_id"]].append((r["chat_id"], "confirmed", r["created_at"], False))

        # A reported chat ID that a tracked chat carries ties the agent to that chat's work.
        tracked = self._chats(c, {ref[0] for refs in chat_refs.values() for ref in refs})
        by_chat_id: dict[str, list[sqlite3.Row]] = {}
        for chat in tracked:
            by_chat_id.setdefault(chat["chat_id"], []).append(chat)
        tracked_by_id = {chat["id"]: chat for chat in tracked}
        holders: dict[str, list[_Tied]] = {}
        for identity, refs in chat_refs.items():
            agent = tied[identity]
            for chat_id, via, at, current in refs:
                for chat in by_chat_id.get(chat_id, []):
                    agent.chats.add(chat["id"], via, at, current)
                    prior = agent.chat_ties.get(chat["id"], (None, False))
                    agent.chat_ties[chat["id"]] = (_latest(prior[0], at), prior[1] or current)
            for chat_key, (at, current) in agent.chat_ties.items():
                chat = tracked_by_id[chat_key]
                {"item": agent.items, "roadmap": agent.roadmaps}[chat["owner_type"]].add(chat["owner_id"], "chat", at, current)
                holders.setdefault(chat_key, []).append(agent)
        for r in rows_in(c, "SELECT chat_id,repository_id FROM chat_repositories WHERE chat_id IN ({})", holders):
            for agent in holders[r["chat_id"]]:
                agent.repositories.add(r["repository_id"], "chat", *agent.chat_ties[r["chat_id"]])

        steps = self._step_roadmaps(c, [step[0] for agent in tied.values() for step in agent.steps])
        work = self._work_titles(c, [item for agent in tied.values() for item in agent.items.entries])
        for agent in tied.values():
            for step_id, via, at, current in agent.steps:
                agent.roadmaps.add(steps.get(step_id, {}).get("roadmapId"), via, at, current)
            # Work on a roadmap's item is work on that roadmap, for every reason it was tied.
            for item_id, entry in agent.items.entries.items():
                roadmap_id = work.get(item_id, {}).get("roadmapId")
                for via in entry["via"]:
                    agent.roadmaps.add(roadmap_id, via, entry["lastAt"], entry["current"])
        return tied, tracked, by_chat_id, work

    def _ties(self, c: sqlite3.Connection, roles: dict[str, list[dict]]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
        """What each agent's work ties it to (`_gather`), each record named, as an agent's page lists
        them: its work items, roadmaps, repositories and tracked chats.

        Also gives the tracked chats that carry each chat ID those agents' runs, messages and
        confirmations name, for the runs that report them."""
        tied, tracked, by_chat_id, work = self._gather(c, roles)
        roadmap_titles = self._roadmap_titles(c, [r for agent in tied.values() for r in agent.roadmaps.entries])
        for record in work.values():
            record["roadmap"] = roadmap_titles.get(record.pop("roadmapId") or "")
        repos = {r["id"]: {"id": r["id"], "name": r["name"], "archived": bool(r["archived"])}
                 for r in rows_in(c, "SELECT id,name,archived FROM repositories WHERE id IN ({})",
                                  [r for agent in tied.values() for r in agent.repositories.entries])}
        # Every tracked chat's owner was tied above, for the agent whose reference found it, so its
        # title is already resolved.
        owner_titles = {**{k: v["title"] for k, v in work.items()}, **{k: v["title"] for k, v in roadmap_titles.items()}}
        chat_records = {chat["id"]: {"id": chat["id"], "chatId": chat["chat_id"], "title": chat["title"],
                                     "provider": chat["provider"], "model": chat["model"], "archived": bool(chat["archived"]),
                                     "ownerType": chat["owner_type"], "ownerId": chat["owner_id"],
                                     "ownerTitle": owner_titles.get(chat["owner_id"], chat["owner_id"])} for chat in tracked}
        rendered = {identity: {"workItems": agent.items.render(work), "roadmaps": agent.roadmaps.render(roadmap_titles),
                               "repositories": agent.repositories.render(repos), "chats": agent.chats.render(chat_records)}
                    for identity, agent in tied.items()}
        return rendered, {chat_id: [chat_records[chat["id"]] for chat in chats] for chat_id, chats in by_chat_id.items()}

    def agent(self, c: sqlite3.Connection, identity: str) -> dict:
        row = self._agent(c, identity)
        summary = self._summaries(c, [row])[0]
        ties, carried = self._ties(c, {identity: summary["roles"]})
        runs = c.execute("SELECT * FROM agent_runs WHERE agent_id=? ORDER BY created_at DESC,id DESC", (identity,)).fetchall()
        credentials = c.execute("SELECT cr.id,cr.label,cr.created_at,cr.revoked FROM credentials cr JOIN communication_credentials cc ON cc.credential_id=cr.id WHERE cc.agent_id=? ORDER BY cr.created_at DESC,cr.id", (identity,)).fetchall()
        sponsors = registered_by(c, [r["id"] for r in credentials])
        registered = registered_chats(c, [r["id"] for r in credentials])
        return {**summary, "avatar": row["avatar"], **ties[identity],
                "credentials": [{"id": r["id"], "label": r["label"], "createdAt": r["created_at"], "revoked": bool(r["revoked"]),
                                 "registeredBy": sponsors.get(r["id"]), "chatId": registered.get(r["id"])} for r in credentials],
                "runs": {"items": [{**CoordinationService.run_record(r), "chats": carried.get(r["external_chat_id"] or "", [])}
                                   for r in runs[:TIE_LIMIT]], "total": len(runs)}}

    def roadmap(self, c: sqlite3.Connection, roadmap_id: str, archived: Callable[[str], bool]) -> dict:
        """Who works on a roadmap now and on which of its steps, from current ties only.

        Open participation in its work items, roles owned in it and its tracked chats each tie
        a step: the one a chat was assigned to, or the work item's step. A tracked chat belongs
        to the agents whose runs reported its chat ID and otherwise stands for itself. Work with
        no step, or on a retired one, ties the roadmap itself, with a null stepId.

        Finished work is left out. An agent counts only while it is at work, with an open run
        and an active credential. A chat counts until the tool that keeps it archives it, which
        `archived` asks by chat ID; once runs reported it, it counts only while one of them is
        open under an agent at work.
        """
        if c.execute("SELECT 1 FROM roadmaps WHERE id=?", (roadmap_id,)).fetchone() is None:
            raise AppError(404, "not_found", "Roadmap not found")
        working = {r[0] for r in c.execute(f"SELECT DISTINCT ar.agent_id FROM agent_runs ar JOIN communication_agents a ON a.id=ar.agent_id WHERE ar.ended_at IS NULL AND {LIVE_AGENT}")}
        retired = {r["id"]: bool(r["retired"]) for r in c.execute("SELECT id,retired FROM steps WHERE roadmap_id=?", (roadmap_id,))}
        # A step's roadmap wins over the item's own, as on the Chats page.
        items = {r["id"]: r for r in c.execute("SELECT w.id,w.title,w.step_id FROM work_items w LEFT JOIN steps s ON s.id=w.step_id WHERE w.archived=0 AND COALESCE(s.roadmap_id,w.roadmap_id)=?", (roadmap_id,))}

        def step(step_id: str | None) -> str | None:
            """The step if it is still in the plan; otherwise the work counts for the roadmap."""
            return step_id if retired.get(step_id or "") is False else None

        def titled(item_id: str) -> dict:
            return {"id": item_id, "title": items[item_id]["title"]}

        workers: dict[tuple[str, str], dict[tuple, dict[str, Any]]] = {}

        def tie(kind: str, identity: str, step_id: str | None, via: str, at: str | None, stale: bool = False, *,
                item: dict | None = None, chat: dict | None = None, role: dict | None = None) -> None:
            """One tie per step, reason and record; a repeat keeps the latest time and is stale only if all are."""
            if kind == "agent" and identity not in working:
                return
            key = (step_id, via, *(target and target["id"] for target in (item, chat, role)))
            found = workers.setdefault((kind, identity), {})
            prior = found.get(key)
            if prior is None:
                found[key] = {"stepId": step_id, "via": via, "item": item, "chat": chat, "role": role, "lastAt": at, "stale": stale}
            else:
                prior["lastAt"], prior["stale"] = _latest(prior["lastAt"], at), prior["stale"] and stale

        for r in rows_in(c, "SELECT p.item_id,p.last_seen_at,ar.agent_id FROM participations p JOIN agent_runs ar ON ar.id=p.run_id WHERE p.released_at IS NULL AND p.item_id IN ({})", items):
            tie("agent", r["agent_id"], step(items[r["item_id"]]["step_id"]), "participation", r["last_seen_at"],
                _stale(r["last_seen_at"]), item=titled(r["item_id"]))
        for r in c.execute("SELECT id,label,context_type,context_id,owner_id,updated_at FROM discussion_roles WHERE owner_id IS NOT NULL"):
            kind, context = r["context_type"], r["context_id"]
            if kind == "roadmap" and context == roadmap_id:
                role_step = None
            elif kind == "step" and context in retired:
                role_step = step(context)
            elif kind == "item" and context in items:
                role_step = step(items[context]["step_id"])
            else:
                continue
            tie("agent", r["owner_id"], role_step, "role", r["updated_at"], role={"id": r["id"], "label": r["label"]})

        chats = {r["id"]: r for r in c.execute("SELECT * FROM agent_chats WHERE archived=0 AND owner_type='roadmap' AND owner_id=?", (roadmap_id,))}
        chats.update((r["id"], r) for r in rows_in(c, "SELECT * FROM agent_chats WHERE archived=0 AND owner_type='item' AND owner_id IN ({})", items))
        chats = {key: r for key, r in chats.items() if not archived(r["chat_id"])}
        assigned: dict[str, list[tuple[str | None, str]]] = {}
        for r in rows_in(c, "SELECT chat_id,step_id,created_at FROM chat_steps WHERE chat_id IN ({})", chats):
            if step(r["step_id"]):
                assigned.setdefault(r["chat_id"], []).append((r["step_id"], r["created_at"]))
        # Every chat a run reported is noted; only open runs of agents at work carry it.
        carriers: dict[str, set[str]] = {}
        for r in rows_in(c, "SELECT external_chat_id,agent_id,ended_at FROM agent_runs WHERE external_chat_id IN ({})", [ch["chat_id"] for ch in chats.values()]):
            carried = carriers.setdefault(r["external_chat_id"], set())
            if r["ended_at"] is None and r["agent_id"] in working:
                carried.add(r["agent_id"])
        for chat in chats.values():
            if chat["chat_id"] in carriers and not carriers[chat["chat_id"]]:
                continue  # every run that reported it has finished
            owner = titled(chat["owner_id"]) if chat["owner_type"] == "item" else None
            # A roadmap's chat works on the steps assigned to it, or on the roadmap while it has none.
            places = assigned.get(chat["id"]) or [(step(items[chat["owner_id"]]["step_id"]) if owner else None, chat["created_at"])]
            record = {"id": chat["id"], "title": chat["title"]}
            for kind, identity in [("agent", a) for a in sorted(carriers.get(chat["chat_id"], ()))] or [("chat", chat["id"])]:
                for step_id, at in places:
                    tie(kind, identity, step_id, "chat", at, item=owner, chat=record)

        agents = {r["id"]: r for r in rows_in(c, "SELECT id,label,avatar FROM communication_agents WHERE id IN ({})", [i for kind, i in workers if kind == "agent"])}
        result = []
        for (kind, identity), found in workers.items():
            if kind == "chat":
                row = chats[identity]
                who = {"kind": "chat", "id": identity, "label": row["title"], "avatar": None, "provider": row["provider"], "model": row["model"]}
            elif identity in agents:
                row = agents[identity]
                who = {"kind": "agent", "id": identity, "label": row["label"], "avatar": row["avatar"], "provider": "", "model": ""}
            else:
                continue
            ties = sorted(found.values(), key=lambda t: t["lastAt"] or "", reverse=True)
            result.append({**who, "ties": ties, "lastAt": ties[0]["lastAt"], "stale": all(t["stale"] for t in ties)})
        # Fresh work first, then the most recent.
        result.sort(key=lambda w: (w["lastAt"] or "", w["id"]), reverse=True)
        result.sort(key=lambda w: w["stale"])
        return {"items": result}

    def roster(self, c: sqlite3.Connection, roadmap_id: str) -> dict:
        """Every agent whose work ties it to the roadmap, now or before. The rule is the one that
        names the roadmaps on an agent's card and page (`_gather`), so a roadmap lists exactly the
        agents whose cards name it. Revoked identities stay: their work happened.

        Each comes with every reason, whether something still open ties it, the latest time, and the
        steps still in the plan that its work ties it to: a work item's step, a step's roles and
        discussions, and each step a roadmap's tracked chat is assigned to. Work with no step, or on a
        retired one, ties the roadmap alone. Current ties first, then the latest; the first TIE_LIMIT,
        and `total` counts them all.
        """
        if c.execute("SELECT 1 FROM roadmaps WHERE id=?", (roadmap_id,)).fetchone() is None:
            raise AppError(404, "not_found", "Roadmap not found")
        agents = c.execute(f"SELECT a.id,a.label,a.avatar,{LIVE_AGENT} AS active FROM communication_agents a ORDER BY a.id").fetchall()
        owned = self._roles(c, None)
        tied, tracked, _, _ = self._gather(c, {a["id"]: owned.get(a["id"], []) for a in agents})
        plan = {r[0] for r in c.execute("SELECT id FROM steps WHERE roadmap_id=? AND retired=0", (roadmap_id,))}
        # A work item's step, where `_gather` finds the item's roadmap: the step's, over the item's own.
        item_steps = {r[0]: r[1] for r in c.execute("SELECT w.id,w.step_id FROM work_items w JOIN steps s ON s.id=w.step_id "
                                                    "WHERE s.roadmap_id=? AND s.retired=0", (roadmap_id,))}
        chats = [chat["id"] for chat in tracked if chat["owner_type"] == "roadmap" and chat["owner_id"] == roadmap_id]
        assigned: dict[str, list[str]] = {}
        for r in rows_in(c, "SELECT chat_id,step_id FROM chat_steps WHERE chat_id IN ({})", chats):
            if r["step_id"] in plan:
                assigned.setdefault(r["chat_id"], []).append(r["step_id"])
        items = []
        for a in agents:
            agent = tied[a["id"]]
            entry = agent.roadmaps.entries.get(roadmap_id)
            if entry is None:
                continue
            places = _Ties()
            for item_id, tie in agent.items.entries.items():
                if item_id in item_steps:
                    for via in tie["via"]:
                        places.add(item_steps[item_id], via, tie["lastAt"], tie["current"])
            for step_id, via, at, current in agent.steps:
                if step_id in plan:
                    places.add(step_id, via, at, current)
            for chat_key, (at, current) in agent.chat_ties.items():
                for step_id in assigned.get(chat_key, ()):
                    places.add(step_id, "chat", at, current)
            steps = _first([{"stepId": step_id, **_why(tie)} for step_id, tie in sorted(places.entries.items())])
            items.append({"id": a["id"], "label": a["label"], "avatar": a["avatar"], "active": bool(a["active"]),
                          **_why(entry), "steps": steps})
        items = _first(items)
        return {"items": items[:TIE_LIMIT], "total": len(items)}

    def activity(self, c: sqlite3.Connection, identity: str, limit: int = 50, offset: int = 0) -> dict:
        """The agent's discussion messages and status updates, newest first."""
        self._agent(c, identity)
        union = "SELECT 'message' AS kind,id,created_at FROM discussion_messages WHERE author_id=? AND author_kind='agent' UNION ALL SELECT 'update',id,created_at FROM coordination_updates WHERE agent_id=?"
        total = c.execute("SELECT COUNT(*) FROM (" + union + ")", (identity, identity)).fetchone()[0]
        page = c.execute(union + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (identity, identity, limit, offset)).fetchall()
        messages = {r["id"]: r for r in rows_in(c, "SELECT m.*,d.title AS discussion_title FROM discussion_messages m JOIN discussions d ON d.id=m.discussion_id WHERE m.id IN ({})", [p["id"] for p in page if p["kind"] == "message"])}
        updates = {r["id"]: r for r in rows_in(c, "SELECT u.*,w.title AS item_title FROM coordination_updates u LEFT JOIN work_items w ON w.id=u.item_id WHERE u.id IN ({})", [p["id"] for p in page if p["kind"] == "update"])}
        tracked: dict[str, list[dict]] = {}
        for chat in self._chats(c, {m["chat_id"] for m in messages.values() if m["chat_id"]}):
            tracked.setdefault(chat["chat_id"], []).append({"id": chat["id"], "title": chat["title"]})
        result = []
        for p in page:
            if p["kind"] == "message":
                m = messages[p["id"]]
                result.append({"kind": "message", "id": m["id"], "createdAt": m["created_at"],
                               "text": m["text"][:SNIPPET], "truncated": len(m["text"]) > SNIPPET,
                               "discussionId": m["discussion_id"], "discussionTitle": m["discussion_title"],
                               "chatId": m["chat_id"], "chats": tracked.get(m["chat_id"] or "", [])})
            else:
                u = updates[p["id"]]
                result.append({"kind": "update", "id": u["id"], "createdAt": u["created_at"],
                               "text": u["text"][:SNIPPET], "truncated": len(u["text"]) > SNIPPET,
                               "updateKind": u["kind"], "runName": u["author_label"],
                               "participationId": u["participation_id"],
                               "item": {"id": u["item_id"], "title": u["item_title"]} if u["item_id"] else None,
                               "contexts": json.loads(u["contexts_json"])})
        return {"items": result, "total": total, "limit": limit, "offset": offset}

    def profile(self, c: sqlite3.Connection, identity: str) -> dict:
        """One participant as any caller may see it: who it is, whether its credential is live, what it
        reports working on, and the roles it owns, so an agent can tell whether the one whose work
        overlaps its own is still at it before it writes to it. Unlike the Agents page, which is the
        person's, it shows no credential and no chat its runs report."""
        if identity == "local-user":
            return {"id": "local-user", "label": "You", "kind": "human", "avatar": None, "active": True}
        row = self._agent(c, identity)
        return {**self._summaries(c, [row])[0], "kind": "agent", "avatar": row["avatar"]}

    @staticmethod
    def _identities(c: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict]:
        """Agent identities as the participants list shows them."""
        return {r["id"]: {"id": r["id"], "label": r["label"], "kind": "agent", "avatar": r["avatar"], "active": bool(r["active"])}
                for r in rows_in(c, f"SELECT a.id,a.label,a.avatar,{LIVE_AGENT} AS active FROM communication_agents a "
                                    "WHERE a.id IN ({})", ids)}

    @staticmethod
    def _named(values: Iterable[str], field: str) -> list[str]:
        named = list(dict.fromkeys(text(value, field, required=True, limit=200) for value in values))
        if len(named) > OVERLAP_IDS:
            raise AppError(422, "validation", f"Name at most {OVERLAP_IDS} records of each kind", {field: "limit"})
        return named

    @staticmethod
    def _existing(c: sqlite3.Connection, kind: str, ids: list[str], field: str) -> None:
        found = {row["id"]: row for row in record_kinds.rows(c, kind, ids)}
        for identity in ids:
            if identity not in found:
                raise AppError(404, "not_found", f"No {kind} has that id", {field: identity})
            if kind == "repository" and found[identity]["mergedInto"]:
                raise AppError(409, "repository_merged", f"{found[identity]['title']} was merged into another repository; "
                               "check that one instead", {"repositoryId": found[identity]["mergedInto"]})

    def overlaps(self, c: sqlite3.Connection, caller: str, *, repositories: Iterable[str] = (),
                 items: Iterable[str] = (), steps: Iterable[str] = (), tickets: Iterable[str] = (),
                 limit: int = 20) -> dict:
        """Who else is on these records, and what they said about them: what an agent checks before it
        changes them (the agent guide's rule 11). Leaving out what the caller itself did, it gives:
        each repository with its own discussion; the latest messages there, the opening one aside,
        each saying whether its author's credential is live and whether it reports work in that
        repository now; active participation in those repositories, on those work items or on work
        items on those steps; the other open threads tagged with any of the records where someone
        besides the caller wrote, pinned first;
        the roles others own on those work items and steps; others' ties to the records; and each
        ticket's assignee. Like everything it gathers, it reserves nothing, and it shows no chat a run
        reports, which is the person's to see (policy.py)."""
        repos, work = self._named(repositories, "repositoryId"), self._named(items, "itemId")
        step_ids, refs = self._named(steps, "stepId"), self._named(tickets, "ticketId")
        if not (repos or work or step_ids or refs):
            raise AppError(422, "validation", "Name a repository, work item, step or ticket to check")
        for kind, ids, field in (("repository", repos, "repositoryId"), ("item", work, "itemId"), ("step", step_ids, "stepId")):
            self._existing(c, kind, ids, field)
        def ticket(ref: str) -> sqlite3.Row:
            try:
                return TicketService.resolve(c, ref)
            except AppError as error:
                if error.code != "not_found":
                    raise
                raise AppError(404, "not_found", "No ticket has that key, number or id", {"ticketId": ref}) from None

        # A ticket named by its key and by its ID is one ticket.
        ticket_rows = list({row["id"]: row for row in map(ticket, refs)}.values())
        ticket_ids = [row["id"] for row in ticket_rows]

        def marks(values: list[str]) -> str:
            return ",".join("?" * len(values))

        homes = DiscussionService.home_discussions(c, repos)
        names = {row["id"]: row["title"] for row in record_kinds.rows(c, "repository", repos)} if repos else {}
        agent_ids: set[str] = set()

        # Participation in those repositories, or on that work, whether named or on those steps.
        scopes, args = [], []
        if repos:
            scopes.append(f"EXISTS(SELECT 1 FROM participation_contexts pc WHERE pc.participation_id=p.id AND pc.repository_id IN ({marks(repos)}))")
            args += repos
        if work:
            scopes.append(f"p.item_id IN ({marks(work)})")
            args += work
        if step_ids:
            scopes.append(f"p.item_id IN (SELECT id FROM work_items WHERE step_id IN ({marks(step_ids)}))")
            args += step_ids
        participating = c.execute(
            "SELECT p.*,ar.agent_id,ar.display_name FROM participations p JOIN agent_runs ar ON ar.id=p.run_id "
            "WHERE p.released_at IS NULL AND ar.agent_id<>? AND (" + " OR ".join(scopes) + ") "
            "ORDER BY p.last_seen_at DESC,p.id DESC LIMIT ?", [caller, *args, limit]).fetchall() if scopes else []
        agent_ids |= {r["agent_id"] for r in participating}

        # The latest messages in each repository's own discussion: its announcements.
        posted: list[tuple[str, sqlite3.Row]] = []
        reporting: dict[str, set[str]] = {}
        for repository in repos:
            discussion = homes.get(repository)
            if discussion is None:
                continue
            opening = c.execute("SELECT id FROM discussion_messages WHERE discussion_id=? ORDER BY created_at,id LIMIT 1",
                                (discussion,)).fetchone()
            # A message holds up to 1,000,000 bytes: read only as much as the snippet shows.
            posted += [(repository, m) for m in c.execute(
                "SELECT id,discussion_id,author_id,author_label,author_kind,created_at,substr(text,1,?) AS text "
                "FROM discussion_messages WHERE discussion_id=? AND author_id<>? AND id<>? ORDER BY created_at DESC,id DESC LIMIT ?",
                (SNIPPET + 1, discussion, caller, opening[0] if opening else "", limit))]
            # Reporting there now: unreleased participation in the repository, and not stale.
            reporting[repository] = {r["agent_id"] for r in c.execute(
                "SELECT ar.agent_id,p.last_seen_at FROM participations p JOIN agent_runs ar ON ar.id=p.run_id "
                "JOIN participation_contexts pc ON pc.participation_id=p.id "
                "WHERE p.released_at IS NULL AND pc.repository_id=?", (repository,)) if not _stale(r["last_seen_at"])}
        agent_ids |= {m["author_id"] for _, m in posted if m["author_kind"] == "agent"}

        # Other open threads tagged with any of the records, where someone besides the caller wrote.
        tagged = [("repository", r) for r in repos] + [("item", i) for i in work] + [("step", s) for s in step_ids] + \
                 [("ticket", t) for t in ticket_ids]
        pairs = " OR ".join("(dc.context_type=? AND dc.context_id=?)" for _ in tagged)
        flat = [value for pair in tagged for value in pair]
        held = list(homes.values())
        threads = c.execute(
            "SELECT d.id,d.title,d.pinned_at,d.updated_at FROM discussions d WHERE d.archived=0 AND EXISTS(SELECT 1 FROM "
            f"discussion_contexts dc WHERE dc.discussion_id=d.id AND ({pairs})) AND EXISTS(SELECT 1 FROM discussion_messages m "
            "WHERE m.discussion_id=d.id AND m.author_id<>?)" + (f" AND d.id NOT IN ({marks(held)})" if held else "") +
            " ORDER BY " + LISTED + " LIMIT ?", [*flat, caller, *held, limit]).fetchall()
        matched: dict[str, list[dict]] = {}
        for row in rows_in(c, "SELECT discussion_id,context_type,context_id FROM discussion_contexts WHERE discussion_id IN ({}) "
                              "ORDER BY context_type,context_id", [t["id"] for t in threads]):
            if (row["context_type"], row["context_id"]) in tagged:
                matched.setdefault(row["discussion_id"], []).append({"type": row["context_type"], "id": row["context_id"]})

        # Roles others own on the work, and others' ties to any of the records.
        owned_on = [("item", i) for i in work] + [("step", s) for s in step_ids]
        roles = c.execute(
            "SELECT * FROM discussion_roles WHERE owner_id IS NOT NULL AND owner_id<>? AND (" +
            " OR ".join("(context_type=? AND context_id=?)" for _ in owned_on) + ") ORDER BY context_type,context_id,label_key,id LIMIT ?",
            [caller, *[value for pair in owned_on for value in pair], limit]).fetchall() if owned_on else []
        ties = c.execute(
            "SELECT * FROM agent_links WHERE agent_id<>? AND (" + " OR ".join("(target_type=? AND target_id=?)" for _ in tagged) +
            ") ORDER BY target_type,target_id,agent_id LIMIT ?", [caller, *flat, limit]).fetchall()
        agent_ids |= {r["owner_id"] for r in roles} | {r["agent_id"] for r in ties}
        agent_ids |= {r["assignee_id"] for r in ticket_rows if r["assignee_id"] and r["assignee_id"] != "local-user"}

        agents = self._identities(c, agent_ids)
        person = {"id": "local-user", "label": "You", "kind": "human", "avatar": None, "active": True}
        titles = self._work_titles(c, [r["item_id"] for r in participating if r["item_id"]])

        def who(identity: str, kind: str = "agent") -> dict | None:
            return person if kind == "human" or identity == "local-user" else agents.get(identity)

        return {
            "repositories": [{"id": r, "name": names[r], "discussionId": homes.get(r)} for r in repos],
            "participations": [{
                "participationId": r["id"], "agent": who(r["agent_id"]), "runId": r["run_id"], "runName": r["display_name"],
                "description": r["description"], "lastSeenAt": r["last_seen_at"], "stale": _stale(r["last_seen_at"]),
                "item": {k: titles[r["item_id"]][k] for k in ("id", "title", "status")} if r["item_id"] in titles else None,
                "contexts": CoordinationService._contexts(c, r["id"])} for r in participating],
            "announcements": [{
                "repositoryId": repository, "discussionId": m["discussion_id"], "messageId": m["id"],
                "author": who(m["author_id"], m["author_kind"]), "authorId": m["author_id"], "authorLabel": m["author_label"],
                "authorReporting": m["author_id"] in reporting.get(repository, set()), "createdAt": m["created_at"],
                "text": m["text"][:SNIPPET], "truncated": len(m["text"]) > SNIPPET}
                for repository, m in sorted(posted, key=lambda entry: (entry[1]["created_at"], entry[1]["id"]), reverse=True)],
            "discussions": [{"id": t["id"], "title": t["title"], "pinned": t["pinned_at"] is not None, "updatedAt": t["updated_at"],
                             "matched": matched.get(t["id"], [])} for t in threads],
            "roles": [{"id": r["id"], "label": r["label"], "contextType": r["context_type"], "contextId": r["context_id"],
                       "owner": who(r["owner_id"])} for r in roles],
            "ties": [{"agent": who(r["agent_id"]), "targetType": r["target_type"], "targetId": r["target_id"],
                      "relationship": r["relationship"], "setBy": r["set_by"]} for r in ties],
            "tickets": [{"id": r["id"], "number": r["number"], "title": r["title"], "status": r["status"],
                         "assignee": who(r["assignee_id"]) if r["assignee_id"] else None} for r in ticket_rows],
        }
