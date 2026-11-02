"""Who works on what, gathered from the records agents already write.

The registry writes nothing. Runs, participation, status updates, discussion
messages, responsibility roles and tracked chats each remain the one record of
their fact; this module only collects them per agent identity, so it cannot drift
from them. Reported chat IDs, paths and branches stay provenance, never proof.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from .common import AppError
from .coordination import CoordinationService
from .registration import registered_by

STALE_AFTER = timedelta(hours=2)
TIE_LIMIT = 200
SNIPPET = 1000
# How an agent came to be tied to a record, in display order.
VIA = ("participation", "role", "update", "message", "run", "chat")
ACTIVE = "EXISTS(SELECT 1 FROM communication_credentials cc JOIN credentials cr ON cr.id=cc.credential_id WHERE cc.agent_id=a.id AND cr.revoked=0)"


def _rows(c: sqlite3.Connection, sql: str, ids: Iterable[str]) -> list[sqlite3.Row]:
    """Run an `IN ({})` lookup in chunks that stay below SQLite's variable limit."""
    ids = list(dict.fromkeys(ids))
    result: list[sqlite3.Row] = []
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        result.extend(c.execute(sql.format(",".join("?" * len(chunk))), chunk).fetchall())
    return result


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
        row = c.execute(f"SELECT a.*,{ACTIVE} AS active FROM communication_agents a WHERE a.id=?", (identity,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Agent not found")
        return row

    @staticmethod
    def _work_titles(c: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict]:
        # A step's roadmap wins over the item's own roadmap, as on the Chats page.
        rows = _rows(c, "SELECT w.id,w.title,w.status,w.archived,COALESCE(s.roadmap_id,w.roadmap_id) AS roadmap_id FROM work_items w LEFT JOIN steps s ON s.id=w.step_id WHERE w.id IN ({})", ids)
        return {r["id"]: {"id": r["id"], "title": r["title"], "status": r["status"],
                          "archived": bool(r["archived"]), "roadmapId": r["roadmap_id"]} for r in rows}

    @staticmethod
    def _roadmap_titles(c: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict]:
        rows = _rows(c, "SELECT id,title,archived FROM roadmaps WHERE id IN ({})", ids)
        return {r["id"]: {"id": r["id"], "title": r["title"], "archived": bool(r["archived"])} for r in rows}

    @staticmethod
    def _step_roadmaps(c: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict]:
        rows = _rows(c, "SELECT id,title,roadmap_id FROM steps WHERE id IN ({})", ids)
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

    def agents(self, c: sqlite3.Connection, limit: int = 50, offset: int = 0) -> dict:
        """Agents with an active credential first, then the most recently active."""
        rows = c.execute(f"SELECT a.*,{ACTIVE} AS active FROM communication_agents a").fetchall()
        summaries = self._summaries(c, rows) if rows else []
        summaries.sort(key=lambda s: (s["lastActivityAt"] or s["createdAt"], s["id"]), reverse=True)
        summaries.sort(key=lambda s: not s["active"])
        return {"items": summaries[offset:offset + limit], "total": len(summaries), "limit": limit, "offset": offset}

    @staticmethod
    def _chats(c: sqlite3.Connection, chat_ids: Iterable[str]) -> list[sqlite3.Row]:
        return _rows(c, "SELECT * FROM agent_chats WHERE chat_id IN ({})", chat_ids)

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
        for r in _rows(c, "SELECT * FROM discussion_contexts WHERE discussion_id IN ({})", latest_by_discussion):
            posted = latest_by_discussion[r["discussion_id"]]
            if r["context_type"] == "step":
                step_ties.append((r["context_id"], "message", posted, False))
            else:
                {"item": items, "roadmap": roadmaps}[r["context_type"]].add(r["context_id"], "message", posted)
        chat_refs += [(r["external_chat_id"], "run", r["created_at"], r["ended_at"] is None) for r in runs if r["external_chat_id"]]

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
        for r in _rows(c, "SELECT chat_id,repository_id FROM chat_repositories WHERE chat_id IN ({})", chat_ties):
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
                 for r in _rows(c, "SELECT id,name,archived FROM repositories WHERE id IN ({})", repositories.entries)}
        # Every tracked chat's owner was tied above, so its title is already resolved.
        owner_titles = {**{k: v["title"] for k, v in work.items()}, **{k: v["title"] for k, v in roadmap_titles.items()}}
        chat_records = {chat["id"]: {"id": chat["id"], "chatId": chat["chat_id"], "title": chat["title"],
                                     "provider": chat["provider"], "model": chat["model"], "archived": bool(chat["archived"]),
                                     "ownerType": chat["owner_type"], "ownerId": chat["owner_id"],
                                     "ownerTitle": owner_titles.get(chat["owner_id"], chat["owner_id"])} for chat in tracked}

        credentials = c.execute("SELECT cr.id,cr.label,cr.created_at,cr.revoked FROM credentials cr JOIN communication_credentials cc ON cc.credential_id=cr.id WHERE cc.agent_id=? ORDER BY cr.created_at DESC,cr.id", (identity,)).fetchall()
        sponsors = registered_by(c, [r["id"] for r in credentials])
        return {**summary,
                "credentials": [{"id": r["id"], "label": r["label"], "createdAt": r["created_at"], "revoked": bool(r["revoked"]),
                                 "registeredBy": sponsors.get(r["id"])} for r in credentials],
                "runs": {"items": [{**CoordinationService.run_record(r), "chats": [chat_records[chat["id"]] for chat in by_chat_id.get(r["external_chat_id"] or "", [])]} for r in runs[:TIE_LIMIT]], "total": len(runs)},
                "workItems": items.render(work), "roadmaps": roadmaps.render(roadmap_titles),
                "repositories": repositories.render(repos), "chats": chats.render(chat_records)}

    def activity(self, c: sqlite3.Connection, identity: str, limit: int = 50, offset: int = 0) -> dict:
        """The agent's discussion messages and status updates, newest first."""
        self._agent(c, identity)
        union = "SELECT 'message' AS kind,id,created_at FROM discussion_messages WHERE author_id=? AND author_kind='agent' UNION ALL SELECT 'update',id,created_at FROM coordination_updates WHERE agent_id=?"
        total = c.execute("SELECT COUNT(*) FROM (" + union + ")", (identity, identity)).fetchone()[0]
        page = c.execute(union + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (identity, identity, limit, offset)).fetchall()
        messages = {r["id"]: r for r in _rows(c, "SELECT m.*,d.title AS discussion_title FROM discussion_messages m JOIN discussions d ON d.id=m.discussion_id WHERE m.id IN ({})", [p["id"] for p in page if p["kind"] == "message"])}
        updates = {r["id"]: r for r in _rows(c, "SELECT u.*,w.title AS item_title FROM coordination_updates u LEFT JOIN work_items w ON w.id=u.item_id WHERE u.id IN ({})", [p["id"] for p in page if p["kind"] == "update"])}
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
