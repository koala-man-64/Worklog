"""Schema v19 added releases, which v28 drops again (step 0.5 of docs/ROADMAPS-RETHINK.md). What stays:
the upgrade from v18, which a failure rolls back whole, the way back to it, which older upgrade tests
build their databases with, and that neither shape can pass for the other."""

import sqlite3
import uuid
from contextlib import closing, contextmanager

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
RELEASE_TABLES = ("releases", "release_contents", "release_checks")
RELEASE_TYPES = "('release','release_check')"
STAMP = "2026-11-01T00:00:00+00:00"
# From version 19 to version 18, with Worklog stopped and after a manual backup. The README gave this way back
# while version 19 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
BEGIN;
DELETE FROM search_index WHERE rowid IN (SELECT doc FROM search_keys WHERE type IN ('release','release_check'));
DELETE FROM search_keys WHERE type IN ('release','release_check');
DROP TABLE release_checks;
DROP TABLE release_contents;
DROP TABLE releases;
PRAGMA user_version=18;
COMMIT;
"""


def to_v19(path):
    """Take a current database back to the v19 shape the documented way."""
    from test_reminder_recovery import to_v19 as from_v20

    from_v20(path)


def to_v18(path):
    """Take a current database back to the v18 shape the documented way: to version 19 first."""
    to_v19(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


@contextmanager
def person(directory):
    app = create_app(directory, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert response.status_code == 200, response.text
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def write(c, method, path, payload=None):
    response = c.request(method, "/api/v1" + path, headers={"Idempotency-Key": str(uuid.uuid4())}, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def work(c):
    """A work item with a PR, a roadmap and a person: what any version holds."""
    item = write(c, "POST", "/work-items", {"title": "Export button"})
    write(c, "POST", "/github", {"ownerType": "item", "ownerId": item["id"], "kind": "pr",
                                 "url": "https://github.com/acme/app/pull/7", "title": "Add the export button"})
    roadmap = write(c, "POST", "/roadmaps", {"title": "Exports"})
    ana = write(c, "POST", "/people", {"name": "Ana"})
    return item, roadmap, ana


def index(conn, identity, kind, title, owner):
    doc = conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,?,?,'',?,0)",
                       (identity, kind, title, owner)).lastrowid
    conn.execute("INSERT INTO search_keys(entity_id,doc,type,owner_id) VALUES(?,?,?,?)", (identity, doc, kind, owner))


def populate(path, item, roadmap, ana):
    """A release as v19 kept it, holding the work item and the roadmap, with a check that names the
    person and one due on a day, each with the search row its service wrote."""
    release, checks = str(uuid.uuid4()), (str(uuid.uuid4()), str(uuid.uuid4()))
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO releases(id,title,description,release_date,created_at,updated_at) "
                     "VALUES(?,'November release','Exports ship.','2026-11-20',?,?)", (release, STAMP, STAMP))
        for kind, target in (("item", item), ("roadmap", roadmap)):
            conn.execute("INSERT INTO release_contents(release_id,target_type,target_id,created_at) VALUES(?,?,?,?)",
                         (release, kind, target["id"], STAMP))
        conn.execute("INSERT INTO release_checks(id,release_id,kind,text,person_id,created_at,updated_at) "
                     "VALUES(?,?,'notify','Tell support',?,?,?)", (checks[0], release, ana["id"], STAMP, STAMP))
        conn.execute("INSERT INTO release_checks(id,release_id,kind,text,due_date,created_at,updated_at) "
                     "VALUES(?,?,'prepare','Add the Key Vault secret','2026-11-13',?,?)", (checks[1], release, STAMP, STAMP))
        index(conn, release, "release", "November release", release)
        for check, text in zip(checks, ("Tell support", "Add the Key Vault secret"), strict=True):
            index(conn, check, "release_check", text, release)
        conn.commit()


def tables(path):
    """Every table but the search index, its keys and its FTS5 shadows, whose release rows the way
    back removes: search_rows compares what they hold."""
    with closing(sqlite3.connect(path)) as conn:
        return [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE 'search_index%' AND name<>'search_keys' ORDER BY name")]


def rows(path, names):
    with closing(sqlite3.connect(path)) as conn:
        return {name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in names}


def search_rows(path, where):
    with closing(sqlite3.connect(path)) as conn:
        return sorted(conn.execute("SELECT k.entity_id,k.type,s.title,s.body,s.owner_id,s.archived FROM search_keys k "
                                   "JOIN search_index s ON s.rowid=k.doc WHERE " + where).fetchall())


def state(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0], recovery._schema_signature(conn)


def test_the_way_back_to_v18_drops_only_the_releases_and_upgrades_again(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        records = work(c)
    to_v19(path)
    populate(path, *records)
    # The release and its two checks are in the index under the types the way back removes.
    assert sorted(kind for _, kind, *_ in search_rows(path, "k.type IN " + RELEASE_TYPES)) == [
        "release", "release_check", "release_check"]
    kept_tables = [name for name in tables(path) if name not in RELEASE_TABLES]
    kept, kept_search = rows(path, kept_tables), search_rows(path, "k.type NOT IN " + RELEASE_TYPES)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)
    recovery.validate_database(path)
    assert state(path) == (18, sealed.shape(18))
    assert search_rows(path, "1") == kept_search
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM search_index WHERE type IN " + RELEASE_TYPES).fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.execute("INSERT INTO search_index(search_index) VALUES('integrity-check')")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert rows(path, kept_tables) == kept
    Database(path).initialize()
    assert state(path) == (migrations.SCHEMA_VERSION, sealed.current())
    # v19 adds the release tables and v28 drops them again.
    assert not set(RELEASE_TABLES) & set(tables(path))


def test_the_upgrade_from_v18_snapshots_first_and_changes_no_record(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    with person(path.parent) as c:
        work(c)
    to_v18(path)
    # v25, v26 and v29 drop the plan import, task and ties tables, empty here.
    dropped = ["plan_imports", "tasks", "agent_links"]
    assert rows(path, dropped) == {"plan_imports": [], "tasks": [], "agent_links": []}
    names = [name for name in tables(path) if name not in dropped]
    before, search = rows(path, names), search_rows(path, "1")
    Database(path).initialize()
    assert state(path) == (migrations.SCHEMA_VERSION, sealed.current())
    assert set(tables(path)) == set(names)
    assert rows(path, names) == before
    assert search_rows(path, "1") == search
    snapshots = sorted((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert snapshots
    assert state(snapshots[-1]) == (18, sealed.shape(18))


def test_a_failed_release_schema_rolls_back_the_v18_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        work(c)
    to_v18(path)
    names = tables(path)
    before = rows(path, names)
    execute = migrations.execute_schema

    def fail_after_releases(conn, schema):
        execute(conn, schema)
        if schema == migrations.RELEASE_SCHEMA:
            raise RuntimeError("Injected release schema failure")

    monkeypatch.setattr(migrations, "execute_schema", fail_after_releases)
    with pytest.raises(RuntimeError, match="Injected"):
        Database(path).initialize()
    assert state(path) == (18, sealed.shape(18))
    assert rows(path, names) == before
    recovery.validate_database(path)


def test_a_v18_shape_cannot_claim_version_19_and_a_v19_shape_cannot_claim_18(tmp_path):
    older, newer = tmp_path / "older.sqlite3", tmp_path / "newer.sqlite3"
    for path in (older, newer):
        Database(path).initialize()
    to_v18(older)
    to_v19(newer)
    for path in (older, newer):
        recovery.validate_database(path)
    for path, claimed in ((older, 19), (newer, 18)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={claimed}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
