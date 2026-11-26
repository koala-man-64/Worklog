"""Filesystem claim arbitration shared by caller binding and managed provisioning."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import slot_worktrees
from .common import AppError


def _matches_local_claim(path: str, key: str) -> bool:
    try:
        return slot_worktrees.worktree_claim_key(path) == key
    except AppError:
        # Restored leases and reported participation paths can refer to a different host OS.
        return False


def reserve(c: sqlite3.Connection, lease_id: str, path: Path) -> str:
    """Reserve the physical directory inside the same write transaction as the lease change."""
    key = slot_worktrees.worktree_claim_key(path)
    # Older active managed leases predate claim keys. They remain authoritative until released.
    legacy = c.execute(
        "SELECT worktree_path FROM repository_leases WHERE id<>? AND worktree_claim_key IS NULL "
        "AND worktree_path<>'' AND state IN ('offered','active','expired_dirty')",
        (lease_id,),
    )
    if any(_matches_local_claim(row["worktree_path"], key) for row in legacy):
        raise AppError(409, "worktree_in_use", "Another lease occupies this worktree")
    try:
        c.execute("UPDATE repository_leases SET worktree_claim_key=? WHERE id=?", (key, lease_id))
    except sqlite3.IntegrityError as exc:
        if exc.sqlite_errorname != "SQLITE_CONSTRAINT_UNIQUE":
            raise
        raise AppError(409, "worktree_in_use", "Another lease occupies this worktree") from exc
    return key


def last_holder(c: sqlite3.Connection, key: str, lease_id: str) -> sqlite3.Row | None:
    return c.execute(
        "SELECT id,agent_id,caller_worktree_path FROM repository_leases WHERE worktree_claim_key=? "
        "AND id<>? "
        "ORDER BY COALESCE(released_at,acquired_at) DESC, id DESC LIMIT 1",
        (key, lease_id),
    ).fetchone()


def require_caller_path(c: sqlite3.Connection, path: Path) -> None:
    """A logical lease may not adopt a directory reserved for a managed slot pool."""
    # v37 already stored resolved physical paths. Preserve those managed directories even if
    # the person removed the old pool policy before upgrading.
    legacy = c.execute(
        "SELECT DISTINCT worktree_path FROM repository_leases WHERE worktree_claim_key IS NULL AND worktree_path<>''"
    )
    if any(Path(row["worktree_path"]) == path for row in legacy):
        raise AppError(409, "worktree_managed", "This directory belongs to a managed slot")
    policies = c.execute(
        "SELECT p.base_checkout_path,p.slot_root_path,p.max_active_leases,r.name FROM repository_slot_policies p "
        "JOIN repositories r ON r.id=p.repository_id WHERE p.base_checkout_path<>''"
    )
    for policy in policies:
        if not Path(policy["base_checkout_path"]).is_absolute():
            continue  # A policy restored from another OS cannot own a local directory on this host.
        pool = slot_worktrees.slot_pool_path(policy["base_checkout_path"], policy["slot_root_path"], policy["name"])
        if path.is_relative_to(pool) or any(
            path.is_relative_to((pool / f"slot-{i}").resolve()) for i in range(1, policy["max_active_leases"] + 1)
        ):
            raise AppError(409, "worktree_managed", "This directory belongs to a managed slot pool")


def binding_provenance(
    c: sqlite3.Connection, lease: sqlite3.Row, worktree: slot_worktrees.CallerWorktree,
    key: str, acquired_at: datetime, handoff_message_id: str | None,
) -> str:
    """Attribute a fresh Git registration to the authenticated attestation, or verify its handoff."""
    prior = last_holder(c, key, lease["id"])
    if prior is not None and prior["caller_worktree_path"] is None:
        raise AppError(409, "worktree_managed", "This directory belongs to a managed slot")
    previous_owner = None
    if prior is not None:
        receipt = c.execute(
            "SELECT after_json FROM events WHERE kind='lease.bind_worktree' AND entity_id=?",
            (prior["id"],),
        ).fetchone()
        # A prior holder's authority follows its registered worktree, including a later lease.
        # Recreating a directory at the same address does not inherit that holder's evidence.
        if receipt and json.loads(receipt["after_json"]).get("registrationId") == worktree.registration_id:
            previous_owner = prior["agent_id"]
    # A participation is advisory. A peer's conflicting report calls for an explicit handoff,
    # and a self-reported directory never grants authority to sign one.
    peer_paths = c.execute(
        "SELECT pc.worktree_path FROM participation_contexts pc JOIN participations p ON p.id=pc.participation_id "
        "JOIN agent_runs r ON r.id=p.run_id WHERE pc.worktree_path<>'' "
        "AND p.released_at IS NULL AND r.ended_at IS NULL AND r.agent_id<>?",
        (lease["agent_id"],),
    )
    disputed = any(_matches_local_claim(r["worktree_path"], key) for r in peer_paths)
    if not handoff_message_id:
        if not disputed and previous_owner == lease["agent_id"]:
            return "previous_binding"
        registered_after_grant = worktree.registered_at_ns >= int(acquired_at.timestamp() * 1_000_000_000)
        if not disputed and previous_owner is None and registered_after_grant and worktree.clean:
            return "session_attestation"
    else:
        # The immutable message names this one lease and physical path. Only the person or the
        # previous verified caller holder can authorize existing work to change hands.
        message = c.execute(
            "SELECT m.text,m.author_id,m.author_kind FROM discussion_messages m "
            "JOIN discussion_contexts d ON d.discussion_id=m.discussion_id AND d.home=1 "
            "WHERE m.id=? AND d.context_type='repository' AND d.context_id=?",
            (handoff_message_id, lease["repository_id"]),
        ).fetchone()
        if message and (message["author_kind"] == "human" or message["author_id"] == previous_owner):
            lines = message["text"].splitlines()
            if (len(lines) == 2 and lines[0] == f"Hand off worktree to lease {lease['id']}"
                    and Path(lines[1]) == worktree.path):
                return "handoff"
    raise AppError(
        409, "worktree_handoff_required",
        "This existing worktree needs an explicit repository discussion handoff from the person "
        "or its previous lease holder. Supply the handoff message ID.",
    )
