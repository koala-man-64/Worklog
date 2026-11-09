"""A work item's tasks in the person's order (schema v14), with when each was added and last
changed. A move is checked against the moved task's own revision and changes no task's revision
or last-updated time. Upgrade, the way back and restore validation are here too."""

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
# From version 14 to version 13, with Worklog stopped and after a manual backup. The README gave this way back
# while version 14 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
BEGIN;
ALTER TABLE tasks DROP COLUMN position;
PRAGMA user_version=13;
COMMIT;
"""
# How version 13 read a work item's tasks (support.for_owner).
V13_ORDER = "SELECT id FROM tasks WHERE item_id=? ORDER BY created_at"


def to_v13(path):
    """Take a current database back to the v13 shape the documented way: to version 14 first."""
    from test_waiting_status import to_v14

    to_v14(path)
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


@pytest.fixture
def client(tmp_path):
    with person(tmp_path) as c:
        yield c


def write(c, method, path, payload=None):
    return c.request(method, "/api/v1" + path, headers={"Idempotency-Key": str(uuid.uuid4())}, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]


def work_item(c, title="Ship the release"):
    return ok(write(c, "POST", "/work-items", {"title": title}))["id"]


def add(c, item_id, *texts):
    return [ok(write(c, "POST", "/tasks", {"itemId": item_id, "text": text}))["id"] for text in texts]


def tasks(c, item_id):
    """The item's tasks as its page reads them, archived ones included."""
    return ok(c.get(f"/api/v1/work-items/{item_id}"))["tasks"]


def listed(c, item_id):
    """The texts of the item's active tasks, in the order its list shows them."""
    return [t["text"] for t in tasks(c, item_id) if not t["archived"]]


def find(c, item_id, text):
    return next(t for t in tasks(c, item_id) if t["text"] == text)


def move(c, item_id, text, position, revision=None):
    record = find(c, item_id, text)
    return write(c, "POST", f"/tasks/{record['id']}/move",
                 {"revision": record["revision"] if revision is None else revision, "position": position})


def test_tasks_list_in_the_order_added_and_say_when_each_was_added_and_last_changed(client):
    item = work_item(client)
    add(client, item, "Tag it", "Write notes", "Announce it")
    assert listed(client, item) == ["Tag it", "Write notes", "Announce it"]
    added = {t["text"]: t for t in tasks(client, item)}
    for record in added.values():
        # The list's order is the place: no task carries a position of its own.
        assert set(record) == {"id", "itemId", "text", "done", "archived", "revision", "createdAt", "updatedAt"}
        # A new task was last updated when it was added.
        assert record["updatedAt"] == record["createdAt"]
    notes = added["Write notes"]
    ok(write(client, "PATCH", f"/tasks/{notes['id']}", {"revision": notes["revision"], "done": True}))
    ticked = find(client, item, "Write notes")
    assert ticked["createdAt"] == notes["createdAt"] and ticked["updatedAt"] > notes["updatedAt"]
    ok(write(client, "PATCH", f"/tasks/{notes['id']}", {"revision": ticked["revision"], "text": "Write the notes"}))
    renamed = find(client, item, "Write the notes")
    assert renamed["createdAt"] == notes["createdAt"] and renamed["updatedAt"] > ticked["updatedAt"]
    assert ok(client.get(f"/api/v1/tasks/{notes['id']}")) == renamed
    # Changing one task changes no other.
    assert find(client, item, "Tag it") == added["Tag it"]
    assert listed(client, item) == ["Tag it", "Write the notes", "Announce it"]


def test_a_task_moves_up_down_first_and_past_the_last(client):
    item = work_item(client)
    add(client, item, "A", "B", "C", "D")
    assert ok(move(client, item, "D", 1)) == {"id": find(client, item, "D")["id"], "position": 1}
    assert listed(client, item) == ["D", "A", "B", "C"]
    assert ok(move(client, item, "D", 3))["position"] == 3
    assert listed(client, item) == ["A", "B", "D", "C"]
    assert ok(move(client, item, "B", 1))["position"] == 1
    assert listed(client, item) == ["B", "A", "D", "C"]
    # A place past the last moves the task last.
    assert ok(move(client, item, "A", 99))["position"] == 4
    assert listed(client, item) == ["B", "D", "C", "A"]
    # Another item's list is its own.
    other = work_item(client, "Plan the next one")
    add(client, other, "Z")
    assert ok(move(client, other, "Z", 2))["position"] == 1
    assert listed(client, other) == ["Z"]
    assert listed(client, item) == ["B", "D", "C", "A"]
    # A new task goes last.
    add(client, item, "E")
    assert listed(client, item) == ["B", "D", "C", "A", "E"]


