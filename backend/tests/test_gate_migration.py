"""Schema v40 adds an empty step_gates table without changing existing records."""

import sqlite3
from contextlib import closing

import pytest
import sealed
from test_repo_execution_schema import to_v39
from test_sealed_schemas import contents
from worklog.db import Database
from worklog.recovery import validate_database


def test_v39_upgrade_adds_step_gates_table_and_preserves_existing_records(tmp_path):
    db_path = tmp_path / "upgrade-v39.sqlite3"
    sealed.build(db_path, 39)
    stamp = "2026-11-28T10:00:00+00:00"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO roadmaps(id,title,overview,revision,archived,created_at,updated_at,parent_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("roadmap-a", "Plan A", "Overview A", 3, 0, stamp, stamp, None),
        )
        conn.execute(
            "INSERT INTO steps(id,roadmap_id,title,description,parent_id,position,status,revision,retired,outcome,work_class) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("step-a", "roadmap-a", "Build", "Build the release", None, 0, "doing", 2, 0,
             "A release archive", "standard"),
        )
        conn.execute(
            "INSERT INTO deliverables(id,roadmap_id,title,definition,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("deliv-a", "roadmap-a", "Archive", '{"requirements":[]}', stamp, stamp),
        )
        conn.commit()

    before_version, before_shape, before = contents(db_path)
    assert (before_version, before_shape) == (39, sealed.shape(39))
    validate_database(db_path)

    Database(db_path).initialize()

    after_version, after_shape, after = contents(db_path)
    assert (after_version, after_shape) == (40, sealed.current())
    assert set(after) - set(before) == {"step_gates"}
    assert after["step_gates"] == []
    assert {table: rows for table, rows in after.items() if table != "step_gates"} == before
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    validate_database(db_path)


def test_v40_fixture_projection_refuses_to_drop_gate_data(tmp_path):
    db_path = tmp_path / "fixture-v40.sqlite3"
    Database(db_path).initialize()
    stamp = "2026-11-28T10:00:00+00:00"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO roadmaps(id,title,overview,revision,archived,created_at,updated_at,parent_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("roadmap-a", "Plan A", "", 1, 0, stamp, stamp, None),
        )
        conn.execute(
            "INSERT INTO steps(id,roadmap_id,title,description,parent_id,position,status,revision,retired,outcome,work_class) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("step-a", "roadmap-a", "Build", "", None, 0, "open", 1, 0, "", "standard"),
        )
        conn.execute(
            "INSERT INTO step_gates(id,step_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("gate-a", "step-a", "Security review", stamp, stamp),
        )
        conn.commit()

    with pytest.raises(ValueError, match="step_gates table must be empty"):
        to_v39(db_path)

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 40
        assert conn.execute("SELECT id FROM step_gates").fetchall() == [("gate-a",)]
