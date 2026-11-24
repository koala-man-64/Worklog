"""Offline compaction, `worklog compact` (consolidation step 4.3, K6; B-04, B-12, REM-19, REM-20).

Worklog keeps every record and every event row. Compaction lets go of what K6 decided it may (K6 as
revised on 2026-11-24, audit step 2.4, adds the first two):

- The copies of whole proposals stored in their history, at any age: each status change
  (proposal.submit, approve, reject, withdraw, reopen) keeps the summary proposals.summary
  writes today, and each proposal.update's before-state the same. proposal.create and the
  after-state of proposal.update keep the content, and proposal.stale stays whole: an upgrade
  step reads it. The proposal family is not THINNED, whose rule would blank proposal.create too.
- The step lists stored in roadmap.revise, archive, recover and move events written before those
  recorded only what changed, at any age: a revise keeps its KEPT fields, and an archive, recovery
  or move its roadmap's id, title, revision and the field that changed, as plans.py writes today.

- The payloads of display-only history older than HISTORY_DAYS. An event of a THINNED family
  keeps only the KEPT fields of its before and after records, so an object with none of them
  becomes {}: Today's completed and blocked lists, and each day entry's status on its day, replay
  item events by those fields. A payload that is not an object becomes null. Every other family
  keeps its payloads whole, but for the proposal events above, since they hold state, are read at
  runtime, or are history the guides promise (CONSOLIDATION.md's *Rules*): credentials,
  proposal.create and proposal.stale, chats, discussions, resources, settings and the rest. No event row goes: discussion_changes names events,
  and history is read in the order it was written, by rowid, which VACUUM must keep.
- Each roadmap's versions but its VERSIONS_KEPT newest.
- Idempotency receipts past their retention (maintenance.prune_receipts).

VACUUM then hands the space back. Compaction runs while Worklog is stopped, under the server's
lock, after a pre-compaction backup. It works on a copy beside the database: the copy is
compacted, vacuumed and validated, and only then renamed into the database's place, so a failure
before that rename leaves the database as it was, and takes the run's backup away again.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .maintenance import prune_receipts
from .migrations import SCHEMA_VERSION
from .recovery import (
    InstanceLock,
    _assert_database_offline,
    _readonly,
    _snapshot,
    backup_database,
    prune_backups,
    validate_database,
)

HISTORY_DAYS = 90
# The evidence of a proposal withdrawn or rejected more than this many days ago is emptied: the file
# keeps its row, name, size and hash, and no bytes (audit finding B7, step 2.6; Rudy's answer of
# 2026-11-24). The pre-compaction backup is the way back.
EVIDENCE_DAYS = 30
# A proposal's status changes, whose payloads compaction reduces to a summary at any age.
PROPOSAL_STATUS_KINDS = frozenset({"proposal.submit", "proposal.approve", "proposal.reject", "proposal.withdraw",
                                   "proposal.reopen"})
# The roadmap events whose stored payloads may hold a whole step list, and for each the field an
# archive, recovery or move changes (a revise keeps only its KEPT fields).
ROADMAP_PLAN_KINDS: dict[str, str | None] = {"roadmap.revise": None, "roadmap.archive": "archived",
                                             "roadmap.recover": "archived", "roadmap.move": "parentId"}
VERSIONS_KEPT = 20
# The pre-compaction backups kept: those of the newest compactions that finished.
BACKUPS_KEPT = 3
# The families of kinds, by the part of a kind's name before its first dot, whose payloads only show
# in history: work items and the day's plan, supporting records (work item checklists' tasks among
# them, which databases from before v26 recorded), and roadmaps with their steps, progress and
# imports (the HTML plan import, which databases from before v25 recorded). Every kind
# of a listed family is thinned, a new one too, so a payload of these families must never hold
# state: state goes in columns (CONSOLIDATION.md's *Rules*). A kind of any other family keeps its
# payloads whole.
THINNED = frozenset({"item", "entry", "day", "backlog", "note", "task", "link", "github", "reference",
                     "question", "decision", "attachment", "step", "roadmap", "progress", "import"})
KEPT = ("id", "title", "status", "_statusChanged")
SIDECARS = ("-journal", "-wal", "-shm")
# Each roadmap's versions after its VERSIONS_KEPT newest, whatever gaps its numbers have.
OLD_VERSIONS = ("DELETE FROM roadmap_versions WHERE rowid IN (SELECT version_row FROM ("
                "SELECT rowid AS version_row,row_number() OVER (PARTITION BY roadmap_id ORDER BY number DESC) AS place "
                "FROM roadmap_versions) WHERE place>?)")


def thinned(payload: Any) -> Any:
    """An event payload as compaction leaves it: an object's KEPT fields, and null for anything else."""
    if isinstance(payload, dict):
        return {key: payload[key] for key in KEPT if key in payload}
    return None