def test_a_move_changes_no_revision_or_last_updated_time_and_is_recorded(client):
    item = work_item(client)
    add(client, item, "A", "B", "C")
    before = {t["id"]: (t["revision"], t["updatedAt"]) for t in tasks(client, item)}
    c_id = find(client, item, "C")["id"]
    ok(move(client, item, "C", 1))
    assert {t["id"]: (t["revision"], t["updatedAt"]) for t in tasks(client, item)} == before
    history = ok(client.get(f"/api/v1/history?entityId={c_id}"))["items"]
    assert [(e["kind"], e["before"], e["after"], e["actorKind"]) for e in history[:1]] == [
        ("task.move", {"position": 3}, {"position": 1}, "human")]
    # A move to the place a task already holds changes nothing and records nothing.
    assert ok(move(client, item, "C", 1)) == {"id": c_id, "position": 1}
    assert len(ok(client.get(f"/api/v1/history?entityId={c_id}"))["items"]) == len(history)
    # An edit open on any task still saves after the move.
    a = find(client, item, "A")
    ok(write(client, "PATCH", f"/tasks/{a['id']}", {"revision": before[a["id"]][0], "text": "A, edited"}))
    assert listed(client, item) == ["C", "A, edited", "B"]


def test_a_stale_view_cannot_move_a_task_that_changed(client):
    item = work_item(client)
    add(client, item, "A", "B")
    stale = find(client, item, "B")["revision"]
    b = find(client, item, "B")
    ok(write(client, "PATCH", f"/tasks/{b['id']}", {"revision": b["revision"], "done": True}))
    failure = error(move(client, item, "B", 1, revision=stale), 409, "conflict")
    assert failure["fields"]["currentRevision"] == stale + 1
    assert listed(client, item) == ["A", "B"]


def test_an_archived_task_keeps_its_place_and_comes_back_there(client):
    item = work_item(client)
    add(client, item, "A", "B", "C", "D")
    b = find(client, item, "B")
    ok(write(client, "POST", f"/tasks/{b['id']}/archive", {"revision": b["revision"]}))
    assert listed(client, item) == ["A", "C", "D"]
    error(move(client, item, "B", 1), 409, "conflict")
    # Places count active tasks only.
    assert ok(move(client, item, "D", 1))["position"] == 1
    assert listed(client, item) == ["D", "A", "C"]
    assert ok(move(client, item, "A", 3))["position"] == 3
    assert listed(client, item) == ["D", "C", "A"]
    archived = find(client, item, "B")
    ok(write(client, "POST", f"/tasks/{b['id']}/recover", {"revision": archived["revision"]}))
    # B held its place before C, where it was archived; the moves around it left it there.
    assert listed(client, item) == ["D", "B", "C", "A"]


def test_tied_or_zero_positions_read_in_a_fixed_order_and_a_move_numbers_them_again(client, tmp_path):
    # Only a writer that skipped the column leaves such positions. Ties read by when each task was
    # added, and the first move gives every task of the item a place of its own.
    item = work_item(client)
    add(client, item, "A", "B", "C", "D")
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        conn.executemany("UPDATE tasks SET position=?,created_at=? WHERE text=?",
                         [(0, "2026-11-01T00:00:01+00:00", "A"), (2, "2026-11-01T00:00:02+00:00", "B"),
                          (0, "2026-11-01T00:00:03+00:00", "C"), (2, "2026-11-01T00:00:04+00:00", "D")])
        conn.commit()
    assert listed(client, item) == ["A", "C", "B", "D"]
    assert ok(move(client, item, "D", 2))["position"] == 2
    assert listed(client, item) == ["A", "D", "C", "B"]
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        assert [r[0] for r in conn.execute("SELECT text FROM tasks WHERE item_id=? ORDER BY position", (item,))] == ["A", "D", "C", "B"]
        assert sorted(r[0] for r in conn.execute("SELECT position FROM tasks WHERE item_id=?", (item,))) == [1, 2, 3, 4]


