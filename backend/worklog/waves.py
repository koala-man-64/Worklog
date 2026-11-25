"""Synchronized release and contract waves across dependency tiers (schema v35)."""

from __future__ import annotations

import sqlite3
from typing import Any

from .common import AppError, event, now, require_revision, text, uid
from .coordination import _get as _get_repo
from .coordination import _refuse_merged, commit_sha, pull_request
from .discussions import DiscussionService
from .plans import _row as _roadmap_row
from .repository_links import repository_dependencies, roadmap_step_repositories

WAVE_STATUSES = {"planning", "active", "completed", "cancelled"}
FREEZE_STATUSES = {"open", "batching", "frozen"}
REPIN_STATUSES = {"pending", "in_progress", "verified", "waived"}


def _wave_row(conn: sqlite3.Connection, wave_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM waves WHERE id=?", (wave_id,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Wave not found")
    return row


def _tier(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1 or value > 9:
        raise AppError(422, "validation", "tier must be an integer between 1 and 9", {"tier": "range"})
    return value


def _active_repo(conn: sqlite3.Connection, repository_id: str) -> sqlite3.Row:
    rid = text(repository_id, "repositoryId", required=True, limit=200)
    repo = _get_repo(conn, "repositories", rid)
    _refuse_merged(conn, repo)
    if repo["archived"]:
        raise AppError(409, "archived", f"Recover {repo['name']} before adding it to a wave", {"repositoryId": rid})
    return repo


def _wave_base(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "roadmapId": row["roadmap_id"],
        "name": row["name"],
        "summary": row["summary"],
        "status": row["status"],
        "position": row["position"],
        "discussionId": row["discussion_id"],
        "archived": bool(row["archived"]),
        "revision": row["revision"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "activatedAt": row["activated_at"],
        "completedAt": row["completed_at"],
    }


def _repin_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "waveId": row["wave_id"],
        "upstreamRepoId": row["upstream_repo_id"],
        "upstreamRepoName": row["upstream_repo_name"],
        "consumerRepoId": row["consumer_repo_id"],
        "consumerRepoName": row["consumer_repo_name"],
        "status": row["status"],
        "pinnedRef": row["pinned_ref"],
        "prUrl": row["pr_url"],
        "commitSha": row["commit_sha"],
        "reportedByLabel": row["reported_by_label"],
        "verifiedAt": row["verified_at"],
        "revision": row["revision"],
        "updatedAt": row["updated_at"],
    }


def _repin_row(conn: sqlite3.Connection, wave_id: str, upstream_id: str, consumer_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT wr.*,u.name AS upstream_repo_name,c.name AS consumer_repo_name "
        "FROM wave_repins wr "
        "JOIN repositories u ON u.id=wr.upstream_repo_id "
        "JOIN repositories c ON c.id=wr.consumer_repo_id "
        "WHERE wr.wave_id=? AND wr.upstream_repo_id=? AND wr.consumer_repo_id=?",
        (wave_id, upstream_id, consumer_id),
    ).fetchone()


class WaveService:
    def __init__(self, db: Any = None):
        self.db = db

    def list_waves(self, conn: sqlite3.Connection, roadmap_id: str, *, status: str | None = None,
                   archived: bool = False) -> dict[str, Any]:
        _roadmap_row(conn, roadmap_id)
        if status is not None and status not in WAVE_STATUSES:
            raise AppError(422, "validation", "Invalid wave status")
        where = ["roadmap_id=?", "archived=?"]
        args: list[Any] = [roadmap_id, int(archived)]
        if status is not None:
            where.append("status=?")
            args.append(status)
        rows = conn.execute(
            "SELECT * FROM waves WHERE " + " AND ".join(where) +
            " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'planning' THEN 1 WHEN 'completed' THEN 2 ELSE 3 END,"
            "position,created_at,id",
            args,
        ).fetchall()
        wave_ids = [r["id"] for r in rows]
        step_counts: dict[str, tuple[int, int]] = {}
        frozen_counts: dict[str, int] = {}
        pending_repins: dict[str, int] = {}
        if wave_ids:
            marks = ",".join("?" for _ in wave_ids)
            for wid, total, done in conn.execute(
                f"SELECT ws.wave_id,COUNT(*),SUM(CASE WHEN s.status='done' THEN 1 ELSE 0 END) "
                f"FROM wave_steps ws JOIN steps s ON s.id=ws.step_id "
                f"WHERE ws.wave_id IN ({marks}) AND s.retired=0 GROUP BY ws.wave_id",
                wave_ids,
            ):
                step_counts[wid] = (int(total or 0), int(done or 0))
            for wid, count in conn.execute(
                f"SELECT wave_id,COUNT(*) FROM wave_repositories WHERE wave_id IN ({marks}) "
                "AND freeze_status='frozen' GROUP BY wave_id",
                wave_ids,
            ):
                frozen_counts[wid] = int(count or 0)
            for wid, count in conn.execute(
                f"SELECT wave_id,COUNT(*) FROM wave_repins WHERE wave_id IN ({marks}) "
                "AND status IN ('pending','in_progress') GROUP BY wave_id",
                wave_ids,
            ):
                pending_repins[wid] = int(count or 0)
        items = []
        for row in rows:
            wid = row["id"]
            total, done = step_counts.get(wid, (0, 0))
            items.append({
                **_wave_base(row),
                "stepCount": total,
                "doneStepCount": done,
                "frozenRepoCount": frozen_counts.get(wid, 0),
                "pendingRepinCount": pending_repins.get(wid, 0),
            })
        return {"items": items, "total": len(items)}

    def detail(self, conn: sqlite3.Connection, wave_id: str) -> dict[str, Any]:
        row = _wave_row(conn, wave_id)
        base = _wave_base(row)
        step_rows = conn.execute(
            "SELECT s.id,s.title,s.status,s.work_class,s.parent_id,s.position "
            "FROM wave_steps ws JOIN steps s ON s.id=ws.step_id "
            "WHERE ws.wave_id=? AND s.retired=0 ORDER BY s.position,s.id",
            (wave_id,),
        ).fetchall()
        step_repos = roadmap_step_repositories(conn, row["roadmap_id"])
        steps = [
            {
                "id": s["id"],
                "title": s["title"],
                "status": s["status"],
                "workClass": s["work_class"],
                "parentId": s["parent_id"],
                "repositoryIds": step_repos.get(s["id"], []),
            }
            for s in step_rows
        ]
        repo_rows = conn.execute(
            "SELECT wr.*,r.name AS repository_name,r.tier AS catalog_tier "
            "FROM wave_repositories wr JOIN repositories r ON r.id=wr.repository_id "
            "WHERE wr.wave_id=? ORDER BY COALESCE(wr.tier,r.tier,99),r.name COLLATE NOCASE,r.id",
            (wave_id,),
        ).fetchall()
        repositories = [
            {
                "repositoryId": r["repository_id"],
                "repositoryName": r["repository_name"],
                "tier": r["tier"] if r["tier"] is not None else r["catalog_tier"],
                "freezeStatus": r["freeze_status"],
                "frozenRef": r["frozen_ref"],
                "frozenAt": r["frozen_at"],
                "frozenByLabel": r["frozen_by_label"],
                "notes": r["notes"],
                "revision": r["revision"],
                "updatedAt": r["updated_at"],
            }
            for r in repo_rows
        ]
        repin_rows = conn.execute(
            "SELECT wr.*,u.name AS upstream_repo_name,c.name AS consumer_repo_name "
            "FROM wave_repins wr "
            "JOIN repositories u ON u.id=wr.upstream_repo_id "
            "JOIN repositories c ON c.id=wr.consumer_repo_id "
            "WHERE wr.wave_id=? ORDER BY u.name COLLATE NOCASE,c.name COLLATE NOCASE,wr.upstream_repo_id,wr.consumer_repo_id",
            (wave_id,),
        ).fetchall()
        repins = [_repin_record(r) for r in repin_rows]

        # Build tier pipeline grouping wave repositories by effective tier.
        step_counts_by_repo: dict[str, dict[str, int]] = {}
        for st in steps:
            for rid in st["repositoryIds"]:
                bucket = step_counts_by_repo.setdefault(rid, {"total": 0, "doing": 0, "done": 0, "blocked": 0})
                bucket["total"] += 1
                if st["status"] in {"doing", "done", "blocked"}:
                    bucket[st["status"]] += 1
        tiers_map: dict[int | None, list[dict[str, Any]]] = {}
        for repo in repositories:
            counts = step_counts_by_repo.get(repo["repositoryId"], {"total": 0, "doing": 0, "done": 0, "blocked": 0})
            tiers_map.setdefault(repo["tier"], []).append({
                **repo,
                "stepCount": counts["total"],
                "doingCount": counts["doing"],
                "doneCount": counts["done"],
                "blockedCount": counts["blocked"],
                "pendingDownstreamRepins": sum(
                    1 for rp in repins if rp["upstreamRepoId"] == repo["repositoryId"] and rp["status"] in {"pending", "in_progress"}
                ),
                "pendingUpstreamRepins": sum(
                    1 for rp in repins if rp["consumerRepoId"] == repo["repositoryId"] and rp["status"] in {"pending", "in_progress"}
                ),
            })
        tier_pipeline = [
            {"tier": t, "repositories": tiers_map[t]}
            for t in sorted(tiers_map, key=lambda key: (key is None, key or 0))
        ]
        return {
            **base,
            "stepCount": len(steps),
            "doneStepCount": sum(1 for s in steps if s["status"] == "done"),
            "frozenRepoCount": sum(1 for r in repositories if r["freezeStatus"] == "frozen"),
            "pendingRepinCount": sum(1 for rp in repins if rp["status"] in {"pending", "in_progress"}),
            "stepIds": [s["id"] for s in steps],
            "steps": steps,
            "repositories": repositories,
            "repins": repins,
            "tierPipeline": tier_pipeline,
        }

    def _ensure_wave_repos(self, conn: sqlite3.Connection, wave_id: str, repo_specs: list[tuple[str, int | None]]) -> None:
        stamp = now()
        for rid, explicit_tier in repo_specs:
            existing = conn.execute(
                "SELECT tier FROM wave_repositories WHERE wave_id=? AND repository_id=?",
                (wave_id, rid),
            ).fetchone()
            if existing is None:
                repo = _active_repo(conn, rid)
                effective_tier = explicit_tier if explicit_tier is not None else (repo["tier"] if repo["tier"] is not None else 2)
                conn.execute(
                    "INSERT INTO wave_repositories(wave_id,repository_id,tier,freeze_status,frozen_ref,frozen_at,"
                    "frozen_by_label,notes,revision,updated_at) VALUES(?,?,?,'open','',NULL,'','',1,?)",
                    (wave_id, rid, effective_tier, stamp),
                )
            elif explicit_tier is not None and existing["tier"] != explicit_tier:
                conn.execute(
                    "UPDATE wave_repositories SET tier=?,revision=revision+1,updated_at=? WHERE wave_id=? AND repository_id=?",
                    (explicit_tier, stamp, wave_id, rid),
                )

    def _apply_steps(self, conn: sqlite3.Connection, wave_id: str, roadmap_id: str, mode: str,
                     step_ids: list[str]) -> None:
        if mode not in {"replace", "add", "remove"}:
            raise AppError(422, "validation", "mode must be replace, add, or remove")
        if not isinstance(step_ids, list) or len(step_ids) > 2000 or not all(isinstance(s, str) and s for s in step_ids):
            raise AppError(422, "validation", "Provide up to 2000 step IDs")
        unique_ids = list(dict.fromkeys(step_ids))
        if mode == "remove":
            if unique_ids:
                conn.executemany(
                    "DELETE FROM wave_steps WHERE wave_id=? AND step_id=?",
                    [(wave_id, sid) for sid in unique_ids],
                )
            return
        if unique_ids:
            marks = ",".join("?" for _ in unique_ids)
            found = {
                r["id"]: r
                for r in conn.execute(f"SELECT id,roadmap_id,retired FROM steps WHERE id IN ({marks})", unique_ids)
            }
            for sid in unique_ids:
                step = found.get(sid)
                if step is None:
                    raise AppError(404, "not_found", "Step not found", {"stepIds": sid})
                if step["roadmap_id"] != roadmap_id:
                    raise AppError(422, "validation", "Every step in a wave must belong to that wave's roadmap", {"stepIds": sid})
                if step["retired"]:
                    raise AppError(409, "conflict", "Retired steps cannot be added to a wave", {"stepIds": sid})
        if mode == "replace":
            conn.execute("DELETE FROM wave_steps WHERE wave_id=?", (wave_id,))
        stamp = now()
        if unique_ids:
            # A step belongs to at most one active or planning wave at a time.
            marks = ",".join("?" for _ in unique_ids)
            conn.execute(
                f"DELETE FROM wave_steps WHERE step_id IN ({marks}) AND wave_id<>? "
                "AND wave_id IN (SELECT id FROM waves WHERE archived=0 AND status IN ('planning','active'))",
                [*unique_ids, wave_id],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO wave_steps(wave_id,step_id,position,added_at) VALUES(?,?,?,?)",
                [(wave_id, sid, idx, stamp) for idx, sid in enumerate(unique_ids)],
            )
            # Ensure every active repository linked to added steps appears in the wave's tier pipeline.
            linked_repos = [
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT sr.repository_id FROM step_repositories sr "
                    f"JOIN repositories r ON r.id=sr.repository_id "
                    f"WHERE sr.step_id IN ({marks}) AND r.archived=0 AND r.merged_into IS NULL",
                    unique_ids,
                )
            ]
            self._ensure_wave_repos(conn, wave_id, [(rid, None) for rid in linked_repos])

    def create(self, conn: sqlite3.Connection, actor: dict, payload: dict) -> dict[str, Any]:
        roadmap_id = text(payload.get("roadmapId"), "roadmapId", required=True, limit=200)
        roadmap = _roadmap_row(conn, roadmap_id)
        if roadmap["archived"]:
            raise AppError(409, "conflict", "Recover the roadmap before creating a wave on it")
        name = text(payload.get("name"), "name", required=True, limit=200).strip()
        if not name:
            raise AppError(422, "validation", "Wave name is required")
        summary = text(payload.get("summary", ""), "summary", limit=10000)
        repos_raw = payload.get("repositories") or []
        if not isinstance(repos_raw, list) or len(repos_raw) > 100:
            raise AppError(422, "validation", "Choose up to 100 repositories")
        repo_specs = [
            (_active_repo(conn, item["repositoryId"])["id"], _tier(item.get("tier")))
            for item in repos_raw
        ]
        position = conn.execute(
            "SELECT COALESCE(MAX(position),-1)+1 FROM waves WHERE roadmap_id=?", (roadmap_id,)
        ).fetchone()[0]
        discussion_id = None
        if payload.get("openDiscussion"):
            created_disc = DiscussionService(self.db).create(
                conn,
                actor,
                {
                    "title": f"Wave: {name}",
                    "text": summary or f"Synchronized release wave **{name}**.",
                    "contexts": [{"type": "roadmap", "id": roadmap_id}],
                },
            )
            discussion_id = created_disc["discussion"]["id"]
        wave_id, stamp = uid(), now()
        conn.execute(
            "INSERT INTO waves(id,roadmap_id,name,summary,status,position,discussion_id,archived,revision,"
            "created_at,updated_at,activated_at,completed_at) VALUES(?,?,?,?,'planning',?,?,0,1,?,?,NULL,NULL)",
            (wave_id, roadmap_id, name, summary, position, discussion_id, stamp, stamp),
        )
        if repo_specs:
            self._ensure_wave_repos(conn, wave_id, repo_specs)
        step_ids = payload.get("stepIds") or []
        if step_ids:
            self._apply_steps(conn, wave_id, roadmap_id, "replace", step_ids)
        result = self.detail(conn, wave_id)
        event(conn, actor, "wave.create", wave_id, None, _wave_base(_wave_row(conn, wave_id)))
        return result

    def update(self, conn: sqlite3.Connection, actor: dict, wave_id: str, payload: dict) -> dict[str, Any]:
        row = _wave_row(conn, wave_id)
        require_revision(row, payload.get("revision"))
        if row["archived"]:
            raise AppError(409, "conflict", "Recover the wave before editing it")
        before = _wave_base(row)
        name = text(payload["name"], "name", required=True, limit=200).strip() if payload.get("name") is not None else row["name"]
        if not name:
            raise AppError(422, "validation", "Wave name is required")
        summary = text(payload["summary"], "summary", limit=10000) if payload.get("summary") is not None else row["summary"]
        position = row["position"]
        if payload.get("position") is not None:
            if type(payload["position"]) is not int or payload["position"] < 0:
                raise AppError(422, "validation", "Invalid wave position")
            position = payload["position"]
        if "repositories" in payload and payload["repositories"] is not None:
            repos_raw = payload["repositories"]
            if not isinstance(repos_raw, list) or len(repos_raw) > 100:
                raise AppError(422, "validation", "Choose up to 100 repositories")
            specs = [
                (_active_repo(conn, item["repositoryId"])["id"], _tier(item.get("tier")))
                for item in repos_raw
            ]
            desired_ids = {rid for rid, _ in specs}
            existing = conn.execute(
                "SELECT repository_id,freeze_status FROM wave_repositories WHERE wave_id=?", (wave_id,)
            ).fetchall()
            for ex in existing:
                if ex["repository_id"] not in desired_ids:
                    if ex["freeze_status"] == "frozen":
                        raise AppError(409, "conflict", "Unfreeze a repository before removing it from the wave",
                                       {"repositoryId": ex["repository_id"]})
                    conn.execute(
                        "DELETE FROM wave_repositories WHERE wave_id=? AND repository_id=?",
                        (wave_id, ex["repository_id"]),
                    )
            self._ensure_wave_repos(conn, wave_id, specs)
        stamp = now()
        conn.execute(
            "UPDATE waves SET name=?,summary=?,position=?,revision=revision+1,updated_at=? WHERE id=?",
            (name, summary, position, stamp, wave_id),
        )
        after = _wave_base(_wave_row(conn, wave_id))
        event(conn, actor, "wave.update", wave_id, before, after)
        return self.detail(conn, wave_id)

    def set_steps(self, conn: sqlite3.Connection, actor: dict, wave_id: str, payload: dict) -> dict[str, Any]:
        row = _wave_row(conn, wave_id)
        require_revision(row, payload.get("revision"))
        if row["archived"] or row["status"] in {"completed", "cancelled"}:
            raise AppError(409, "conflict", "Cannot modify steps on an archived, completed, or cancelled wave")
        before = [r[0] for r in conn.execute("SELECT step_id FROM wave_steps WHERE wave_id=? ORDER BY step_id", (wave_id,))]
        mode = payload.get("mode", "replace")
        self._apply_steps(conn, wave_id, row["roadmap_id"], mode, payload.get("stepIds") or [])
        stamp = now()
        conn.execute("UPDATE waves SET revision=revision+1,updated_at=? WHERE id=?", (stamp, wave_id))
        after = [r[0] for r in conn.execute("SELECT step_id FROM wave_steps WHERE wave_id=? ORDER BY step_id", (wave_id,))]
        event(conn, actor, "wave.steps", wave_id, {"stepIds": before}, {"stepIds": after, "mode": mode})
        return self.detail(conn, wave_id)

    def set_status(self, conn: sqlite3.Connection, actor: dict, wave_id: str, payload: dict) -> dict[str, Any]:
        if actor["kind"] != "human":
            raise AppError(403, "forbidden", "Only a person may change wave lifecycle status")
        row = _wave_row(conn, wave_id)
        require_revision(row, payload.get("revision"))
        if row["archived"]:
            raise AppError(409, "conflict", "Recover the wave before changing its status")
        target = payload.get("status")
        if target not in WAVE_STATUSES:
            raise AppError(422, "validation", "Invalid wave status")
        if target in {"planning", "active"} and row["status"] in {"completed", "cancelled"}:
            # Ensure reopening does not violate single active/planning wave per step.
            clash = conn.execute(
                "SELECT w.id,w.name FROM wave_steps ws1 JOIN wave_steps ws2 ON ws2.step_id=ws1.step_id "
                "JOIN waves w ON w.id=ws2.wave_id "
                "WHERE ws1.wave_id=? AND ws2.wave_id<>? AND w.archived=0 AND w.status IN ('planning','active') LIMIT 1",
                (wave_id, wave_id),
            ).fetchone()
            if clash is not None:
                raise AppError(409, "conflict", f"Some steps now belong to wave {clash['name']}; remove them before reopening",
                               {"waveId": clash["id"]})
        before = _wave_base(row)
        stamp = now()
        activated_at = row["activated_at"] or (stamp if target == "active" else None)
        completed_at = stamp if target == "completed" else (None if target in {"planning", "active"} else row["completed_at"])
        conn.execute(
            "UPDATE waves SET status=?,activated_at=?,completed_at=?,revision=revision+1,updated_at=? WHERE id=?",
            (target, activated_at, completed_at, stamp, wave_id),
        )
        after = _wave_base(_wave_row(conn, wave_id))
        event(conn, actor, "wave.status", wave_id, before, after)
        return self.detail(conn, wave_id)

    def set_freeze(self, conn: sqlite3.Connection, actor: dict, wave_id: str, payload: dict) -> dict[str, Any]:
        if actor["kind"] != "human":
            raise AppError(403, "forbidden", "Only a person may freeze or unfreeze wave repositories")
        row = _wave_row(conn, wave_id)
        require_revision(row, payload.get("revision"))
        if row["archived"] or row["status"] in {"completed", "cancelled"}:
            raise AppError(409, "conflict", "Cannot change freeze state on an archived, completed, or cancelled wave")
        repo = _active_repo(conn, payload.get("repositoryId", ""))
        repo_id = repo["id"]
        freeze_status = payload.get("freezeStatus")
        if freeze_status not in FREEZE_STATUSES:
            raise AppError(422, "validation", "freezeStatus must be open, batching, or frozen")
        frozen_ref = text(payload.get("frozenRef", ""), "frozenRef", limit=500).strip()
        notes = text(payload.get("notes", ""), "notes", limit=4000)
        stamp = now()
        existing = conn.execute(
            "SELECT * FROM wave_repositories WHERE wave_id=? AND repository_id=?",
            (wave_id, repo_id),
        ).fetchone()
        before = dict(existing) if existing else None
        frozen_at = stamp if freeze_status == "frozen" else None
        frozen_by_label = actor["label"] if freeze_status == "frozen" else ""
        effective_tier = repo["tier"] if repo["tier"] is not None else 2
        if existing is None:
            conn.execute(
                "INSERT INTO wave_repositories(wave_id,repository_id,tier,freeze_status,frozen_ref,frozen_at,"
                "frozen_by_label,notes,revision,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?)",
                (wave_id, repo_id, effective_tier, freeze_status, frozen_ref, frozen_at, frozen_by_label, notes, stamp),
            )
        else:
            conn.execute(
                "UPDATE wave_repositories SET freeze_status=?,frozen_ref=?,frozen_at=?,frozen_by_label=?,notes=?,"
                "revision=revision+1,updated_at=? WHERE wave_id=? AND repository_id=?",
                (freeze_status, frozen_ref, frozen_at, frozen_by_label, notes, stamp, wave_id, repo_id),
            )
        seeded: list[str] = []
        if freeze_status == "frozen":
            deps = repository_dependencies(conn, [repo_id])[repo_id]["downstreamIds"]
            if deps:
                wave_repo_ids = {
                    r[0] for r in conn.execute("SELECT repository_id FROM wave_repositories WHERE wave_id=?", (wave_id,))
                }
                roadmap_repo_ids = {
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT sr.repository_id FROM step_repositories sr JOIN steps s ON s.id=sr.step_id "
                        "WHERE s.roadmap_id=? AND s.retired=0",
                        (row["roadmap_id"],),
                    )
                }
                active_downstream = {
                    r[0]
                    for r in conn.execute(
                        f"SELECT id FROM repositories WHERE archived=0 AND merged_into IS NULL AND id IN ({','.join('?' for _ in deps)})",
                        deps,
                    )
                }
                consumers = sorted(cid for cid in active_downstream if cid in wave_repo_ids or cid in roadmap_repo_ids)
                for consumer_id in consumers:
                    repin = conn.execute(
                        "SELECT status,pinned_ref FROM wave_repins WHERE wave_id=? AND upstream_repo_id=? AND consumer_repo_id=?",
                        (wave_id, repo_id, consumer_id),
                    ).fetchone()
                    if repin is None:
                        conn.execute(
                            "INSERT INTO wave_repins(wave_id,upstream_repo_id,consumer_repo_id,status,"
                            "pinned_ref,pr_url,commit_sha,reported_by_label,verified_at,revision,updated_at) "
                            "VALUES(?,?,?,'pending','','','','',NULL,1,?)",
                            (wave_id, repo_id, consumer_id, stamp),
                        )
                        seeded.append(consumer_id)
                    elif frozen_ref and repin["status"] == "verified" and repin["pinned_ref"] != frozen_ref:
                        # A new frozen contract ref invalidates an older verified re-pin.
                        conn.execute(
                            "UPDATE wave_repins SET status='pending',verified_at=NULL,revision=revision+1,updated_at=? "
                            "WHERE wave_id=? AND upstream_repo_id=? AND consumer_repo_id=?",
                            (stamp, wave_id, repo_id, consumer_id),
                        )
                        seeded.append(consumer_id)
        conn.execute("UPDATE waves SET revision=revision+1,updated_at=? WHERE id=?", (stamp, wave_id))
        event(
            conn,
            actor,
            "wave.freeze",
            wave_id,
            before,
            {"repositoryId": repo_id, "freezeStatus": freeze_status, "frozenRef": frozen_ref, "seededConsumers": seeded},
        )
        return self.detail(conn, wave_id)

    def report_repin(self, conn: sqlite3.Connection, actor: dict, wave_id: str, payload: dict) -> dict[str, Any]:
        row = _wave_row(conn, wave_id)
        if row["archived"] or row["status"] not in {"planning", "active"}:
            raise AppError(409, "conflict", "Re-pin reports require a planning or active wave")
        upstream_id = text(payload.get("upstreamRepoId"), "upstreamRepoId", required=True, limit=200)
        consumer_id = text(payload.get("consumerRepoId"), "consumerRepoId", required=True, limit=200)
        if upstream_id == consumer_id:
            raise AppError(422, "validation", "upstreamRepoId and consumerRepoId must differ")
        pinned_ref = text(payload.get("pinnedRef", ""), "pinnedRef", limit=500).strip()
        pr_raw = text(payload.get("prUrl", ""), "prUrl", limit=2000).strip()
        pr_url = ""
        if pr_raw:
            number, normalized_url, _ = pull_request(pr_raw)
            pr_url = normalized_url or number
        sha_raw = text(payload.get("commitSha", ""), "commitSha", limit=200).strip()
        sha = commit_sha(sha_raw) if sha_raw else ""
        if not pinned_ref and not pr_url and not sha:
            raise AppError(422, "validation", "Provide pinnedRef, prUrl, or commitSha")
        existing = _repin_row(conn, wave_id, upstream_id, consumer_id)
        stamp = now()
        if existing is None:
            # Allow reporting if both repos exist and upstream is registered in this wave.
            _active_repo(conn, upstream_id)
            _active_repo(conn, consumer_id)
            in_wave = conn.execute(
                "SELECT 1 FROM wave_repositories WHERE wave_id=? AND repository_id=?",
                (wave_id, upstream_id),
            ).fetchone()
            if in_wave is None:
                raise AppError(404, "not_found", "Re-pin record not found for this wave and repository pair")
            conn.execute(
                "INSERT INTO wave_repins(wave_id,upstream_repo_id,consumer_repo_id,status,"
                "pinned_ref,pr_url,commit_sha,reported_by_label,verified_at,revision,updated_at) "
                "VALUES(?,?,?,'in_progress',?,?,?,?,NULL,1,?)",
                (wave_id, upstream_id, consumer_id, pinned_ref, pr_url, sha, actor["label"], stamp),
            )
            before = None
        else:
            if existing["status"] in {"verified", "waived"}:
                raise AppError(409, "conflict", "This re-pin is already verified or waived")
            before = _repin_record(existing)
            conn.execute(
                "UPDATE wave_repins SET status='in_progress',pinned_ref=?,pr_url=?,commit_sha=?,"
                "reported_by_label=?,revision=revision+1,updated_at=? "
                "WHERE wave_id=? AND upstream_repo_id=? AND consumer_repo_id=?",
                (
                    pinned_ref or existing["pinned_ref"],
                    pr_url or existing["pr_url"],
                    sha or existing["commit_sha"],
                    actor["label"],
                    stamp,
                    wave_id,
                    upstream_id,
                    consumer_id,
                ),
            )
        after_row = _repin_row(conn, wave_id, upstream_id, consumer_id)
        assert after_row is not None
        after = _repin_record(after_row)
        event(conn, actor, "wave.repin.report", wave_id, before, after)
        return after

    def verify_repin(self, conn: sqlite3.Connection, actor: dict, wave_id: str, payload: dict) -> dict[str, Any]:
        if actor["kind"] != "human":
            raise AppError(403, "forbidden", "Only a person may verify or waive wave re-pins")
        row = _wave_row(conn, wave_id)
        if row["archived"]:
            raise AppError(409, "conflict", "Recover the wave before updating re-pins")
        upstream_id = text(payload.get("upstreamRepoId"), "upstreamRepoId", required=True, limit=200)
        consumer_id = text(payload.get("consumerRepoId"), "consumerRepoId", required=True, limit=200)
        existing = _repin_row(conn, wave_id, upstream_id, consumer_id)
        if existing is None:
            raise AppError(404, "not_found", "Re-pin record not found for this wave and repository pair")
        require_revision(existing, payload.get("revision"))
        status = payload.get("status")
        if status not in REPIN_STATUSES:
            raise AppError(422, "validation", "Invalid re-pin status")
        pinned_ref = existing["pinned_ref"] if payload.get("pinnedRef") is None else text(payload["pinnedRef"], "pinnedRef", limit=500).strip()
        pr_url = existing["pr_url"]
        if payload.get("prUrl") is not None:
            pr_raw = text(payload["prUrl"], "prUrl", limit=2000).strip()
            if pr_raw:
                number, normalized_url, _ = pull_request(pr_raw)
                pr_url = normalized_url or number
            else:
                pr_url = ""
        sha = existing["commit_sha"]
        if payload.get("commitSha") is not None:
            sha_raw = text(payload["commitSha"], "commitSha", limit=200).strip()
            sha = commit_sha(sha_raw) if sha_raw else ""
        before = _repin_record(existing)
        stamp = now()
        verified_at = stamp if status in {"verified", "waived"} else None
        conn.execute(
            "UPDATE wave_repins SET status=?,pinned_ref=?,pr_url=?,commit_sha=?,verified_at=?,"
            "revision=revision+1,updated_at=? WHERE wave_id=? AND upstream_repo_id=? AND consumer_repo_id=?",
            (status, pinned_ref, pr_url, sha, verified_at, stamp, wave_id, upstream_id, consumer_id),
        )
        after_row = _repin_row(conn, wave_id, upstream_id, consumer_id)
        assert after_row is not None
        after = _repin_record(after_row)
        event(conn, actor, "wave.repin.verify", wave_id, before, after)
        return after

    def set_archived(self, conn: sqlite3.Connection, actor: dict, wave_id: str, payload: dict, *, archived: bool) -> dict[str, Any]:
        if actor["kind"] != "human":
            raise AppError(403, "forbidden", "Only a person may archive or recover a wave")
        row = _wave_row(conn, wave_id)
        require_revision(row, payload.get("revision"))
        before = _wave_base(row)
        stamp = now()
        conn.execute(
            "UPDATE waves SET archived=?,revision=revision+1,updated_at=? WHERE id=?",
            (int(archived), stamp, wave_id),
        )
        after = _wave_base(_wave_row(conn, wave_id))
        event(conn, actor, "wave.archive" if archived else "wave.recover", wave_id, before, after)
        return self.detail(conn, wave_id)

    def apply(self, conn: sqlite3.Connection, actor: dict, action: str, payload: dict) -> dict[str, Any]:
        if action == "wave.create":
            return self.create(conn, actor, payload)
        if action == "wave.update":
            return self.update(conn, actor, payload["id"], payload)
        if action == "wave.steps":
            return self.set_steps(conn, actor, payload["id"], payload)
        raise AppError(422, "validation", "Unsupported wave action")
