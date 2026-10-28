"""SQLite connection and transaction boundary."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .common import AppError, uid

SCHEMA = """
CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE credentials(id TEXT PRIMARY KEY,label TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE work_items(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','doing','blocked','done')),ado_url TEXT,step_id TEXT REFERENCES steps(id),backlog_order INTEGER NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE days(date TEXT PRIMARY KEY,reflection TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE daily_entries(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),date TEXT NOT NULL REFERENCES days(date),planned INTEGER NOT NULL DEFAULT 1 CHECK(planned IN(0,1)),worked INTEGER NOT NULL DEFAULT 0 CHECK(worked IN(0,1)),outcome TEXT NOT NULL DEFAULT '',position INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 1,UNIQUE(item_id,date));
CREATE INDEX daily_by_date ON daily_entries(date,position);
CREATE TABLE notes(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL,owner_id TEXT NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE tasks(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),text TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0 CHECK(done IN(0,1)),archived INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE file_references(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),path TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE TABLE attachments(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL,owner_id TEXT NOT NULL,name TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,content BLOB NOT NULL,archived INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE INDEX notes_owner ON notes(owner_type,owner_id,archived);
CREATE INDEX files_owner ON attachments(owner_type,owner_id,archived);
CREATE TABLE events(id TEXT PRIMARY KEY,entity_id TEXT NOT NULL,kind TEXT NOT NULL,actor_id TEXT NOT NULL,actor_label TEXT NOT NULL,actor_kind TEXT NOT NULL,created_at TEXT NOT NULL,local_date TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL);
CREATE INDEX event_entity ON events(entity_id,created_at);
CREATE INDEX event_day ON events(local_date,kind);
CREATE TABLE receipts(actor_id TEXT NOT NULL,key TEXT NOT NULL,request_hash TEXT NOT NULL,response_json TEXT NOT NULL,PRIMARY KEY(actor_id,key));
CREATE VIRTUAL TABLE search_index USING fts5(entity_id UNINDEXED,type UNINDEXED,title,body,owner_id UNINDEXED,archived UNINDEXED,tokenize='unicode61');
"""


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
        from .plans import SCHEMA as PLAN_SCHEMA
        from .proposals import SCHEMA as PROPOSAL_SCHEMA

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if (self.path.parent / ".restore-in-progress.json").exists():
            raise AppError(503, "restore_interrupted", "An offline restore was interrupted. Preserve the recovery evidence and rerun restore with a verified backup before starting Worklog.")
        existing = self.path.exists()
        if existing and self.path.stat().st_size == 0:
            raise AppError(503, "database_invalid", "Existing database is empty; restore a verified backup")
        with self.read() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if existing:
                if version != 1:
                    raise AppError(503, "schema_version", "Unsupported database schema; no data was changed")
                if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise AppError(503, "database_invalid", "Database integrity check failed")
                required = {"settings", "work_items", "days", "daily_entries", "notes", "tasks", "file_references", "attachments", "credentials", "roadmaps", "steps", "proposals", "events", "receipts", "search_index", "roadmap_versions", "plan_imports"}
                actual = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not required <= actual:
                    raise AppError(503, "database_invalid", "Database schema is incomplete")
                if conn.execute("PRAGMA foreign_key_check").fetchone():
                    raise AppError(503, "database_invalid", "Database has invalid references")
                return
            try:
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.executescript("BEGIN IMMEDIATE;\n" + PLAN_SCHEMA + PROPOSAL_SCHEMA + SCHEMA)
                for key, value in {"timezone": "America/Chicago", "dataId": uid(), "backlogRevision": 1}.items():
                    conn.execute("INSERT INTO settings VALUES(?,?)", (key, json.dumps(value)))
                conn.execute("PRAGMA user_version=1")
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