def test_a_move_needs_a_known_task_and_a_whole_place_from_one(client):
    item = work_item(client)
    (task,) = add(client, item, "A")
    revision = find(client, item, "A")["revision"]
    for position in (0, -1, "2", True, 1.5, None):
        error(write(client, "POST", f"/tasks/{task}/move", {"revision": revision, "position": position}), 422, "validation")
    error(write(client, "POST", f"/tasks/{task}/move", {"position": 1}), 422, "validation")
    error(write(client, "POST", f"/tasks/{uuid.uuid4()}/move", {"revision": 1, "position": 1}), 404, "not_found")
    assert ok(write(client, "POST", f"/tasks/{task}/move", {"revision": revision, "position": 1}))["position"] == 1


def test_the_v14_upgrade_keeps_every_list_as_version_13_showed_it(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        first, second = work_item(c, "First"), work_item(c, "Second")
        add(c, first, "A", "B", "C", "D")
        add(c, second, "P", "Q", "R")
        for item, text in ((first, "B"), (second, "Q")):
            archived = find(c, item, text)
            ok(write(c, "POST", f"/tasks/{archived['id']}/archive", {"revision": archived["revision"]}))
        ok(move(c, first, "D", 1))
    to_v13(path)
    with closing(sqlite3.connect(path)) as conn:
        # Tasks added in the same instant, one of them archived, with IDs that sort against the
        # order they were added in: version 13 listed them in an order of its own, which neither
        # the IDs nor the order of writing gives.
        stamp = conn.execute("SELECT created_at FROM tasks WHERE text='Q'").fetchone()[0]
        conn.execute("UPDATE tasks SET created_at=? WHERE text IN ('P','R')", (stamp,))
        for text, identity in (("P", "ffffffff-ffff-4fff-bfff-ffffffffffff"), ("Q", "cccccccc-cccc-4ccc-bccc-cccccccccccc"),
                               ("R", "00000000-0000-4000-8000-000000000000")):
            conn.execute("UPDATE tasks SET id=? WHERE text=?", (identity, text))
        conn.commit()
        shown = {item: [r[0] for r in conn.execute(V13_ORDER, (item,))] for item in (first, second)}
        kept = conn.execute("SELECT id,revision,updated_at,created_at,done,archived,text FROM tasks ORDER BY id").fetchall()
    recovery.validate_database(path)
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert [tuple(r) for r in conn.execute("SELECT id,revision,updated_at,created_at,done,archived,text FROM tasks ORDER BY id")] == kept
        assert {item: [r[0] for r in conn.execute("SELECT id FROM tasks WHERE item_id=? ORDER BY position", (item,))]
                for item in (first, second)} == shown
        assert sorted(r[0] for r in conn.execute("SELECT position FROM tasks WHERE item_id=?", (first,))) == [1, 2, 3, 4]
    snapshots = list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    with person(tmp_path) as c:
        # The order the person gave before the way back is gone; the archived task kept its place.
        assert [t["text"] for t in tasks(c, first)] == ["A", "B", "C", "D"]
        assert [t["id"] for t in tasks(c, second)] == shown[second]
        ok(move(c, first, "D", 1))
        assert listed(c, first) == ["D", "A", "C"]


def test_the_way_back_to_v13_gives_its_exact_shape_and_upgrades_again(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        item = work_item(c)
        add(c, item, "A", "B")
        ok(move(c, item, "B", 1))
    to_v13(path)
    recovery.validate_database(path)
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        assert recovery._schema_signature(conn) == sealed.shape(13)
        assert [r[0] for r in conn.execute("SELECT text FROM tasks ORDER BY created_at")] == ["A", "B"]
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    with person(tmp_path) as c:
        assert listed(c, item) == ["A", "B"]


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
