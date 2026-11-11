"""Schema v20 added reminders, which v27 drops again (step 0.4 of docs/ROADMAPS-RETHINK.md). What stays:
the upgrade from v19, the way back to it, which older upgrade tests build their databases with, and
that neither shape can pass for the other."""

import sqlite3
from contextlib import closing

import pytest
import sealed
from test_reminder_removal import remind
from test_sealed_schemas import contents
from worklog import migrations, recovery
from worklog.db import Database

STAMP = "2026-11-06T14:00:00+00:00"
# From version 20 to version 19, with Worklog stopped and after a manual backup. The README gave this way back
# while version 20 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
BEGIN;
DELETE FROM search_index WHERE rowid IN (SELECT doc FROM search_keys WHERE type='reminder');
DELETE FROM search_keys WHERE type='reminder';
DROP TABLE reminders;
PRAGMA user_version=19;
COMMIT;
"""


def to_v20(path):
    """Take a current database back to the v20 shape the documented way."""
    from test_reflection_revision import to_v20 as from_v21

    from_v21(path)


def to_v19(path):
    """Take a current database back to the v19 shape the documented way: to version 20 first."""
    to_v20(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


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


def test_the_upgrade_from_v19_snapshots_first_and_changes_no_record(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    add_item(path)
    to_v19(path)
    recovery.validate_database(path)
    _, _, before = contents(path)
    Database(path).initialize()
    version, signature, after = contents(path)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    # v20 adds the reminders table and v27 drops it again; v25, v26, v28 and v29 drop the plan
    # import, task, release and ties tables, empty here.
    assert "reminders" not in after
    assert before.pop("plan_imports") == []
    assert before.pop("tasks") == []
    for table in ("releases", "release_contents", "release_checks", "agent_links"):
        assert before.pop(table) == []
    assert after == before
    snapshots = list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 19
    recovery.validate_database(path)


def test_the_way_back_to_v19_gives_its_exact_shape_and_upgrades_again(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    add_item(path)
    to_v20(path)
    # Reminders as v20 kept them, each with its search row and key.
    with closing(sqlite3.connect(path)) as conn:
        remind(conn, "Merge #113", "Once CI is green")
        remind(conn, "Renew the certificate", "Before Friday")
        conn.commit()
    assert count(path, "SELECT count(*) FROM search_keys WHERE type='reminder'") == 2
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)
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


def test_a_v19_shape_cannot_claim_version_20_and_a_v20_shape_cannot_claim_19(tmp_path):
    older, newer = tmp_path / "older.sqlite3", tmp_path / "newer.sqlite3"
    for path in (older, newer):
        Database(path).initialize()
    to_v19(older)
    to_v20(newer)
    for path in (older, newer):
        recovery.validate_database(path)
    for path, claimed in ((older, 20), (newer, 19)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={claimed}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
