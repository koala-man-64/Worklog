"""Schema v25 removes the HTML plan import (decision R1 in docs/ROADMAPS-RETHINK.md, step 0.1): its
routes are gone, and the upgrade drops its table, with the originals and previews it holds, and
changes no other record. The pre-migration backup keeps the table, and the documented way back
gives v24's exact shape."""

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import sealed
from test_chat_label_links import call, session
from test_sealed_schemas import contents
from worklog import migrations, recovery
from worklog.db import Database

# docs/UPGRADING.md: from version 25 to version 24, with Worklog stopped and after a manual backup.
# The table as version 24 had it, spaced as its seal records it.
WAY_BACK = """\
CREATE TABLE plan_imports ( id TEXT PRIMARY KEY, name TEXT NOT NULL, original BLOB NOT NULL, sha256 TEXT NOT NULL, preview TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL, roadmap_id TEXT REFERENCES roadmaps(id) );
PRAGMA user_version=24;
"""
ORIGINAL = b"<!doctype html><title>A plan</title><h1>Plan</h1><h2>Build</h2><p>Text</p>"


def to_v24(path):
    """Take a current database back to v24 the documented way."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def planned(data):
    """A current database with a roadmap, and its ID."""
    client = session(data)
    try:
        roadmap = call(client, "POST", "/roadmaps", {"title": "Imported", "steps": [{"title": "Build"}]})
    finally:
        client.__exit__(None, None, None)
    return data / "worklog.sqlite3", roadmap["id"]


def test_the_upgrade_drops_the_imports_and_changes_no_other_record(tmp_path):
    database, roadmap = planned(tmp_path / "data")
    to_v24(database)
    # Two imports as v24 kept them: one committed to the roadmap, one only previewed.
    with closing(sqlite3.connect(database)) as conn:
        for identity, target in ((str(uuid.uuid4()), roadmap), (str(uuid.uuid4()), None)):
            conn.execute("INSERT INTO plan_imports(id,name,original,sha256,preview,actor_id,created_at,roadmap_id) "
                         "VALUES(?,?,?,'digest','{}','human','2026-11-01T00:00:00+00:00',?)",
                         (identity, "plan.html", ORIGINAL, target))
        conn.commit()
    recovery.validate_database(database)
    _, _, before = contents(database)
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    assert "plan_imports" not in after
    # Every other table keeps every row; the roadmap an import made is an ordinary roadmap.
    assert after == {table: rows for table, rows in before.items() if table != "plan_imports"}
    assert [row[0] for row in after["roadmaps"]] == [roadmap]
    # The pre-migration backup holds the database as v24 had it, imports included.
    snapshot, = (tmp_path / "data" / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 24
        assert conn.execute("SELECT count(*) FROM plan_imports").fetchone()[0] == 2


def test_the_way_back_gives_v24s_exact_shape_and_upgrades_again(tmp_path):
    database, _ = planned(tmp_path / "data")
    to_v24(database)
    recovery.validate_database(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 24
        assert recovery._schema_signature(conn) == sealed.shape(24)
    Database(database).initialize()
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    # The SQL tested here is the SQL docs/UPGRADING.md gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "docs" / "UPGRADING.md").read_text(encoding="utf-8")


def test_the_import_routes_are_gone_and_a_roadmap_lists_no_imports(tmp_path):
    _, roadmap = planned(tmp_path / "data")
    client = session(tmp_path / "data")
    try:
        identity = str(uuid.uuid4())
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        assert client.post("/api/v1/imports/preview?name=plan.html", content=ORIGINAL, headers=headers).status_code == 404
        assert client.post(f"/api/v1/imports/{identity}/commit", json={"title": "Plan"}, headers=headers).status_code == 404
        assert client.get(f"/api/v1/imports/{identity}/download").status_code == 404
        assert "imports" not in call(client, "GET", f"/roadmaps/{roadmap}")
    finally:
        client.__exit__(None, None, None)
