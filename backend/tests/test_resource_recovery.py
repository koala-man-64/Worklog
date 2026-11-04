"""Schema v9 adds resources: upgrade, rollback, the documented way back, restore and export keep them whole."""

import io
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
from test_people_recovery import to_v9
from worklog import migrations, recovery, resources
from worklog.db import Database

STAMP = "2026-01-01T00:00:00+00:00"
# README: from version 9 to version 8, with Worklog stopped.
WAY_BACK = "DELETE FROM search_index WHERE type='resource'; DROP TABLE resources; PRAGMA user_version=8;"


def v8_database(path):
    """The shape main shipped as v8: v9 without resources, holding a work item."""
    Database(path).initialize()
    to_v9(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TABLE resources")
        conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES('item','Work',0,?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES('item','item','Work','','item',0)")
        conn.execute("PRAGMA user_version=8")
        conn.commit()
    recovery.validate_database(path)


def rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def add_page(path, identity="page", title="Runbook", body="Restart the service, then check the log."):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO resources(id,title,summary,created_at,updated_at,body) VALUES(?,?,'How to recover',?,?,?)",
                     (identity, title, STAMP, STAMP, body))
        conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,'resource',?,?,?,0)",
                     (identity, title, body, identity))
        conn.commit()


def test_v8_upgrade_adds_empty_resources_after_a_snapshot_and_restores(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    v8_database(path)
    before = {t: rows(path, t) for t in ("work_items", "notes", "settings")}
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == recovery._expected_schema()
        assert conn.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
    assert {t: rows(path, t) for t in before} == before
    snapshots = list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert recovery._schema_signature(conn) == recovery._expected_schema(resources=False)
    # A second start finds nothing to upgrade and takes no second snapshot.
    Database(path).initialize()
    assert len(list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))) == 1
    restored = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(restored, snapshots[0], tmp_path / "restore-backups")
    with Database(restored).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == recovery._expected_schema()
        assert conn.execute("SELECT title FROM work_items").fetchone()[0] == "Work"


def test_a_failed_resource_schema_rolls_back_the_v8_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    v8_database(path)
    execute = migrations.execute_schema

    def fail_after_resources(conn, schema):
        execute(conn, schema)
        if schema == resources.SCHEMA:
            raise RuntimeError("Injected resource schema failure")

    monkeypatch.setattr(migrations, "execute_schema", fail_after_resources)
    with pytest.raises(RuntimeError, match="Injected"):
        Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert recovery._schema_signature(conn) == recovery._expected_schema(resources=False)
    recovery.validate_database(path)


def test_a_v8_shape_cannot_claim_version_9_and_a_v9_shape_cannot_claim_8(tmp_path):
    older = tmp_path / "older.sqlite3"
    v8_database(older)
    current = tmp_path / "current.sqlite3"
    Database(current).initialize()
    to_v9(current)
    for path, version in ((older, 9), (current, 8)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)


def test_the_documented_way_back_to_v8_drops_only_the_resources_and_upgrades_again(tmp_path):
    # The SQL tested here is the SQL the README gives the person.
    assert f"`{WAY_BACK}`" in (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    path = tmp_path / "worklog.sqlite3"
    v8_database(path)
    Database(path).initialize()
    add_page(path)
    kept = {t: rows(path, t) for t in ("work_items", "notes", "settings")}
    to_v9(path)  # README: from version 10 to version 9 first
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert recovery._schema_signature(conn) == recovery._expected_schema(resources=False)
        # Version 8's search shows no page it cannot open.
        assert conn.execute("SELECT count(*) FROM search_index WHERE type='resource'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM search_index WHERE type='item'").fetchone()[0] == 1
    assert {t: rows(path, t) for t in kept} == kept
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert conn.execute("SELECT count(*) FROM resources").fetchone()[0] == 0


def test_export_and_restore_keep_resources_whole(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    Database(path).initialize()
    add_page(path, body="## Recover\n\nRestart the service, then check the log.\n")
    with zipfile.ZipFile(io.BytesIO(recovery.export_database(path))) as archive:
        exported = json.loads(archive.read("records/resources.json"))
        assert [(r["title"], r["summary"], r["body"]) for r in exported] == [
            ("Runbook", "How to recover", "## Recover\n\nRestart the service, then check the log.\n")]
        assert "### Runbook" in archive.read("worklog.md").decode("utf-8")
        assert json.loads(archive.read("manifest.json"))["schemaVersion"] == migrations.SCHEMA_VERSION
    backup = recovery.backup_database(path, tmp_path / "manual")
    target = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(target, tmp_path / "manual" / backup["name"], tmp_path / "restore-backups")
    assert rows(target, "resources") == rows(path, "resources")
