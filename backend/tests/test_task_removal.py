"""Schema v26 removes work item checklists (decision R1 in docs/ROADMAPS-RETHINK.md, step 0.3): their
routes and proposal operations are gone, a work item's read lists no tasks and Today's tabs count
none, and the upgrade drops the tasks table, every task with it, and changes no other record. The
pre-migration backup keeps the table, and the documented way back gives v25's exact shape."""

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

# From version 26 to version 25, with Worklog stopped and after a manual backup: the table and its
# index as version 25 had them. docs/UPGRADING.md gave this way back while version 26 was the latest;
# older upgrade tests now build their databases with it.
WAY_BACK = """\
CREATE TABLE tasks(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),text TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0 CHECK(done IN(0,1)),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0 CHECK(position>=0));
CREATE INDEX tasks_item ON tasks(item_id,archived);
PRAGMA user_version=25;
"""
STAMP = "2026-11-01T00:00:00+00:00"
DAY = "2026-11-04"


def to_v25(path):
    """Take a current database back to v25 the documented way: to version 26 first."""
    from test_reminder_removal import to_v26

    to_v26(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def planned(data):
    """A current database with a work item planned on DAY, and the item's ID."""
    client = session(data)
    try:
        item = call(client, "POST", "/work-items", {"title": "Ship the release"})
        call(client, "PUT", f"/days/{DAY}/entries/{item['id']}", {"revision": 0, "planned": True})
    finally:
        client.__exit__(None, None, None)
    return data / "worklog.sqlite3", item["id"]


def test_the_upgrade_drops_every_task_and_changes_no_other_record(tmp_path):
    database, item = planned(tmp_path / "data")
    to_v25(database)
    # Tasks as v25 kept them, one of them archived, and the history one wrote, which stays.
    with closing(sqlite3.connect(database)) as conn:
        for position, (text, archived) in enumerate((("Tag it", 0), ("Write notes", 1)), 1):
            conn.execute("INSERT INTO tasks(id,item_id,text,done,archived,revision,created_at,updated_at,position) "
                         "VALUES(?,?,?,0,?,1,?,?,?)", (str(uuid.uuid4()), item, text, archived, STAMP, STAMP, position))
        conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,"
                     "before_json,after_json) VALUES('task-event','t','task.create','human','You','human',?,"
                     "'2026-11-01','null',?)", (STAMP, json.dumps({"id": "t", "text": "Tag it"})))
        conn.commit()
    recovery.validate_database(database)
    _, _, before = contents(database)
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    assert "tasks" not in after and len(before["tasks"]) == 2
    # Every other table keeps every row, the work item and the task's history among them; v27, which
    # the upgrade also runs, drops the reminders table, empty here.
    assert after == {table: rows for table, rows in before.items() if table not in ("tasks", "reminders")}
    assert [row[2] for row in after["events"] if row[0] == "task-event"] == ["task.create"]
    snapshot, = (tmp_path / "data" / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 25
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 2


def test_the_way_back_gives_v25s_exact_shape_and_upgrades_again(tmp_path):
    database, _ = planned(tmp_path / "data")
    to_v25(database)
    recovery.validate_database(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 25
        assert recovery._schema_signature(conn) == sealed.shape(25)
    Database(database).initialize()
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()


def test_a_work_item_lists_no_tasks_and_the_routes_are_gone(tmp_path):
    _, item = planned(tmp_path / "data")
    client = session(tmp_path / "data")
    try:
        assert "tasks" not in call(client, "GET", f"/work-items/{item}")
        (entry,) = call(client, "GET", f"/days/{DAY}")["entries"]
        assert set(entry["counts"]) == {"links", "files", "scratchpad"}
        identity = str(uuid.uuid4())
        for method, path, body in (("POST", "/tasks", {"itemId": item, "text": "Tag it"}),
                                   ("GET", f"/tasks/{identity}", None), ("PATCH", f"/tasks/{identity}", {"revision": 1}),
                                   ("POST", f"/tasks/{identity}/move", {"revision": 1, "position": 1}),
                                   ("POST", f"/tasks/{identity}/archive", {"revision": 1}),
                                   ("POST", f"/tasks/{identity}/recover", {"revision": 1})):
            response = client.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code in (404, 405), (method, path, response.text)
    finally:
        client.__exit__(None, None, None)


def test_a_proposal_cannot_carry_a_task(tmp_path):
    _, item = planned(tmp_path / "data")
    client = session(tmp_path / "data")
    try:
        token = call(client, "POST", "/credentials", {"label": "Coding agent"})["token"]
        operations = [{"action": "task.create", "payload": {"itemId": item, "text": "Tag it"}}]
        response = client.post("/api/v1/proposals", json={"title": "Add a task", "rationale": "Track it", "operations": operations},
                               headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 422, response.text
    finally:
        client.__exit__(None, None, None)


def test_a_task_change_proposed_before_v26_still_reads_but_can_only_be_rejected(tmp_path):
    # A proposal stored before v26 can hold a task change: its operations are kept as written, so
    # it reads as before, but no update, submit or approval accepts the change, and nothing applies.
    database, item = planned(tmp_path / "data")
    client = session(tmp_path / "data")
    try:
        agent = {"Authorization": "Bearer " + call(client, "POST", "/credentials", {"label": "Coding agent"})["token"]}
        link = [{"action": "link.create", "payload": {"ownerType": "item", "ownerId": item, "url": "https://example.com/a"}}]
        task = [{"action": "task.update", "payload": {"id": str(uuid.uuid4()), "revision": 1, "done": True}}]
        proposal = {"title": "Tick it", "rationale": "Done", "operations": link}
        draft = call(client, "POST", "/proposals", proposal, agent)
        # An edit is checked against the actions there are, before the proposal's own rules run.
        refused = call(client, "PATCH", f"/proposals/{draft['id']}", {"revision": draft["revision"], "operations": task},
                       agent, status=422)
        assert [field["field"] for field in refused["error"]["fields"]] == ["body.operations.0.action"]
        # A stored proposal is refused for the action when it is submitted or approved.
        pending = call(client, "POST", "/proposals", proposal, agent)
        pending = call(client, "POST", f"/proposals/{pending['id']}/submit", {"revision": pending["revision"]}, agent)
        with closing(sqlite3.connect(database)) as conn:
            conn.executemany("UPDATE proposals SET operations=? WHERE id=?",
                             [(json.dumps(task), draft["id"]), (json.dumps(task), pending["id"])])
            conn.commit()
        refused = call(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, agent, status=422)
        assert refused["error"]["message"] == "Unsupported proposal action"
        refused = call(client, "POST", f"/proposals/{pending['id']}/approve", {"revision": pending["revision"]}, status=422)
        assert refused["error"]["message"] == "Unsupported proposal action"
        assert call(client, "GET", f"/proposals/{pending['id']}")["operations"] == task
        assert call(client, "GET", f"/work-items/{item}")["links"] == []
        assert call(client, "POST", f"/proposals/{pending['id']}/reject", {"revision": pending["revision"]})["status"] == "rejected"
    finally:
        client.__exit__(None, None, None)


def test_compaction_still_thins_the_task_events_older_databases_hold(tmp_path):
    # compaction.THINNED keeps the task family on purpose: a database upgraded from before v26 can
    # hold its events, which only ever showed in history.
    database, _ = planned(tmp_path / "data")
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,"
                     "before_json,after_json) VALUES('old-task','t','task.update','human','You','human',"
                     "'2020-01-01T00:00:00+00:00','2020-01-01',?,?)",
                     (json.dumps({"id": "t", "text": "Tag it", "done": False}),
                      json.dumps({"id": "t", "text": "Tag it", "done": True})))
        conn.commit()
    compact_database(database, tmp_path / "data" / "backups")
    with closing(sqlite3.connect(database)) as conn:
        before, after = conn.execute("SELECT before_json,after_json FROM events WHERE id='old-task'").fetchone()
    assert (json.loads(before), json.loads(after)) == ({"id": "t"}, {"id": "t"})
