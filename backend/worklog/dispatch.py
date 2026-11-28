"""Read-only Repo + Wave execution dispatch projection and shared execution guardrails (schema v35)."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from . import gates
from .common import AppError, rows_in
from .discussions import DiscussionService
from .leases import repository_slot_summaries
from .plans import _row as _roadmap_row
from .plans import outline_order
from .repository_links import repository_dependencies
from .waves import WaveService

STALE_AFTER = timedelta(hours=2)
WORK_CLASS_RANK = {
    "stabilization": 0,
    "critical_path": 1,
    "standard": 2,
    "": 3,
    "refactor": 4,
    "gate": 5,
}
_PREFIX_RE = re.compile(r"^\s*(?:\[([^\]]+)\]|([A-Za-z0-9._-]+)\s*:)")


def _stale(last_seen_at: str) -> bool:
    try:
        return datetime.fromisoformat(last_seen_at) < datetime.now(UTC) - STALE_AFTER
    except (TypeError, ValueError):
        return False


def _effective_wip_cap(wip_cap: int | None, slot_summary: dict[str, Any]) -> int | None:
    if wip_cap is not None:
        return int(wip_cap)
    if slot_summary.get("configured"):
        return int(slot_summary["maxActiveLeases"])
    return None


def _active_writers_by_repo(conn: sqlite3.Connection, repo_ids: list[str]) -> tuple[dict[str, int], dict[str, list[dict[str, Any]]]]:
    """Open participations per repository, stale ones included and marked, plus distinct active writers:
    agents with a fresh participation or an offered or active lease. A participation with no report for
    STALE_AFTER is listed but no longer counts toward a WIP cap, so an agent that never released one
    cannot hold a repository over its cap."""
    unique = list(dict.fromkeys(repo_ids))
    participations_by_repo: dict[str, list[dict[str, Any]]] = {rid: [] for rid in unique}
    writer_keys: dict[str, set[str]] = {rid: set() for rid in unique}
    if not unique:
        return {}, {}
    for r in rows_in(
        conn,
        "SELECT p.id,p.run_id,p.item_id,p.step_id,p.description,p.last_seen_at,"
        "ar.agent_id,ar.display_name,a.label AS agent_label,a.avatar,"
        "pc.repository_id,pc.branch,pc.worktree_path,pc.checkout_path "
        "FROM participation_contexts pc "
        "JOIN participations p ON p.id=pc.participation_id "
        "JOIN agent_runs ar ON ar.id=p.run_id "
        "JOIN communication_agents a ON a.id=ar.agent_id "
        "WHERE p.released_at IS NULL AND pc.repository_id IN ({}) "
        "ORDER BY p.last_seen_at DESC,p.id DESC",
        unique,
    ):
        rid = r["repository_id"]
        stale = _stale(r["last_seen_at"])
        if not stale:
            writer_keys[rid].add(f"agent:{r['agent_id']}")
        participations_by_repo[rid].append({
            "participationId": r["id"],
            "agentId": r["agent_id"],
            "agentLabel": r["agent_label"],
            "avatar": r["avatar"],
            "runName": r["display_name"],
            "itemId": r["item_id"],
            "stepId": r["step_id"],
            "description": r["description"],
            "branch": r["branch"],
            "worktreePath": r["worktree_path"] or r["checkout_path"],
            "lastSeenAt": r["last_seen_at"],
            "stale": stale,
        })
    for r in rows_in(
        conn,
        "SELECT id,repository_id,agent_id FROM repository_leases "
        "WHERE state IN ('offered','active') AND repository_id IN ({})",
        unique,
    ):
        rid = r["repository_id"]
        writer_keys[rid].add(f"agent:{r['agent_id']}" if r["agent_id"] else f"lease:{r['id']}")
    return {rid: len(writer_keys[rid]) for rid in unique}, participations_by_repo


def _doing_counts_by_repo(conn: sqlite3.Connection, repo_ids: list[str]) -> dict[str, int]:
    """Active non-retired leaf steps currently in 'doing' per repository across non-archived roadmaps."""
    unique = list(dict.fromkeys(repo_ids))
    counts = dict.fromkeys(unique, 0)
    if not unique:
        return counts
    for rid, total in rows_in(
        conn,
        "SELECT sr.repository_id,COUNT(DISTINCT s.id) FROM step_repositories sr "
        "JOIN steps s ON s.id=sr.step_id JOIN roadmaps rm ON rm.id=s.roadmap_id "
        "WHERE sr.repository_id IN ({}) AND s.retired=0 AND rm.archived=0 AND s.status='doing' "
        "AND NOT EXISTS(SELECT 1 FROM steps c WHERE c.parent_id=s.id AND c.retired=0) "
        "GROUP BY sr.repository_id",
        unique,
    ):
        counts[rid] = int(total or 0)
    return counts


def _suggest_repos(conn: sqlite3.Connection, unassigned_steps: list[dict[str, Any]],
                   catalog_repos: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Non-binding repository suggestions for unassigned leaf steps from linked items, chats, and title prefix."""
    step_ids = [s["id"] for s in unassigned_steps]
    suggestions: dict[str, set[str]] = {sid: set() for sid in step_ids}
    if not step_ids:
        return {}
    for step_id, repo_id in rows_in(
        conn,
        "SELECT w.step_id,ir.repository_id FROM work_items w "
        "JOIN item_repositories ir ON ir.item_id=w.id "
        "JOIN repositories r ON r.id=ir.repository_id "
        "WHERE w.archived=0 AND r.archived=0 AND r.merged_into IS NULL AND w.step_id IN ({})",
        step_ids,
    ):
        suggestions[step_id].add(repo_id)
    for step_id, repo_id in rows_in(
        conn,
        "SELECT cs.step_id,cr.repository_id FROM chat_steps cs "
        "JOIN agent_chats ac ON ac.id=cs.chat_id "
        "JOIN chat_repositories cr ON cr.chat_id=ac.id "
        "JOIN repositories r ON r.id=cr.repository_id "
        "WHERE ac.archived=0 AND r.archived=0 AND r.merged_into IS NULL AND cs.step_id IN ({})",
        step_ids,
    ):
        suggestions[step_id].add(repo_id)
    by_token: dict[str, str] = {}
    for repo in catalog_repos:
        name_cf = repo["name"].strip().casefold()
        if name_cf:
            by_token[name_cf] = repo["id"]
            if "-" in name_cf:
                # Match short tail only when unique across the catalog.
                tail = name_cf.rsplit("-", 1)[-1]
                if len(tail) >= 3:
                    by_token[tail] = "" if tail in by_token and by_token[tail] != repo["id"] else repo["id"]
    for st in unassigned_steps:
        m = _PREFIX_RE.match(st["title"])
        if m:
            token = (m.group(1) or m.group(2) or "").strip().casefold()
            matched = by_token.get(token)
            if matched:
                suggestions[st["id"]].add(matched)
    return {sid: sorted(rids) for sid, rids in suggestions.items()}


