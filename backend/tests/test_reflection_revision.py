"""Schema v21 gives each day's reflection its own revision (consolidation step 4.1, REM-26).

The upgrade starts each reflection at its day's revision and changes no record, and the way back
gives v20's exact shape. The reflection itself is gone since step 0.2 of docs/ROADMAPS-RETHINK.md
(decision R4), but its two columns stay, so both still hold, and older upgrade tests build their
databases with to_v20."""

import sqlite3
from contextlib import closing

import sealed
from test_sealed_schemas import contents
from worklog import migrations, recovery
from worklog.db import Database

# From version 21 to version 20, with Worklog stopped and after a manual backup. The README gave this way back
# while version 21 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
BEGIN;
ALTER TABLE days DROP COLUMN reflection_revision;
PRAGMA user_version=20;
COMMIT;
"""


def to_v20(path):
    """Take a current database back to the v20 shape the documented way: to version 21 first."""
    from test_discussion_tag_recovery import to_v21

    to_v21(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def test_the_v21_upgrade_starts_each_reflection_at_its_days_revision_and_changes_no_record(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO days(date,reflection,revision) VALUES('2026-11-04','Busy',5),('2026-11-05','',1)")
        conn.commit()
    to_v20(path)
    recovery.validate_database(path)
    _, _, before = contents(path)
    Database(path).initialize()
    version, signature, after = contents(path)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    # A draft opened before the upgrade carries its day's revision, which its reflection now starts at.
    assert after.pop("days") == [(*row, row[2]) for row in before.pop("days")]
    # v25, v26 and v27 drop the plan import, task and reminder tables, empty here.
    assert before.pop("plan_imports") == []
    assert before.pop("tasks") == []
    assert before.pop("reminders") == []
    assert after == before
    snapshot, = (tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20


def test_the_way_back_to_v20_gives_its_exact_shape_and_upgrades_again(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO days(date,reflection,revision,reflection_revision) VALUES('2026-11-04','Kept',3,9)")
        conn.commit()
    to_v20(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
        assert recovery._schema_signature(conn) == sealed.shape(20)
        assert conn.execute("SELECT date,reflection,revision FROM days").fetchall() == [("2026-11-04", "Kept", 3)]
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
