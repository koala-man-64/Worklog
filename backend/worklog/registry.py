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

from .common import LIVE_AGENT, AppError, rows_in
from .coordination import CoordinationService
from .discussions import registered_by, registered_chats

STALE_AFTER = timedelta(hours=2)
TIE_LIMIT = 200
SNIPPET = 1000
# How an agent came to be tied to a record, in display order.
VIA = ("participation", "role", "update", "message", "run", "confirmed", "chat")


def _latest(*values: str | None) -> str | None:
    present = [value for value in values if value]
    return max(present) if present else None


def _stale(last_seen_at: str) -> bool:
    return datetime.fromisoformat(last_seen_at) < datetime.now(UTC) - STALE_AFTER


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
        items = [{**records[identity], "via": [v for v in VIA if v in entry["via"]],
                  "current": entry["current"], "lastAt": entry["lastAt"]}
                 for identity, entry in self.entries.items() if identity in records]
        items.sort(key=lambda r: r["lastAt"] or "", reverse=True)
        items.sort(key=lambda r: not r["current"])
        return {"items": items[:TIE_LIMIT], "total": len(items)}


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
        whose label holds it, ignoring case."""
        rows = c.execute(f"SELECT a.*,{LIVE_AGENT} AS active FROM communication_agents a").fetchall()
        if q.strip():
            rows = [r for r in rows if q.strip().casefold() in r["label"].casefold()]
        summaries = self._summaries(c, rows) if rows else []
        summaries.sort(key=lambda s: (s["lastActivityAt"] or s["createdAt"], s["id"]), reverse=True)
        summaries.sort(key=lambda s: not s["active"])
        return {"items": summaries[offset:offset + limit], "total": len(summaries), "limit": limit, "offset": offset}

    @staticmethod
    def _chats(c: sqlite3.Connection, chat_ids: Iterable[str]) -> list[sqlite3.Row]:
        return rows_in(c, "SELECT * FROM agent_chats WHERE chat_id IN ({})", chat_ids)

    def agent(self, c: sqlite3.Connection, identity: str) -> dict:
        row = self._agent(c, identity)
        summary = self._summaries(c, [row])[0]
        items, roadmaps, repositories, chats = _Ties(), _Ties(), _Ties(), _Ties()
        step_ties: list[tuple[str, str, str | None, bool]] = []

        runs = c.execute("SELECT * FROM agent_runs WHERE agent_id=? ORDER BY created_at DESC,id DESC", (identity,)).fetchall()
        for r in c.execute("SELECT p.item_id,p.last_seen_at,p.released_at,pc.repository_id FROM participations p JOIN agent_runs ar ON ar.id=p.run_id LEFT JOIN participation_contexts pc ON pc.participation_id=p.id WHERE ar.agent_id=?", (identity,)):
            current = r["released_at"] is None
            items.add(r["item_id"], "participation", r["last_seen_at"], current)
            repositories.add(r["repository_id"], "participation", r["last_seen_at"], current)
        for r in c.execute("SELECT u.item_id,u.created_at,ur.repository_id FROM coordination_updates u LEFT JOIN coordination_update_repositories ur ON ur.update_id=u.id WHERE u.agent_id=?", (identity,)):
            items.add(r["item_id"], "update", r["created_at"])
            repositories.add(r["repository_id"], "update", r["created_at"])
        for role in summary["roles"]:
            target = {"item": items, "roadmap": roadmaps}.get(role["contextType"])
            if target is not None:
                target.add(role["contextId"], "role", role["updatedAt"], True)
            else:
                step_ties.append((role["contextId"], "role", role["updatedAt"], True))

        # Messages tie the agent to their discussions' work contexts and reported chats.
        latest_by_discussion: dict[str, str] = {}
        chat_refs: list[tuple[str, str, str | None, bool]] = []
        for r in c.execute("SELECT discussion_id,chat_id,created_at FROM discussion_messages WHERE author_id=? AND author_kind='agent'", (identity,)):
            latest_by_discussion[r["discussion_id"]] = _latest(latest_by_discussion.get(r["discussion_id"]), r["created_at"]) or r["created_at"]
            if r["chat_id"]:
                chat_refs.append((r["chat_id"], "message", r["created_at"], False))
        for r in rows_in(c, "SELECT * FROM discussion_contexts WHERE discussion_id IN ({})", latest_by_discussion):
            posted = latest_by_discussion[r["discussion_id"]]
            if r["context_type"] == "step":
                step_ties.append((r["context_id"], "message", posted, False))
            else:
                {"item": items, "roadmap": roadmaps}[r["context_type"]].add(r["context_id"], "message", posted)
        chat_refs += [(r["external_chat_id"], "run", r["created_at"], r["ended_at"] is None) for r in runs if r["external_chat_id"]]
        # The person's word that the agent works in a chat ties it too, though not to current work.
        chat_refs += [(r["chat_id"], "confirmed", r["created_at"], False)
                      for r in c.execute("SELECT chat_id,created_at FROM chat_confirmations WHERE agent_id=?", (identity,))]

        # A reported chat ID that a tracked chat carries ties the agent to that chat's work.
        tracked = self._chats(c, {ref[0] for ref in chat_refs})
        by_chat_id: dict[str, list[sqlite3.Row]] = {}
        for chat in tracked:
            by_chat_id.setdefault(chat["chat_id"], []).append(chat)
        chat_ties: dict[str, tuple[str | None, bool]] = {}
        for chat_id, via, at, current in chat_refs:
            for chat in by_chat_id.get(chat_id, []):
                chats.add(chat["id"], via, at, current)
                prior = chat_ties.get(chat["id"], (None, False))
                chat_ties[chat["id"]] = (_latest(prior[0], at), prior[1] or current)
        tracked_by_id = {chat["id"]: chat for chat in tracked}
        for chat_key, (at, current) in chat_ties.items():
            chat = tracked_by_id[chat_key]
            {"item": items, "roadmap": roadmaps}[chat["owner_type"]].add(chat["owner_id"], "chat", at, current)
        for r in rows_in(c, "SELECT chat_id,repository_id FROM chat_repositories WHERE chat_id IN ({})", chat_ties):
            repositories.add(r["repository_id"], "chat", *chat_ties[r["chat_id"]])

        steps = self._step_roadmaps(c, [s[0] for s in step_ties])
        for step_id, via, at, current in step_ties:
            roadmaps.add(steps.get(step_id, {}).get("roadmapId"), via, at, current)
        work = self._work_titles(c, items.entries)
        # Work on a roadmap's item is work on that roadmap, for every reason it was tied.
        for item_id, entry in items.entries.items():
            roadmap_id = work.get(item_id, {}).get("roadmapId")
            for via in entry["via"]:
                roadmaps.add(roadmap_id, via, entry["lastAt"], entry["current"])
        roadmap_titles = self._roadmap_titles(c, roadmaps.entries)
        for record in work.values():
            record["roadmap"] = roadmap_titles.get(record.pop("roadmapId") or "")
        repos = {r["id"]: {"id": r["id"], "name": r["name"], "archived": bool(r["archived"])}
                 for r in rows_in(c, "SELECT id,name,archived FROM repositories WHERE id IN ({})", repositories.entries)}
        # Every tracked chat's owner was tied above, so its title is already resolved.
        owner_titles = {**{k: v["title"] for k, v in work.items()}, **{k: v["title"] for k, v in roadmap_titles.items()}}
        chat_records = {chat["id"]: {"id": chat["id"], "chatId": chat["chat_id"], "title": chat["title"],
                                     "provider": chat["provider"], "model": chat["model"], "archived": bool(chat["archived"]),
                                     "ownerType": chat["owner_type"], "ownerId": chat["owner_id"],
                                     "ownerTitle": owner_titles.get(chat["owner_id"], chat["owner_id"])} for chat in tracked}

        credentials = c.execute("SELECT cr.id,cr.label,cr.created_at,cr.revoked FROM credentials cr JOIN communication_credentials cc ON cc.credential_id=cr.id WHERE cc.agent_id=? ORDER BY cr.created_at DESC,cr.id", (identity,)).fetchall()
        sponsors = registered_by(c, [r["id"] for r in credentials])
        registered = registered_chats(c, [r["id"] for r in credentials])
        return {**summary,
                "credentials": [{"id": r["id"], "label": r["label"], "createdAt": r["created_at"], "revoked": bool(r["revoked"]),
                                 "registeredBy": sponsors.get(r["id"]), "chatId": registered.get(r["id"])} for r in credentials],
                "runs": {"items": [{**CoordinationService.run_record(r), "chats": [chat_records[chat["id"]] for chat in by_chat_id.get(r["external_chat_id"] or "", [])]} for r in runs[:TIE_LIMIT]], "total": len(runs)},
                "workItems": items.render(work), "roadmaps": roadmaps.render(roadmap_titles),
                "repositories": repositories.render(repos), "chats": chats.render(chat_records)}

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
