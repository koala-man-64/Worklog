"""Schema v4 added messages to a chat's agents and their deliveries, which v30 drops again with
messaging (step 0.7 of docs/ROADMAPS-RETHINK.md). What stays: the upgrade from v3, which adds the
tables and then drops them, its rollback, and the shapes of v3 and v4, which can't claim each
other's version."""

import sqlite3
from contextlib import closing

import pytest
import sealed
from worklog import migrations, recovery
from worklog.db import Database

STAMP = "2026-01-01T00:00:00+00:00"


def v3_database(path):
    """The shape main shipped as v3: avatars, but no messages to chats."""
    sealed.build(path, 3)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO credentials VALUES('credential','Agent','hash-agent',?,0)", (STAMP,))
        conn.execute("INSERT INTO communication_agents(id,label,created_at,avatar) VALUES('credential','Agent',?,'teal-dots-antenna')", (STAMP,))
        conn.execute("INSERT INTO communication_credentials VALUES('credential','credential')")
        conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES('item','Work',0,?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO agent_chats(id,owner_type,owner_id,chat_id,title,created_date,created_at,updated_at) VALUES('chat','item','item','session-1','Chat','2026-01-01',?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO agent_runs(id,agent_id,credential_id,client_run_key,display_name,external_chat_id,created_at,updated_at) VALUES('run','credential','credential','key','Run','session-1',?,?)", (STAMP, STAMP))
        conn.commit()
    recovery.validate_database(path)


def conn_rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def test_the_v3_upgrade_snapshots_first_keeps_every_record_and_restores(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    v3_database(path)
    before = {t: conn_rows(path, t) for t in ("credentials", "communication_agents", "agent_chats", "agent_runs")}
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        # v4 adds the messages and their deliveries, and v30 drops them again.
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('chat_messages','chat_message_deliveries')").fetchone()[0] == 0
    assert {t: conn_rows(path, t) for t in before} == before
    snapshots = list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert recovery._schema_signature(conn) == sealed.shape(3)
    restored = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(restored, snapshots[0], tmp_path / "restore-backups")
    with Database(restored).read() as conn:
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("SELECT end_reason FROM agent_runs").fetchone()[0] == "restore"


def test_failed_message_schema_rolls_back_the_v3_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    v3_database(path)
    execute = migrations.execute_schema

    def fail_after_messages(conn, schema):
        execute(conn, schema)
        if schema == migrations.CHAT_MESSAGE_SCHEMA:
            raise RuntimeError("Injected message schema failure")

    monkeypatch.setattr(migrations, "execute_schema", fail_after_messages)
    with pytest.raises(RuntimeError, match="Injected"):
        Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert recovery._schema_signature(conn) == sealed.shape(3)
    recovery.validate_database(path)


def test_a_v3_shape_cannot_claim_version_4_and_a_v4_shape_cannot_claim_3(tmp_path):
    older = tmp_path / "older.sqlite3"
    v3_database(older)
    current = tmp_path / "current.sqlite3"
    Database(current).initialize()
    for path, version in ((older, 4), (current, 3)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