def proposal_summary(payload: Any) -> Any:
    """A stored proposal record as proposals.summary gives it, from whichever fields it holds: an
    event written by an older version may lack some. A summary already is one; anything else stays."""
    if not isinstance(payload, dict) or "operations" not in payload:
        return payload
    operations = payload["operations"]
    return {"id": payload.get("id"), "title": payload.get("title"), "status": payload.get("status"),
            "revision": payload.get("revision"), "submittedAt": payload.get("submittedAt"),
            "autoApproved": payload.get("autoApproved"),
            "operationCount": len(operations) if isinstance(operations, list) else None}


def roadmap_fields(kind: str, payload: Any) -> Any:
    """A stored roadmap event's payload without its step list, as plans.py writes the kind today."""
    if not isinstance(payload, dict) or "steps" not in payload:
        return payload
    changed = ROADMAP_PLAN_KINDS[kind]
    if changed is None:
        return thinned(payload)
    return {"id": payload.get("id"), "title": payload.get("title"), changed: payload.get(changed),
            "revision": payload.get("revision")}


def reduced_proposal(kind: str, before: Any, after: Any) -> tuple[Any, Any]:
    """A proposal event's payloads as compaction leaves them: a status change summarised on both sides,
    an edit on its before-side only."""
    if kind == "proposal.update":
        return proposal_summary(before), after
    return proposal_summary(before), proposal_summary(after)


def reduced_roadmap(kind: str, before: Any, after: Any) -> tuple[Any, Any]:
    return roadmap_fields(kind, before), roadmap_fields(kind, after)


