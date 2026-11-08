"""Offline compaction, `worklog compact` (consolidation step 4.3, K6; B-04, B-12, REM-19, REM-20).

Worklog keeps every record and every event row. Compaction lets go of what K6 decided it may:

- The payloads of display-only history older than HISTORY_DAYS. An event of a THINNED kind keeps
  the KEPT fields of its before and after records: Today's completed and blocked lists, and each
  day entry's status on its day, replay item events by them. Any other payload becomes null.
  Every other kind keeps its payloads whole, since they hold state, are read at runtime, or are
  history the guides promise (CONSOLIDATION.md's *Rules*): credentials, proposals, chats,
  discussions, resources, releases, settings and the rest. No event row goes:
  discussion_changes names events, and history is read in the order it was written, by rowid,
  which VACUUM keeps.
- Each roadmap's versions but its VERSIONS_KEPT newest.
- Plan imports never committed to a roadmap, IMPORT_DAYS after their preview.
- Idempotency receipts past their retention (maintenance.prune_receipts).

VACUUM then hands the space back. Compaction runs while Worklog is stopped, under the server's
lock, after a pre-compaction backup. It works on a copy beside the database: the copy is
compacted, vacuumed and validated, and only then renamed into the database's place, so a failure
before that rename leaves the database as it was.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
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
    validate_database,
)

HISTORY_DAYS = 90
VERSIONS_KEPT = 20
IMPORT_DAYS = 7
# The kinds, by the part of their name before the first dot, whose payloads only show in history:
# work items and the day's plan, supporting records, and roadmaps with their steps, progress and
# imports. A kind not listed keeps its payloads whole, so a new kind is kept until it is listed.
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


def compact_database(db_path: Path | str, backup_dir: Path | str, *, now: datetime | None = None) -> dict[str, Any]:
    """Compact the stopped database at db_path, as the module says, and report what went."""
    target = Path(db_path).resolve()
    moment = now or datetime.now(UTC)
    with InstanceLock(target.parent):
        _check_offline(target)
        validate_database(target)
        with closing(_readonly(target)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise ValueError(f"The database is at schema version {version}: start Worklog once to upgrade it "
                             f"to version {SCHEMA_VERSION}, stop it, then compact")
        size = target.stat().st_size
        backup = backup_database(target, backup_dir, "pre-compact")
        descriptor, name = tempfile.mkstemp(prefix=".compact-", suffix=".sqlite3", dir=target.parent)
        os.close(descriptor)
        stage = Path(name)
        try:
            _snapshot(target, stage)
            counts = _compact(stage, moment)
            validate_database(stage)
            with stage.open("r+b") as stream:
                os.fsync(stream.fileno())
            compacted = stage.stat().st_size
            _check_offline(target)
            os.replace(stage, target)
        finally:
            stage.unlink(missing_ok=True)
            # The copy's own rollback journal, left by a failure while the copy was written.
            stage.with_name(stage.name + "-journal").unlink(missing_ok=True)
        try:
            validate_database(target)
        except ValueError as exc:
            raise RuntimeError(f"The compacted database failed validation ({exc}); restore {backup['name']} "
                               "from the backups folder") from exc
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
            counts = {
                "eventsThinned": _thin_events(conn, (moment - timedelta(days=HISTORY_DAYS)).isoformat()),
                "versionsRemoved": conn.execute(OLD_VERSIONS, (VERSIONS_KEPT,)).rowcount,
                "importsPurged": conn.execute(
                    "DELETE FROM plan_imports WHERE roadmap_id IS NULL AND created_at<?",
                    ((moment - timedelta(days=IMPORT_DAYS)).isoformat(),)).rowcount,
                "receiptsPruned": prune_receipts(conn, now=moment),
            }
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Compaction would break a reference, so the database was not changed")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        conn.execute("VACUUM")
    return counts


def _thin_events(conn: sqlite3.Connection, cutoff: str) -> int:
    """Thin the display-only history written before cutoff; return how many events changed.

    created_at holds common.now() text, which sorts in time order, as for receipts."""
    changed = []
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
            changed.append((json.dumps(kept[0]), json.dumps(kept[1]), identity))
    conn.executemany("UPDATE events SET before_json=?,after_json=? WHERE id=?", changed)
    return len(changed)
