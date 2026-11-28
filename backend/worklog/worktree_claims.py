"""Filesystem claim arbitration shared by caller binding and managed provisioning."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import slot_worktrees
from .common import AppError

_PEER_REGISTRATION_SKEW_NS = 5_000_000_000


def _matches_local_claim(path: str, key: str) -> bool:
    try:
        return slot_worktrees.worktree_claim_key(path) == key
    except AppError:
        # Restored leases and reported participation paths can refer to a different host OS.
        return False


def _within_local_worktree(path: str, root: Path, key: str) -> bool:
    if _matches_local_claim(path, key):
        return True
    try:
        candidate = slot_worktrees.validate_local_path(path, "directory")
    except AppError:
        return False
    return candidate.is_relative_to(root)


def _iso_to_ns(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp() * 1_000_000_000)


def _registered_session_directory(
    c: sqlite3.Connection,
    lease: sqlite3.Row,
    worktree: slot_worktrees.CallerWorktree,
    key: str,
) -> bool:
    """True when the lease's registered credential recorded this exact worktree and no peer claims it."""
    if not lease["credential_id"]:
        return False
    row = c.execute(
        "SELECT directory FROM communication_credentials WHERE credential_id=?",
        (lease["credential_id"],),
    ).fetchone()
    if row is None or not row["directory"] or not _matches_local_claim(row["directory"], key):
        return False
    # Refuse automatic binding when another unrevoked identity registered inside this worktree
    # for the current Git registration (or has an open run registered here).
    peers = c.execute(
        "SELECT cc.directory, cr.created_at, "
        "EXISTS(SELECT 1 FROM agent_runs r WHERE r.credential_id=cc.credential_id AND r.ended_at IS NULL) AS has_open_run "
        "FROM communication_credentials cc JOIN credentials cr ON cr.id=cc.credential_id "
        "WHERE cc.directory IS NOT NULL AND cc.directory<>'' AND cc.agent_id<>? AND cr.revoked=0",
        (lease["agent_id"],),
    )
    for peer in peers:
        if not _within_local_worktree(peer["directory"], worktree.path, key):
            continue
        if peer["has_open_run"] or not worktree.registration_trustworthy:
            return False
        created_ns = _iso_to_ns(peer["created_at"])
        if created_ns is None or created_ns + _PEER_REGISTRATION_SKEW_NS >= worktree.registered_at_ns:
            return False
    return True


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
    prior_registration_id = ""
    if prior is not None:
        receipt = c.execute(
            "SELECT after_json FROM events WHERE kind='lease.bind_worktree' AND entity_id=?",
            (prior["id"],),
        ).fetchone()
        if receipt:
            prior_registration_id = str(json.loads(receipt["after_json"]).get("registrationId") or "")
        # A prior holder's authority follows its registered worktree, including a later lease.
        # Recreating a directory at the same address does not inherit that holder's evidence.
        if worktree.registration_id and prior_registration_id == worktree.registration_id:
            previous_owner = prior["agent_id"]
    unknown_prior_registration = (
        prior is not None
        and prior["agent_id"] != lease["agent_id"]
        and (not worktree.registration_id or not prior_registration_id)
    )
    # A participation is advisory. A peer's conflicting report calls for an explicit handoff,
    # and a self-reported directory never grants authority to sign one.
    peer_paths = c.execute(
        "SELECT pc.worktree_path, pc.checkout_path FROM participation_contexts pc "
        "JOIN participations p ON p.id=pc.participation_id "
        "JOIN agent_runs r ON r.id=p.run_id WHERE (pc.worktree_path<>'' OR pc.checkout_path<>'') "
        "AND p.released_at IS NULL AND r.ended_at IS NULL AND r.agent_id<>?",
        (lease["agent_id"],),
    )
    disputed = any(
        (r["worktree_path"] and _matches_local_claim(r["worktree_path"], key))
        or (r["checkout_path"] and _matches_local_claim(r["checkout_path"], key))
        for r in peer_paths
    )
    if not handoff_message_id:
        if not disputed and previous_owner == lease["agent_id"]:
            return "previous_binding"
        registered_after_grant = (
            worktree.registration_trustworthy
            and worktree.registered_at_ns >= int(acquired_at.timestamp() * 1_000_000_000)
        )
        if not disputed and previous_owner is None and worktree.still_clean():
            if registered_after_grant:
                return "session_attestation"
            if not unknown_prior_registration and _registered_session_directory(c, lease, worktree, key):
                return "registered_directory"
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
        "This worktree needs an explicit repository discussion handoff from the person "
        "or its previous lease holder. Supply the handoff message ID.",
    )