def compact_database(db_path: Path | str, backup_dir: Path | str, *, now: datetime | None = None) -> dict[str, Any]:
    """Compact the stopped database at db_path, as the module says, and report what went."""
    target, backups = Path(db_path).resolve(), Path(backup_dir)
    moment = now or datetime.now(UTC)
    with InstanceLock(target.parent):
        # A run killed part way leaves its copy behind; the lock keeps any other run out.
        for leftover in target.parent.glob(".compact-*.sqlite3*"):
            leftover.unlink(missing_ok=True)
        _check_offline(target)
        validate_database(target)
        with closing(_readonly(target)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise ValueError(f"The database is at schema version {version}: start Worklog once to upgrade it "
                             f"to version {SCHEMA_VERSION}, stop it, then compact")
        size = target.stat().st_size
        backup = backup_database(target, backups, "pre-compact")
        descriptor, name = tempfile.mkstemp(prefix=".compact-", suffix=".sqlite3", dir=target.parent)
        os.close(descriptor)
        stage = Path(name)
        swapped = False
        try:
            _snapshot(target, stage)
            counts = _compact(stage, moment)
            validate_database(stage)
            with stage.open("r+b") as stream:
                os.fsync(stream.fileno())
            compacted = stage.stat().st_size
            _check_offline(target)
            try:
                os.replace(stage, target)
            except PermissionError as exc:
                raise RuntimeError("The database is open in another program, so it was not changed: close "
                                   "that program, then compact again") from exc
            swapped = True
        finally:
            stage.unlink(missing_ok=True)
            # The copy's own rollback journal, left by a failure while the copy was written.
            stage.with_name(stage.name + "-journal").unlink(missing_ok=True)
            if not swapped:
                # The database is as it was, so this run's backup only repeats it. Taking it away keeps
                # failed runs from pushing out the backup of a compaction that finished.
                (backups / backup["name"]).unlink(missing_ok=True)
        try:
            validate_database(target)
        except ValueError as exc:
            raise RuntimeError(f"The compacted database failed validation ({exc}); restore {backup['name']} "
                               "from the backups folder") from exc
        prune_backups(backups, "pre-compact", BACKUPS_KEPT)
    return {"compacted": target.name, **counts, "bytesBefore": size, "bytesAfter": compacted,
            "bytesReclaimed": size - compacted, "preCompactBackup": backup}


def _check_offline(target: Path) -> None:
    """Refuse a database another process holds, or one beside an unfinished write or restore."""
    _assert_database_offline(target, "compacting")
    for suffix in SIDECARS:
        if target.with_name(target.name + suffix).exists():
            raise RuntimeError(f"{target.name}{suffix} is beside the database: a connection is open, or one "
                               "ended without finishing its write. Close every connection; if the file stays, "
                               "start Worklog once, stop it, then compact")
    if (target.parent / ".restore-in-progress.json").exists():
        raise RuntimeError("An offline restore was interrupted: finish it with restore before compacting")


def _compact(stage: Path, moment: datetime) -> dict[str, int]:
    """Let go of what the module says in one transaction on the copy, then vacuum it."""
    with closing(sqlite3.connect(stage, isolation_level=None)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            proposals, proposal_bytes = _rewrite_events(conn, PROPOSAL_STATUS_KINDS | {"proposal.update"},
                                                        reduced_proposal)
            roadmaps, roadmap_bytes = _rewrite_events(conn, frozenset(ROADMAP_PLAN_KINDS), reduced_roadmap)
            history, history_bytes = _thin_events(conn, (moment - timedelta(days=HISTORY_DAYS)).isoformat())
            evidence, evidence_bytes = _empty_evidence(conn, (moment - timedelta(days=EVIDENCE_DAYS)).isoformat())
            counts = {
                "proposalEventsReduced": proposals, "proposalBytesReclaimed": proposal_bytes,
                "roadmapEventsReduced": roadmaps, "roadmapBytesReclaimed": roadmap_bytes,
                "eventsThinned": history, "eventsThinnedBytes": history_bytes,
                "evidenceEmptied": evidence, "evidenceBytesReclaimed": evidence_bytes,
                "versionsRemoved": conn.execute(OLD_VERSIONS, (VERSIONS_KEPT,)).rowcount,
                "receiptsPruned": prune_receipts(conn, now=moment),
            }
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Compaction would break a reference, so the database was not changed")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        # SQLite documents that VACUUM may renumber the rowids of a table without an INTEGER PRIMARY
        # KEY, as events is. It keeps those of a table with an index, as events has, but history is
        # read in rowid order, so the order is checked rather than trusted.
        order = _event_order(conn)
        conn.execute("VACUUM")
        if _event_order(conn) != order:
            raise ValueError("VACUUM changed the order of events, so the database was not changed")
    return counts


def _event_order(conn: sqlite3.Connection) -> str:
    """A digest of the events' ids in rowid order, the order history is read in."""
    digest = hashlib.sha256()
    for (identity,) in conn.execute("SELECT id FROM events ORDER BY rowid"):
        digest.update(identity.encode("utf-8") + b"\0")
    return digest.hexdigest()


def _empty_evidence(conn: sqlite3.Connection, cutoff: str) -> tuple[int, int]:
    """Empty the evidence of proposals withdrawn or rejected before cutoff (their last change is the
    withdrawal or rejection: nothing changes a closed proposal); return how many files and bytes."""
    found = conn.execute(
        "SELECT a.id,length(a.content) FROM attachments a JOIN proposals p ON p.id=a.owner_id WHERE a.owner_type='proposal'"
        " AND p.status IN ('withdrawn','rejected') AND p.updated_at<? AND length(a.content)>0", (cutoff,)).fetchall()
    conn.executemany("UPDATE attachments SET content=X'' WHERE id=?", [(identity,) for identity, _ in found])
    return len(found), sum(size for _, size in found)


def _payload_bytes(*texts: str) -> int:
    return sum(len(text.encode("utf-8")) for text in texts)


def _rewrite_events(conn: sqlite3.Connection, kinds: frozenset[str],
                    rewrite: Callable[[str, Any, Any], tuple[Any, Any]]) -> tuple[int, int]:
    """Rewrite the payloads of every event of these kinds, whatever its age, with rewrite(kind, before,
    after); return how many events changed and the bytes of payload text they gave back."""
    changed, saved = [], 0
    marks = ",".join("?" for _ in kinds)
    for identity, kind, before_text, after_text in conn.execute(
            f"SELECT id,kind,before_json,after_json FROM events WHERE kind IN ({marks})", sorted(kinds)):
        try:
            before, after = json.loads(before_text), json.loads(after_text)
        except ValueError as exc:
            raise ValueError(f"Event {identity} holds a payload that is not JSON, so the database was "
                             "not changed") from exc
        kept = rewrite(kind, before, after)
        if kept != (before, after):
            # As common.event writes them.
            texts = json.dumps(kept[0]), json.dumps(kept[1])
            saved += _payload_bytes(before_text, after_text) - _payload_bytes(*texts)
            changed.append((*texts, identity))
    conn.executemany("UPDATE events SET before_json=?,after_json=? WHERE id=?", changed)
    return len(changed), saved


def _thin_events(conn: sqlite3.Connection, cutoff: str) -> tuple[int, int]:
    """Thin the display-only history written before cutoff; return how many events changed and the
    bytes of payload text they gave back.

    created_at holds common.now() text, which sorts in time order, as for receipts."""
    changed, saved = [], 0
    for identity, kind, before_text, after_text in conn.execute(
            "SELECT id,kind,before_json,after_json FROM events WHERE created_at<?", (cutoff,)):
        if kind.split(".", 1)[0] not in THINNED:
            continue
        try:
            before, after = json.loads(before_text), json.loads(after_text)
        except ValueError as exc:
            raise ValueError(f"Event {identity} holds a payload that is not JSON, so the database was "
                             "not changed") from exc
        kept = thinned(before), thinned(after)
        if kept != (before, after):
            # As common.event writes them.
            texts = json.dumps(kept[0]), json.dumps(kept[1])
            saved += _payload_bytes(before_text, after_text) - _payload_bytes(*texts)
            changed.append((*texts, identity))
    conn.executemany("UPDATE events SET before_json=?,after_json=? WHERE id=?", changed)
    return len(changed), saved
