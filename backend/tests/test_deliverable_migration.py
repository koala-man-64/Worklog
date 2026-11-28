"""Schema v39 adds empty deliverable tables without changing existing roadmap records."""

import sqlite3
from contextlib import closing

import pytest
import sealed
from test_repo_execution_schema import to_v38
from test_sealed_schemas import contents
from worklog.db import Database
from worklog.recovery import validate_database


def test_v38_upgrade_adds_deliverable_tables_and_preserves_existing_plan(tmp_path):
    db_path = tmp_path / "upgrade-v38.sqlite3"
    sealed.build(db_path, 38)
    stamp = "2026-11-27T12:00:00+00:00"
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
        conn.commit()

    before_version, before_shape, before = contents(db_path)
    assert (before_version, before_shape) == (38, sealed.shape(38))
    validate_database(db_path)

    Database(db_path).initialize()

    after_version, after_shape, after = contents(db_path)
    assert (after_version, after_shape) == (39, sealed.current())
    new_tables = {"deliverables", "deliverable_steps", "deliverable_acceptances"}
    assert set(after) - set(before) == new_tables
    assert all(after[table] == [] for table in new_tables)
    assert {table: rows for table, rows in after.items() if table not in new_tables} == before
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    validate_database(db_path)


def test_v39_fixture_projection_refuses_to_drop_deliverable_data(tmp_path):
    db_path = tmp_path / "fixture-v39.sqlite3"
    Database(db_path).initialize()
    stamp = "2026-11-27T12:00:00+00:00"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO roadmaps(id,title,overview,revision,archived,created_at,updated_at,parent_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("roadmap-a", "Plan A", "", 1, 0, stamp, stamp, None),
        )
        conn.execute(
            "INSERT INTO deliverables(id,roadmap_id,title,definition,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("deliverable-a", "roadmap-a", "Package", '{"requirements":[]}', stamp, stamp),
        )
        conn.commit()

    with pytest.raises(ValueError, match="deliverable tables must be empty"):
        to_v38(db_path)

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 39
        assert conn.execute("SELECT id FROM deliverables").fetchall() == [("deliverable-a",)]
