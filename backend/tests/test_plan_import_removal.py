"""Schema v25 removes the HTML plan import (decision R1 in docs/ROADMAPS-RETHINK.md, step 0.1): its
routes are gone, and the upgrade drops its table, with the originals and previews it holds, and
changes no other record. The pre-migration backup keeps the table, and the documented way back
gives v24's exact shape."""

import json
import sqlite3
import uuid
from contextlib import closing

import sealed
from test_chat_label_links import call, session
from test_sealed_schemas import contents
from worklog import migrations, recovery
from worklog.compaction import compact_database
from worklog.db import Database

# From version 25 to version 24, with Worklog stopped and after a manual backup: the table as version
# 24 had it, spaced as its seal records it. docs/UPGRADING.md gave this way back while version 25 was
# the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
CREATE TABLE plan_imports ( id TEXT PRIMARY KEY, name TEXT NOT NULL, original BLOB NOT NULL, sha256 TEXT NOT NULL, preview TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL, roadmap_id TEXT REFERENCES roadmaps(id) );
PRAGMA user_version=24;
"""
ORIGINAL = b"<!doctype html><title>A plan</title><h1>Plan</h1><h2>Build</h2><p>Text</p>"


def to_v24(path):
    """Take a current database back to v24 the documented way: to version 25 first."""
    from test_task_removal import to_v25

    to_v25(path)
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
        # And the history an import wrote, which stays.
        conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,"
                     "before_json,after_json) VALUES('import-event',?,'import.reference','human','You','human',"
                     "'2026-11-01T00:00:00+00:00','2026-11-01','{}',?)", (roadmap, json.dumps({"sha256": "digest"})))
        conn.commit()
    recovery.validate_database(database)
    _, _, before = contents(database)
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    assert "plan_imports" not in after
    # Every other table keeps every row, events included; the roadmap an import made is an ordinary
    # roadmap. v26 to v30, which the upgrade also runs, drop the task, reminder, release, ties and
    # chat message tables, empty here.
    dropped = ("plan_imports", "tasks", "reminders", "releases", "release_contents", "release_checks", "agent_links",
               "chat_messages", "chat_message_deliveries", "chat_confirmations")
    assert after == {table: rows for table, rows in before.items() if table not in dropped}
    assert [row[0] for row in after["roadmaps"]] == [roadmap]
    assert [row[2] for row in after["events"] if row[0] == "import-event"] == ["import.reference"]
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


def test_compaction_still_thins_the_import_events_older_databases_hold(tmp_path):
    # compaction.THINNED keeps the import family on purpose: a database upgraded from before v25 can
    # hold its events, which only ever showed in history.
    database, roadmap = planned(tmp_path / "data")
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,"
                     "before_json,after_json) VALUES('old-import',?,'import.reference','human','You','human',"
                     "'2020-01-01T00:00:00+00:00','2020-01-01',?,?)",
                     (roadmap, json.dumps({"id": roadmap, "title": "Imported", "overview": "x" * 200}),
                      json.dumps({"importId": "gone", "sha256": "digest"})))
        conn.commit()
    compact_database(database, tmp_path / "data" / "backups")
    with closing(sqlite3.connect(database)) as conn:
        before, after = conn.execute("SELECT before_json,after_json FROM events WHERE id='old-import'").fetchone()
    assert (json.loads(before), json.loads(after)) == ({"id": roadmap, "title": "Imported"}, {})


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
