"""Schema v14 gave a work item's tasks an order of their own. Work item checklists are gone since v26
(step 0.3 of docs/ROADMAPS-RETHINK.md), so only the way back to v13 and its shape checks stay here:
older upgrade tests build their databases with to_v13."""

import sqlite3
from contextlib import closing

import pytest
import sealed
from worklog import migrations, recovery
from worklog.db import Database

# From version 14 to version 13, with Worklog stopped and after a manual backup. The README gave this way back
# while version 14 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
BEGIN;
ALTER TABLE tasks DROP COLUMN position;
PRAGMA user_version=13;
COMMIT;
"""


def to_v13(path):
    """Take a current database back to the v13 shape the documented way: to version 14 first."""
    from test_waiting_status import to_v14

    to_v14(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def test_the_way_back_to_v13_gives_its_exact_shape_and_upgrades_again(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    to_v13(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        assert recovery._schema_signature(conn) == sealed.shape(13)
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()


def test_a_v13_shape_cannot_claim_version_14_and_a_v14_shape_cannot_claim_13(tmp_path):
    from test_waiting_status import to_v14

    older, current = tmp_path / "older.sqlite3", tmp_path / "current.sqlite3"
    Database(older).initialize()
    to_v13(older)
    recovery.validate_database(older)
    Database(current).initialize()
    to_v14(current)
    recovery.validate_database(current)
    for path, version in ((older, 14), (current, 13)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
