"""Atomic bounded per-repository checkout leases and FIFO queue engine (v34).

Enforces configurable per-repository agent concurrency caps across Claude Code, Codex,
and Antigravity with zero AI-client hooks. SQLite BEGIN IMMEDIATE serializes slot
admission and FIFO promotion; all Git worktree operations execute strictly outside
database write transactions using a two-phase reserve -> provision -> activate protocol.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
import weakref
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import active_sessions, antigravity_sessions, slot_worktrees, worktree_claims
from .common import AppError, event, now, require_revision, text, uid
from .db import Database
from .discussions import communication_actor, identity_chat
from .repository_links import _repository_url, repository_identity
from .transcripts import TranscriptReader

logger = logging.getLogger("worklog")

DEFAULT_MAX_ACTIVE_LEASES = 2
DEFAULT_TTL_SECONDS = 1800
MIN_TTL_SECONDS = 300
MAX_TTL_SECONDS = 14400
OFFER_GRACE_SECONDS = 180
QUEUE_HEARTBEAT_STALE_SECONDS = 180
DEAD_SESSION_GRACE_SECONDS = 60
MAX_REPOS_PER_AGENT = 1
SWEEP_INTERVAL_SECONDS = 15
SNAPSHOT_FILE = "leases-snapshot.json"

PRIORITY_ORDER_SQL = "CASE priority WHEN 'incident' THEN 0 WHEN 'wave' THEN 1 WHEN 'pr_fixup' THEN 2 ELSE 3 END"
PRIORITY_RANK = {"incident": 0, "wave": 1, "pr_fixup": 2, "normal": 3}
HOLDING_STATES = ("offered", "active")
OCCUPYING_STATES = ("offered", "active", "expired_dirty")
OPEN_STATES = ("queued", "offered", "active")
CLOSED_STATES = ("released", "expired", "revoked", "cancelled")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _iso_plus(stamp: str, seconds: int) -> str:
    base = _parse_iso(stamp) or datetime.now(UTC)
    return (base + timedelta(seconds=seconds)).isoformat()


def _get_repo(c: sqlite3.Connection, repo_id: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM repositories WHERE id=?", (repo_id,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Repository not found")
    return row


def _resolve_repo_for_lease(
    c: sqlite3.Connection,
    repository_id: str | None,
    repository_url: str | None,
) -> sqlite3.Row:
    if repository_id and repository_url:
        raise AppError(422, "validation", "Provide repositoryId or repositoryUrl, not both")
    if not repository_id and not repository_url:
        raise AppError(422, "validation", "Provide repositoryId or repositoryUrl", {"repositoryId": "required"})
    if repository_id:
        row = _get_repo(c, text(repository_id, "repositoryId", required=True, limit=200))
    else:
        url = _repository_url(repository_url)
        key = repository_identity(url)
        row = c.execute(
            "SELECT * FROM repositories WHERE remote_identity=? ORDER BY merged_into IS NOT NULL,"
            "archived,duplicate_of IS NOT NULL,created_at,id LIMIT 1",
            (key,),
        ).fetchone()
        if row is None:
            raise AppError(404, "not_found", "No repository is registered for that remote", {"repositoryUrl": "not_found"})
        if row["merged_into"]:
            row = _get_repo(c, row["merged_into"])
    if row["merged_into"]:
        survivor = _get_repo(c, row["merged_into"])
        raise AppError(
            409,
            "repository_merged",
            f"{row['name']} was merged into {survivor['name']}; use that repository instead",
            {"repositoryId": survivor["id"]},
        )
    if row["archived"]:
        raise AppError(
            409,
            "repository_archived",
            f"{row['name']} is archived; recover it before acquiring a checkout lease",
            {"repositoryId": row["id"]},
        )
    return row


def policy_record(c: sqlite3.Connection, repo_id: str) -> dict[str, Any]:
    """Return a repository's configured slot policy, or its effective defaults if unconfigured."""
    row = c.execute("SELECT * FROM repository_slot_policies WHERE repository_id=?", (repo_id,)).fetchone()
    if row is None:
        return {
            "repositoryId": repo_id,
            "maxActiveLeases": DEFAULT_MAX_ACTIVE_LEASES,
            "defaultTtlSeconds": DEFAULT_TTL_SECONDS,
            "baseCheckoutPath": "",
            "slotRootPath": "",
            "defaultBranch": "main",
            "baseReadonly": False,
            "autoRescueDirty": True,
            "revision": 0,
            "updatedAt": None,
            "configured": False,
        }
    return {
        "repositoryId": row["repository_id"],
        "maxActiveLeases": int(row["max_active_leases"]),
        "defaultTtlSeconds": int(row["default_ttl_seconds"]),
        "baseCheckoutPath": row["base_checkout_path"],
        "slotRootPath": row["slot_root_path"],
        "defaultBranch": row["default_branch"],
        "baseReadonly": bool(row["base_readonly"]),
        "autoRescueDirty": bool(row["auto_rescue_dirty"]),
        "revision": int(row["revision"]),
        "updatedAt": row["updated_at"],
        "configured": True,
    }


def _queue_positions(c: sqlite3.Connection, repo_id: str) -> dict[str, int]:
    rows = c.execute(
        f"SELECT id FROM repository_leases WHERE repository_id=? AND state='queued' "
        f"ORDER BY {PRIORITY_ORDER_SQL}, queue_seq ASC",
        (repo_id,),
    ).fetchall()
    return {row["id"]: idx for idx, row in enumerate(rows, 1)}


def _repo_name_map(c: sqlite3.Connection, repo_ids: Iterable[str]) -> dict[str, str]:
    ids = list(dict.fromkeys(r for r in repo_ids if r))
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    return {r["id"]: r["name"] for r in c.execute(f"SELECT id,name FROM repositories WHERE id IN ({marks})", ids)}


def _worktree_fields(row: sqlite3.Row) -> dict[str, str]:
    if row["caller_worktree_path"]:
        return {"worktreePath": row["caller_worktree_path"], "worktreeSource": "caller"}
    if row["worktree_path"]:
        return {"worktreePath": row["worktree_path"], "worktreeSource": "managed"}
    return {"worktreePath": "", "worktreeSource": "unbound"}


def lease_record(
    c: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    repo_name: str | None = None,
    queue_position: int | None = None,
    fetch_warning: str = "",
) -> dict[str, Any]:
    if repo_name is None:
        found = c.execute("SELECT name FROM repositories WHERE id=?", (row["repository_id"],)).fetchone()
        repo_name = found["name"] if found else row["repository_id"]
    if queue_position is None and row["state"] == "queued":
        queue_position = _queue_positions(c, row["repository_id"]).get(row["id"])
    return {
        "id": row["id"],
        "repositoryId": row["repository_id"],
        "repositoryName": repo_name,
        "agentId": row["agent_id"],
        "credentialId": row["credential_id"],
        "runId": row["run_id"],
        "provider": row["provider"],
        "chatId": row["chat_id"],
        "holderLabel": row["holder_label"],
        "slotIndex": row["slot_index"],
        **_worktree_fields(row),
        "branch": row["branch"],
        "mode": row["mode"],
        "state": row["state"],
        "priority": row["priority"],
        "queueSeq": int(row["queue_seq"]),
        "queuePosition": queue_position if row["state"] == "queued" else None,
        "reason": row["reason"],
        "requestedAt": row["requested_at"],
        "offeredAt": row["offered_at"],
        "acquiredAt": row["acquired_at"],
        "lastHeartbeatAt": row["last_heartbeat_at"],
        "expiresAt": row["expires_at"],
        "releasedAt": row["released_at"],
        "releaseReason": row["release_reason"],
        "rescueRef": row["rescue_ref"],
        "revision": int(row["revision"]),
        "fetchWarning": fetch_warning,
    }


