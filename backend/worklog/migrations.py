"""Shared, transaction-owned upgrades for startup and offline restoration."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2

COORDINATION_SCHEMA = """
CREATE TABLE repositories(id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX repositories_url ON repositories(url) WHERE url<>'';
CREATE TABLE item_repositories(item_id TEXT NOT NULL REFERENCES work_items(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(item_id,repository_id));
CREATE INDEX item_repositories_repository ON item_repositories(repository_id,item_id);
CREATE TABLE chat_repositories(chat_id TEXT NOT NULL REFERENCES agent_chats(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(chat_id,repository_id));
CREATE INDEX chat_repositories_repository ON chat_repositories(repository_id,chat_id);
CREATE TABLE agent_runs(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL REFERENCES communication_agents(id),credential_id TEXT NOT NULL REFERENCES credentials(id),client_run_key TEXT NOT NULL,display_name TEXT NOT NULL,provider TEXT NOT NULL DEFAULT '',external_chat_id TEXT,revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,ended_at TEXT,end_reason TEXT,UNIQUE(credential_id,client_run_key));
CREATE INDEX agent_runs_credential ON agent_runs(credential_id,ended_at);
CREATE TABLE participations(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES agent_runs(id),item_id TEXT REFERENCES work_items(id),description TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,released_at TEXT,release_reason TEXT);
CREATE INDEX participations_run ON participations(run_id,released_at);
CREATE INDEX participations_item ON participations(item_id,released_at);
CREATE TABLE participation_contexts(participation_id TEXT NOT NULL REFERENCES participations(id),position INTEGER NOT NULL CHECK(position>=0),repository_id TEXT NOT NULL REFERENCES repositories(id),checkout_path TEXT NOT NULL DEFAULT '',worktree_path TEXT NOT NULL DEFAULT '',branch TEXT NOT NULL DEFAULT '',PRIMARY KEY(participation_id,position));
CREATE INDEX participation_contexts_repository ON participation_contexts(repository_id,participation_id);
CREATE TABLE coordination_updates(id TEXT PRIMARY KEY,participation_id TEXT NOT NULL REFERENCES participations(id),run_id TEXT NOT NULL REFERENCES agent_runs(id),agent_id TEXT NOT NULL REFERENCES communication_agents(id),credential_id TEXT NOT NULL REFERENCES credentials(id),author_label TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN('update','blocker','handoff')),text TEXT NOT NULL,item_id TEXT REFERENCES work_items(id),contexts_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX coordination_updates_participation ON coordination_updates(participation_id,created_at,id);
CREATE INDEX coordination_updates_item ON coordination_updates(item_id,created_at,id);
CREATE INDEX coordination_updates_created ON coordination_updates(created_at,id);
CREATE TABLE coordination_update_repositories(update_id TEXT NOT NULL REFERENCES coordination_updates(id),repository_id TEXT NOT NULL REFERENCES repositories(id),PRIMARY KEY(update_id,repository_id));
CREATE INDEX coordination_update_repositories_repository ON coordination_update_repositories(repository_id,update_id);
"""


def execute_schema(conn: sqlite3.Connection, schema: str) -> None:
    """Unlike executescript, preserve the caller's transaction and rollback boundary."""
    for statement in filter(str.strip, schema.split(";")):
        conn.execute(statement)


def upgrade(conn: sqlite3.Connection) -> None:
    """Upgrade an accepted v1 shape atomically inside the caller's transaction.

    Credential IDs, proposal authors, receipts, and legacy chat repository labels
    are deliberately unchanged. No repository names are guessed or imported.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version != 1:
        raise ValueError(f"Unsupported database schema version {version}")
    from .db import (
        AGENT_CHAT_COLUMNS,
        CHAT_STEPS_SCHEMA,
        LINKS_CHATS_GITHUB_SCHEMA,
        PEOPLE_SCHEMA,
        QUESTIONS_DECISIONS_SCHEMA,
    )
    from .discussions import migrate as migrate_discussions

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(work_items)")}
    if "roadmap_id" not in columns:
        conn.execute("ALTER TABLE work_items ADD COLUMN roadmap_id TEXT REFERENCES roadmaps(id)")
        conn.execute("UPDATE work_items SET roadmap_id=(SELECT roadmap_id FROM steps WHERE steps.id=work_items.step_id) WHERE step_id IS NOT NULL AND roadmap_id IS NULL")
    if not {"people", "person_links"} <= tables:
        execute_schema(conn, PEOPLE_SCHEMA)
    if not {"web_links", "agent_chats", "github_refs"} <= tables:
        execute_schema(conn, LINKS_CHATS_GITHUB_SCHEMA)
    chat_columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_chats)")}
    if "provider" not in chat_columns:
        for column in AGENT_CHAT_COLUMNS.split(", ")[1:]:
            conn.execute("ALTER TABLE agent_chats ADD COLUMN " + column)
    if "chat_steps" not in tables:
        execute_schema(conn, CHAT_STEPS_SCHEMA)
    if "parent_id" not in {row[1] for row in conn.execute("PRAGMA table_info(roadmaps)")}:
        conn.execute("ALTER TABLE roadmaps ADD COLUMN parent_id TEXT REFERENCES roadmaps(id)")
    if not {"roadmap_questions", "roadmap_decisions"} <= tables:
        execute_schema(conn, QUESTIONS_DECISIONS_SCHEMA)
    migrate_discussions(conn)
    execute_schema(conn, COORDINATION_SCHEMA)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def invalidate_restored_presence(conn: sqlite3.Connection, stamp: str) -> None:
    """Retain reports while making restored runs and participation visibly ended."""
    conn.execute("UPDATE participations SET released_at=?,release_reason='restore',updated_at=?,revision=revision+1 WHERE released_at IS NULL", (stamp, stamp))
    conn.execute("UPDATE agent_runs SET ended_at=?,end_reason='restore',updated_at=?,revision=revision+1 WHERE ended_at IS NULL", (stamp, stamp))
