"""Schema v11 adds derived data only: a key for each search row, lookup indexes, and a date on
each idempotency receipt. Upgrade, the documented way back, restore validation and the writers
that keep keys and rows in step."""

import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from worklog import migrations, recovery, search
from worklog.app import create_app
from worklog.db import Database
from worklog.maintenance import prune_receipts

ORIGIN = "http://127.0.0.1:8877"
# README: from version 11 to version 10, with Worklog stopped and after a manual backup.
WAY_BACK = """\
BEGIN;
DROP TABLE search_keys;
DROP INDEX tasks_item;
DROP INDEX file_references_item;
DROP INDEX work_items_step;
DROP INDEX work_items_roadmap;
DROP INDEX github_refs_chat;
DROP INDEX steps_parent;
DROP INDEX receipts_created;
ALTER TABLE receipts DROP COLUMN created_at;
PRAGMA user_version=10;
COMMIT;
"""


def to_v10(path):
    """Take a current database back to the v10 shape the documented way."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def keyed(conn):
    """Whether every search row has exactly one key that names it, and every key a row."""
    rows = conn.execute("SELECT count(*) FROM search_index").fetchone()[0]
    keys = conn.execute("SELECT count(*) FROM search_keys").fetchone()[0]
    matched = conn.execute("SELECT count(*) FROM search_keys k JOIN search_index s ON s.rowid=k.doc AND s.entity_id=k.entity_id"
                           " AND s.type=k.type AND s.owner_id=k.owner_id").fetchone()[0]
    return rows == keys == matched


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def write(c, method, path, body=None, **kwargs):
    response = c.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())}, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def populate(c):
    item = write(c, "POST", "/work-items", {"title": "Quokka work"})
    write(c, "POST", "/notes", {"ownerType": "item", "ownerId": item["id"], "title": "Plan", "text": "quokka notes"})
    steps = [{"id": str(uuid.uuid4()), "title": f"Quokka step {n}", "description": "", "parentId": None, "position": n}
             for n in range(3)]
    write(c, "POST", "/roadmaps", {"title": "Quokka plan", "steps": steps})
    ticket = write(c, "POST", "/tickets", {"title": "Quokka bug"})
    write(c, "POST", f"/tickets/{ticket['id']}/comments", {"text": "quokka comment"})
    write(c, "POST", "/resources", {"title": "Quokka page", "body": "quokka text"})
    return item


def search_count(c, word="quokka"):
    return c.get("/api/v1/search", params={"q": word, "archived": True, "limit": 200}).json()["total"]


def test_a_new_database_is_v11_with_every_row_keyed(client):
    populate(client)
    with client.app.state.db.read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION == 11
        assert recovery._schema_signature(conn) == recovery._expected_schema()
        assert keyed(conn)
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"tasks_item", "file_references_item", "work_items_step", "work_items_roadmap", "github_refs_chat",
                "steps_parent", "receipts_created", "search_keys_owner"} <= names
        dated = conn.execute("SELECT count(*) FROM receipts WHERE created_at<>''").fetchone()[0]
        assert dated == conn.execute("SELECT count(*) FROM receipts").fetchone()[0] > 0


def test_v10_upgrade_keys_every_row_drops_duplicates_and_dates_receipts(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        c.headers["X-CSRF-Token"] = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={}).json()["csrfToken"]
        item = populate(c)
        found = search_count(c)
    path = tmp_path / "worklog.sqlite3"
    to_v10(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert recovery._schema_signature(conn) == recovery._expected_schema(search_keys=False)
        # As a writer that inserted without deleting could leave it: the item indexed again, later.
        conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,'item','Newer quokka','',?,0)",
                     (item["id"], item["id"]))
        conn.execute("INSERT INTO receipts VALUES('credential','old-key','hash','{}')")
        conn.commit()
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        assert recovery._schema_signature(conn) == recovery._expected_schema()
        assert keyed(conn)
        # The newest row is the item's current one, and the only one left.
        assert [r[0] for r in conn.execute("SELECT title FROM search_index WHERE entity_id=?", (item["id"],))] == ["Newer quokka"]
        assert conn.execute("SELECT count(*) FROM receipts WHERE created_at=''").fetchone()[0] == 0
    assert list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    with TestClient(create_app(tmp_path, testing=True), base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert search_count(c) == found


def test_the_documented_way_back_to_v10_gives_its_exact_shape_and_upgrades_again(client, tmp_path):
    # The SQL tested here is the SQL the README gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    populate(client)
    path = tmp_path / "worklog.sqlite3"
    to_v10(path)
    recovery.validate_database(path)
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        assert keyed(conn)


def test_a_v10_shape_cannot_claim_version_11_and_a_v11_shape_cannot_claim_10(tmp_path):
    older, current = tmp_path / "older.sqlite3", tmp_path / "current.sqlite3"
    Database(older).initialize()
    to_v10(older)
    Database(current).initialize()
    for path, version in ((older, 11), (current, 10)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)


def test_receipts_past_the_window_are_pruned(tmp_path):
    db = Database(tmp_path / "worklog.sqlite3")
    db.initialize()
    stamp = datetime(2026, 11, 4, 12, tzinfo=UTC)
    with db.transaction() as conn:
        for key, age in (("fresh", 1), ("edge", 13), ("old", 15), ("older", 400)):
            conn.execute("INSERT INTO receipts VALUES('credential',?,'hash','{}',?)", (key, (stamp - timedelta(days=age)).isoformat()))
        assert prune_receipts(conn, now=stamp) == 2
        assert sorted(r[0] for r in conn.execute("SELECT key FROM receipts")) == ["edge", "fresh"]


def test_a_busy_database_at_startup_leaves_receipts_for_the_watcher(tmp_path, monkeypatch):
    from worklog import app as application
    from worklog.common import AppError

    def busy(conn):
        raise AppError(503, "database_busy", "Database is busy; retry this change")

    monkeypatch.setattr(application, "prune_receipts", busy)
    assert create_app(tmp_path, testing=True) is not None


def test_every_writer_keeps_keys_and_rows_in_step(client):
    item = populate(client)
    db = client.app.state.db
    detail = client.get(f"/api/v1/work-items/{item['id']}").json()
    write(client, "PATCH", f"/work-items/{item['id']}", {"revision": detail["revision"], "title": "Quokka work, renamed"})
    roadmap = client.get("/api/v1/roadmaps").json()["items"][0]
    full = client.get(f"/api/v1/roadmaps/{roadmap['id']}").json()
    kept_steps = [{key: step[key] for key in ("id", "title", "description", "parentId", "position")}
                  for step in full["steps"][:2]]
    revised = write(client, "POST", f"/roadmaps/{roadmap['id']}/revisions",
                    {"title": "Quokka plan", "revision": full["revision"], "steps": kept_steps})
    archived = write(client, "POST", f"/roadmaps/{roadmap['id']}/archive", {"revision": revised["revision"]})
    write(client, "POST", f"/roadmaps/{roadmap['id']}/recover", {"revision": archived["revision"]})
    ticket = client.get("/api/v1/tickets").json()["items"][0]
    write(client, "PATCH", f"/tickets/{ticket['id']}", {"revision": ticket["revision"], "title": "Quokka bug, retitled"})
    discussion = write(client, "POST", "/discussions", {"title": "Quokka thread", "text": "quokka post"})["discussion"]
    write(client, "POST", f"/discussions/{discussion['id']}/archive", {"revision": discussion["revision"], "archived": True})
    with db.read() as conn:
        assert keyed(conn)
        assert [tuple(r) for r in conn.execute("SELECT DISTINCT archived FROM search_index WHERE owner_id=?",
                                               (discussion["id"],))] == [(1,)]
        assert [r[0] for r in conn.execute("SELECT title FROM search_index WHERE type='ticket_comment'")] == [
            ticket["key"] + " Quokka bug, retitled"]
    with db.transaction() as conn:
        search.remove(conn, item["id"])
        search.remove_plans(conn)
        assert keyed(conn)
        assert conn.execute("SELECT count(*) FROM search_keys WHERE type IN ('item','roadmap','step')").fetchone()[0] == 0


def test_merging_a_repository_moves_its_files_rows_and_keys_to_the_survivor(client):
    kept = write(client, "POST", "/repositories", {"name": "api", "url": "https://github.com/example/api"})
    merged = write(client, "POST", "/repositories", {"name": "api-old", "url": "https://github.com/example/api-old"})
    response = client.post(f"/api/v1/attachments?ownerType=repository&ownerId={merged['id']}&name=quokka.txt",
                           content=b"notes", headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    write(client, "POST", f"/repositories/{merged['id']}/merge",
          {"revision": merged["revision"], "intoId": kept["id"], "intoRevision": kept["revision"]})
    with client.app.state.db.read() as conn:
        assert keyed(conn)
        assert [tuple(r) for r in conn.execute("SELECT owner_id FROM search_keys WHERE type='attachment'")] == [(kept["id"],)]
        assert [tuple(r) for r in conn.execute("SELECT owner_id FROM search_index WHERE type='attachment'")] == [(kept["id"],)]
