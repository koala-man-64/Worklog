# mypy: disable-error-code="operator,assignment,call-overload,arg-type,list-item"
"""Read-only aggregation for the person's live workflow map.

The database half of this service only combines facts already owned by their domain tables. The
filesystem half (``enrich``) runs after the SQLite snapshot has closed.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Any

from . import proposal_targets, support
from .active_sessions import ORDER
from .common import LIVE_AGENT, AppError, now
from .discussions import DiscussionService
from .transcripts import TranscriptReader

STALE_AFTER = timedelta(hours=2)
STATE_RANK = {"working": 0, "waiting": 1, "reporting": 2, "stale": 3, "idle": 4, "revoked": 5}
WORK_VIA = ("participation", "role", "chat", "proposal", "discussion")
REPO_VIA = ("participation", "chat", "update")
COMM_RANK = {"ack_request": 0, "role_offer": 1, "shared_thread": 2, "sponsorship": 3}
PULSE_RANK = {
    "update": 0,
    "message": 1,
    "ack": 2,
    "offer": 3,
    "proposal": 4,
    "participation": 5,
    "run": 6,
}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _latest(*values: str | None) -> str | None:
    present = [value for value in values if _parse(value) is not None]
    return max(
        present, key=lambda value: _parse(value) or datetime.min.replace(tzinfo=UTC), default=None
    )


def _href(kind: str, identity: str | None, roadmap_id: str | None = None) -> str:
    if not identity:
        return "#live"
    if kind == "step":
        return f"#roadmaps/{roadmap_id}/{identity}"
    return f"#{kind}/{identity}"


def _work_roadmap(item: dict, steps: dict[str, dict]) -> str | None:
    step = steps.get(item["step_id"])
    return step["roadmap_id"] if step else item["roadmap_id"]


class LiveService:
    def __init__(self, transcripts: TranscriptReader):
        self.transcripts = transcripts

    def snapshot(
        self,
        c: sqlite3.Connection,
        found_sessions: list[dict],
        roadmap_id: str | None = None,
        window_minutes: int = 60,
        include_idle: bool = False,
    ) -> dict:
        generated = now()
        cutoff_dt = datetime.now(UTC) - timedelta(minutes=window_minutes)
        cutoff = cutoff_dt.isoformat()

        def recent(value: str | None) -> bool:
            parsed = _parse(value)
            return parsed is not None and parsed >= cutoff_dt

        roadmaps_list = [dict(r) for r in c.execute("SELECT rowid AS _rowid,* FROM roadmaps")]
        roadmaps = {r["id"]: r for r in roadmaps_list}
        if roadmap_id is not None and roadmap_id not in roadmaps:
            raise AppError(404, "not_found", "Roadmap not found")
        scoped: set[str] | None = None
        if roadmap_id is not None:
            scoped = {roadmap_id}
            children: dict[str, list[str]] = defaultdict(list)
            for roadmap in roadmaps_list:
                if roadmap["parent_id"]:
                    children[roadmap["parent_id"]].append(roadmap["id"])
            queue = deque([roadmap_id])
            while queue:
                parent = queue.popleft()
                for child in children.get(parent, []):
                    if child not in scoped:
                        scoped.add(child)
                        queue.append(child)

        question_counts = {
            r["roadmap_id"]: int(r["total"])
            for r in c.execute(
                "SELECT roadmap_id,COUNT(*) AS total FROM roadmap_questions WHERE status='open' AND archived=0 GROUP BY roadmap_id"
            )
        }
        roadmap_options = [
            {
                "id": r["id"],
                "title": r["title"],
                "parentId": r["parent_id"],
                "openQuestions": int(question_counts.get(r["id"], 0)),
            }
            for r in sorted(
                (r for r in roadmaps_list if not r["archived"]),
                key=lambda r: (r["title"].casefold(), int(r["_rowid"])),
            )
        ]
        steps_list = [dict(r) for r in c.execute("SELECT rowid AS _rowid,* FROM steps")]
        steps = {r["id"]: r for r in steps_list}
        items_list = [dict(r) for r in c.execute("SELECT rowid AS _rowid,* FROM work_items")]
        items = {r["id"]: r for r in items_list}
        repos_list = [
            dict(r)
            for r in c.execute(
                "SELECT rowid AS _rowid,* FROM repositories WHERE archived=0 AND merged_into IS NULL"
            )
        ]
        repos = {r["id"]: r for r in repos_list}

        runs = [dict(r) for r in c.execute("SELECT rowid AS _rowid,* FROM agent_runs")]
        participations = [
            dict(r)
            for r in c.execute(
                "SELECT p.rowid AS _rowid,p.*,ar.agent_id,ar.credential_id FROM participations p JOIN agent_runs ar ON ar.id=p.run_id"
            )
        ]
        contexts: dict[str, list[dict]] = defaultdict(list)
        for row in c.execute(
            "SELECT pc.rowid AS _rowid,pc.*,r.name AS repository_name FROM participation_contexts pc "
            "JOIN repositories r ON r.id=pc.repository_id ORDER BY pc.participation_id,pc.position,pc.rowid"
        ):
            contexts[row["participation_id"]].append(dict(row))
        updates = [
            dict(r)
            for r in c.execute(
                "SELECT u.rowid AS _rowid,u.*,ur.repository_id FROM coordination_updates u "
                "LEFT JOIN coordination_update_repositories ur ON ur.update_id=u.id"
            )
        ]
        roles = [dict(r) for r in c.execute("SELECT rowid AS _rowid,* FROM discussion_roles")]
        discussions = {
            r["id"]: dict(r) for r in c.execute("SELECT rowid AS _rowid,* FROM discussions")
        }
        discussion_contexts: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for row in c.execute("SELECT rowid AS _rowid,* FROM discussion_contexts ORDER BY rowid"):
            discussion_contexts[row["discussion_id"]].append(
                (row["context_type"], row["context_id"])
            )
        messages = [dict(r) for r in c.execute("SELECT rowid AS _rowid,* FROM discussion_messages")]
        chats = support.chat_overview(c, False)
        chat_rowids = {
            r["id"]: int(r["_rowid"])
            for r in c.execute("SELECT rowid AS _rowid,id FROM agent_chats")
        }
        session_ties = support.session_ties(c, found_sessions)

        proposals: list[dict] = []
        proposal_ties: dict[str, set[tuple[str, str]]] = {}
        for row in c.execute("SELECT rowid AS _rowid,* FROM proposals"):
            proposal = dict(row)
            try:
                operations = json.loads(proposal["operations"])
            except (TypeError, ValueError):
                operations = []
            touched: set[tuple[str, str]] = set()
            if isinstance(operations, list):
                for operation in operations:
                    if isinstance(operation, dict):
                        touched.update(
                            proposal_targets.targets(
                                c, operation.get("action"), operation.get("payload")
                            )
                        )
            proposal_ties[proposal["id"]] = touched
            if proposal["status"] in {"draft", "submitted"} or recent(proposal["updated_at"]):
                proposals.append(proposal)

        def roadmap_for(kind: str, identity: str) -> str | None:
            if kind == "roadmap":
                return identity if identity in roadmaps else None
            if kind == "step":
                return steps.get(identity, {}).get("roadmap_id")
            if kind == "item" and identity in items:
                return _work_roadmap(items[identity], steps)
            return None

        def in_scope(kind: str, identity: str) -> bool:
            return scoped is None or roadmap_for(kind, identity) in scoped

        submitted_by_target: dict[tuple[str, str], list[str]] = defaultdict(list)
        for proposal in proposals:
            if proposal["status"] == "submitted":
                for target in proposal_ties[proposal["id"]]:
                    submitted_by_target[target].append(proposal["id"])

        # Gather all agent-to-work reasons before filtering either side.
        work_reasons: dict[tuple[str, str], dict[str, Any]] = {}

        def add_work(
            agent: str,
            kind: str,
            identity: str | None,
            via: str,
            at: str | None,
            active: bool = False,
            stale: bool = False,
        ) -> None:
            if not identity or identity not in (
                {"roadmap": roadmaps, "step": steps, "item": items}.get(kind) or {}
            ):
                return
            edge = work_reasons.setdefault(
                (agent, identity),
                {"kind": kind, "via": set(), "active": False, "stale": True, "lastAt": None},
            )
            edge["via"].add(via)
            edge["active"] = bool(edge["active"] or active)
            edge["stale"] = bool(edge["stale"] and stale)
            edge["lastAt"] = _latest(edge["lastAt"], at)

        for participation in participations:
            item_id = participation["item_id"]
            active = participation["released_at"] is None
            stale = (
                _parse(participation["last_seen_at"]) < datetime.now(UTC) - STALE_AFTER
                if _parse(participation["last_seen_at"])
                else True
            )
            add_work(
                participation["agent_id"],
                "item",
                item_id,
                "participation",
                participation["last_seen_at"],
                active,
                stale,
            )
            if item_id in items:
                item = items[item_id]
                add_work(
                    participation["agent_id"],
                    "step",
                    item["step_id"],
                    "participation",
                    participation["last_seen_at"],
                    active,
                    stale,
                )
                add_work(
                    participation["agent_id"],
                    "roadmap",
                    _work_roadmap(item, steps),
                    "participation",
                    participation["last_seen_at"],
                    active,
                    stale,
                )
        for role in roles:
            if role["owner_id"]:
                add_work(
                    role["owner_id"],
                    role["context_type"],
                    role["context_id"],
                    "role",
                    role["updated_at"],
                    True,
                )
                rid = roadmap_for(role["context_type"], role["context_id"])
                add_work(role["owner_id"], "roadmap", rid, "role", role["updated_at"], True)
        for chat in chats:
            targets = [(chat["ownerType"], chat["ownerId"])] + [
                ("step", step["id"]) for step in chat["steps"]
            ]
            targets += [("roadmap", r["id"]) for r in chat["roadmaps"]]
            for agent in chat["agents"]:
                for kind, identity in targets:
                    add_work(agent["id"], kind, identity, "chat", chat["updatedAt"], True)
        for proposal in proposals:
            if proposal["actor_kind"] == "agent":
                for kind, identity in proposal_ties[proposal["id"]]:
                    add_work(
                        proposal["actor_id"],
                        kind,
                        identity,
                        "proposal",
                        proposal["updated_at"],
                        proposal["status"] == "submitted",
                    )
                    add_work(
                        proposal["actor_id"],
                        "roadmap",
                        roadmap_for(kind, identity),
                        "proposal",
                        proposal["updated_at"],
                        proposal["status"] == "submitted",
                    )
        for message in messages:
            if message["author_kind"] != "agent":
                continue
            for kind, identity in discussion_contexts.get(message["discussion_id"], []):
                if kind in {"item", "roadmap", "step"}:
                    add_work(
                        message["author_id"], kind, identity, "discussion", message["created_at"]
                    )
                    add_work(
                        message["author_id"],
                        "roadmap",
                        roadmap_for(kind, identity),
                        "discussion",
                        message["created_at"],
                    )

        active_item_ids = {
            p["item_id"]
            for p in participations
            if p["item_id"]
            and (p["released_at"] is None or recent(p["last_seen_at"]) or recent(p["released_at"]))
        }
        role_targets = {(r["context_type"], r["context_id"]) for r in roles if r["owner_id"]}
        chat_item_ids = {ch["ownerId"] for ch in chats if ch["ownerType"] == "item"}
        chat_step_ids = {step["id"] for ch in chats for step in ch["steps"]}
        discussion_targets = {
            (kind, identity)
            for m in messages
            if recent(m["created_at"])
            for kind, identity in discussion_contexts.get(m["discussion_id"], [])
        }

        include_items: set[str] = set()
        for item in items_list:
            if item["archived"] or not in_scope("item", item["id"]):
                continue
            connected = (
                item["id"] in active_item_ids
                or ("item", item["id"]) in role_targets
                or item["id"] in chat_item_ids
                or ("item", item["id"]) in submitted_by_target
                or ("item", item["id"]) in discussion_targets
            )
            if item["status"] in {"doing", "waiting", "blocked"} or connected:
                include_items.add(item["id"])

        include_steps: set[str] = set()
        for step in steps_list:
            if step["retired"] or not in_scope("step", step["id"]):
                continue
            connected = (
                any(items[i]["step_id"] == step["id"] for i in include_items)
                or ("step", step["id"]) in role_targets
                or step["id"] in chat_step_ids
                or ("step", step["id"]) in discussion_targets
            )
            if step["status"] in {"doing", "blocked"} or connected:
                include_steps.add(step["id"])

        include_roadmaps: set[str] = set()
        directly_connected_roadmaps = {
            role["context_id"]
            for role in roles
            if role["owner_id"] and role["context_type"] == "roadmap"
        }
        directly_connected_roadmaps.update(
            roadmap["id"]
            for chat in chats
            if recent(chat["updatedAt"])
            for roadmap in chat["roadmaps"]
        )
        for roadmap in roadmaps_list:
            if scoped is not None:
                if roadmap["id"] in scoped and (
                    not roadmap["archived"] or roadmap["id"] == roadmap_id
                ):
                    include_roadmaps.add(roadmap["id"])
                continue
            connected = (
                any(steps[s]["roadmap_id"] == roadmap["id"] for s in include_steps)
                or any(_work_roadmap(items[i], steps) == roadmap["id"] for i in include_items)
                or question_counts.get(roadmap["id"], 0) > 0
                or ("roadmap", roadmap["id"]) in submitted_by_target
                or roadmap["id"] in directly_connected_roadmaps
            )
            if not roadmap["archived"] and connected:
                include_roadmaps.add(roadmap["id"])

        roadmap_order = sorted(
            include_roadmaps,
            key=lambda rid: (roadmaps[rid]["title"].casefold(), int(roadmaps[rid]["_rowid"])),
        )
        included_work_order: list[tuple[str, str]] = []
        for rid in roadmap_order:
            included_work_order.append(("roadmap", rid))
            included_work_order.extend(
                ("step", s["id"])
                for s in sorted(
                    (s for s in steps_list if s["id"] in include_steps and s["roadmap_id"] == rid),
                    key=lambda s: (int(s["position"]), int(s["_rowid"])),
                )
            )
            included_work_order.extend(
                ("item", i["id"])
                for i in sorted(
                    (
                        i
                        for i in items_list
                        if i["id"] in include_items and _work_roadmap(i, steps) == rid
                    ),
                    key=lambda i: (
                        i["backlog_order"] is None,
                        int(i["backlog_order"] or 0),
                        int(i["_rowid"]),
                    ),
                )
            )
        included_work_order.extend(
            ("item", i["id"])
            for i in sorted(
                (
                    i
                    for i in items_list
                    if i["id"] in include_items and _work_roadmap(i, steps) is None
                ),
                key=lambda i: (
                    i["backlog_order"] is None,
                    int(i["backlog_order"] or 0),
                    int(i["_rowid"]),
                ),
            )
        )
        included_work_ids = {identity for _, identity in included_work_order}

        request_rows = [
            dict(r)
            for r in c.execute(
                "SELECT dr.rowid AS _rowid,dr.*,dm.discussion_id,dm.author_id,dm.author_label,dm.author_kind,dm.created_at "
                "FROM discussion_requests dr JOIN discussion_messages dm ON dm.id=dr.message_id"
            )
        ]
        target_rows = [
            dict(r)
            for r in c.execute(
                "SELECT dt.rowid AS _rowid,dt.*,da.created_at AS acknowledged_at,da.actor_id AS acknowledged_by,"
                "da.actor_label AS acknowledged_by_label,da.actor_kind AS acknowledged_by_kind "
                "FROM discussion_targets dt LEFT JOIN discussion_acknowledgements da ON da.target_id=dt.id"
            )
        ]
        targets_by_message: dict[str, list[dict]] = defaultdict(list)
        for target in target_rows:
            targets_by_message[target["message_id"]].append(target)
        offers = [
            dict(r)
            for r in c.execute(
                "SELECT o.rowid AS _rowid,o.*,r.label AS role_label,r.context_type,r.context_id "
                "FROM discussion_role_offers o JOIN discussion_roles r ON r.id=o.role_id"
            )
        ]

        pending_for: set[str] = set()
        for request in request_rows:
            if request["state"] != "pending":
                continue
            for target in targets_by_message[request["message_id"]]:
                if target["kind"] == "agent":
                    pending_for.add(target["recipient_id"])
                elif target["kind"] == "role":
                    role = next((r for r in roles if r["id"] == target["recipient_id"]), None)
                    if role and role["owner_id"]:
                        pending_for.add(role["owner_id"])
        pending_for.update(o["to_id"] for o in offers if o["state"] == "pending")

        agent_rows = [
            dict(r)
            for r in c.execute(
                f"SELECT a.rowid AS _rowid,a.*,{LIVE_AGENT} AS active FROM communication_agents a"
            )
        ]
        agent_labels = {a["id"]: a["label"] for a in agent_rows}
        runs_by_agent: dict[str, list[dict]] = defaultdict(list)
        for run in runs:
            runs_by_agent[run["agent_id"]].append(run)
        parts_by_agent: dict[str, list[dict]] = defaultdict(list)
        for participation in participations:
            parts_by_agent[participation["agent_id"]].append(participation)
        roles_by_agent: dict[str, list[dict]] = defaultdict(list)
        for role in roles:
            if role["owner_id"]:
                roles_by_agent[role["owner_id"]].append(role)
        updates_by_agent: dict[str, list[dict]] = defaultdict(list)
        for update in updates:
            updates_by_agent[update["agent_id"]].append(update)
        messages_by_agent: dict[str, list[dict]] = defaultdict(list)
        for message in messages:
            if message["author_kind"] == "agent":
                messages_by_agent[message["author_id"]].append(message)
        proposals_by_agent: dict[str, list[dict]] = defaultdict(list)
        for proposal in proposals:
            if proposal["actor_kind"] == "agent":
                proposals_by_agent[proposal["actor_id"]].append(proposal)
        chats_by_agent: dict[str, list[dict]] = defaultdict(list)
        for chat in chats:
            for agent in chat["agents"]:
                chats_by_agent[agent["id"]].append(chat)
        sessions_by_agent: dict[str, list[dict]] = defaultdict(list)
        for session in session_ties:
            for agent in session["agents"]:
                sessions_by_agent[agent["id"]].append(session)

        sponsors: dict[str, tuple[str, str]] = {}
        for row in c.execute(
            "SELECT cc.agent_id,cc.registered_by,cc.registered_by_label FROM communication_credentials cc "
            "JOIN credentials cr ON cr.id=cc.credential_id WHERE cc.registered_by IS NOT NULL "
            "ORDER BY cc.agent_id,cr.revoked,cr.created_at DESC,cr.rowid DESC"
        ):
            sponsors.setdefault(row["agent_id"], (row["registered_by"], row["registered_by_label"]))

        agent_nodes: list[dict] = []
        for agent in agent_rows:
            aid = agent["id"]
            visible_roles = [
                role
                for role in roles_by_agent[aid]
                if scoped is None or in_scope(role["context_type"], role["context_id"])
            ]
            visible_chats = [
                chat
                for chat in chats_by_agent[aid]
                if scoped is None or any(roadmap["id"] in scoped for roadmap in chat["roadmaps"])
            ]
            visible_updates = [
                update
                for update in updates_by_agent[aid]
                if scoped is None or (update["item_id"] and in_scope("item", update["item_id"]))
            ]
            open_runs = [r for r in runs_by_agent[aid] if r["ended_at"] is None]
            open_parts = [p for p in parts_by_agent[aid] if p["released_at"] is None]
            fresh_parts = [
                p
                for p in open_parts
                if not (
                    _parse(p["last_seen_at"]) is None
                    or _parse(p["last_seen_at"]) < datetime.now(UTC) - STALE_AFTER
                )
            ]
            recent_runs = [
                r
                for r in open_runs
                if _parse(r["created_at"]) is not None
                and _parse(r["created_at"]) >= datetime.now(UTC) - STALE_AFTER
            ]
            if not bool(agent["active"]):
                declared = "revoked"
            elif fresh_parts or recent_runs:
                declared = "reporting"
            elif open_parts:
                declared = "stale"
            else:
                declared = "none"
            states = [
                {"busy": "working", "working": "working", "waiting": "waiting", "idle": "idle"}.get(
                    s.get("status") or s.get("state"), "idle"
                )
                for s in sessions_by_agent[aid]
            ]
            session_state = (
                "waiting"
                if "waiting" in states
                else "working"
                if "working" in states
                else "idle"
                if states
                else None
            )
            if not bool(agent["active"]):
                state = "revoked"
            elif session_state == "waiting":
                state = "waiting"
            elif session_state == "working":
                state = "working"
            elif declared == "reporting":
                state = "reporting"
            elif declared == "stale":
                state = "stale"
            else:
                state = "idle"
            providers = sorted(
                {str(r["provider"]) for r in runs_by_agent[aid] if r["provider"]},
                key=lambda provider: (provider.casefold(), provider),
            )
            model = next(
                (
                    ch["model"]
                    for ch in sorted(
                        visible_chats,
                        key=lambda ch: (ch["updatedAt"], chat_rowids[ch["id"]]),
                        reverse=True,
                    )
                    if ch["model"]
                ),
                "",
            )
            latest_note_row = max(
                visible_updates,
                key=lambda u: (u["created_at"], int(u["_rowid"])),
                default=None,
            )
            last_at = _latest(
                *[p["last_seen_at"] for p in parts_by_agent[aid]],
                *[r["updated_at"] for r in runs_by_agent[aid]],
                *[u["created_at"] for u in visible_updates],
                *[m["created_at"] for m in messages_by_agent[aid]],
                *[p["updated_at"] for p in proposals_by_agent[aid]],
                *[ch["updatedAt"] for ch in visible_chats],
            )
            sponsor = sponsors.get(aid)
            tied_in_scope = any(
                agent_id == aid
                and identity in included_work_ids
                and (edge["active"] or recent(edge["lastAt"]))
                for (agent_id, identity), edge in work_reasons.items()
            )
            base_keep = bool(
                include_idle
                or state in {"working", "waiting", "reporting", "stale"}
                or roles_by_agent[aid]
                or aid in pending_for
                or any(p["status"] == "submitted" for p in proposals_by_agent[aid])
                or recent(last_at)
            )
            node_chats = [
                {
                    "id": ch["id"],
                    "chatId": ch["chatId"],
                    "title": ch["title"],
                    "provider": ch["provider"],
                    "model": ch["model"],
                    "working": False,
                    "lastAt": ch["updatedAt"],
                    "href": _href("chats", ch["id"]),
                }
                for ch in sorted(
                    visible_chats,
                    key=lambda ch: (ch["title"].casefold(), ch["createdAt"], chat_rowids[ch["id"]]),
                )
            ]
            agent_nodes.append(
                {
                    "id": aid,
                    "label": agent["label"],
                    "avatar": agent["avatar"],
                    "active": bool(agent["active"]),
                    "state": state,
                    "declaredState": declared,
                    "sessionState": session_state,
                    "providers": list(providers),
                    "model": model,
                    "registeredById": sponsor[0] if sponsor else None,
                    "registeredByLabel": sponsor[1] if sponsor else None,
                    "openRuns": int(len(open_runs)),
                    "openParticipations": int(len(open_parts)),
                    "roles": [
                        r["label"]
                        for r in sorted(
                            visible_roles,
                            key=lambda r: (r["label"].casefold(), int(r["_rowid"])),
                        )
                    ],
                    "chats": list(node_chats),
                    "lastAt": last_at,
                    "latestNote": latest_note_row["text"] if latest_note_row else "",
                    "href": _href("agents", aid),
                    "_rowid": int(agent["_rowid"]),
                    "_keepBase": bool(tied_in_scope if scoped is not None else base_keep),
                    "_scope": bool(tied_in_scope),
                }
            )
        # Agents tied to a tracked chat are retained until transcript enrichment can decide whether
        # that chat is actively working; all other inclusion decisions are already final.
        candidate_agent_ids = {
            a["id"] for a in agent_nodes if a["_keepBase"] or (scoped is None and a["chats"])
        }

        work_edges: list[dict] = []
        work_index = {identity: index for index, (_, identity) in enumerate(included_work_order)}
        for (aid, wid), edge in work_reasons.items():
            if aid not in candidate_agent_ids or wid not in included_work_ids:
                continue
            work_edges.append(
                {
                    "id": f"work:{aid}:{wid}",
                    "agentId": aid,
                    "workId": wid,
                    "workKind": edge["kind"],
                    "via": [v for v in WORK_VIA if v in edge["via"]],
                    "active": bool(edge["active"]),
                    "stale": bool(edge["stale"]),
                    "lastAt": edge["lastAt"],
                }
            )

        # Repository reasons are kept at agent/repository granularity.
        repo_reasons: dict[tuple[str, str], dict[str, Any]] = {}

        def add_repo(
            agent: str,
            repository: str | None,
            via: str,
            at: str | None,
            active: bool = False,
            stale: bool = False,
            branch: str = "",
            worktree: str = "",
        ) -> None:
            if not repository or repository not in repos:
                return
            edge = repo_reasons.setdefault(
                (agent, repository),
                {
                    "via": set(),
                    "active": False,
                    "stale": True,
                    "lastAt": None,
                    "branch": "",
                    "worktree": "",
                },
            )
            edge["via"].add(via)
            edge["active"] = bool(edge["active"] or active)
            edge["stale"] = bool(edge["stale"] and stale)
            edge["lastAt"] = _latest(edge["lastAt"], at)
            if active or not edge["branch"]:
                edge["branch"], edge["worktree"] = branch, worktree

        for participation in participations:
            if scoped is not None and participation["item_id"] not in included_work_ids:
                continue
            stale = (
                _parse(participation["last_seen_at"]) < datetime.now(UTC) - STALE_AFTER
                if _parse(participation["last_seen_at"])
                else True
            )
            for context in contexts[participation["id"]]:
                add_repo(
                    participation["agent_id"],
                    context["repository_id"],
                    "participation",
                    participation["last_seen_at"],
                    participation["released_at"] is None,
                    stale,
                    context["branch"],
                    context["worktree_path"] or context["checkout_path"],
                )
        for chat in chats:
            if scoped is not None and not any(r["id"] in scoped for r in chat["roadmaps"]):
                continue
            for agent in chat["agents"]:
                for repository in chat["repositoryIds"]:
                    add_repo(agent["id"], repository, "chat", chat["updatedAt"], True)
        for update in updates:
            if scoped is not None and update["item_id"] not in included_work_ids:
                continue
            add_repo(
                update["agent_id"],
                update["repository_id"],
                "update",
                update["created_at"],
                recent(update["created_at"]),
            )

        include_repo_ids: set[str] = set()
        linked_item_repos = {
            r["repository_id"]
            for r in c.execute("SELECT * FROM item_repositories")
            if r["item_id"] in include_items
        }
        for (aid, rid), edge in repo_reasons.items():
            if aid in candidate_agent_ids and (edge["active"] or recent(edge["lastAt"])):
                include_repo_ids.add(rid)
        include_repo_ids.update(rid for rid in linked_item_repos if rid in repos)
        repo_edges = [
            {
                "id": f"repo:{aid}:{rid}",
                "agentId": aid,
                "repositoryId": rid,
                "via": [v for v in REPO_VIA if v in edge["via"]],
                "branch": edge["branch"],
                "worktreePath": edge["worktree"],
                "active": bool(edge["active"]),
                "stale": bool(edge["stale"]),
                "lastAt": edge["lastAt"],
            }
            for (aid, rid), edge in repo_reasons.items()
            if aid in candidate_agent_ids and rid in include_repo_ids
        ]

        home_discussions = DiscussionService.home_discussions(c, include_repo_ids)
        open_branches: dict[str, set[str]] = defaultdict(set)
        overlap_by_repo: dict[str, set[str]] = defaultdict(set)
        for participation in participations:
            if participation["released_at"] is not None:
                continue
            parsed_seen = _parse(participation["last_seen_at"])
            for context in contexts[participation["id"]]:
                if context["repository_id"] not in include_repo_ids:
                    continue
                if context["branch"]:
                    open_branches[context["repository_id"]].add(context["branch"])
                if parsed_seen is not None and parsed_seen >= datetime.now(UTC) - STALE_AFTER:
                    overlap_by_repo[context["repository_id"]].add(participation["agent_id"])
        repo_nodes: list[dict] = []
        for rid in include_repo_ids:
            repository = repos[rid]
            related = [edge for edge in repo_edges if edge["repositoryId"] == rid]
            branches = sorted(
                open_branches[rid],
                key=lambda branch: (branch.casefold(), branch),
            )
            overlap_agents = overlap_by_repo[rid]
            repo_nodes.append(
                {
                    "id": rid,
                    "name": repository["name"],
                    "url": repository["url"] or None,
                    "unreviewed": bool(not repository["reviewed"]),
                    "discussionId": home_discussions.get(rid),
                    "branches": list(branches),
                    "agentCount": int(len({edge["agentId"] for edge in related})),
                    "overlapCount": int(len(overlap_agents)),
                    "lastAt": _latest(*[edge["lastAt"] for edge in related]),
                    "href": _href("repositories", rid),
                    "_rowid": int(repository["_rowid"]),
                    "_branches": set(branches),
                    "_overlapAgents": set(overlap_agents),
                }
            )
        repo_nodes.sort(
            key=lambda r: (
                -int(r["overlapCount"]),
                -int(r["agentCount"]),
                r["name"].casefold(),
                int(r["_rowid"]),
            )
        )

        work_agent_counts = {
            identity: len({edge["agentId"] for edge in work_edges if edge["workId"] == identity})
            for identity in included_work_ids
        }
        work_nodes: list[dict] = []
        for kind, identity in included_work_order:
            if kind == "roadmap":
                row = roadmaps[identity]
                proposal_ids = submitted_by_target.get(("roadmap", identity), [])
                questions = int(question_counts.get(identity, 0))
                work_nodes.append(
                    {
                        "id": identity,
                        "kind": "roadmap",
                        "title": row["title"],
                        "status": "open",
                        "roadmapId": identity,
                        "roadmapTitle": row["title"],
                        "parentId": row["parent_id"],
                        "stepTitle": None,
                        "rank": None,
                        "openQuestions": questions,
                        "waitingProposals": int(len(proposal_ids)),
                        "waitingProposalIds": list(proposal_ids),
                        "agentCount": int(work_agent_counts.get(identity, 0)),
                        "needsYou": bool(questions or proposal_ids),
                        "href": _href("roadmaps", identity),
                    }
                )
            elif kind == "step":
                row = steps[identity]
                roadmap = roadmaps.get(row["roadmap_id"])
                proposal_ids = submitted_by_target.get(("step", identity), [])
                work_nodes.append(
                    {
                        "id": identity,
                        "kind": "step",
                        "title": row["title"],
                        "status": row["status"],
                        "roadmapId": row["roadmap_id"],
                        "roadmapTitle": roadmap["title"] if roadmap else None,
                        "parentId": row["parent_id"],
                        "stepTitle": row["title"],
                        "rank": int(row["position"]),
                        "openQuestions": 0,
                        "waitingProposals": int(len(proposal_ids)),
                        "waitingProposalIds": list(proposal_ids),
                        "agentCount": int(work_agent_counts.get(identity, 0)),
                        "needsYou": bool(row["status"] == "blocked" or proposal_ids),
                        "href": _href("step", identity, row["roadmap_id"]),
                    }
                )
            else:
                row = items[identity]
                rid = _work_roadmap(row, steps)
                roadmap = roadmaps.get(rid)
                step = steps.get(row["step_id"])
                proposal_ids = submitted_by_target.get(("item", identity), [])
                work_nodes.append(
                    {
                        "id": identity,
                        "kind": "item",
                        "title": row["title"],
                        "status": row["status"],
                        "roadmapId": rid,
                        "roadmapTitle": roadmap["title"] if roadmap else None,
                        "parentId": row["step_id"],
                        "stepTitle": step["title"] if step else None,
                        "rank": int(row["backlog_order"])
                        if row["backlog_order"] is not None
                        else None,
                        "openQuestions": 0,
                        "waitingProposals": int(len(proposal_ids)),
                        "waitingProposalIds": list(proposal_ids),
                        "agentCount": int(work_agent_counts.get(identity, 0)),
                        "needsYou": bool(row["status"] in {"waiting", "blocked"} or proposal_ids),
                        "href": _href("work", identity),
                    }
                )

        def actor(kind: str, identity: str, label: str) -> tuple[str, str, str]:
            if kind in {"human", "person"}:
                return "person", "local-user", "You"
            return "agent", identity, label or agent_labels.get(identity, identity)

        included_discussions: set[str] = (
            {did for did, discussion in discussions.items() if not discussion["archived"]}
            if scoped is None
            else set()
        )
        for did, held in discussion_contexts.items():
            if scoped is not None and any(
                kind in {"item", "roadmap", "step"} and in_scope(kind, identity)
                for kind, identity in held
            ):
                included_discussions.add(did)

        comm_edges: list[dict] = []
        ack_discussions: set[str] = set()
        comm_seq = 0
        for request in sorted(request_rows, key=lambda r: int(r["_rowid"])):
            if request["discussion_id"] not in included_discussions:
                continue
            satisfied_recently = recent(request["terminal_at"])
            if request["state"] != "pending" and not satisfied_recently:
                continue
            from_kind, from_id, from_label = actor(
                request["author_kind"], request["author_id"], request["author_label"]
            )
            for target in sorted(
                targets_by_message[request["message_id"]], key=lambda r: int(r["_rowid"])
            ):
                to_kind: str
                to_id: str
                to_label: str
                label = "Acknowledgement"
                if target["kind"] == "agent":
                    to_kind, to_id = "agent", target["recipient_id"]
                    to_label = agent_labels.get(to_id, to_id)
                elif target["kind"] == "human":
                    to_kind, to_id, to_label = "person", "local-user", "You"
                elif target["kind"] == "role":
                    role = next((r for r in roles if r["id"] == target["recipient_id"]), None)
                    if role is None or not role["owner_id"]:
                        continue
                    to_kind, to_id, to_label = (
                        "agent",
                        role["owner_id"],
                        agent_labels.get(role["owner_id"], role["owner_id"]),
                    )
                    label = f"Ack ({role['label']})"
                else:
                    continue
                if not ({from_id, to_id} & candidate_agent_ids):
                    continue
                status = (
                    "pending"
                    if target["acknowledged_at"] is None and request["state"] == "pending"
                    else "satisfied"
                )
                if status == "satisfied" and not (
                    recent(target["acknowledged_at"]) or satisfied_recently
                ):
                    continue
                comm_seq += 1
                comm_edges.append(
                    {
                        "id": f"ack:{request['message_id']}:{int(target['_rowid'])}",
                        "fromKind": from_kind,
                        "fromId": from_id,
                        "fromLabel": from_label,
                        "toKind": to_kind,
                        "toId": to_id,
                        "toLabel": to_label,
                        "kind": "ack_request",
                        "status": status,
                        "label": label,
                        "discussionId": request["discussion_id"],
                        "messageId": request["message_id"],
                        "lastAt": target["acknowledged_at"] or request["created_at"],
                        "href": _href("discussions", request["discussion_id"]),
                        "_seq": comm_seq,
                    }
                )
                ack_discussions.add(request["discussion_id"])
        for offer in sorted(offers, key=lambda o: int(o["_rowid"])):
            at = offer["ended_at"] or offer["created_at"]
            if offer["state"] not in {"pending", "accepted", "declined"}:
                continue
            if offer["state"] != "pending" and not recent(at):
                continue
            if not ({offer["from_id"], offer["to_id"]} & candidate_agent_ids):
                continue
            if scoped is not None and not in_scope(offer["context_type"], offer["context_id"]):
                continue
            comm_seq += 1
            comm_edges.append(
                {
                    "id": f"offer:{offer['id']}",
                    "fromKind": "agent",
                    "fromId": offer["from_id"],
                    "fromLabel": agent_labels.get(offer["from_id"], offer["from_id"]),
                    "toKind": "agent",
                    "toId": offer["to_id"],
                    "toLabel": agent_labels.get(offer["to_id"], offer["to_id"]),
                    "kind": "role_offer",
                    "status": "pending" if offer["state"] == "pending" else "satisfied",
                    "label": f"Role: {offer['role_label']}",
                    "discussionId": None,
                    "messageId": None,
                    "lastAt": at,
                    "href": _href(
                        offer["context_type"] if offer["context_type"] != "item" else "work",
                        offer["context_id"],
                        roadmap_for(offer["context_type"], offer["context_id"]),
                    ),
                    "_seq": comm_seq,
                }
            )
        messages_by_discussion: dict[str, list[dict]] = defaultdict(list)
        for message in messages:
            if recent(message["created_at"]):
                messages_by_discussion[message["discussion_id"]].append(message)
        for did, posted in messages_by_discussion.items():
            if (
                did in ack_discussions
                or did not in included_discussions
                or discussions.get(did, {}).get("archived")
            ):
                continue
            authors: dict[tuple[str, str], dict] = {}
            for message in sorted(posted, key=lambda m: (m["created_at"], int(m["_rowid"]))):
                kind, aid, label = actor(
                    message["author_kind"], message["author_id"], message["author_label"]
                )
                if aid in candidate_agent_ids or aid == "local-user":
                    authors[(kind, aid)] = {
                        "kind": kind,
                        "id": aid,
                        "label": label,
                        "message": message,
                    }
            ordered = sorted(
                authors.values(),
                key=lambda a: (a["message"]["created_at"], int(a["message"]["_rowid"])),
            )
            for previous, latest in zip(ordered, ordered[1:], strict=False):
                if not ({previous["id"], latest["id"]} & candidate_agent_ids):
                    continue
                comm_seq += 1
                comm_edges.append(
                    {
                        "id": f"thread:{did}:{comm_seq}",
                        "fromKind": latest["kind"],
                        "fromId": latest["id"],
                        "fromLabel": latest["label"],
                        "toKind": previous["kind"],
                        "toId": previous["id"],
                        "toLabel": previous["label"],
                        "kind": "shared_thread",
                        "status": "active",
                        "label": f"Thread: {discussions[did]['title']}",
                        "discussionId": did,
                        "messageId": latest["message"]["id"],
                        "lastAt": latest["message"]["created_at"],
                        "href": _href("discussions", did),
                        "_seq": comm_seq,
                    }
                )
        for agent in agent_nodes:
            sponsor_id = agent["registeredById"]
            if agent["id"] in candidate_agent_ids and sponsor_id in candidate_agent_ids:
                comm_seq += 1
                comm_edges.append(
                    {
                        "id": f"sponsor:{agent['id']}",
                        "fromKind": "agent",
                        "fromId": sponsor_id,
                        "fromLabel": agent["registeredByLabel"]
                        or agent_labels.get(sponsor_id, sponsor_id),
                        "toKind": "agent",
                        "toId": agent["id"],
                        "toLabel": agent["label"],
                        "kind": "sponsorship",
                        "status": "active",
                        "label": "Registered subagent",
                        "discussionId": None,
                        "messageId": None,
                        "lastAt": None,
                        "href": _href("agents", agent["id"]),
                        "_seq": comm_seq,
                    }
                )

        lanes: list[dict] = []
        participation_run_ids: set[str] = set()
        for participation in participations:
            if participation["agent_id"] not in candidate_agent_ids:
                continue
            if scoped is not None and participation["item_id"] not in included_work_ids:
                continue
            if not (
                participation["released_at"] is None
                or recent(participation["last_seen_at"])
                or recent(participation["released_at"])
            ):
                continue
            participation_run_ids.add(participation["run_id"])
            run = next(r for r in runs if r["id"] == participation["run_id"])
            held_contexts = contexts[participation["id"]] or [None]
            for position, context in enumerate(held_contexts):
                repository_id = context["repository_id"] if context else None
                if repository_id is not None and repository_id not in include_repo_ids:
                    continue
                item = items.get(participation["item_id"])
                stale = (
                    _parse(participation["last_seen_at"]) < datetime.now(UTC) - STALE_AFTER
                    if _parse(participation["last_seen_at"])
                    else True
                )
                lanes.append(
                    {
                        "id": f"participation:{participation['id']}:{position}",
                        "agentId": participation["agent_id"],
                        "label": participation["description"] or run["display_name"],
                        "workId": participation["item_id"],
                        "workKind": "item" if item else None,
                        "repositoryId": repository_id,
                        "repositoryName": context["repository_name"] if context else None,
                        "branch": context["branch"] if context else "",
                        "startedAt": participation["created_at"],
                        "endedAt": participation["released_at"],
                        "lastSeenAt": participation["last_seen_at"],
                        "stale": bool(stale),
                        "href": _href("work", participation["item_id"])
                        if item
                        else _href("agents", participation["agent_id"]),
                        "_rowid": int(participation["_rowid"]),
                    }
                )
        for run in runs:
            if (
                run["ended_at"] is not None
                or run["id"] in participation_run_ids
                or run["agent_id"] not in candidate_agent_ids
            ):
                continue
            stale = (
                _parse(run["updated_at"]) < datetime.now(UTC) - STALE_AFTER
                if _parse(run["updated_at"])
                else True
            )
            lanes.append(
                {
                    "id": f"run:{run['id']}",
                    "agentId": run["agent_id"],
                    "label": run["display_name"],
                    "workId": None,
                    "workKind": None,
                    "repositoryId": None,
                    "repositoryName": None,
                    "branch": "",
                    "startedAt": run["created_at"],
                    "endedAt": None,
                    "lastSeenAt": run["updated_at"],
                    "stale": bool(stale),
                    "href": _href("agents", run["agent_id"]),
                    "_rowid": int(run["_rowid"]),
                }
            )

        work_title = {node["id"]: node["title"] for node in work_nodes}
        pulses: list[dict] = []

        def pulse_allowed(work_id: str | None, discussion_id: str | None = None) -> bool:
            if scoped is None:
                return True
            if work_id in included_work_ids:
                return True
            return discussion_id in included_discussions if discussion_id else False

        seen_updates: set[str] = set()
        for update in updates:
            if update["id"] in seen_updates or not recent(update["created_at"]):
                continue
            seen_updates.add(update["id"])
            if not pulse_allowed(update["item_id"]):
                continue
            repository_id = (
                update["repository_id"] if update["repository_id"] in include_repo_ids else None
            )
            pulses.append(
                {
                    "id": f"update:{update['id']}",
                    "kind": "blocker" if update["kind"] == "blocker" else "update",
                    "at": update["created_at"],
                    "actorKind": "agent",
                    "actorId": update["agent_id"],
                    "actorLabel": update["author_label"],
                    "targetActorId": None,
                    "targetActorLabel": None,
                    "workId": update["item_id"],
                    "workTitle": work_title.get(update["item_id"]),
                    "repositoryId": repository_id,
                    "repositoryName": repos.get(repository_id, {}).get("name"),
                    "discussionId": None,
                    "proposalId": None,
                    "summary": update["text"],
                    "href": _href("work", update["item_id"])
                    if update["item_id"]
                    else _href("agents", update["agent_id"]),
                    "_rank": PULSE_RANK["update"],
                    "_rowid": int(update["_rowid"]),
                }
            )
        requests_by_message = {r["message_id"]: r for r in request_rows}
        for message in messages:
            if not recent(message["created_at"]) or not pulse_allowed(
                None, message["discussion_id"]
            ):
                continue
            author_kind, author_id, author_label = actor(
                message["author_kind"], message["author_id"], message["author_label"]
            )
            work_id = next(
                (
                    identity
                    for kind, identity in discussion_contexts.get(message["discussion_id"], [])
                    if kind in {"item", "step", "roadmap"} and identity in included_work_ids
                ),
                None,
            )
            requested = message["id"] in requests_by_message
            pulses.append(
                {
                    "id": f"message:{message['id']}",
                    "kind": "ack_requested" if requested else "message",
                    "at": message["created_at"],
                    "actorKind": author_kind,
                    "actorId": author_id,
                    "actorLabel": author_label,
                    "targetActorId": None,
                    "targetActorLabel": None,
                    "workId": work_id,
                    "workTitle": work_title.get(work_id),
                    "repositoryId": None,
                    "repositoryName": None,
                    "discussionId": message["discussion_id"],
                    "proposalId": None,
                    "summary": message["text"],
                    "href": _href("discussions", message["discussion_id"]),
                    "_rank": PULSE_RANK["message"],
                    "_rowid": int(message["_rowid"]),
                }
            )
        for target in target_rows:
            if not recent(target["acknowledged_at"]):
                continue
            request = requests_by_message.get(target["message_id"])
            if request is None or not pulse_allowed(None, request["discussion_id"]):
                continue
            target_kind, target_id, target_label = actor(
                target["acknowledged_by_kind"] or "human",
                target["acknowledged_by"] or "local-user",
                target["acknowledged_by_label"] or "You",
            )
            pulses.append(
                {
                    "id": f"ack:{target['id']}",
                    "kind": "ack_satisfied",
                    "at": target["acknowledged_at"],
                    "actorKind": target_kind,
                    "actorId": target_id,
                    "actorLabel": target_label,
                    "targetActorId": request["author_id"]
                    if request["author_kind"] == "agent"
                    else "local-user",
                    "targetActorLabel": request["author_label"]
                    if request["author_kind"] == "agent"
                    else "You",
                    "workId": None,
                    "workTitle": None,
                    "repositoryId": None,
                    "repositoryName": None,
                    "discussionId": request["discussion_id"],
                    "proposalId": None,
                    "summary": "Acknowledgement received",
                    "href": _href("discussions", request["discussion_id"]),
                    "_rank": PULSE_RANK["ack"],
                    "_rowid": int(target["_rowid"]),
                }
            )
        for offer in offers:
            at = offer["ended_at"] or offer["created_at"]
            if not recent(at) or (
                scoped is not None and not in_scope(offer["context_type"], offer["context_id"])
            ):
                continue
            pulses.append(
                {
                    "id": f"offer:{offer['id']}",
                    "kind": "role_handoff",
                    "at": at,
                    "actorKind": "agent",
                    "actorId": offer["from_id"],
                    "actorLabel": agent_labels.get(offer["from_id"], offer["from_id"]),
                    "targetActorId": offer["to_id"],
                    "targetActorLabel": agent_labels.get(offer["to_id"], offer["to_id"]),
                    "workId": offer["context_id"]
                    if offer["context_id"] in included_work_ids
                    else None,
                    "workTitle": work_title.get(offer["context_id"]),
                    "repositoryId": None,
                    "repositoryName": None,
                    "discussionId": None,
                    "proposalId": None,
                    "summary": f"Role handoff: {offer['role_label']}",
                    "href": _href("agents", offer["to_id"]),
                    "_rank": PULSE_RANK["offer"],
                    "_rowid": int(offer["_rowid"]),
                }
            )
        for proposal in proposals:
            if proposal["status"] == "submitted" and recent(proposal["submitted_at"]):
                kind, at = "proposal_submitted", proposal["submitted_at"]
            elif proposal["status"] == "approved" and recent(proposal["updated_at"]):
                kind, at = "proposal_approved", proposal["updated_at"]
            else:
                continue
            targets = proposal_ties[proposal["id"]]
            if scoped is not None and not any(
                in_scope(target_kind, target_id) for target_kind, target_id in targets
            ):
                continue
            proposal_actor_kind, proposal_actor_id, proposal_actor_label = actor(
                proposal["actor_kind"], proposal["actor_id"], proposal["actor_label"]
            )
            target_work = min(
                (target_id for _, target_id in targets if target_id in included_work_ids),
                key=lambda identity: work_index[identity],
                default=None,
            )
            pulses.append(
                {
                    "id": f"proposal:{proposal['id']}:{kind}",
                    "kind": kind,
                    "at": at,
                    "actorKind": proposal_actor_kind,
                    "actorId": proposal_actor_id,
                    "actorLabel": proposal_actor_label,
                    "targetActorId": None,
                    "targetActorLabel": None,
                    "workId": target_work,
                    "workTitle": work_title.get(target_work),
                    "repositoryId": None,
                    "repositoryName": None,
                    "discussionId": None,
                    "proposalId": proposal["id"],
                    "summary": proposal["title"],
                    "href": _href("review", proposal["id"]),
                    "_rank": PULSE_RANK["proposal"],
                    "_rowid": int(proposal["_rowid"]),
                }
            )
        participation_seconds = {(p["run_id"], p["created_at"][:19]) for p in participations}
        for participation in participations:
            if (
                recent(participation["created_at"])
                and participation["agent_id"] in candidate_agent_ids
                and pulse_allowed(participation["item_id"])
            ):
                pulses.append(
                    {
                        "id": f"participation:{participation['id']}",
                        "kind": "participation_started",
                        "at": participation["created_at"],
                        "actorKind": "agent",
                        "actorId": participation["agent_id"],
                        "actorLabel": agent_labels.get(
                            participation["agent_id"], participation["agent_id"]
                        ),
                        "targetActorId": None,
                        "targetActorLabel": None,
                        "workId": participation["item_id"],
                        "workTitle": work_title.get(participation["item_id"]),
                        "repositoryId": None,
                        "repositoryName": None,
                        "discussionId": None,
                        "proposalId": None,
                        "summary": participation["description"] or "Participation started",
                        "href": _href("work", participation["item_id"])
                        if participation["item_id"]
                        else _href("agents", participation["agent_id"]),
                        "_rank": PULSE_RANK["participation"],
                        "_rowid": int(participation["_rowid"]),
                    }
                )
        for run in runs:
            if (
                recent(run["created_at"])
                and run["agent_id"] in candidate_agent_ids
                and (run["id"], run["created_at"][:19]) not in participation_seconds
            ):
                pulses.append(
                    {
                        "id": f"run:{run['id']}",
                        "kind": "run_started",
                        "at": run["created_at"],
                        "actorKind": "agent",
                        "actorId": run["agent_id"],
                        "actorLabel": agent_labels.get(run["agent_id"], run["agent_id"]),
                        "targetActorId": None,
                        "targetActorLabel": None,
                        "workId": None,
                        "workTitle": None,
                        "repositoryId": None,
                        "repositoryName": None,
                        "discussionId": None,
                        "proposalId": None,
                        "summary": run["display_name"],
                        "href": _href("agents", run["agent_id"]),
                        "_rank": PULSE_RANK["run"],
                        "_rowid": int(run["_rowid"]),
                    }
                )

        untracked = (
            []
            if scoped is not None
            else [
                session
                for session in session_ties
                if not any(not tracked["archived"] for tracked in session["tracked"])
            ]
        )
        return {
            "generatedAt": generated,
            "windowMinutes": int(window_minutes),
            "roadmapId": roadmap_id,
            "roadmapTitle": roadmaps[roadmap_id]["title"] if roadmap_id else None,
            "summary": {},
            "roadmaps": list(roadmap_options),
            "work": list(work_nodes),
            "agents": list(agent_nodes),
            "untrackedSessions": list(untracked),
            "repositories": list(repo_nodes),
            "workEdges": list(work_edges),
            "repoEdges": list(repo_edges),
            "commEdges": list(comm_edges),
            "lanes": list(lanes),
            "pulses": list(pulses),
            "_scoped": bool(scoped is not None),
            "_includeIdle": bool(include_idle),
            "_cutoff": cutoff,
            "_workIndex": work_index,
        }

    def enrich(self, draft: dict) -> dict:
        """Read transcripts after the database snapshot and return the exact typed response shape."""
        summaries: dict[str, dict] = {}
        for agent in draft["agents"]:
            for chat in agent["chats"]:
                summary = summaries.setdefault(
                    chat["chatId"], self.transcripts.summary(chat["chatId"])
                )
                chat["working"] = bool(summary.get("state") == "working")
                chat["lastAt"] = _latest(chat["lastAt"], summary.get("lastActivityAt"))
            if not agent["active"]:
                agent["state"] = "revoked"
            elif agent["sessionState"] == "waiting":
                agent["state"] = "waiting"
            elif (
                any(chat["working"] for chat in agent["chats"])
                or agent["sessionState"] == "working"
            ):
                agent["state"] = "working"
            elif agent["declaredState"] == "reporting":
                agent["state"] = "reporting"
            elif agent["declaredState"] == "stale":
                agent["state"] = "stale"
            else:
                agent["state"] = "idle"
            agent["lastAt"] = _latest(agent["lastAt"], *[chat["lastAt"] for chat in agent["chats"]])

        chosen = (
            [agent for agent in draft["agents"] if agent["_scope"]]
            if draft["_scoped"]
            else [
                agent
                for agent in draft["agents"]
                if draft["_includeIdle"] or agent["_keepBase"] or agent["state"] == "working"
            ]
        )
        chosen.sort(
            key=lambda a: (
                not a["active"],
                STATE_RANK[a["state"]],
                a["label"].casefold(),
                int(a["_rowid"]),
            )
        )
        agent_ids = {agent["id"] for agent in chosen}
        agent_index = {agent["id"]: index for index, agent in enumerate(chosen)}

        draft["workEdges"] = [edge for edge in draft["workEdges"] if edge["agentId"] in agent_ids]
        draft["workEdges"].sort(
            key=lambda edge: (agent_index[edge["agentId"]], draft["_workIndex"][edge["workId"]])
        )
        for work in draft["work"]:
            work["agentCount"] = int(
                len(
                    {edge["agentId"] for edge in draft["workEdges"] if edge["workId"] == work["id"]}
                )
            )

        draft["repoEdges"] = [edge for edge in draft["repoEdges"] if edge["agentId"] in agent_ids]
        for repository in draft["repositories"]:
            edges = [
                edge for edge in draft["repoEdges"] if edge["repositoryId"] == repository["id"]
            ]
            repository["agentCount"] = int(len({edge["agentId"] for edge in edges}))
            repository["overlapCount"] = int(len(repository["_overlapAgents"] & agent_ids))
            repository["branches"] = list(
                sorted(
                    repository["_branches"],
                    key=lambda branch: (branch.casefold(), branch),
                )
            )
            repository["lastAt"] = _latest(*[edge["lastAt"] for edge in edges])
        draft["repositories"].sort(
            key=lambda r: (
                -int(r["overlapCount"]),
                -int(r["agentCount"]),
                r["name"].casefold(),
                int(r["_rowid"]),
            )
        )
        repo_index = {
            repository["id"]: index for index, repository in enumerate(draft["repositories"])
        }
        draft["repoEdges"].sort(
            key=lambda edge: (agent_index[edge["agentId"]], repo_index[edge["repositoryId"]])
        )

        def present(kind: str, identity: str) -> bool:
            return identity == "local-user" if kind == "person" else identity in agent_ids

        draft["commEdges"] = [
            edge
            for edge in draft["commEdges"]
            if present(edge["fromKind"], edge["fromId"])
            and present(edge["toKind"], edge["toId"])
            and ({edge["fromId"], edge["toId"]} & agent_ids)
        ]
        actor_order = {**agent_index, "local-user": len(agent_index)}
        draft["commEdges"].sort(
            key=lambda edge: (
                edge["status"] != "pending",
                COMM_RANK[edge["kind"]],
                actor_order.get(edge["fromId"], len(actor_order)),
                actor_order.get(edge["toId"], len(actor_order)),
                int(edge["_seq"]),
            )
        )
        draft["lanes"] = [lane for lane in draft["lanes"] if lane["agentId"] in agent_ids]
        draft["lanes"].sort(
            key=lambda lane: (agent_index[lane["agentId"]], lane["startedAt"], int(lane["_rowid"]))
        )
        draft["lanes"] = draft["lanes"][:200]
        draft["pulses"].sort(
            key=lambda pulse: (
                _parse(pulse["at"]) or datetime.min.replace(tzinfo=UTC),
                int(pulse["_rank"]),
                int(pulse["_rowid"]),
            ),
            reverse=True,
        )
        draft["pulses"] = draft["pulses"][:60]

        untracked: list[dict] = []
        for session in draft["untrackedSessions"]:
            title, model = self.transcripts.headline(session["chatId"])
            state = {
                "busy": "working",
                "working": "working",
                "waiting": "waiting",
                "idle": "idle",
            }.get(session.get("status") or session.get("state"), "idle")
            untracked.append(
                {
                    "pid": int(session.get("pid") or 0),
                    "sessionId": session["sessionId"],
                    "hostSessionId": session.get("hostSessionId"),
                    "name": session.get("name")
                    or title
                    or self.transcripts.label(session["chatId"])
                    or "Claude session",
                    "state": state,
                    "entrypoint": session.get("entrypoint") or "",
                    "cwd": session.get("cwd") or "",
                    "model": model,
                    "startedAt": session.get("startedAt") or draft["generatedAt"],
                    "agentIds": list(
                        sorted(
                            (a["id"] for a in session["agents"] if a["id"] in agent_ids),
                            key=lambda aid: agent_index[aid],
                        )
                    ),
                }
            )
        untracked.sort(
            key=lambda session: (
                ORDER[session["state"]],
                -(_parse(session["startedAt"]) or datetime.min.replace(tzinfo=UTC)).timestamp(),
                int(session["pid"]),
            )
        )

        pending_keys = {
            (edge["kind"], edge["messageId"] or edge["id"])
            for edge in draft["commEdges"]
            if edge["status"] == "pending"
        }
        pending = len(pending_keys)
        waiting_proposals = len(
            {proposal_id for work in draft["work"] for proposal_id in work["waitingProposalIds"]}
        )
        open_questions = sum(
            option["openQuestions"]
            for option in draft["roadmaps"]
            if draft["roadmapId"] is None
            or option["id"] in {work["roadmapId"] for work in draft["work"]}
        )
        draft["summary"] = {
            "activeAgents": int(sum(agent["state"] not in {"idle", "revoked"} for agent in chosen)),
            "workingChats": int(
                len({chat["id"] for agent in chosen for chat in agent["chats"] if chat["working"]})
            ),
            "activeWorkItems": int(
                sum(
                    work["kind"] == "item" and work["status"] in {"doing", "waiting", "blocked"}
                    for work in draft["work"]
                )
            ),
            "activeSteps": int(
                sum(
                    work["kind"] == "step" and work["status"] in {"doing", "blocked"}
                    for work in draft["work"]
                )
            ),
            "activeRepos": int(len(draft["repositories"])),
            "repoOverlaps": int(
                sum(repository["overlapCount"] > 1 for repository in draft["repositories"])
            ),
            "pendingRequests": int(pending),
            "waitingProposals": int(waiting_proposals),
            "openQuestions": int(open_questions),
            "needsYou": int(pending + waiting_proposals + open_questions),
        }

        def clean(record: dict) -> dict:
            return {key: value for key, value in record.items() if not key.startswith("_")}

        return {
            "generatedAt": draft["generatedAt"],
            "windowMinutes": int(draft["windowMinutes"]),
            "roadmapId": draft["roadmapId"],
            "roadmapTitle": draft["roadmapTitle"],
            "summary": dict(draft["summary"]),
            "roadmaps": list(draft["roadmaps"]),
            "work": [clean(work) for work in draft["work"]],
            "agents": [clean(agent) for agent in chosen],
            "untrackedSessions": list(untracked),
            "repositories": [clean(repository) for repository in draft["repositories"]],
            "workEdges": list(draft["workEdges"]),
            "repoEdges": list(draft["repoEdges"]),
            "commEdges": [clean(edge) for edge in draft["commEdges"]],
            "lanes": [clean(lane) for lane in draft["lanes"]],
            "pulses": [clean(pulse) for pulse in draft["pulses"]],
        }
