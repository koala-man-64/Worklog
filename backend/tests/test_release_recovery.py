"""Schema v19 adds releases: what ships together and when, what each holds, and its checks.

An older database comes up with none, a failed upgrade changes nothing, and restore, export and
the way back keep everything else whole. test_releases.py has the rules of releases.
"""

import io
import json
import sqlite3
import uuid
import zipfile
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


def to_v18(path):
    """Take a current database back to the v18 shape the documented way: to version 19 first."""
    from test_reminder_recovery import to_v19

    to_v19(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def dangling(path):
    """The rows of release_contents that name no record of their type: none, ever."""
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(
            """SELECT rc.release_id,rc.target_type,rc.target_id FROM release_contents rc WHERE NOT EXISTS(
                   SELECT 1 FROM work_items WHERE rc.target_type='item' AND id=rc.target_id
                   UNION ALL SELECT 1 FROM roadmaps WHERE rc.target_type='roadmap' AND id=rc.target_id
                   UNION ALL SELECT 1 FROM steps WHERE rc.target_type='step' AND id=rc.target_id
                   UNION ALL SELECT 1 FROM github_refs WHERE rc.target_type='github' AND id=rc.target_id)""").fetchall()


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


def populate(c):
    """Work, and a release holding it, with a check that names a person and one with a due date."""
    item, roadmap, ana = work(c)
    release = write(c, "POST", "/releases", {"title": "November release", "releaseDate": "2026-11-20",
                                             "description": "Exports ship."})
    for target_type, target in (("item", item), ("roadmap", roadmap)):
        write(c, "POST", "/release-contents", {"releaseId": release["id"], "targetType": target_type,
                                               "targetId": target["id"]})
    write(c, "POST", "/release-checks", {"releaseId": release["id"], "kind": "notify", "text": "Tell support",
                                         "personId": ana["id"]})
    write(c, "POST", "/release-checks", {"releaseId": release["id"], "kind": "prepare",
                                         "text": "Add the Key Vault secret", "dueDate": "2026-11-13"})
    return release


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
        populate(c)
    # The release and its two checks are in the index under the types the way back removes.
    assert sorted(kind for _, kind, *_ in search_rows(path, "k.type IN " + RELEASE_TYPES)) == [
        "release", "release_check", "release_check"]
    # The way back to v19, which to_v18 takes first, drops v20's reminders, of which this test holds none.
    kept_tables = [name for name in tables(path) if name not in (*RELEASE_TABLES, "reminders")]
    kept, kept_search = rows(path, kept_tables), search_rows(path, "k.type NOT IN " + RELEASE_TYPES)
    to_v18(path)
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
    assert rows(path, RELEASE_TABLES) == {name: [] for name in RELEASE_TABLES}
    assert rows(path, kept_tables) == kept


def test_the_v19_upgrade_adds_empty_releases_after_a_snapshot_and_changes_no_record(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    with person(path.parent) as c:
        work(c)
    to_v18(path)
    # v25 drops the plan import table, empty here.
    assert rows(path, ["plan_imports"]) == {"plan_imports": []}
    names = [name for name in tables(path) if name != "plan_imports"]
    before, search = rows(path, names), search_rows(path, "1")
    Database(path).initialize()
    assert state(path) == (migrations.SCHEMA_VERSION, sealed.current())
    assert rows(path, RELEASE_TABLES) == {name: [] for name in RELEASE_TABLES}
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


def test_restoring_a_v18_backup_upgrades_it_with_no_releases(tmp_path):
    source, target = tmp_path / "source" / "worklog.sqlite3", tmp_path / "target" / "worklog.sqlite3"
    with person(source.parent) as c:
        work(c)
    to_v18(source)
    kept = rows(source, ["work_items", "github_refs", "people"])
    Database(target).initialize()
    recovery.restore_database(target, source, tmp_path / "restore-backups")
    assert state(target) == (migrations.SCHEMA_VERSION, sealed.current())
    assert rows(target, RELEASE_TABLES) == {name: [] for name in RELEASE_TABLES}
    assert rows(target, ["work_items", "github_refs", "people"]) == kept


def test_restoring_a_v19_backup_keeps_its_releases_whole(tmp_path):
    source, target = tmp_path / "source" / "worklog.sqlite3", tmp_path / "target" / "worklog.sqlite3"
    with person(source.parent) as c:
        populate(c)
    kept = rows(source, RELEASE_TABLES)
    assert all(kept.values())
    Database(target).initialize()
    recovery.restore_database(target, source, tmp_path / "restore-backups")
    assert state(target) == (migrations.SCHEMA_VERSION, sealed.current())
    assert rows(target, RELEASE_TABLES) == kept
    assert dangling(target) == []


def test_a_v18_shape_cannot_claim_version_19_and_a_v19_shape_cannot_claim_18(tmp_path):
    older, current = tmp_path / "older.sqlite3", tmp_path / "current.sqlite3"
    Database(older).initialize()
    to_v18(older)
    recovery.validate_database(older)
    Database(current).initialize()
    recovery.validate_database(current)
    for path, claimed in ((older, 19), (current, 18)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={claimed}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)


def test_the_dates_hold_only_calendar_days(tmp_path):
    # Past-due flags and the list's order compare dates as text, so the database keeps only real days.
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    stamp = "2026-01-01T00:00:00+00:00"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO releases(id,title,release_date,created_at,updated_at) VALUES('dated','R','2026-11-06',?,?)",
                     (stamp, stamp))
        conn.execute("INSERT INTO releases(id,title,release_date,created_at,updated_at) VALUES('open','R',NULL,?,?)",
                     (stamp, stamp))
        for bad in ("2026-02-30", "2026-1-6", "", "nonsense", "2026-11-06T10:00"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO releases(id,title,release_date,created_at,updated_at) VALUES('bad','R',?,?,?)",
                             (bad, stamp, stamp))
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO release_checks(id,release_id,kind,text,due_date,created_at,updated_at) "
                             "VALUES('bad','dated','test','T',?,?,?)", (bad, stamp, stamp))


def test_export_carries_releases_their_contents_and_checks(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        release = populate(c)
    with zipfile.ZipFile(io.BytesIO(recovery.export_database(path))) as archive:
        exported = json.loads(archive.read("records/releases.json"))
        assert [(r["id"], r["title"], r["release_date"]) for r in exported] == [
            (release["id"], "November release", "2026-11-20")]
        assert sorted(r["target_type"] for r in json.loads(archive.read("records/release_contents.json"))) == [
            "item", "roadmap"]
        assert sorted(r["text"] for r in json.loads(archive.read("records/release_checks.json"))) == [
            "Add the Key Vault secret", "Tell support"]
        assert "### November release" in archive.read("worklog.md").decode("utf-8")
