"""Schema v5 adds tickets: upgrade, rollback, restore and export keep them whole."""

import io
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest
import sealed
from worklog import migrations, recovery
from worklog.db import Database

STAMP = "2026-01-01T00:00:00+00:00"


def v4_database(path):
    """The shape main shipped as v4: messages to chats, but no tickets."""
    sealed.build(path, 4)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO credentials VALUES('credential','Agent','hash-agent',?,0)", (STAMP,))
        conn.execute("INSERT INTO communication_agents(id,label,created_at,avatar) VALUES('credential','Agent',?,'teal-dots-antenna')", (STAMP,))
        conn.execute("INSERT INTO communication_credentials VALUES('credential','credential')")
        conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES('item','Work',0,?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO agent_chats(id,owner_type,owner_id,chat_id,title,created_date,created_at,updated_at) VALUES('chat','item','item','session-1','Chat','2026-01-01',?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO chat_messages VALUES('message','chat','Hello','local-user','You',?)", (STAMP,))
        conn.commit()
    recovery.validate_database(path)


def rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def test_v4_upgrade_adds_tickets_after_a_snapshot_and_restores(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    v4_database(path)
    before = {t: rows(path, t) for t in ("credentials", "communication_agents", "work_items", "chat_messages")}
    # v4 already holds messages to chats, so the upgrade must not create them again.
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
    assert {t: rows(path, t) for t in before} == before
    snapshots = list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert recovery._schema_signature(conn) == sealed.shape(4)
    Database(path).initialize()
    assert len(list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))) == 1
    restored = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(restored, snapshots[0], tmp_path / "restore-backups")
    with Database(restored).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("SELECT text FROM chat_messages").fetchone()[0] == "Hello"


def test_failed_ticket_schema_rolls_back_the_v4_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    v4_database(path)
    execute = migrations.execute_schema

    def fail_after_tickets(conn, schema):
        execute(conn, schema)
        if schema == migrations.TICKET_SCHEMA:
            raise RuntimeError("Injected ticket schema failure")

    monkeypatch.setattr(migrations, "execute_schema", fail_after_tickets)
    with pytest.raises(RuntimeError, match="Injected"):
        Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert recovery._schema_signature(conn) == sealed.shape(4)
    recovery.validate_database(path)


def test_a_v4_shape_cannot_claim_version_5_and_a_v5_shape_cannot_claim_4(tmp_path):
    older = tmp_path / "older.sqlite3"
    v4_database(older)
    current = tmp_path / "current.sqlite3"
    Database(current).initialize()
    for path, version in ((older, 5), (current, 4)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)


def test_export_and_restore_keep_tickets_with_their_comments_labels_and_links(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    db = Database(path)
    db.initialize()
    with db.transaction() as conn:
        conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES('item','Work',0,?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO repositories(id,name,created_at,updated_at) VALUES('repo','Repository',?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO tickets(id,number,title,status,resolution,item_id,author_id,author_label,author_kind,created_at,updated_at,closed_at,last_activity_at) "
                     "VALUES('ticket',1,'Backup locked the database','done','Retry with a fresh snapshot.','item','local-user','You','human',?,?,?,?)", (STAMP, STAMP, STAMP, STAMP))
        conn.execute("INSERT INTO ticket_labels VALUES('ticket','sqlite')")
        conn.execute("INSERT INTO ticket_repositories VALUES('ticket','repo',?)", (STAMP,))
        conn.execute("INSERT INTO ticket_comments VALUES('comment','ticket','Seen twice during backup.','local-user','You','human',?)", (STAMP,))
    with zipfile.ZipFile(io.BytesIO(recovery.export_database(path))) as archive:
        exported = json.loads(archive.read("records/tickets.json"))
        assert [(t["number"], t["status"], t["resolution"]) for t in exported] == [(1, "done", "Retry with a fresh snapshot.")]
        assert json.loads(archive.read("records/ticket_comments.json"))[0]["text"] == "Seen twice during backup."
        assert json.loads(archive.read("records/ticket_labels.json")) == [{"ticket_id": "ticket", "label": "sqlite"}]
        assert json.loads(archive.read("records/ticket_repositories.json"))[0]["repository_id"] == "repo"
        assert "### Backup locked the database" in archive.read("worklog.md").decode("utf-8")
        assert json.loads(archive.read("manifest.json"))["schemaVersion"] == migrations.SCHEMA_VERSION
    backup = recovery.backup_database(path, tmp_path / "manual")
    target = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(target, tmp_path / "manual" / backup["name"], tmp_path / "restore-backups")
    for table in ("tickets", "ticket_comments", "ticket_labels", "ticket_repositories"):
        assert rows(target, table) == rows(path, table)