def dispatch_advisories(conn: sqlite3.Connection, repo_ids: list[str], step_ids: list[str]) -> dict[str, Any]:
    """Shared execution guardrail advisories for coordination-overlaps and Review proposals."""
    step_list = list(dict.fromkeys(step_ids))
    repo_set = set(repo_ids)
    step_repos: dict[str, list[str]] = defaultdict(list)
    if step_list:
        for sid, rid in rows_in(
            conn,
            "SELECT step_id,repository_id FROM step_repositories WHERE step_id IN ({}) ORDER BY repository_id",
            step_list,
        ):
            step_repos[sid].append(rid)
            repo_set.add(rid)
    all_repo_ids = sorted(repo_set)
    repo_rows = {
        r["id"]: r
        for r in rows_in(
            conn,
            "SELECT id,name,wip_cap,tier,archived,merged_into FROM repositories WHERE id IN ({})",
            all_repo_ids,
        )
    } if all_repo_ids else {}
    live_repo_ids = [rid for rid, r in repo_rows.items() if not r["archived"] and not r["merged_into"]]
    slot_summaries = repository_slot_summaries(conn, live_repo_ids) if live_repo_ids else {}
    writers, _ = _active_writers_by_repo(conn, live_repo_ids)
    doing = _doing_counts_by_repo(conn, live_repo_ids)

    frozen_by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    repins_by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if live_repo_ids:
        for r in rows_in(
            conn,
            "SELECT wr.repository_id,wr.freeze_status,wr.frozen_ref,w.id AS wave_id,w.name AS wave_name,w.roadmap_id "
            "FROM wave_repositories wr JOIN waves w ON w.id=wr.wave_id "
            "WHERE wr.repository_id IN ({}) AND wr.freeze_status IN ('batching','frozen') "
            "AND w.archived=0 AND w.status IN ('planning','active') "
            "ORDER BY w.status='active' DESC,w.position,w.id",
            live_repo_ids,
        ):
            frozen_by_repo[r["repository_id"]].append({
                "waveId": r["wave_id"],
                "waveName": r["wave_name"],
                "roadmapId": r["roadmap_id"],
                "freezeStatus": r["freeze_status"],
                "frozenRef": r["frozen_ref"],
            })
        # Query pending repins cleanly for upstream or consumer in live_repo_ids.
        marks = ",".join("?" for _ in live_repo_ids)
        for r in conn.execute(
            f"SELECT rp.wave_id,w.name AS wave_name,rp.upstream_repo_id,u.name AS upstream_repo_name,"
            f"rp.consumer_repo_id,c.name AS consumer_repo_name,rp.status "
            f"FROM wave_repins rp JOIN waves w ON w.id=rp.wave_id "
            f"JOIN repositories u ON u.id=rp.upstream_repo_id "
            f"JOIN repositories c ON c.id=rp.consumer_repo_id "
            f"WHERE (rp.upstream_repo_id IN ({marks}) OR rp.consumer_repo_id IN ({marks})) "
            f"AND rp.status IN ('pending','in_progress') AND w.archived=0 AND w.status IN ('planning','active') "
            f"ORDER BY w.position,u.name COLLATE NOCASE,c.name COLLATE NOCASE",
            [*live_repo_ids, *live_repo_ids],
        ):
            entry = {
                "waveId": r["wave_id"],
                "waveName": r["wave_name"],
                "upstreamRepoId": r["upstream_repo_id"],
                "upstreamRepoName": r["upstream_repo_name"],
                "consumerRepoId": r["consumer_repo_id"],
                "consumerRepoName": r["consumer_repo_name"],
                "status": r["status"],
            }
            if r["upstream_repo_id"] in repo_rows:
                repins_by_repo[r["upstream_repo_id"]].append(entry)
            if r["consumer_repo_id"] in repo_rows and r["consumer_repo_id"] != r["upstream_repo_id"]:
                repins_by_repo[r["consumer_repo_id"]].append(entry)

    repo_advisories = []
    for rid in sorted(live_repo_ids, key=lambda x: (repo_rows[x]["name"].casefold(), x)):
        row = repo_rows[rid]
        slot = slot_summaries.get(rid, {"configured": False, "maxActiveLeases": 2})
        eff_cap = _effective_wip_cap(row["wip_cap"], slot)
        active_w = writers.get(rid, 0)
        doing_s = doing.get(rid, 0)
        repo_advisories.append({
            "repositoryId": rid,
            "repositoryName": row["name"],
            "wipCap": row["wip_cap"],
            "effectiveWipCap": eff_cap,
            "tier": row["tier"],
            "activeWriters": active_w,
            "doingSteps": doing_s,
            "overWipCap": bool(eff_cap is not None and (active_w > eff_cap or doing_s > eff_cap)),
            "frozenWaves": frozen_by_repo.get(rid, []),
            "pendingRepins": repins_by_repo.get(rid, []),
        })

    multi_repo_steps: list[dict[str, Any]] = []
    competing_wave_steps: list[dict[str, Any]] = []
    if step_list:
        step_rows = rows_in(
            conn,
            "SELECT s.id,s.title,s.roadmap_id,s.work_class,s.retired,"
            "EXISTS(SELECT 1 FROM steps c WHERE c.parent_id=s.id AND c.retired=0) AS has_children "
            "FROM steps s WHERE s.id IN ({})",
            step_list,
        )
        roadmap_ids = list({r["roadmap_id"] for r in step_rows})
        active_waves_by_roadmap: dict[str, tuple[str, str]] = {}
        wave_step_membership: dict[str, set[str]] = defaultdict(set)
        if roadmap_ids:
            for w in rows_in(
                conn,
                "SELECT id,roadmap_id,name FROM waves WHERE roadmap_id IN ({}) AND archived=0 AND status='active' "
                "ORDER BY position,created_at,id",
                roadmap_ids,
            ):
                active_waves_by_roadmap.setdefault(w["roadmap_id"], (w["id"], w["name"]))
            active_wids = [wid for wid, _ in active_waves_by_roadmap.values()]
            if active_wids:
                for wid, sid in rows_in(conn, "SELECT wave_id,step_id FROM wave_steps WHERE wave_id IN ({})", active_wids):
                    wave_step_membership[wid].add(sid)
        for st in step_rows:
            if st["retired"] or st["has_children"]:
                continue
            rids = step_repos.get(st["id"], [])
            if len(rids) >= 2 and st["work_class"] != "gate":
                multi_repo_steps.append({
                    "stepId": st["id"],
                    "stepTitle": st["title"],
                    "roadmapId": st["roadmap_id"],
                    "repositoryIds": rids,
                })
            active_wave = active_waves_by_roadmap.get(st["roadmap_id"])
            if active_wave is not None and st["work_class"] != "stabilization":
                wid, wname = active_wave
                if st["id"] not in wave_step_membership[wid]:
                    competing_wave_steps.append({
                        "stepId": st["id"],
                        "stepTitle": st["title"],
                        "roadmapId": st["roadmap_id"],
                        "activeWaveId": wid,
                        "activeWaveName": wname,
                    })

    return {
        "repositories": repo_advisories,
        "multiRepoSteps": multi_repo_steps,
        "competingWaveSteps": competing_wave_steps,
    }


