"""Schema v20 adds reminders: the upgrade, the documented way back, restore and export keep them whole."""

import io
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
import sealed
from test_sealed_schemas import contents
from worklog import migrations, recovery
from worklog.db import Database
from worklog.reminders import ReminderService

STAMP = "2026-11-06T14:00:00+00:00"
PERSON = {"id": "local-user", "label": "You", "kind": "human"}
# README: from version 20 to version 19, with Worklog stopped and after a manual backup.
WAY_BACK = """\
BEGIN;
DELETE FROM search_index WHERE rowid IN (SELECT doc FROM search_keys WHERE type='reminder');
DELETE FROM search_keys WHERE type='reminder';
DROP TABLE reminders;
PRAGMA user_version=19;
COMMIT;
"""


def to_v19(path):
    """Take a current database back to the v19 shape the documented way: to version 20 first."""
    from test_discussion_tag_recovery import to_v20

    to_v20(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def add_reminder(path, title="Merge #113", notes="Once CI is green"):
    with Database(path).transaction() as conn:
        return ReminderService().create(conn, PERSON, {"title": title, "notes": notes, "date": "2026-11-06", "time": "14:25"})


def add_item(path):
    """A work item and its search row, which the way back leaves alone."""
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES('item','Ship notes',0,?,?)", (STAMP, STAMP))
        doc = conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES('item','item','Ship notes','','item',0)").lastrowid
        conn.execute("INSERT INTO search_keys(entity_id,doc,type,owner_id) VALUES('item',?,'item','item')", (doc,))
        conn.commit()


def count(path, sql):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchone()[0]


def test_the_v20_upgrade_adds_empty_reminders_after_a_snapshot_and_changes_no_record(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    add_item(path)
    to_v19(path)
    recovery.validate_database(path)
    _, _, before = contents(path)
    Database(path).initialize()
    version, signature, after = contents(path)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    assert after.pop("reminders") == []
    assert after == before
    snapshots = list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 19
    recovery.validate_database(path)


def test_the_documented_way_back_to_v19_gives_its_exact_shape_and_upgrades_again(tmp_path):
    # The SQL tested here is the SQL the README gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    add_item(path)
    add_reminder(path)
    add_reminder(path, "Renew the certificate", "Before Friday")
    assert count(path, "SELECT count(*) FROM search_keys WHERE type='reminder'") == 2
    to_v19(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 19
        assert recovery._schema_signature(conn) == sealed.shape(19)
        # Every search row and key of a reminder goes, and only those.
        assert conn.execute("SELECT entity_id,type FROM search_keys").fetchall() == [("item", "item")]
        assert conn.execute("SELECT entity_id,type FROM search_index").fetchall() == [("item", "item")]
        assert conn.execute("SELECT count(*) FROM search_index WHERE search_index MATCH 'certificate'").fetchone()[0] == 0
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("SELECT count(*) FROM reminders").fetchone()[0] == 0


def test_restore_and_export_keep_every_reminder(tmp_path):
    source, target = tmp_path / "source" / "worklog.sqlite3", tmp_path / "target" / "worklog.sqlite3"
    Database(source).initialize()
    reminder = add_reminder(source)
    Database(target).initialize()
    recovery.restore_database(target, source, tmp_path / "restore-backups")
    with closing(sqlite3.connect(target)) as conn:
        assert conn.execute("SELECT id,title,due_date,due_time,notes FROM reminders").fetchall() == [
            (reminder["id"], "Merge #113", "2026-11-06", "14:25", "Once CI is green")]
    archive = zipfile.ZipFile(io.BytesIO(recovery.export_database(target)))
    exported = json.loads(archive.read("records/reminders.json"))
    assert [(r["id"], r["title"], r["notes"]) for r in exported] == [(reminder["id"], "Merge #113", "Once CI is green")]


def test_a_v19_shape_cannot_claim_version_20_and_a_v20_shape_cannot_claim_19(tmp_path):
    older, current = tmp_path / "older.sqlite3", tmp_path / "current.sqlite3"
    Database(older).initialize()
    to_v19(older)
    recovery.validate_database(older)
    Database(current).initialize()
    recovery.validate_database(current)
    for path, claimed in ((older, 20), (current, 19)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={claimed}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