def repository_slot_summaries(c: sqlite3.Connection, repo_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Compact slot summary per repository for lists, overlaps, and Live cards."""
    ids = list(dict.fromkeys(r for r in repo_ids if r))
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    policies = {
        r["repository_id"]: r
        for r in c.execute(f"SELECT * FROM repository_slot_policies WHERE repository_id IN ({marks})", ids)
    }
    counts: dict[str, dict[str, int]] = {rid: {"active": 0, "queued": 0, "dirty": 0, "rescued": 0} for rid in ids}
    holders: dict[str, list[dict[str, Any]]] = {rid: [] for rid in ids}
    for r in c.execute(
        f"SELECT * FROM repository_leases WHERE repository_id IN ({marks}) "
        f"AND state IN ('queued','offered','active','expired_dirty') ORDER BY slot_index, queue_seq",
        ids,
    ):
        rid = r["repository_id"]
        if r["state"] in HOLDING_STATES:
            counts[rid]["active"] += 1
            holders[rid].append({
                "leaseId": r["id"],
                "agentId": r["agent_id"],
                "holderLabel": r["holder_label"],
                "provider": r["provider"],
                "slotIndex": r["slot_index"],
                "branch": r["branch"],
                **_worktree_fields(r),
                "mode": r["mode"],
                "state": r["state"],
                "expiresAt": r["expires_at"],
            })
        elif r["state"] == "queued":
            counts[rid]["queued"] += 1
        elif r["state"] == "expired_dirty":
            counts[rid]["dirty"] += 1
    for r in c.execute(
        f"SELECT repository_id, COUNT(*) AS n FROM repository_leases "
        f"WHERE repository_id IN ({marks}) AND rescue_ref<>'' GROUP BY repository_id",
        ids,
    ):
        counts[r["repository_id"]]["rescued"] = int(r["n"])
    result: dict[str, dict[str, Any]] = {}
    for rid in ids:
        pol = policies.get(rid)
        max_slots = int(pol["max_active_leases"]) if pol is not None else DEFAULT_MAX_ACTIVE_LEASES
        result[rid] = {
            "maxActiveLeases": max_slots,
            "activeCount": counts[rid]["active"],
            "queuedCount": counts[rid]["queued"],
            "dirtyCount": counts[rid]["dirty"],
            "rescuedCount": counts[rid]["rescued"],
            "baseReadonly": bool(pol["base_readonly"]) if pol is not None else False,
            "configured": pol is not None,
            "revision": int(pol["revision"]) if pol is not None else 0,
            "activeLeases": holders[rid],
        }
    return result


def repository_slots_view(c: sqlite3.Connection, repo_id: str) -> dict[str, Any]:
    """Full slot status for GET /api/v1/repositories/{identity}/slots."""
    repo = _get_repo(c, repo_id)
    pol = policy_record(c, repo_id)
    open_rows = c.execute(
        "SELECT * FROM repository_leases WHERE repository_id=? AND state IN ('offered','active','expired_dirty') "
        "ORDER BY slot_index ASC, queue_seq ASC",
        (repo_id,),
    ).fetchall()
    queued_rows = c.execute(
        f"SELECT * FROM repository_leases WHERE repository_id=? AND state='queued' "
        f"ORDER BY {PRIORITY_ORDER_SQL}, queue_seq ASC",
        (repo_id,),
    ).fetchall()
    recent_rows = c.execute(
        "SELECT * FROM repository_leases WHERE repository_id=? AND state IN ('released','expired','revoked','cancelled') "
        "ORDER BY COALESCE(released_at,requested_at) DESC, id DESC LIMIT 10",
        (repo_id,),
    ).fetchall()

    by_slot: dict[int, dict[str, Any]] = {}
    active_leases: list[dict[str, Any]] = []
    dirty_count = 0
    has_exclusive = False
    for row in open_rows:
        rec = lease_record(c, row, repo_name=repo["name"])
        if row["slot_index"] is not None:
            by_slot[int(row["slot_index"])] = rec
        if row["state"] in HOLDING_STATES:
            active_leases.append(rec)
            if row["mode"] == "exclusive":
                has_exclusive = True
        elif row["state"] == "expired_dirty":
            dirty_count += 1
    queued_leases = [
        lease_record(c, row, repo_name=repo["name"], queue_position=pos)
        for pos, row in enumerate(queued_rows, 1)
    ]
    recent_leases = [lease_record(c, row, repo_name=repo["name"]) for row in recent_rows]

    max_slot_idx = max([pol["maxActiveLeases"], *by_slot.keys()], default=0)
    slots: list[dict[str, Any]] = []
    for idx in range(1, max_slot_idx + 1):
        held = by_slot.get(idx)
        expected_path = ""
        if pol["baseCheckoutPath"]:
            try:
                expected_path = str(
                    slot_worktrees.resolve_slot_path(pol["baseCheckoutPath"], pol["slotRootPath"], repo["name"], idx)
                )
            except AppError:
                expected_path = ""
        slots.append({
            "slotIndex": idx,
            "expectedWorktreePath": expected_path,
            "state": held["state"] if held else "free",
            "lease": held,
        })
    available = 0 if (has_exclusive or pol["maxActiveLeases"] <= 0) else max(
        0, pol["maxActiveLeases"] - len(active_leases) - dirty_count
    )
    return {
        "repositoryId": repo["id"],
        "repositoryName": repo["name"],
        "policy": pol,
        "activeCount": len(active_leases),
        "queuedCount": len(queued_leases),
        "dirtyCount": dirty_count,
        "availableSlots": available,
        "slots": slots,
        "activeLeases": active_leases,
        "queuedLeases": queued_leases,
        "recentLeases": recent_leases,
    }


def _promote_next_in_tx(c: sqlite3.Connection, repo_id: str, stamp: str | None = None) -> bool:
    """Promote eligible FIFO waiters into free slots inside an existing transaction.

    Returns True if at least one dispatch_queue-backed waiter was promoted.
    """
    stamp = stamp or now()
    pol = policy_record(c, repo_id)
    max_slots = pol["maxActiveLeases"]
    if max_slots <= 0:
        return False

    holding = c.execute(
        "SELECT id,slot_index,mode,branch,agent_id FROM repository_leases "
        "WHERE repository_id=? AND state IN ('offered','active')",
        (repo_id,),
    ).fetchall()
    dirty = c.execute(
        "SELECT slot_index FROM repository_leases WHERE repository_id=? AND state='expired_dirty'",
        (repo_id,),
    ).fetchall()
    if any(r["mode"] == "exclusive" for r in holding):
        return False

    occupied_slots = {int(r["slot_index"]) for r in [*holding, *dirty] if r["slot_index"] is not None}
    held_branches = {r["branch"] for r in holding if r["branch"]}
    active_count = len(holding)
    queued = c.execute(
        f"SELECT * FROM repository_leases WHERE repository_id=? AND state='queued' "
        f"ORDER BY {PRIORITY_ORDER_SQL}, queue_seq ASC",
        (repo_id,),
    ).fetchall()

    promoted_dispatch = False
    for candidate in queued:
        free_slots = [i for i in range(1, max_slots + 1) if i not in occupied_slots]
        if not free_slots or active_count >= max_slots:
            break
        if candidate["mode"] == "exclusive":
            if active_count > 0:
                # Do not skip ahead of an exclusive waiter so it cannot starve.
                break
        if candidate["branch"] and candidate["branch"] in held_branches:
            continue
        if candidate["agent_id"]:
            other = c.execute(
                "SELECT 1 FROM repository_leases WHERE agent_id=? AND repository_id<>? AND state IN ('offered','active')",
                (candidate["agent_id"], repo_id),
            ).fetchone()
            if other is not None:
                continue

        slot = free_slots[0]
        c.execute(
            "UPDATE repository_leases SET state='offered',slot_index=?,offered_at=?,last_heartbeat_at=?,"
            "revision=revision+1 WHERE id=? AND state='queued'",
            (slot, stamp, stamp, candidate["id"]),
        )
        occupied_slots.add(slot)
        active_count += 1
        if candidate["branch"]:
            held_branches.add(candidate["branch"])
        if c.execute("SELECT 1 FROM dispatch_queue WHERE lease_id=? AND status='queued'", (candidate["id"],)).fetchone():
            promoted_dispatch = True
        if candidate["mode"] == "exclusive":
            break
    return promoted_dispatch


def _cancel_or_release_in_tx(
    c: sqlite3.Connection,
    actor: dict[str, Any],
    rows: list[sqlite3.Row],
    reason: str,
) -> set[str]:
    """Release logical leases immediately inside a transaction; defer managed worktree leases for immediate post-commit rescue."""
    stamp = now()
    touched_repos: set[str] = set()
    for row in rows:
        repo_id = row["repository_id"]
        if row["state"] == "queued":
            c.execute(
                "UPDATE repository_leases SET state='cancelled',released_at=?,release_reason=?,revision=revision+1 WHERE id=?",
                (stamp, reason, row["id"]),
            )
            c.execute(
                "UPDATE dispatch_queue SET status='cancelled',message=?,updated_at=?,revision=revision+1 "
                "WHERE lease_id=? AND status='queued'",
                (f"Cancelled ({reason})", stamp, row["id"]),
            )
            event(c, actor, "lease.cancel", row["id"], {"state": "queued"}, {"state": "cancelled", "reason": reason})
            touched_repos.add(repo_id)
        elif row["state"] in HOLDING_STATES:
            if not row["worktree_path"]:
                c.execute(
                    "UPDATE repository_leases SET state='released',released_at=?,release_reason=?,revision=revision+1 WHERE id=?",
                    (stamp, reason, row["id"]),
                )
                event(c, actor, "lease.release", row["id"], {"state": row["state"]}, {"state": "released", "reason": reason})
                touched_repos.add(repo_id)
            else:
                # Managed slot worktree: mark immediately overdue with release_reason so post-commit
                # cleanup (or the 15s reaper) runs Git auto-rescue outside the DB write transaction.
                c.execute(
                    "UPDATE repository_leases SET expires_at=?,release_reason=?,revision=revision+1 WHERE id=?",
                    (stamp, reason, row["id"]),
                )
                touched_repos.add(repo_id)
    for repo_id in sorted(touched_repos):
        _promote_next_in_tx(c, repo_id, stamp)
    return touched_repos


def release_run_leases(c: sqlite3.Connection, actor: dict[str, Any], run_id: str, reason: str = "run_ended") -> int:
    rows = c.execute(
        "SELECT * FROM repository_leases WHERE run_id=? AND state IN ('queued','offered','active')",
        (run_id,),
    ).fetchall()
    if not rows:
        return 0
    _cancel_or_release_in_tx(c, actor, rows, reason)
    return len(rows)


def release_credential_leases(
    c: sqlite3.Connection,
    actor: dict[str, Any],
    credential_id: str,
    reason: str = "credential_revoked",
) -> int:
    rows = c.execute(
        "SELECT * FROM repository_leases WHERE credential_id=? AND state IN ('queued','offered','active')",
        (credential_id,),
    ).fetchall()
    if not rows:
        return 0
    _cancel_or_release_in_tx(c, actor, rows, reason)
    return len(rows)


def release_repository_leases(
    c: sqlite3.Connection,
    actor: dict[str, Any],
    repo_id: str,
    reason: str = "repository_archived",
) -> int:
    rows = c.execute(
        "SELECT * FROM repository_leases WHERE repository_id=? AND state IN ('queued','offered','active')",
        (repo_id,),
    ).fetchall()
    if not rows:
        return 0
    _cancel_or_release_in_tx(c, actor, rows, reason)
    return len(rows)


def transfer_continued_leases(
    c: sqlite3.Connection,
    agent_id: str,
    old_credential_id: str,
    new_credential_id: str,
    new_chat_id: str,
) -> int:
    """Move open leases from a continuing agent's old credential/chat to its new one.

    Clears run_id because the old credential's runs are ended immediately during continuation.
    """
    stamp = now()
    cur = c.execute(
        "UPDATE repository_leases SET credential_id=?,chat_id=?,run_id=NULL,last_heartbeat_at=?,"
        "revision=revision+1 WHERE agent_id=? AND credential_id=? AND state IN ('queued','offered','active')",
        (new_credential_id, new_chat_id, stamp, agent_id, old_credential_id),
    )
    return int(cur.rowcount)


def merge_repository_leases(c: sqlite3.Connection, source_id: str, survivor_id: str) -> int:
    """Move slot policy, leases, and dispatch queue rows from source repository into survivor."""
    stamp = now()
    # Copy policy only if survivor has none.
    source_pol = c.execute("SELECT * FROM repository_slot_policies WHERE repository_id=?", (source_id,)).fetchone()
    survivor_pol = c.execute("SELECT 1 FROM repository_slot_policies WHERE repository_id=?", (survivor_id,)).fetchone()
    if source_pol is not None:
        if survivor_pol is None:
            c.execute(
                "INSERT INTO repository_slot_policies(repository_id,max_active_leases,default_ttl_seconds,"
                "base_checkout_path,slot_root_path,default_branch,base_readonly,auto_rescue_dirty,revision,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    survivor_id,
                    source_pol["max_active_leases"],
                    source_pol["default_ttl_seconds"],
                    source_pol["base_checkout_path"],
                    source_pol["slot_root_path"],
                    source_pol["default_branch"],
                    source_pol["base_readonly"],
                    source_pol["auto_rescue_dirty"],
                    1,
                    stamp,
                ),
            )
        c.execute("DELETE FROM repository_slot_policies WHERE repository_id=?", (source_id,))

    # Cancel any queued/offered source leases whose slot/branch/worktree would collide on survivor,
    # or renumber open source leases safely.
    next_seq = int(
        c.execute("SELECT COALESCE(MAX(queue_seq),0) FROM repository_leases WHERE repository_id=?", (survivor_id,)).fetchone()[0]
    )
    used_slots = {
        int(r["slot_index"])
        for r in c.execute(
            "SELECT slot_index FROM repository_leases WHERE repository_id=? AND state IN ('offered','active','expired_dirty') AND slot_index IS NOT NULL",
            (survivor_id,),
        )
    }
    used_branches = {
        r["branch"]
        for r in c.execute(
            "SELECT branch FROM repository_leases WHERE repository_id=? AND state IN ('offered','active') AND branch<>''",
            (survivor_id,),
        )
    }
    used_worktrees = {
        r["worktree_path"]
        for r in c.execute(
            "SELECT worktree_path FROM repository_leases WHERE repository_id=? AND state IN ('offered','active','expired_dirty') AND worktree_path<>''",
            (survivor_id,),
        )
    }
    source_rows = c.execute(
        "SELECT * FROM repository_leases WHERE repository_id=? ORDER BY queue_seq ASC, id ASC",
        (source_id,),
    ).fetchall()
    moved = 0
    for row in source_rows:
        next_seq += 1
        slot_index = row["slot_index"]
        state = row["state"]
        branch = row["branch"]
        wt = row["worktree_path"]
        if state in OCCUPYING_STATES:
            if (branch and branch in used_branches) or (wt and wt in used_worktrees):
                state = "released"
                slot_index = None
            else:
                if slot_index is not None and slot_index in used_slots:
                    candidate = 1
                    while candidate in used_slots:
                        candidate += 1
                    slot_index = candidate
                if slot_index is not None:
                    used_slots.add(slot_index)
                if branch:
                    used_branches.add(branch)
                if wt:
                    used_worktrees.add(wt)
        c.execute(
            "UPDATE repository_leases SET repository_id=?,queue_seq=?,slot_index=?,state=?,"
            "released_at=CASE WHEN ?='released' AND released_at IS NULL THEN ? ELSE released_at END,"
            "release_reason=CASE WHEN ?='released' AND release_reason IS NULL THEN 'repository_merged' ELSE release_reason END "
            "WHERE id=?",
            (survivor_id, next_seq, slot_index, state, state, stamp, state, row["id"]),
        )
        moved += 1
    c.execute("UPDATE dispatch_queue SET repository_id=?,updated_at=? WHERE repository_id=?", (survivor_id, stamp, source_id))
    return moved


def touch_agent_leases(c: sqlite3.Connection, agent_id: str, repository_ids: Iterable[str]) -> int:
    """Implicitly refresh heartbeat and extend TTL on active leases matching agent + repos."""
    repos = list(dict.fromkeys(r for r in repository_ids if r))
    if not agent_id or not repos:
        return 0
    stamp = now()
    marks = ",".join("?" for _ in repos)
    rows = c.execute(
        f"SELECT id,repository_id,expires_at FROM repository_leases "
        f"WHERE agent_id=? AND state='active' AND repository_id IN ({marks})",
        [agent_id, *repos],
    ).fetchall()
    touched = 0
    for row in rows:
        pol = policy_record(c, row["repository_id"])
        new_expiry = _iso_plus(stamp, pol["defaultTtlSeconds"])
        if row["expires_at"] and row["expires_at"] > new_expiry:
            new_expiry = row["expires_at"]
        c.execute(
            "UPDATE repository_leases SET last_heartbeat_at=?,expires_at=? WHERE id=?",
            (stamp, new_expiry, row["id"]),
        )
        touched += 1
    return touched


class LeaseService:
    """Manages repository slot policies, leases, FIFO promotion, background reaping, and local snapshots."""

    def __init__(
        self,
        db: Database,
        transcripts: TranscriptReader,
        *,
        antigravity_brain_root: Path | None = None,
        on_promote: Callable[[], None] | None = None,
    ) -> None:
        self.db = db
        self.transcripts = transcripts
        self.antigravity_brain_root = antigravity_brain_root
        self.on_promote = on_promote
        self._sweep_lock = threading.Lock()
        self._snapshot_lock = threading.Lock()
        self._provision_locks: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
        self._provision_guard = threading.Lock()

    def snapshot_path(self) -> Path:
        return self.db.path.parent / SNAPSHOT_FILE

    def write_snapshot(self) -> None:
        """Atomically write WorklogData/leases-snapshot.json (best-effort derived file)."""
        try:
            target = self.snapshot_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            with self._snapshot_lock:
                with self.db.read() as c:
                    repos = c.execute(
                        "SELECT id,name FROM repositories WHERE archived=0 AND merged_into IS NULL ORDER BY name COLLATE NOCASE"
                    ).fetchall()
                    ids = [r["id"] for r in repos]
                    summaries = repository_slot_summaries(c, ids)
                    policies = {rid: policy_record(c, rid) for rid in ids}
                    payload = {
                        "generatedAt": now(),
                        "repositories": {
                            r["id"]: {
                                "name": r["name"],
                                "maxActiveLeases": policies[r["id"]]["maxActiveLeases"],
                                "baseCheckoutPath": policies[r["id"]]["baseCheckoutPath"],
                                "slotRootPath": policies[r["id"]]["slotRootPath"],
                                "baseReadonly": policies[r["id"]]["baseReadonly"],
                                "activeCount": summaries[r["id"]]["activeCount"],
                                "queuedCount": summaries[r["id"]]["queuedCount"],
                                "dirtyCount": summaries[r["id"]]["dirtyCount"],
                                "activeLeases": summaries[r["id"]]["activeLeases"],
                            }
                            for r in repos
                            if summaries[r["id"]]["configured"]
                            or summaries[r["id"]]["activeCount"] > 0
                            or summaries[r["id"]]["queuedCount"] > 0
                        },
                    }
                tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
                try:
                    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                    os.replace(tmp, target)
                finally:
                    tmp.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to write %s", SNAPSHOT_FILE, exc_info=True)

    def _notify_promoted(self) -> None:
        if self.on_promote is not None:
            try:
                self.on_promote()
            except Exception:
                logger.warning("Promotion callback failed", exc_info=True)

    # ---- Policy & Base Lock ----

    def get_policy(self, repo_id: str) -> dict[str, Any]:
        with self.db.read() as c:
            _get_repo(c, repo_id)
            return policy_record(c, repo_id)

    def get_slots(self, repo_id: str) -> dict[str, Any]:
        with self.db.read() as c:
            return repository_slots_view(c, repo_id)

    def update_policy(self, actor: dict[str, Any], repo_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if actor.get("kind") != "human":
            raise AppError(403, "agent_scope", "Only the person changes repository slot policies")
        with self.db.read() as c:
            repo = _get_repo(c, repo_id)
            if repo["merged_into"]:
                raise AppError(409, "repository_merged", "Cannot edit slot policy on a merged repository", {"repositoryId": repo["merged_into"]})
            current = policy_record(c, repo_id)

        base_path = payload.get("baseCheckoutPath", current["baseCheckoutPath"])
        slot_root = payload.get("slotRootPath", current["slotRootPath"])
        default_branch = payload.get("defaultBranch", current["defaultBranch"])
        if isinstance(base_path, str):
            base_path = base_path.strip()
        if isinstance(slot_root, str):
            slot_root = slot_root.strip()
        if isinstance(default_branch, str):
            default_branch = default_branch.strip()
            if not default_branch or len(default_branch) > 200 or any(ch.isspace() for ch in default_branch):
                raise AppError(422, "validation", "Give a valid default branch name", {"defaultBranch": "invalid"})

        # Validate a path only when it changes, outside the SQLite write transaction. A stored path was
        # validated when it was saved, so the slot count, TTL or branch still change while a moved base
        # checkout waits to be fixed; a lease that provisions from it is refused meanwhile.
        base_changed = base_path != current["baseCheckoutPath"]
        if base_path and base_changed:
            base_path = str(slot_worktrees.validate_base_checkout(base_path, repo["remote_identity"]))
        if slot_root and (base_changed or slot_root != current["slotRootPath"]):
            if base_path:
                slot_worktrees.resolve_slot_path(base_path, slot_root, repo["name"], 1)
            slot_root = str(slot_worktrees.validate_local_path(slot_root, "slotRootPath"))

        promoted_dispatch = False
        with self.db.transaction() as c:
            _get_repo(c, repo_id)
            existing = c.execute("SELECT * FROM repository_slot_policies WHERE repository_id=?", (repo_id,)).fetchone()
            if payload.get("revision") is not None:
                expected_row = {"revision": existing["revision"] if existing else 0}
                require_revision(expected_row, payload["revision"])
            before = policy_record(c, repo_id)
            max_active = int(payload["maxActiveLeases"]) if payload.get("maxActiveLeases") is not None else before["maxActiveLeases"]
            ttl = int(payload["defaultTtlSeconds"]) if payload.get("defaultTtlSeconds") is not None else before["defaultTtlSeconds"]
            auto_rescue = bool(payload["autoRescueDirty"]) if payload.get("autoRescueDirty") is not None else before["autoRescueDirty"]
            base_readonly = bool(before["baseReadonly"]) if base_path == before["baseCheckoutPath"] else (
                slot_worktrees.is_base_locked_on_disk(base_path) if base_path else False
            )
            stamp = now()
            if existing is None:
                c.execute(
                    "INSERT INTO repository_slot_policies(repository_id,max_active_leases,default_ttl_seconds,"
                    "base_checkout_path,slot_root_path,default_branch,base_readonly,auto_rescue_dirty,revision,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,1,?)",
                    (repo_id, max_active, ttl, base_path, slot_root, default_branch, int(base_readonly), int(auto_rescue), stamp),
                )
            else:
                c.execute(
                    "UPDATE repository_slot_policies SET max_active_leases=?,default_ttl_seconds=?,"
                    "base_checkout_path=?,slot_root_path=?,default_branch=?,base_readonly=?,auto_rescue_dirty=?,"
                    "revision=revision+1,updated_at=? WHERE repository_id=?",
                    (max_active, ttl, base_path, slot_root, default_branch, int(base_readonly), int(auto_rescue), stamp, repo_id),
                )
            after = policy_record(c, repo_id)
            event(c, actor, "repository.slot_policy", repo_id, before, after)
            if after["maxActiveLeases"] > before["maxActiveLeases"]:
                promoted_dispatch = _promote_next_in_tx(c, repo_id, stamp)
        self.write_snapshot()
        if promoted_dispatch:
            self._notify_promoted()
        return after

    def set_base_lock(self, actor: dict[str, Any], repo_id: str, locked: bool) -> dict[str, Any]:
        if actor.get("kind") != "human":
            raise AppError(403, "agent_scope", "Only the person locks or unlocks a repository base checkout")
        with self.db.read() as c:
            repo = _get_repo(c, repo_id)
            pol = policy_record(c, repo_id)
        if not pol["baseCheckoutPath"]:
            raise AppError(
                422,
                "validation",
                "Configure baseCheckoutPath on this repository's slot policy before locking it",
                {"baseCheckoutPath": "required"},
            )
        if locked:
            detail = slot_worktrees.lock_base_readonly(pol["baseCheckoutPath"], pol["defaultBranch"], repo["name"])
        else:
            detail = slot_worktrees.unlock_base_readonly(pol["baseCheckoutPath"])

        with self.db.transaction() as c:
            existing = c.execute("SELECT * FROM repository_slot_policies WHERE repository_id=?", (repo_id,)).fetchone()
            stamp = now()
            if existing is None:
                c.execute(
                    "INSERT INTO repository_slot_policies(repository_id,max_active_leases,default_ttl_seconds,"
                    "base_checkout_path,slot_root_path,default_branch,base_readonly,auto_rescue_dirty,revision,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,1,?)",
                    (repo_id, pol["maxActiveLeases"], pol["defaultTtlSeconds"], pol["baseCheckoutPath"],
                     pol["slotRootPath"], pol["defaultBranch"], int(locked), int(pol["autoRescueDirty"]), stamp),
                )
            else:
                c.execute(
                    "UPDATE repository_slot_policies SET base_readonly=?,revision=revision+1,updated_at=? WHERE repository_id=?",
                    (int(locked), stamp, repo_id),
                )
            after = policy_record(c, repo_id)
            event(c, actor, "repository.base_lock", repo_id, {"baseReadonly": pol["baseReadonly"]}, {"baseReadonly": locked})
        self.write_snapshot()
        return {
            "repositoryId": repo_id,
            "basePath": detail["basePath"],
            "baseReadonly": locked,
            "filesAffected": int(detail.get("filesLocked", detail.get("filesUnlocked", 0))),
            "detachedAt": str(detail.get("detachedAt", "")),
            "policy": after,
        }

    # ---- Reads ----

    def list_leases(
        self,
        *,
        repository_id: str | None = None,
        agent_id: str | None = None,
        state: str | None = None,
        include_closed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        with self.db.read() as c:
            where = ["1=1"]
            args: list[Any] = []
            if repository_id:
                where.append("repository_id=?")
                args.append(repository_id)
            if agent_id:
                where.append("agent_id=?")
                args.append(agent_id)
            if state:
                where.append("state=?")
                args.append(state)
            elif not include_closed:
                where.append("state IN ('queued','offered','active','expired_dirty')")
            sql = "SELECT * FROM repository_leases WHERE " + " AND ".join(where) + " ORDER BY requested_at DESC, id DESC"
            total = int(c.execute("SELECT COUNT(*) FROM (" + sql + ")", args).fetchone()[0])
            rows = c.execute(sql + " LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
            names = _repo_name_map(c, [r["repository_id"] for r in rows])
            positions: dict[str, dict[str, int]] = {}
            items = []
            for r in rows:
                pos = None
                if r["state"] == "queued":
                    rid = r["repository_id"]
                    if rid not in positions:
                        positions[rid] = _queue_positions(c, rid)
                    pos = positions[rid].get(r["id"])
                items.append(lease_record(c, r, repo_name=names.get(r["repository_id"]), queue_position=pos))
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_lease(self, lease_id: str) -> dict[str, Any]:
        with self.db.read() as c:
            row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Lease not found")
            return lease_record(c, row)

    # ---- Acquire / Poll / Renew / Release / Rescue ----

    def _provision_and_activate(
        self, lease_id: str, repo_id: str, repo_name: str, slot_index: int, requested_branch: str,
        ttl_seconds: int, pol: dict[str, Any], actor: dict[str, Any], *, rollback_on_failure: bool = True,
    ) -> dict[str, Any]:
        # A slow or cancelled offer may still be running Git. Serialize physical slots across
        # offers while allowing unrelated directories to provision independently.
        target = None
        key = lease_id
        lock = None
        try:
            if pol["baseCheckoutPath"]:
                target = slot_worktrees.resolve_slot_path(pol["baseCheckoutPath"], pol["slotRootPath"], repo_name, slot_index)
                key = slot_worktrees.worktree_claim_key(target)
            with self._provision_guard:
                lock = self._provision_locks.setdefault(key, threading.Lock())
            with lock:
                return self._activate_offer(
                    lease_id, repo_name, slot_index, requested_branch, ttl_seconds, pol, actor, target, key,
                )
        except AppError:
            if rollback_on_failure and pol["baseCheckoutPath"]:
                # A retry may have taken the same reservation after a Git failure. Let that
                # attempt finish before cancelling an offer that is still outstanding.
                with (lock or nullcontext()), self.db.transaction() as c:
                    stamp = now()
                    c.execute(
                        "UPDATE repository_leases SET state='cancelled',released_at=?,release_reason='provision_failed',"
                        "revision=revision+1 WHERE id=? AND state='offered' "
                        "AND (worktree_claim_key IS NULL OR worktree_claim_key=?)", (stamp, lease_id, key),
                    )
                    promoted_dispatch = _promote_next_in_tx(c, repo_id, stamp)
                self.write_snapshot()
                if promoted_dispatch:
                    self._notify_promoted()
            raise

    def _is_provisioning(self, key: str | None) -> bool:
        with self._provision_guard:
            lock = self._provision_locks.get(key) if key else None
            return lock is not None and lock.locked()

    def _activate_offer(
        self,
        lease_id: str,
        repo_name: str,
        slot_index: int,
        requested_branch: str,
        ttl_seconds: int,
        pol: dict[str, Any],
        actor: dict[str, Any],
        target: Path | None,
        key: str,
    ) -> dict[str, Any]:
        """Run Git slot provisioning outside SQLite lock, then finalize lease to active."""
        fetch_warning = ""
        worktree_path = ""
        branch = requested_branch
        if target is not None:
            with self.db.transaction() as c:
                offer = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
                if offer is not None and offer["state"] == "active":
                    return lease_record(c, offer, repo_name=repo_name)
                if offer is None or offer["state"] != "offered":
                    raise AppError(409, "conflict", "Lease offer is no longer available")
                if offer["worktree_claim_key"] not in (None, key):
                    raise AppError(409, "conflict", "The managed slot path changed during provisioning")
                if worktree_claims.reserve(c, lease_id, target) != key:
                    raise AppError(409, "conflict", "The managed slot path changed during provisioning")
                prior = worktree_claims.last_holder(c, key, lease_id)
                if prior is not None and prior["caller_worktree_path"] and target.exists():
                    raise AppError(409, "caller_worktree", "The caller must archive its worktree before slot reuse")
            prov = slot_worktrees.provision_slot(
                pol["baseCheckoutPath"], pol["slotRootPath"], repo_name, slot_index,
                pol["defaultBranch"], requested_branch, lease_id,
            )
            worktree_path, branch, fetch_warning = prov.worktree_path, prov.branch, prov.fetch_warning
            if slot_worktrees.worktree_claim_key(worktree_path) != key:
                raise AppError(409, "conflict", "The provisioned path no longer matches the reserved slot")

        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if row is not None and row["state"] == "active":
                return lease_record(c, row, repo_name=repo_name)
            if row is None or row["state"] != "offered":
                raise AppError(409, "conflict", "Lease offer is no longer available")
            stamp = now()
            expires_at = _iso_plus(stamp, ttl_seconds)
            try:
                c.execute(
                    "UPDATE repository_leases SET state='active',worktree_path=?,branch=?,acquired_at=?,"
                    "last_heartbeat_at=?,expires_at=?,revision=revision+1 WHERE id=?",
                    (worktree_path, branch, stamp, stamp, expires_at, lease_id),
                )
            except sqlite3.IntegrityError as exc:
                raise AppError(409, "branch_in_use", f"Branch '{branch}' is already held by another active lease", {"branch": branch}) from exc
            updated = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            record = lease_record(c, updated, repo_name=repo_name, fetch_warning=fetch_warning)
            event(c, actor, "lease.acquire", lease_id, None, record)
        self.write_snapshot()
        return record

    def acquire(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Atomically acquire a checkout slot lease or enqueue FIFO."""
        branch = slot_worktrees.validate_branch_name(str(payload.get("branch") or ""))
        mode = str(payload.get("mode") or "shared")
        if mode not in {"shared", "exclusive"}:
            raise AppError(422, "validation", "mode must be shared or exclusive", {"mode": "invalid"})
        priority = str(payload.get("priority") or "normal")
        if priority not in PRIORITY_RANK:
            raise AppError(422, "validation", "Invalid lease priority", {"priority": "invalid"})
        reason = text(payload.get("reason", ""), "reason", limit=1000)
        requested_ttl = payload.get("ttlSeconds")
        if requested_ttl is not None and not (MIN_TTL_SECONDS <= int(requested_ttl) <= MAX_TTL_SECONDS):
            raise AppError(422, "validation", f"ttlSeconds must be between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS}", {"ttlSeconds": "invalid"})

        # Best-effort sweep first so freshly expired slots are immediately reusable.
        self.sweep()

        provision_args: tuple[str, str, str, int, str, int, dict[str, Any]] | None = None
        immediate_result: dict[str, Any] | None = None

        with self.db.transaction() as c:
            repo = _resolve_repo_for_lease(c, payload.get("repositoryId"), payload.get("repositoryUrl"))
            repo_id = repo["id"]
            repo_name = repo["name"]
            pol = policy_record(c, repo_id)
            ttl = int(requested_ttl) if requested_ttl is not None else pol["defaultTtlSeconds"]
            stamp = now()

            if actor.get("kind") == "agent":
                principal = communication_actor(c, actor)
                agent_id: str | None = principal["id"]
                credential_id: str | None = actor["id"]
                holder_label = principal["label"]
                chat_id = text(payload.get("chatId") or "", "chatId", limit=500) or (identity_chat(c, principal["id"]) or "")
                provider = text(payload.get("provider") or "", "provider", limit=100)
                run_id = payload.get("runId")
                if run_id is not None:
                    run_row = c.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
                    if run_row is None:
                        raise AppError(404, "not_found", "Run not found")
                    if run_row["credential_id"] != actor["id"] or run_row["agent_id"] != principal["id"]:
                        raise AppError(403, "coordination_scope", "Only the originating credential may bind this run")
                    if run_row["ended_at"] is not None:
                        raise AppError(409, "run_ended", "This run has ended; start a new run")
                    if not provider:
                        provider = run_row["provider"]
                    if not chat_id and run_row["external_chat_id"]:
                        chat_id = run_row["external_chat_id"]
                else:
                    open_run = c.execute(
                        "SELECT id,provider,external_chat_id FROM agent_runs WHERE credential_id=? AND ended_at IS NULL "
                        "ORDER BY created_at DESC, id DESC LIMIT 1",
                        (actor["id"],),
                    ).fetchone()
                    if open_run is not None:
                        run_id = open_run["id"]
                        if not provider:
                            provider = open_run["provider"]
                        if not chat_id and open_run["external_chat_id"]:
                            chat_id = open_run["external_chat_id"]
            else:
                agent_id = None
                credential_id = None
                run_id = None
                holder_label = text(payload.get("holderLabel") or actor.get("label") or "You", "holderLabel", limit=200)
                provider = text(payload.get("provider") or "", "provider", limit=100)
                chat_id = text(payload.get("chatId") or "", "chatId", limit=500)

            # Idempotent re-acquire and single-repo anti-deadlock check for agent identities.
            if agent_id is not None:
                same_repo = c.execute(
                    "SELECT * FROM repository_leases WHERE agent_id=? AND repository_id=? AND state IN ('active','offered','queued') "
                    "ORDER BY requested_at DESC LIMIT 1",
                    (agent_id, repo_id),
                ).fetchone()
                if same_repo is not None:
                    if same_repo["state"] == "active":
                        new_exp = _iso_plus(stamp, ttl)
                        if same_repo["expires_at"] and same_repo["expires_at"] > new_exp:
                            new_exp = same_repo["expires_at"]
                        c.execute(
                            "UPDATE repository_leases SET last_heartbeat_at=?,expires_at=? WHERE id=?",
                            (stamp, new_exp, same_repo["id"]),
                        )
                        refreshed = c.execute("SELECT * FROM repository_leases WHERE id=?", (same_repo["id"],)).fetchone()
                        immediate_result = lease_record(c, refreshed, repo_name=repo_name)
                    elif same_repo["state"] == "queued":
                        c.execute("UPDATE repository_leases SET last_heartbeat_at=? WHERE id=?", (stamp, same_repo["id"]))
                        refreshed = c.execute("SELECT * FROM repository_leases WHERE id=?", (same_repo["id"],)).fetchone()
                        immediate_result = lease_record(c, refreshed, repo_name=repo_name)
                    else:
                        # Offered to this same agent: proceed to provision & activate outside tx.
                        c.execute("UPDATE repository_leases SET last_heartbeat_at=? WHERE id=?", (stamp, same_repo["id"]))
                        provision_args = (
                            same_repo["id"],
                            repo_id,
                            repo_name,
                            int(same_repo["slot_index"]),
                            branch or same_repo["branch"],
                            ttl,
                            pol,
                        )
                else:
                    other_held = c.execute(
                        "SELECT l.id,l.repository_id,r.name FROM repository_leases l JOIN repositories r ON r.id=l.repository_id "
                        "WHERE l.agent_id=? AND l.repository_id<>? AND l.state IN ('offered','active') LIMIT 1",
                        (agent_id, repo_id),
                    ).fetchone()
                    if other_held is not None:
                        raise AppError(
                            409,
                            "agent_lease_limit",
                            f"You already hold an active lease on {other_held['name']}; release it before acquiring another repository",
                            {"leaseId": other_held["id"], "repositoryId": other_held["repository_id"]},
                        )

            if immediate_result is None and provision_args is None:
                holding = c.execute(
                    "SELECT id,slot_index,mode,branch FROM repository_leases WHERE repository_id=? AND state IN ('offered','active')",
                    (repo_id,),
                ).fetchall()
                dirty = c.execute(
                    "SELECT slot_index FROM repository_leases WHERE repository_id=? AND state='expired_dirty'",
                    (repo_id,),
                ).fetchall()
                queued_ahead = c.execute(
                    f"SELECT id,mode,priority FROM repository_leases WHERE repository_id=? AND state='queued' "
                    f"ORDER BY {PRIORITY_ORDER_SQL}, queue_seq ASC LIMIT 1",
                    (repo_id,),
                ).fetchone()

                max_slots = pol["maxActiveLeases"]
                occupied = {int(r["slot_index"]) for r in [*holding, *dirty] if r["slot_index"] is not None}
                free_slots = [i for i in range(1, max_slots + 1) if i not in occupied] if max_slots > 0 else []

                if branch:
                    clash = next((r for r in holding if r["branch"] == branch), None)
                    if clash is not None:
                        raise AppError(
                            409,
                            "branch_in_use",
                            f"Branch '{branch}' is already checked out by another active lease",
                            {"branch": branch, "leaseId": clash["id"]},
                        )

                blocked_by_exclusive_waiter = queued_ahead is not None and (
                    queued_ahead["mode"] == "exclusive" or PRIORITY_RANK[queued_ahead["priority"]] <= PRIORITY_RANK[priority]
                )
                can_grant = (
                    max_slots > 0
                    and len(free_slots) > 0
                    and len(holding) < max_slots
                    and not any(r["mode"] == "exclusive" for r in holding)
                    and (mode != "exclusive" or len(holding) == 0)
                    and not blocked_by_exclusive_waiter
                )

                lease_id = uid()
                queue_seq = int(
                    c.execute(
                        "SELECT COALESCE(MAX(queue_seq),0)+1 FROM repository_leases WHERE repository_id=?",
                        (repo_id,),
                    ).fetchone()[0]
                )
                if not can_grant:
                    c.execute(
                        "INSERT INTO repository_leases(id,repository_id,agent_id,credential_id,run_id,provider,chat_id,"
                        "holder_label,slot_index,worktree_path,branch,mode,state,priority,queue_seq,reason,"
                        "requested_at,last_heartbeat_at,revision) "
                        "VALUES(?,?,?,?,?,?,?,?,NULL,'',?,?,'queued',?,?,?,?,?,1)",
                        (
                            lease_id, repo_id, agent_id, credential_id, run_id, provider, chat_id,
                            holder_label, branch, mode, priority, queue_seq, reason, stamp, stamp,
                        ),
                    )
                    row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
                    immediate_result = lease_record(c, row, repo_name=repo_name)
                    event(c, actor, "lease.queue", lease_id, None, immediate_result)
                elif not pol["baseCheckoutPath"]:
                    slot = free_slots[0]
                    expires_at = _iso_plus(stamp, ttl)
                    c.execute(
                        "INSERT INTO repository_leases(id,repository_id,agent_id,credential_id,run_id,provider,chat_id,"
                        "holder_label,slot_index,worktree_path,branch,mode,state,priority,queue_seq,reason,"
                        "requested_at,offered_at,acquired_at,last_heartbeat_at,expires_at,revision) "
                        "VALUES(?,?,?,?,?,?,?,?,?,'',?,?,'active',?,?,?,?,?,?,?,?,1)",
                        (
                            lease_id, repo_id, agent_id, credential_id, run_id, provider, chat_id,
                            holder_label, slot, branch, mode, priority, queue_seq, reason,
                            stamp, stamp, stamp, stamp, expires_at,
                        ),
                    )
                    row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
                    immediate_result = lease_record(c, row, repo_name=repo_name)
                    event(c, actor, "lease.acquire", lease_id, None, immediate_result)
                else:
                    # Two-phase reserve: commit slot in 'offered', provision worktree outside DB lock.
                    slot = free_slots[0]
                    c.execute(
                        "INSERT INTO repository_leases(id,repository_id,agent_id,credential_id,run_id,provider,chat_id,"
                        "holder_label,slot_index,worktree_path,branch,mode,state,priority,queue_seq,reason,"
                        "requested_at,offered_at,last_heartbeat_at,revision) "
                        "VALUES(?,?,?,?,?,?,?,?,?,'',?,?,'offered',?,?,?,?,?,?,1)",
                        (
                            lease_id, repo_id, agent_id, credential_id, run_id, provider, chat_id,
                            holder_label, slot, branch, mode, priority, queue_seq, reason,
                            stamp, stamp, stamp,
                        ),
                    )
                    provision_args = (lease_id, repo_id, repo_name, slot, branch, ttl, pol)

        if provision_args is not None:
            return self._provision_and_activate(*provision_args, actor=actor, rollback_on_failure=True)
        assert immediate_result is not None
        self.write_snapshot()
        return immediate_result

    def acquire_for_dispatch(
        self,
        c: sqlite3.Connection,
        actor: dict[str, Any],
        *,
        repo_id: str,
        provider: str,
        holder_label: str,
        branch: str = "",
        mode: str = "shared",
        priority: str = "normal",
        reason: str = "",
    ) -> dict[str, Any]:
        """Reserve a slot ('offered') or enqueue ('queued') atomically inside caller's transaction for dispatch_queue."""
        branch = slot_worktrees.validate_branch_name(branch)
        repo = _resolve_repo_for_lease(c, repo_id, None)
        pol = policy_record(c, repo["id"])
        stamp = now()
        holding = c.execute(
            "SELECT id,slot_index,mode,branch FROM repository_leases WHERE repository_id=? AND state IN ('offered','active')",
            (repo["id"],),
        ).fetchall()
        dirty = c.execute(
            "SELECT slot_index FROM repository_leases WHERE repository_id=? AND state='expired_dirty'",
            (repo["id"],),
        ).fetchall()
        queued_ahead = c.execute(
            f"SELECT id,mode,priority FROM repository_leases WHERE repository_id=? AND state='queued' "
            f"ORDER BY {PRIORITY_ORDER_SQL}, queue_seq ASC LIMIT 1",
            (repo["id"],),
        ).fetchone()
        max_slots = pol["maxActiveLeases"]
        occupied = {int(r["slot_index"]) for r in [*holding, *dirty] if r["slot_index"] is not None}
        free_slots = [i for i in range(1, max_slots + 1) if i not in occupied] if max_slots > 0 else []

        if branch:
            clash = next((r for r in holding if r["branch"] == branch), None)
            if clash is not None:
                raise AppError(
                    409,
                    "branch_in_use",
                    f"Branch '{branch}' is already checked out by another active lease",
                    {"branch": branch, "leaseId": clash["id"]},
                )

        blocked_by_waiter = queued_ahead is not None and (
            queued_ahead["mode"] == "exclusive" or PRIORITY_RANK[queued_ahead["priority"]] <= PRIORITY_RANK[priority]
        )
        can_grant = (
            max_slots > 0
            and len(free_slots) > 0
            and len(holding) < max_slots
            and not any(r["mode"] == "exclusive" for r in holding)
            and (mode != "exclusive" or len(holding) == 0)
            and not blocked_by_waiter
        )
        lease_id = uid()
        queue_seq = int(
            c.execute(
                "SELECT COALESCE(MAX(queue_seq),0)+1 FROM repository_leases WHERE repository_id=?",
                (repo["id"],),
            ).fetchone()[0]
        )
        if can_grant:
            slot = free_slots[0]
            c.execute(
                "INSERT INTO repository_leases(id,repository_id,agent_id,credential_id,run_id,provider,chat_id,"
                "holder_label,slot_index,worktree_path,branch,mode,state,priority,queue_seq,reason,"
                "requested_at,offered_at,last_heartbeat_at,revision) "
                "VALUES(?,?,NULL,NULL,NULL,?,'',?,?,'',?,?,'offered',?,?,?,?,?,?,1)",
                (lease_id, repo["id"], provider, holder_label, slot, branch, mode, priority, queue_seq, reason, stamp, stamp, stamp),
            )
        else:
            c.execute(
                "INSERT INTO repository_leases(id,repository_id,agent_id,credential_id,run_id,provider,chat_id,"
                "holder_label,slot_index,worktree_path,branch,mode,state,priority,queue_seq,reason,"
                "requested_at,last_heartbeat_at,revision) "
                "VALUES(?,?,NULL,NULL,NULL,?,'',?,NULL,'',?,?,'queued',?,?,?,?,?,1)",
                (lease_id, repo["id"], provider, holder_label, branch, mode, priority, queue_seq, reason, stamp, stamp),
            )
        row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
        rec = lease_record(c, row, repo_name=repo["name"])
        event(c, actor, "lease.queue" if rec["state"] == "queued" else "lease.offer", lease_id, None, rec)
        return rec

    def activate_dispatch_lease(
        self,
        lease_id: str,
        actor: dict[str, Any],
        *,
        requested_branch: str = "",
        chat_id: str = "",
    ) -> dict[str, Any]:
        """Provision slot worktree (if configured) and activate an offered dispatch lease."""
        with self.db.read() as c:
            row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Lease not found")
            repo = _get_repo(c, row["repository_id"])
            pol = policy_record(c, repo["id"])
            if row["state"] == "active":
                return lease_record(c, row, repo_name=repo["name"])
            if row["state"] != "offered" or row["slot_index"] is None:
                raise AppError(409, "conflict", "Lease is not ready for activation")
            slot_index = int(row["slot_index"])
            branch = requested_branch or row["branch"]
        record = self._provision_and_activate(
            lease_id,
            repo["id"],
            repo["name"],
            slot_index,
            branch,
            pol["defaultTtlSeconds"],
            pol,
            actor,
            rollback_on_failure=False,
        )
        if chat_id:
            with self.db.transaction() as c:
                c.execute("UPDATE repository_leases SET chat_id=? WHERE id=?", (chat_id, lease_id))
                row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
                record = lease_record(c, row, repo_name=repo["name"])
            self.write_snapshot()
        return record

    def bind_dispatch_chat(self, lease_id: str, chat_id: str) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE repository_leases SET chat_id=?,last_heartbeat_at=? WHERE id=?", (chat_id, now(), lease_id))
        self.write_snapshot()

    def cancel_dispatch_lease(self, lease_id: str, actor: dict[str, Any], reason: str = "cancelled") -> None:
        with self.db.read() as c:
            row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if row is None or row["state"] in CLOSED_STATES:
                return
            pol = policy_record(c, row["repository_id"])
        rescue_ref = ""
        if row["worktree_path"]:
            res = slot_worktrees.recycle_or_rescue_slot(
                row["worktree_path"],
                pol["defaultBranch"],
                lease_id,
                branch=row["branch"],
                allow_rescue=pol["autoRescueDirty"],
            )
            rescue_ref = res.rescue_ref
        promoted_dispatch = False
        with self.db.transaction() as c:
            stamp = now()
            c.execute(
                "UPDATE repository_leases SET state='cancelled',released_at=?,release_reason=?,rescue_ref=?,revision=revision+1 "
                "WHERE id=? AND state IN ('queued','offered','active')",
                (stamp, reason, rescue_ref, lease_id),
            )
            promoted_dispatch = _promote_next_in_tx(c, row["repository_id"], stamp)
        self.write_snapshot()
        if promoted_dispatch:
            self._notify_promoted()

    def poll(self, actor: dict[str, Any], lease_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Refresh waiter heartbeat and confirm/provision an offered slot."""
        branch_override = slot_worktrees.validate_branch_name(str(payload.get("branch") or ""))
        self.sweep()

        provision_args: tuple[str, str, str, int, str, int, dict[str, Any]] | None = None
        result: dict[str, Any] | None = None

        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Lease not found")
            if actor.get("kind") == "agent":
                principal = communication_actor(c, actor)
                if row["agent_id"] != principal["id"]:
                    raise AppError(403, "lease_scope", "Only the lease owner may poll this lease")
            repo = _get_repo(c, row["repository_id"])
            pol = policy_record(c, repo["id"])
            stamp = now()
            if row["state"] == "queued":
                c.execute("UPDATE repository_leases SET last_heartbeat_at=? WHERE id=?", (stamp, lease_id))
                # Attempt promotion in case capacity just opened up.
                _promote_next_in_tx(c, repo["id"], stamp)
                row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if row["state"] == "offered":
                c.execute("UPDATE repository_leases SET last_heartbeat_at=? WHERE id=?", (stamp, lease_id))
                provision_args = (
                    lease_id,
                    repo["id"],
                    repo["name"],
                    int(row["slot_index"]),
                    branch_override or row["branch"],
                    pol["defaultTtlSeconds"],
                    pol,
                )
            elif row["state"] == "active":
                c.execute("UPDATE repository_leases SET last_heartbeat_at=? WHERE id=?", (stamp, lease_id))
                refreshed = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
                result = lease_record(c, refreshed, repo_name=repo["name"])
            else:
                result = lease_record(c, row, repo_name=repo["name"])

        if provision_args is not None:
            return self._provision_and_activate(*provision_args, actor=actor, rollback_on_failure=True)
        assert result is not None
        return result

    def _bindable_lease(
        self, c: sqlite3.Connection, actor: dict[str, Any], lease_id: str, revision: int,
    ) -> tuple[sqlite3.Row, sqlite3.Row, dict[str, Any]]:
        if actor.get("kind") != "agent":
            raise AppError(403, "lease_scope", "Only the authenticated lease owner can bind a worktree")
        principal = communication_actor(c, actor)
        row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Lease not found")
        if row["agent_id"] != principal["id"]:
            raise AppError(403, "lease_scope", "Only the lease owner can bind a worktree")
        require_revision(row, revision)
        expiry = _parse_iso(row["expires_at"])
        if row["state"] != "active" or row["release_reason"] or expiry is None or expiry <= datetime.now(UTC):
            raise AppError(409, "lease_closed", "Only an active, unexpired lease can bind a worktree")
        repo = _get_repo(c, row["repository_id"])
        pol = policy_record(c, repo["id"])
        if pol["baseCheckoutPath"] or row["worktree_path"]:
            raise AppError(409, "worktree_managed", "This lease uses the repository's managed slot policy")
        if row["worktree_claim_key"] is not None or row["caller_worktree_path"]:
            raise AppError(409, "worktree_in_use", "The lease already has a worktree binding")
        if repo["archived"] or repo["merged_into"] or pol["maxActiveLeases"] == 0:
            raise AppError(409, "lease_closed", "The repository is unavailable for worktree binding")
        if not row["branch"] or not repo["remote_identity"]:
            raise AppError(422, "validation", "Binding requires a named lease branch and a catalog origin")
        return row, repo, pol

    def prepare_worktree_binding(
        self, actor: dict[str, Any], lease_id: str, payload: dict[str, Any],
    ) -> Callable[[sqlite3.Connection], dict[str, Any]]:
        """Inspect Git outside the write lock; commit the claim and its receipt in one transaction."""
        revision = payload["revision"]
        with self.db.read() as c:
            row, repo, pol = self._bindable_lease(c, actor, lease_id, revision)
        worktree = slot_worktrees.validate_caller_worktree(
            payload["worktreePath"], repo["remote_identity"], row["branch"], pol["defaultBranch"]
        )

        def commit(c: sqlite3.Connection) -> dict[str, Any]:
            current, current_repo, policy = self._bindable_lease(c, actor, lease_id, revision)
            if (repo["remote_identity"] != current_repo["remote_identity"] or repo["id"] != current_repo["id"]
                    or row["branch"] != current["branch"] or pol["defaultBranch"] != policy["defaultBranch"]):
                raise AppError(409, "conflict", "Repository or lease changed while verifying the worktree")
            worktree.require_current()
            worktree_claims.require_caller_path(c, worktree.path)
            key = worktree_claims.reserve(c, lease_id, worktree.path)
            if self._is_provisioning(key):
                raise AppError(409, "worktree_in_use", "A managed offer is provisioning this directory")
            acquired = _parse_iso(current["acquired_at"])
            if acquired is None:
                raise AppError(409, "lease_closed", "The lease has no recorded acquisition")
            provenance = worktree_claims.binding_provenance(
                c, current, worktree, key, acquired, payload.get("handoffMessageId")
            )
            c.execute(
                "UPDATE repository_leases SET caller_worktree_path=?,revision=revision+1 WHERE id=?",
                (str(worktree.path), lease_id),
            )
            bound = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            result = lease_record(c, bound, repo_name=current_repo["name"])
            credential = c.execute(
                "SELECT chat_id FROM communication_credentials WHERE credential_id=?", (actor["id"],)
            ).fetchone()
            event(c, actor, "lease.bind_worktree", lease_id, None, {
                "worktreePath": result["worktreePath"], "agentId": current["agent_id"],
                "chatId": credential["chat_id"], "provenance": provenance,
                "registeredAtNs": str(worktree.registered_at_ns),
                "registrationId": worktree.registration_id,
                "handoffMessageId": payload.get("handoffMessageId"), "revision": result["revision"],
            })
            return result

        return commit

    def renew(self, actor: dict[str, Any], lease_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        requested_ttl = payload.get("ttlSeconds")
        if requested_ttl is not None and not (MIN_TTL_SECONDS <= int(requested_ttl) <= MAX_TTL_SECONDS):
            raise AppError(422, "validation", f"ttlSeconds must be between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS}", {"ttlSeconds": "invalid"})
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Lease not found")
            if actor.get("kind") == "agent":
                principal = communication_actor(c, actor)
                if row["agent_id"] != principal["id"]:
                    raise AppError(403, "lease_scope", "Only the lease owner may renew this lease")
            require_revision(row, payload.get("revision"))
            if row["state"] != "active":
                raise AppError(409, "lease_closed", "Only an active lease can be renewed")
            pol = policy_record(c, row["repository_id"])
            ttl = int(requested_ttl) if requested_ttl is not None else pol["defaultTtlSeconds"]
            stamp = now()
            expires_at = _iso_plus(stamp, ttl)
            c.execute(
                "UPDATE repository_leases SET last_heartbeat_at=?,expires_at=?,revision=revision+1 WHERE id=?",
                (stamp, expires_at, lease_id),
            )
            updated = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            record = lease_record(c, updated)
            event(c, actor, "lease.renew", lease_id, {"expiresAt": row["expires_at"]}, {"expiresAt": expires_at})
        self.write_snapshot()
        return record

    def release(self, actor: dict[str, Any], lease_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        force = bool(payload.get("force", False))
        rescue = bool(payload.get("rescue", False))
        reason = text(payload.get("reason", ""), "reason", limit=1000)

        with self.db.read() as c:
            row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Lease not found")
            if actor.get("kind") == "agent":
                principal = communication_actor(c, actor)
                if force:
                    raise AppError(403, "agent_scope", "Only the person may force-release a lease")
                if row["agent_id"] != principal["id"]:
                    raise AppError(403, "lease_scope", "Only the lease owner may release this lease")
            require_revision(row, payload.get("revision"))
            if row["state"] in CLOSED_STATES:
                raise AppError(409, "lease_closed", "Lease is already closed")
            if row["state"] == "expired_dirty":
                raise AppError(409, "slot_dirty", "Lease slot is expired_dirty; use rescue to recover and recycle it")
            if row["caller_worktree_path"] and rescue:
                raise AppError(409, "caller_worktree", "The caller owns recovery and archival. Release without rescue.")
            pol = policy_record(c, row["repository_id"])

        # Phase 2 (outside DB lock): clean/detach/rescue managed worktree if present.
        rescue_ref = ""
        if row["state"] in HOLDING_STATES and row["worktree_path"]:
            allow_rescue = rescue if actor.get("kind") == "agent" else (rescue or force or pol["autoRescueDirty"])
            recycled = slot_worktrees.recycle_or_rescue_slot(
                row["worktree_path"],
                pol["defaultBranch"],
                lease_id,
                branch=row["branch"],
                allow_rescue=allow_rescue,
            )
            if not recycled.clean:
                raise AppError(
                    409,
                    "slot_dirty",
                    "Slot has uncommitted changes; commit/push them or release with --rescue to snapshot to refs/worklog-rescue",
                    {"worktreePath": row["worktree_path"], "dirtyFiles": list(recycled.dirty_files[:20])},
                )
            rescue_ref = recycled.rescue_ref

        promoted_dispatch = False
        with self.db.transaction() as c:
            current = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if current is None:
                raise AppError(404, "not_found", "Lease not found")
            require_revision(current, payload.get("revision"))
            stamp = now()
            if current["state"] == "queued":
                new_state = "cancelled"
                final_reason = reason or "cancelled"
                c.execute(
                    "UPDATE dispatch_queue SET status='cancelled',message='Cancelled',updated_at=?,revision=revision+1 "
                    "WHERE lease_id=? AND status='queued'",
                    (stamp, lease_id),
                )
            else:
                new_state = "revoked" if (actor.get("kind") == "human" and force) else "released"
                final_reason = reason or ("force_released" if new_state == "revoked" else ("rescued" if rescue_ref else "released"))
            c.execute(
                "UPDATE repository_leases SET state=?,released_at=?,release_reason=?,rescue_ref=?,revision=revision+1 WHERE id=?",
                (new_state, stamp, final_reason, rescue_ref or current["rescue_ref"], lease_id),
            )
            updated = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            record = lease_record(c, updated)
            event(c, actor, "lease.revoke" if new_state == "revoked" else "lease.release", lease_id, {"state": current["state"]}, record)
            promoted_dispatch = _promote_next_in_tx(c, current["repository_id"], stamp)

        self.write_snapshot()
        if promoted_dispatch:
            self._notify_promoted()
        return record

    def rescue(self, actor: dict[str, Any], lease_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Human-initiated rescue & recycle for an expired_dirty (or active) slot."""
        if actor.get("kind") != "human":
            raise AppError(403, "agent_scope", "Only the person may rescue a dirty slot lease")
        reason = text(payload.get("reason", ""), "reason", limit=1000) or "human_rescued"
        with self.db.read() as c:
            row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Lease not found")
            require_revision(row, payload.get("revision"))
            if row["state"] not in {"expired_dirty", "active", "offered"}:
                raise AppError(409, "lease_closed", "Lease is already closed")
            if row["caller_worktree_path"]:
                raise AppError(409, "caller_worktree", "The caller owns recovery and archival of this worktree")
            pol = policy_record(c, row["repository_id"])

        rescue_ref = row["rescue_ref"]
        if row["worktree_path"]:
            recycled = slot_worktrees.recycle_or_rescue_slot(
                row["worktree_path"],
                pol["defaultBranch"],
                lease_id,
                branch=row["branch"],
                allow_rescue=True,
            )
            if not recycled.clean:
                raise AppError(
                    409,
                    "slot_dirty",
                    f"Could not rescue and clean slot: {recycled.error or 'working tree remained dirty'}",
                    {"worktreePath": row["worktree_path"], "dirtyFiles": list(recycled.dirty_files[:20])},
                )
            rescue_ref = recycled.rescue_ref or rescue_ref

        promoted_dispatch = False
        with self.db.transaction() as c:
            current = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            if current is None:
                raise AppError(404, "not_found", "Lease not found")
            require_revision(current, payload.get("revision"))
            stamp = now()
            c.execute(
                "UPDATE repository_leases SET state='released',released_at=?,release_reason=?,rescue_ref=?,revision=revision+1 WHERE id=?",
                (stamp, reason, rescue_ref, lease_id),
            )
            updated = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
            record = lease_record(c, updated)
            event(c, actor, "lease.rescue", lease_id, {"state": current["state"]}, record)
            promoted_dispatch = _promote_next_in_tx(c, current["repository_id"], stamp)

        self.write_snapshot()
        if promoted_dispatch:
            self._notify_promoted()
        return record

    # ---- Background Reaper & Liveness ----

    def _dead_claude_sessions(self) -> set[str]:
        """Return Claude session IDs whose session file exists on disk but whose OS process has exited."""
        dead: set[str] = set()
        alive_ids = {s["sessionId"] for s in active_sessions.running_sessions(self.transcripts.roots.claude_sessions)}
        for folder in self.transcripts.roots.claude_sessions:
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if not active_sessions.RECORD.fullmatch(entry.name):
                            continue
                        rec = active_sessions.read_record(Path(entry.path))
                        if rec is not None and rec["sessionId"] not in alive_ids and not active_sessions.running(rec):
                            dead.add(rec["sessionId"])
            except OSError:
                continue
        return dead

    def _session_confirmed_dead(self, row: sqlite3.Row, dead_claude: set[str], now_dt: datetime) -> str | None:
        chat_id = (row["chat_id"] or "").strip()
        if not chat_id:
            return None
        last_hb = _parse_iso(row["last_heartbeat_at"])
        if last_hb is not None and (now_dt - last_hb).total_seconds() < DEAD_SESSION_GRACE_SECONDS:
            return None
        provider = (row["provider"] or "").strip().lower()
        if provider == "claude":
            if chat_id.lower() in dead_claude:
                return "session_dead"
            if self.transcripts.archived(chat_id):
                return "session_archived"
        elif provider == "codex":
            if self.transcripts.archived(chat_id):
                return "session_archived"
        elif provider == "antigravity":
            state = antigravity_sessions.session_state(chat_id, brain_root=self.antigravity_brain_root)
            if state == "ended":
                return "session_ended"
        return None

    def sweep(self) -> dict[str, int]:
        """Reclaim abandoned waiters, expired offers, TTL-expired leases, and dead-session leases."""
        if not self._sweep_lock.acquire(blocking=False):
            return {"cancelled": 0, "expired": 0, "rescued": 0, "dirty": 0}
        try:
            return self._sweep_locked()
        finally:
            self._sweep_lock.release()

    def reap_deferred(self) -> dict[str, int]:
        """Blocking sweep called immediately after run end / retire / revoke / archive commits."""
        with self._sweep_lock:
            return self._sweep_locked()

    def _sweep_locked(self) -> dict[str, int]:
        counts = {"cancelled": 0, "expired": 0, "rescued": 0, "dirty": 0}
        now_dt = datetime.now(UTC)
        stamp = now_dt.isoformat()
        stale_queue_cutoff = (now_dt - timedelta(seconds=QUEUE_HEARTBEAT_STALE_SECONDS)).isoformat()
        offer_cutoff = (now_dt - timedelta(seconds=OFFER_GRACE_SECONDS)).isoformat()
        changed = False
        promoted_dispatch = False
        reclaim_candidates: list[tuple[dict[str, Any], dict[str, Any], str, str]] = []

        # Phase 1: handle abandoned CLI queued waiters & expired CLI offers, and gather active candidates.
        with self.db.transaction() as c:
            touched_repos: set[str] = set()
            # 1a. Cancel CLI queued waiters with stale heartbeat (> 180s). Dispatch-queue waiters never time out here.
            stale_queued = c.execute(
                "SELECT id,repository_id FROM repository_leases l WHERE state='queued' AND last_heartbeat_at<? "
                "AND NOT EXISTS(SELECT 1 FROM dispatch_queue dq WHERE dq.lease_id=l.id AND dq.status='queued')",
                (stale_queue_cutoff,),
            ).fetchall()
            for row in stale_queued:
                c.execute(
                    "UPDATE repository_leases SET state='cancelled',released_at=?,release_reason='waiter_disconnected',"
                    "revision=revision+1 WHERE id=? AND state='queued'",
                    (stamp, row["id"]),
                )
                counts["cancelled"] += 1
                changed = True
                touched_repos.add(row["repository_id"])

            # 1b. Expire unconfirmed CLI offered leases past 180s (excluding dispatch_queue offers).
            stale_offered = c.execute(
                "SELECT id,repository_id,worktree_claim_key FROM repository_leases l WHERE state='offered' "
                "AND COALESCE(offered_at,requested_at)<? "
                "AND NOT EXISTS(SELECT 1 FROM dispatch_queue dq WHERE dq.lease_id=l.id AND dq.status IN ('queued','provisioning','preparing'))",
                (offer_cutoff,),
            ).fetchall()
            for row in stale_offered:
                if self._is_provisioning(row["worktree_claim_key"]):
                    continue
                c.execute(
                    "UPDATE repository_leases SET state='expired',released_at=?,release_reason='offer_timeout',"
                    "revision=revision+1 WHERE id=? AND state='offered'",
                    (stamp, row["id"]),
                )
                counts["expired"] += 1
                changed = True
                touched_repos.add(row["repository_id"])

            for repo_id in sorted(touched_repos):
                if _promote_next_in_tx(c, repo_id, stamp):
                    promoted_dispatch = True

            active_rows = c.execute("SELECT * FROM repository_leases WHERE state='active'").fetchall()
            policies = {r["repository_id"]: policy_record(c, r["repository_id"]) for r in active_rows}

        # Phase 2 (outside DB lock): inspect liveness & run Git auto-rescue for overdue/dead active leases.
        dead_claude = self._dead_claude_sessions() if any((r["provider"] or "").lower() == "claude" for r in active_rows) else set()
        for row in active_rows:
            reclaim_reason: str | None = None
            target_state = "expired"
            if row["release_reason"]:
                reclaim_reason = row["release_reason"]
                target_state = "released"
            else:
                exp = _parse_iso(row["expires_at"])
                if exp is not None and exp <= now_dt:
                    reclaim_reason = "ttl_expired"
                else:
                    dead_reason = self._session_confirmed_dead(row, dead_claude, now_dt)
                    if dead_reason is not None:
                        reclaim_reason = dead_reason
            if reclaim_reason is not None:
                reclaim_candidates.append((dict(row), policies[row["repository_id"]], reclaim_reason, target_state))

        for lease_dict, pol, reclaim_reason, target_state in reclaim_candidates:
            rescue_ref = lease_dict["rescue_ref"]
            final_state = target_state
            if lease_dict["worktree_path"]:
                recycled = slot_worktrees.recycle_or_rescue_slot(
                    lease_dict["worktree_path"],
                    pol["defaultBranch"],
                    lease_dict["id"],
                    branch=lease_dict["branch"],
                    allow_rescue=bool(pol["autoRescueDirty"]),
                )
                rescue_ref = recycled.rescue_ref or rescue_ref
                if recycled.rescued:
                    counts["rescued"] += 1
                if not recycled.clean:
                    final_state = "expired_dirty"
                    counts["dirty"] += 1

            with self.db.transaction() as c:
                cur_row = c.execute("SELECT * FROM repository_leases WHERE id=?", (lease_dict["id"],)).fetchone()
                if cur_row is None or cur_row["state"] != "active" or int(cur_row["revision"]) != int(lease_dict["revision"]):
                    # Renewed or released concurrently while we were inspecting/recycling.
                    continue
                liveness_field = "expires_at" if reclaim_reason == "ttl_expired" else "last_heartbeat_at"
                if (not lease_dict["worktree_path"] and not cur_row["release_reason"]
                        and cur_row[liveness_field] != lease_dict[liveness_field]):
                    # Participation and reacquisition refresh a logical/caller lease without
                    # incrementing its revision. A heartbeat supersedes this liveness scan.
                    continue
                end_stamp = now()
                c.execute(
                    "UPDATE repository_leases SET state=?,released_at=?,release_reason=?,rescue_ref=?,revision=revision+1 WHERE id=?",
                    (final_state, end_stamp, reclaim_reason, rescue_ref, lease_dict["id"]),
                )
                if final_state != "expired_dirty":
                    counts["expired"] += 1
                    if _promote_next_in_tx(c, lease_dict["repository_id"], end_stamp):
                        promoted_dispatch = True
                changed = True

        if changed:
            self.write_snapshot()
        if promoted_dispatch:
            self._notify_promoted()
        return counts
