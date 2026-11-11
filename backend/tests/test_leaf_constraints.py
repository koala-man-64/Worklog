"""Schema v23 states what code alone kept true of four tables nothing references (consolidation step
4.4, B-09): archived is 0 or 1 in tasks, notes, file references and attachments, and a note or an
attachment names an owner of a kind it can have. The upgrade changes no record, rowids included,
refuses a row the new CHECKs would refuse and changes nothing then, and the way back gives
v22's exact shape. v26 dropped tasks with work item checklists, so the tests below hold rows in the
other three."""

import hashlib
import sqlite3
import uuid
from contextlib import closing

import pytest
import sealed
from fastapi.testclient import TestClient
from test_chat_label_links import to_v23
from test_sealed_schemas import contents
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
LEAVES = ("notes", "file_references", "attachments")
# From version 23 to version 22, with Worklog stopped and after a manual backup. docs/UPGRADING.md gave this
# way back while version 23 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
BEGIN;
ALTER TABLE tasks RENAME TO tasks_v23;
CREATE TABLE tasks(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),text TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0 CHECK(done IN(0,1)),archived INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0 CHECK(position>=0));
INSERT INTO tasks(rowid,id,item_id,text,done,archived,revision,created_at,updated_at,position) SELECT rowid,id,item_id,text,done,archived,revision,created_at,updated_at,position FROM tasks_v23;
DROP TABLE tasks_v23;
CREATE INDEX tasks_item ON tasks(item_id,archived);
ALTER TABLE notes RENAME TO notes_v23;
CREATE TABLE notes(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL,owner_id TEXT NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
INSERT INTO notes(rowid,id,owner_type,owner_id,title,text,archived,revision,created_at,updated_at) SELECT rowid,id,owner_type,owner_id,title,text,archived,revision,created_at,updated_at FROM notes_v23;
DROP TABLE notes_v23;
CREATE UNIQUE INDEX notes_item_scratchpad ON notes(owner_id) WHERE owner_type='item' AND archived=0;
CREATE INDEX notes_owner ON notes(owner_type,owner_id,archived);
ALTER TABLE file_references RENAME TO file_references_v23;
CREATE TABLE file_references(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),path TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
INSERT INTO file_references(rowid,id,item_id,path,archived,revision,created_at) SELECT rowid,id,item_id,path,archived,revision,created_at FROM file_references_v23;
DROP TABLE file_references_v23;
CREATE INDEX file_references_item ON file_references(item_id,archived);
ALTER TABLE attachments RENAME TO attachments_v23;
CREATE TABLE attachments(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL,owner_id TEXT NOT NULL,name TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,content BLOB NOT NULL,archived INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
INSERT INTO attachments(rowid,id,owner_type,owner_id,name,mime,size,sha256,content,archived,revision,created_at) SELECT rowid,id,owner_type,owner_id,name,mime,size,sha256,content,archived,revision,created_at FROM attachments_v23;
DROP TABLE attachments_v23;
CREATE INDEX files_owner ON attachments(owner_type,owner_id,archived);
PRAGMA user_version=22;
COMMIT;
"""


def to_v22(path):
    """Take a current database back to the v22 shape, through v23."""
    to_v23(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def populated(data):
    """A current database with rows in the three tables, written through the API, the newest row of
    each then moved to a higher rowid, so the rowids the rebuild must keep have gaps."""
    with TestClient(create_app(data, testing=True), base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]

        def call(method, path, **options):
            options.setdefault("headers", {})["Idempotency-Key"] = str(uuid.uuid4())
            response = c.request(method, "/api/v1" + path, **options)
            assert response.status_code == 200, response.text
            return response.json()

        item = call("POST", "/work-items", json={"title": "Parser"})
        call("POST", "/notes", json={"ownerType": "item", "ownerId": item["id"], "title": "Scratch", "text": "Notes"})
        roadmap = call("POST", "/roadmaps", json={"title": "Plan", "steps": []})
        call("POST", "/notes", json={"ownerType": "roadmap", "ownerId": roadmap["id"], "title": "Why", "text": "Because"})
        call("POST", "/references", json={"itemId": item["id"], "path": "C:/work/spec.md"})
        call("POST", f"/attachments?ownerType=item&ownerId={item['id']}&name=a.bin", content=b"\x00bytes",
             headers={"Content-Type": "application/octet-stream"})
    database = data / "worklog.sqlite3"
    with closing(sqlite3.connect(database)) as conn:
        # Gaps in the rowids, which the rebuild must keep: a row copied away and back at a new rowid.
        for table in LEAVES:
            conn.execute(f"UPDATE {table} SET rowid=rowid+100 WHERE rowid=(SELECT max(rowid) FROM {table})")
        conn.commit()
    return database


def leaves(path):
    """Every row of the three tables, rowid first, in rowid order."""
    with closing(sqlite3.connect(path)) as conn:
        return {table: conn.execute(f"SELECT rowid,* FROM {table} ORDER BY rowid").fetchall() for table in LEAVES}


def test_a_new_database_refuses_what_code_alone_used_to(tmp_path):
    database = populated(tmp_path / "data")
    with closing(sqlite3.connect(database)) as conn:
        for statement in ("UPDATE notes SET archived=-1", "UPDATE file_references SET archived=3",
                          "UPDATE attachments SET archived=9", "UPDATE notes SET owner_type='ticket'",
                          "UPDATE attachments SET owner_type='person'"):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                conn.execute(statement)


def test_the_v23_upgrade_keeps_every_row_and_rowid_and_gives_the_sealed_shape(tmp_path):
    database = populated(tmp_path / "data")
    to_v22(database)
    recovery.validate_database(database)
    before, (_, _, records) = leaves(database), contents(database)
    assert all(len(rows) >= 1 for rows in before.values())
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    # Every row stays; v25 and v26, which the upgrade also runs, drop the plan import and task tables,
    # empty here.
    assert after == {table: rows for table, rows in records.items() if table not in ("plan_imports", "tasks")}
    assert leaves(database) == before
    snapshot, = (tmp_path / "data" / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 22


@pytest.mark.parametrize("statement", [
    "UPDATE notes SET archived=2", "UPDATE notes SET owner_type='ticket'", "UPDATE file_references SET archived=5",
    "UPDATE attachments SET owner_type='person'"])
def test_a_row_the_checks_refuse_stops_the_upgrade_and_changes_nothing(tmp_path, statement):
    database = populated(tmp_path / "data")
    to_v22(database)
    with closing(sqlite3.connect(database)) as conn:
        conn.execute(statement)
        conn.commit()
    before = leaves(database)
    with pytest.raises(ValueError, match="break rules this version adds") as refused:
        Database(database).initialize()
    table = statement.split()[1]
    assert table in str(refused.value)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 22
        assert recovery._schema_signature(conn) == sealed.shape(22)
    assert leaves(database) == before


def test_restore_refuses_a_backup_with_a_row_the_checks_refuse_and_changes_nothing(tmp_path):
    # The refusal is a ValueError, so `worklog restore` prints it rather than failing (from the review).
    candidate = populated(tmp_path / "old")
    to_v22(candidate)
    with closing(sqlite3.connect(candidate)) as conn:
        conn.execute("UPDATE notes SET archived=2")
        conn.commit()
    target = populated(tmp_path / "live")
    original = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="break rules this version adds"):
        recovery.restore_database(target, candidate, tmp_path / "live" / "backups")
    assert hashlib.sha256(target.read_bytes()).hexdigest() == original
    assert [p.name for p in target.parent.iterdir() if p.name.startswith(".restore")] == []


def test_the_way_back_to_v22_gives_its_exact_shape_and_upgrades_again(tmp_path):
    database = populated(tmp_path / "data")
    before = leaves(database)
    to_v22(database)
    recovery.validate_database(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 22
        assert recovery._schema_signature(conn) == sealed.shape(22)
    assert leaves(database) == before
    Database(database).initialize()
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    assert leaves(database) == before


def test_a_v22_shape_cannot_claim_version_23_and_a_v23_shape_cannot_claim_22(tmp_path):
    database = populated(tmp_path / "data")
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA user_version=22")
    with pytest.raises(ValueError, match="schema definitions"):
        recovery.validate_database(database)
    to_v22(database)  # the v22 shape, now claiming 22
    recovery.validate_database(database)
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA user_version=23")
    with pytest.raises(ValueError, match="schema definitions"):
        recovery.validate_database(database)
