"""SQLite connection and transaction boundary."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from .common import AppError, uid
from .schema import BASELINE

T = TypeVar("T")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def initialize(self) -> None:
        """Open the file, creating a new one at the current version from schema.BASELINE. An existing
        file is checked first, and one at an older version is backed up and upgraded in one transaction."""
        from .migrations import SCHEMA_VERSION, UPGRADABLE_VERSIONS, upgrade

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if (self.path.parent / ".restore-in-progress.json").exists():
            raise AppError(503, "restore_interrupted", "An offline restore was interrupted. Preserve the recovery evidence and rerun restore with a verified backup before starting Worklog.")
        existing = self.path.exists()
        if existing and self.path.stat().st_size == 0:
            raise AppError(503, "database_invalid", "Existing database is empty; restore a verified backup")
        with self.read() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if existing:
                if version not in UPGRADABLE_VERSIONS | {SCHEMA_VERSION}:
                    raise AppError(503, "schema_version", "Unsupported database schema; no data was changed")
                if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise AppError(503, "database_invalid", "Database integrity check failed")
                required = {"settings", "work_items", "days", "daily_entries", "notes", "file_references", "attachments", "credentials", "roadmaps", "steps", "proposals", "events", "receipts", "search_index", "roadmap_versions"}
                actual = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not required <= actual:
                    raise AppError(503, "database_invalid", "Database schema is incomplete")
                from .recovery import validate_database

                try:
                    validate_database(self.path)
                except ValueError as exc:
                    raise AppError(503, "database_invalid", str(exc)) from exc
                if version != SCHEMA_VERSION:
                    # Preserve the original shape, including older supported upgrades.
                    # Failure to preserve a snapshot prevents any migration below.
                    from .recovery import backup_database

                    backup_database(self.path, self.path.parent / "backups", "pre-migration")
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    upgrade(conn)
                    if conn.execute("PRAGMA foreign_key_check").fetchone():
                        raise AppError(503, "database_invalid", "Database has invalid references")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                return
            try:
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.executescript("BEGIN IMMEDIATE;\n" + BASELINE)
                for key, value in {"timezone": "America/Chicago", "dataId": uid(), "backlogRevision": 1}.items():
                    conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value)))
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def snapshot(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Return fn's result, everything it read taken from one snapshot of the database.

        A deferred BEGIN holds a shared lock until the connection closes, which ends the read
        transaction with nothing committed: a write fn makes is discarded, so only reads belong here.
        """
        with self.read() as conn:
            conn.execute("BEGIN")
            return fn(conn)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.read() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except sqlite3.OperationalError as exc:
                conn.rollback()
                if "locked" in str(exc) or "busy" in str(exc):
                    raise AppError(503, "database_busy", "Database is busy; retry this change") from exc
                raise
            except Exception:
                conn.rollback()
                raise