class DispatchService:
    def __init__(self, db: Any = None):
        self.db = db
        self.waves = WaveService(db)

    def roadmap_dispatch(self, conn: sqlite3.Connection, roadmap_id: str, *, wave_id: str | None = None,
                         repository_id: str | None = None, include_done: bool = False) -> dict[str, Any]:
        rm = _roadmap_row(conn, roadmap_id)
        # Lean step read: no description/outcome prose, keeping payload small even at 500+ steps.
        step_rows = conn.execute(
            "SELECT id,title,parent_id,position,status,revision,work_class FROM steps "
            "WHERE roadmap_id=? AND retired=0 ORDER BY position,id",
            (roadmap_id,),
        ).fetchall()
        steps = [
            {
                "id": r["id"],
                "title": r["title"],
                "parentId": r["parent_id"],
                "position": r["position"],
                "status": r["status"],
                "revision": r["revision"],
                "workClass": r["work_class"],
            }
            for r in step_rows
        ]
        by_id = {s["id"]: s for s in steps}
        gate_summaries = gates.summaries_by_step(conn, by_id)
        for sid, s in by_id.items():
            s["gateSummary"] = gate_summaries.get(sid, gates.empty_summary())
        children: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for s in steps:
            children[s["parentId"]].append(s)

        def derive(step: dict[str, Any]) -> tuple[str, bool]:
            kids = children.get(step["id"], [])
            summary = step["gateSummary"]
            if kids:
                results = [derive(k) for k in kids]
                states = [st for st, _ in results]
                all_eff_done = all(eff for _, eff in results)
                if all_eff_done and summary["blocking"] == 0:
                    step["status"] = "done"
                elif "blocked" in states:
                    step["status"] = "blocked"
                elif any(x in {"doing", "done"} for x in states):
                    step["status"] = "doing"
                else:
                    step["status"] = "open"
                return step["status"], step["status"] == "done"
            return step["status"], (step["status"] == "done" and summary["blocking"] == 0)

        for root in children.get(None, []):
            derive(root)

        ordered = outline_order(steps)
        outline_index = {s["id"]: idx for idx, s in enumerate(ordered)}
        leaves = [s for s in ordered if not children.get(s["id"])]

        def phase_path(step_id: str) -> list[dict[str, str]]:
            path: list[dict[str, str]] = []
            seen = {step_id}
            parent = by_id[step_id]["parentId"]
            while parent in by_id and parent not in seen:
                seen.add(parent)
                p = by_id[parent]
                path.insert(0, {"id": p["id"], "title": p["title"]})
                parent = p["parentId"]
            return path

        # Step -> repo links + per-repo queue positions in 1 query.
        step_repos: dict[str, list[str]] = defaultdict(list)
        repo_step_pos: dict[tuple[str, str], int | None] = {}
        for sid, rid, pos in conn.execute(
            "SELECT sr.step_id,sr.repository_id,sr.position FROM step_repositories sr "
            "JOIN steps s ON s.id=sr.step_id WHERE s.roadmap_id=? AND s.retired=0 "
            "ORDER BY sr.step_id,sr.repository_id",
            (roadmap_id,),
        ):
            step_repos[sid].append(rid)
            repo_step_pos[(rid, sid)] = pos

        waves_list = self.waves.list_waves(conn, roadmap_id, archived=False)["items"]
        active_wave: dict[str, Any] | None = None
        if wave_id is not None:
            match = next((w for w in waves_list if w["id"] == wave_id), None)
            if match is None:
                raise AppError(404, "not_found", "Wave not found on this roadmap")
            active_wave = self.waves.detail(conn, match["id"])
        else:
            default_wave = next((w for w in waves_list if w["status"] == "active"), None) or next(
                (w for w in waves_list if w["status"] == "planning"), None
            )
            if default_wave is not None:
                active_wave = self.waves.detail(conn, default_wave["id"])

        # Map each step to its active/planning wave ID (at most one).
        step_wave_id: dict[str, str] = {}
        for sid, wid in conn.execute(
            "SELECT ws.step_id,ws.wave_id FROM wave_steps ws JOIN waves w ON w.id=ws.wave_id "
            "WHERE w.roadmap_id=? AND w.archived=0 AND w.status IN ('active','planning') "
            "ORDER BY w.status='active' DESC,w.position,w.id",
            (roadmap_id,),
        ):
            step_wave_id.setdefault(sid, wid)

        active_wave_step_ids = set(active_wave["stepIds"]) if active_wave else set()
        active_wave_repos = {r["repositoryId"]: r for r in active_wave["repositories"]} if active_wave else {}

        # Gather every repository appearing in steps or active wave.
        lane_repo_ids: set[str] = set()
        for rids in step_repos.values():
            lane_repo_ids.update(rids)
        lane_repo_ids.update(active_wave_repos)
        if repository_id is not None:
            lane_repo_ids.add(repository_id)

        repo_rows = {
            r["id"]: r
            for r in rows_in(
                conn,
                "SELECT id,name,url,wip_cap,tier,archived,merged_into FROM repositories WHERE id IN ({})",
                lane_repo_ids,
            )
        } if lane_repo_ids else {}
        visible_repo_ids = [
            rid for rid, r in repo_rows.items()
            if not r["archived"] and not r["merged_into"]
            and (repository_id is None or rid == repository_id)
        ]
        slot_summaries = repository_slot_summaries(conn, visible_repo_ids) if visible_repo_ids else {}
        deps = repository_dependencies(conn, visible_repo_ids) if visible_repo_ids else {}
        home_discussions = DiscussionService.home_discussions(conn, visible_repo_ids) if visible_repo_ids else {}
        writers_by_repo, parts_by_repo = _active_writers_by_repo(conn, visible_repo_ids)

        # Direct step-linked active participations on this roadmap (so step cards can show who's on them).
        step_agents: dict[str, list[dict[str, Any]]] = defaultdict(list)
        leaf_ids = [s["id"] for s in leaves]
        if leaf_ids:
            for r in rows_in(
                conn,
                "SELECT p.step_id,ar.agent_id,a.label,a.avatar,p.last_seen_at FROM participations p "
                "JOIN agent_runs ar ON ar.id=p.run_id JOIN communication_agents a ON a.id=ar.agent_id "
                "WHERE p.released_at IS NULL AND p.step_id IN ({}) ORDER BY p.last_seen_at DESC",
                leaf_ids,
            ):
                step_agents[r["step_id"]].append({
                    "agentId": r["agent_id"],
                    "label": r["label"],
                    "avatar": r["avatar"],
                    "stale": _stale(r["last_seen_at"]),
                })

        def step_card(st: dict[str, Any], repo_id: str | None = None) -> dict[str, Any]:
            rids = step_repos.get(st["id"], [])
            wid = step_wave_id.get(st["id"])
            return {
                "id": st["id"],
                "title": st["title"],
                "status": st["status"],
                "revision": st["revision"],
                "workClass": st["workClass"],
                "repositoryIds": rids,
                "multiRepo": len(rids) >= 2 and st["workClass"] != "gate",
                "waveId": wid,
                "inActiveWave": bool(active_wave is not None and st["id"] in active_wave_step_ids),
                "queuePosition": repo_step_pos.get((repo_id, st["id"])) if repo_id else None,
                "phasePath": phase_path(st["id"]),
                "activeAgents": step_agents.get(st["id"], []),
                "gateSummary": st["gateSummary"],
            }

        repo_lanes = []
        competing_doing_total = 0
        for rid in visible_repo_ids:
            repo = repo_rows[rid]
            slot = slot_summaries[rid]
            eff_cap = _effective_wip_cap(repo["wip_cap"], slot)
            repo_leaves = [s for s in leaves if rid in step_repos.get(s["id"], ())]

            def sort_key(st: dict[str, Any], _rid: str = rid) -> tuple:
                pos = repo_step_pos.get((_rid, st["id"]))
                return (
                    WORK_CLASS_RANK.get(st["workClass"], 3),
                    0 if (active_wave is not None and st["id"] in active_wave_step_ids) else 1,
                    pos if pos is not None else 10**9,
                    outline_index[st["id"]],
                )

            doing_all = sorted((s for s in repo_leaves if s["status"] == "doing"), key=sort_key)
            doing_in_wave: list[dict[str, Any]] = []
            competing_wip: list[dict[str, Any]] = []
            for st in doing_all:
                card = step_card(st, rid)
                if active_wave is not None and not card["inActiveWave"] and st["workClass"] != "stabilization":
                    competing_wip.append(card)
                else:
                    doing_in_wave.append(card)
            competing_doing_total += len(competing_wip)
            queued = [step_card(s, rid) for s in sorted((s for s in repo_leaves if s["status"] == "open"), key=sort_key)]
            blocked = [step_card(s, rid) for s in sorted((s for s in repo_leaves if s["status"] == "blocked"), key=sort_key)]
            done_leaves = [s for s in repo_leaves if s["status"] == "done"]
            done_cards = [step_card(s, rid) for s in sorted(done_leaves, key=sort_key)] if include_done else []

            active_w = writers_by_repo.get(rid, 0)
            doing_count = len(doing_all)
            over_cap = bool(eff_cap is not None and (active_w > eff_cap or doing_count > eff_cap))
            freeze_entry = active_wave_repos.get(rid)
            wave_repins = [
                rp for rp in (active_wave["repins"] if active_wave else [])
                if rp["upstreamRepoId"] == rid or rp["consumerRepoId"] == rid
            ]
            tier = freeze_entry["tier"] if (freeze_entry and freeze_entry["tier"] is not None) else repo["tier"]
            repo_lanes.append({
                "repositoryId": rid,
                "repositoryName": repo["name"],
                "url": repo["url"],
                "discussionId": home_discussions.get(rid),
                "wipCap": repo["wip_cap"],
                "effectiveWipCap": eff_cap,
                "tier": tier,
                "upstreamIds": deps[rid]["upstreamIds"],
                "downstreamIds": deps[rid]["downstreamIds"],
                "slotSummary": {
                    "maxActiveLeases": slot["maxActiveLeases"],
                    "activeCount": slot["activeCount"],
                    "queuedCount": slot["queuedCount"],
                    "dirtyCount": slot["dirtyCount"],
                    "rescuedCount": slot["rescuedCount"],
                    "baseReadonly": slot["baseReadonly"],
                    "configured": slot["configured"],
                    "revision": slot["revision"],
                },
                "activeWriters": active_w,
                "doingCount": doing_count,
                "overWipCap": over_cap,
                "freezeState": freeze_entry,
                "repins": wave_repins,
                "doing": doing_in_wave,
                "competingWip": competing_wip,
                "queued": queued,
                "blocked": blocked,
                "done": done_cards,
                "blockedCount": len(blocked),
                "doneCount": len(done_leaves),
                "participations": parts_by_repo.get(rid, []),
            })
        repo_lanes.sort(key=lambda lane: (
            0 if lane["overWipCap"] else 1,
            lane["tier"] if lane["tier"] is not None else 99,
            lane["repositoryName"].casefold(),
            lane["repositoryId"],
        ))

        multi_repo_leaves = [
            step_card(s)
            for s in sorted(
                (s for s in leaves if len(step_repos.get(s["id"], ())) >= 2 and s["workClass"] != "gate"
                 and (include_done or s["status"] != "done")),
                key=lambda st: (0 if st["status"] == "doing" else 1, outline_index[st["id"]]),
            )
        ]

        unassigned_raw = [
            s for s in leaves
            if len(step_repos.get(s["id"], ())) == 0 and (include_done or s["status"] != "done")
        ]
        catalog_repos = [
            {"id": r["id"], "name": r["name"]}
            for r in conn.execute("SELECT id,name FROM repositories WHERE archived=0 AND merged_into IS NULL")
        ]
        suggested = _suggest_repos(conn, unassigned_raw, catalog_repos)
        unassigned_leaves = [
            {**step_card(s), "suggestedRepoIds": suggested.get(s["id"], [])}
            for s in unassigned_raw
        ]

        # Readiness gates: workClass=='gate' leaves + top-level phases with leaf progress rollup.
        gate_leaves = [step_card(s) for s in leaves if s["workClass"] == "gate"]
        phase_rollups = []
        for root in children.get(None, []):
            # Collect descendant leaves of root.
            stack = [root]
            root_leaves: list[dict[str, Any]] = []
            while stack:
                node = stack.pop()
                kids = children.get(node["id"], [])
                if not kids:
                    root_leaves.append(node)
                else:
                    stack.extend(kids)
            phase_rollups.append({
                "id": root["id"],
                "title": root["title"],
                "status": root["status"],
                "totalLeaves": len(root_leaves),
                "doneLeaves": sum(1 for x in root_leaves if x["status"] == "done"),
                "doingLeaves": sum(1 for x in root_leaves if x["status"] == "doing"),
                "blockedLeaves": sum(1 for x in root_leaves if x["status"] == "blocked"),
                "openLeaves": sum(1 for x in root_leaves if x["status"] == "open"),
            })

        return {
            "roadmap": {
                "id": rm["id"],
                "title": rm["title"],
                "revision": rm["revision"],
                "archived": bool(rm["archived"]),
            },
            "summary": {
                "totalLeaves": len(leaves),
                "doingLeaves": sum(1 for s in leaves if s["status"] == "doing"),
                "blockedLeaves": sum(1 for s in leaves if s["status"] == "blocked"),
                "doneLeaves": sum(1 for s in leaves if s["status"] == "done"),
                "overCapRepos": sum(1 for lane in repo_lanes if lane["overWipCap"]),
                "competingDoingCount": competing_doing_total,
                "multiRepoLeafCount": len(multi_repo_leaves),
                "unassignedLeafCount": len(unassigned_leaves),
                "pendingRepinCount": active_wave["pendingRepinCount"] if active_wave else 0,
            },
            "waves": waves_list,
            "activeWave": active_wave,
            "repoLanes": repo_lanes,
            "multiRepoLeaves": multi_repo_leaves,
            "unassignedLeaves": unassigned_leaves,
            "gates": {
                "gateSteps": gate_leaves,
                "phases": phase_rollups,
            },
        }

    def repository_queue(self, conn: sqlite3.Connection, repository_id: str, *, include_done: bool = False) -> dict[str, Any]:
        repo = conn.execute("SELECT * FROM repositories WHERE id=?", (repository_id,)).fetchone()
        if repo is None:
            raise AppError(404, "not_found", "Repository not found")
        slot = repository_slot_summaries(conn, [repository_id])[repository_id]
        deps = repository_dependencies(conn, [repository_id])[repository_id]
        writers, parts_by_repo = _active_writers_by_repo(conn, [repository_id])

        # Active/planning wave freezes & pending repins touching this repo across all roadmaps.
        freezes = [
            {
                "waveId": r["wave_id"],
                "waveName": r["wave_name"],
                "roadmapId": r["roadmap_id"],
                "roadmapTitle": r["roadmap_title"],
                "tier": r["tier"] if r["tier"] is not None else repo["tier"],
                "freezeStatus": r["freeze_status"],
                "frozenRef": r["frozen_ref"],
                "frozenAt": r["frozen_at"],
                "notes": r["notes"],
            }
            for r in conn.execute(
                "SELECT wr.*,w.name AS wave_name,w.roadmap_id,rm.title AS roadmap_title "
                "FROM wave_repositories wr JOIN waves w ON w.id=wr.wave_id "
                "JOIN roadmaps rm ON rm.id=w.roadmap_id "
                "WHERE wr.repository_id=? AND w.archived=0 AND rm.archived=0 AND w.status IN ('planning','active') "
                "ORDER BY w.status='active' DESC,w.position,w.id",
                (repository_id,),
            )
        ]
        repins = [
            {
                "waveId": r["wave_id"],
                "waveName": r["wave_name"],
                "upstreamRepoId": r["upstream_repo_id"],
                "upstreamRepoName": r["upstream_repo_name"],
                "consumerRepoId": r["consumer_repo_id"],
                "consumerRepoName": r["consumer_repo_name"],
                "status": r["status"],
                "pinnedRef": r["pinned_ref"],
                "prUrl": r["pr_url"],
                "commitSha": r["commit_sha"],
                "reportedByLabel": r["reported_by_label"],
                "verifiedAt": r["verified_at"],
                "revision": r["revision"],
                "updatedAt": r["updated_at"],
            }
            for r in conn.execute(
                "SELECT rp.*,w.name AS wave_name,u.name AS upstream_repo_name,c.name AS consumer_repo_name "
                "FROM wave_repins rp JOIN waves w ON w.id=rp.wave_id "
                "JOIN repositories u ON u.id=rp.upstream_repo_id "
                "JOIN repositories c ON c.id=rp.consumer_repo_id "
                "WHERE (rp.upstream_repo_id=? OR rp.consumer_repo_id=?) "
                "AND w.archived=0 AND w.status IN ('planning','active') "
                "ORDER BY w.position,u.name COLLATE NOCASE,c.name COLLATE NOCASE",
                (repository_id, repository_id),
            )
        ]

        # All active leaf steps linked to this repo across non-archived roadmaps.
        rows = conn.execute(
            "SELECT s.id,s.roadmap_id,rm.title AS roadmap_title,s.title,s.status,s.revision,s.work_class,"
            "s.position AS step_position,sr.position AS queue_position "
            "FROM step_repositories sr "
            "JOIN steps s ON s.id=sr.step_id "
            "JOIN roadmaps rm ON rm.id=s.roadmap_id "
            "WHERE sr.repository_id=? AND s.retired=0 AND rm.archived=0 "
            "AND NOT EXISTS(SELECT 1 FROM steps c WHERE c.parent_id=s.id AND c.retired=0)",
            (repository_id,),
        ).fetchall()
        step_ids = [r["id"] for r in rows]
        wave_by_step: dict[str, tuple[str, str, str]] = {}
        repo_counts: dict[str, int] = defaultdict(int)
        if step_ids:
            for sid, wid, wname, wstatus in rows_in(
                conn,
                "SELECT ws.step_id,w.id,w.name,w.status FROM wave_steps ws JOIN waves w ON w.id=ws.wave_id "
                "WHERE ws.step_id IN ({}) AND w.archived=0 AND w.status IN ('active','planning') "
                "ORDER BY w.status='active' DESC,w.position,w.id",
                step_ids,
            ):
                wave_by_step.setdefault(sid, (wid, wname, wstatus))
            for sid, cnt in rows_in(
                conn,
                "SELECT step_id,COUNT(*) FROM step_repositories WHERE step_id IN ({}) GROUP BY step_id",
                step_ids,
            ):
                repo_counts[sid] = int(cnt or 0)

        def entry(r: sqlite3.Row) -> dict[str, Any]:
            w = wave_by_step.get(r["id"])
            return {
                "id": r["id"],
                "roadmapId": r["roadmap_id"],
                "roadmapTitle": r["roadmap_title"],
                "title": r["title"],
                "status": r["status"],
                "revision": r["revision"],
                "workClass": r["work_class"],
                "queuePosition": r["queue_position"],
                "waveId": w[0] if w else None,
                "waveName": w[1] if w else None,
                "waveStatus": w[2] if w else None,
                "multiRepo": repo_counts.get(r["id"], 1) >= 2 and r["work_class"] != "gate",
            }

        def order(r: sqlite3.Row) -> tuple:
            w = wave_by_step.get(r["id"])
            return (
                WORK_CLASS_RANK.get(r["work_class"], 3),
                0 if (w and w[2] == "active") else 1 if w else 2,
                r["queue_position"] if r["queue_position"] is not None else 10**9,
                r["roadmap_title"].casefold(),
                r["step_position"],
                r["id"],
            )

        doing = [entry(r) for r in sorted((r for r in rows if r["status"] == "doing"), key=order)]
        queued = [entry(r) for r in sorted((r for r in rows if r["status"] == "open"), key=order)]
        blocked = [entry(r) for r in sorted((r for r in rows if r["status"] == "blocked"), key=order)]
        done_rows = [r for r in rows if r["status"] == "done"]
        done = [entry(r) for r in sorted(done_rows, key=order)] if include_done else []

        eff_cap = _effective_wip_cap(repo["wip_cap"], slot)
        active_w = writers.get(repository_id, 0)
        return {
            "repositoryId": repository_id,
            "repositoryName": repo["name"],
            "wipCap": repo["wip_cap"],
            "effectiveWipCap": eff_cap,
            "tier": repo["tier"],
            "upstreamIds": deps["upstreamIds"],
            "downstreamIds": deps["downstreamIds"],
            "activeWriters": active_w,
            "doingCount": len(doing),
            "overWipCap": bool(eff_cap is not None and (active_w > eff_cap or len(doing) > eff_cap)),
            "freezes": freezes,
            "repins": repins,
            "participations": parts_by_repo.get(repository_id, []),
            "doing": doing,
            "queued": queued,
            "blocked": blocked,
            "done": done,
            "doneCount": len(done_rows),
        }
