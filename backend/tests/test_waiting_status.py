"""Work that waits on someone else (schema v15). Waiting is a work item's status, and only a work
item's: tickets and roadmap steps keep Open, Doing, Blocked and Done. Waiting work is unfinished, so
it keeps its stack rank and its place in the backlog, but Today neither offers it as the next work
to continue nor counts it as blocked. Upgrade, the way back and restore are here too."""

import sqlite3
import uuid
from contextlib import closing, contextmanager

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import migrations, operations, recovery
from worklog.app import create_app
from worklog.common import AppError, today
from worklog.db import Database
from worklog.plans import PlanService
from worklog.proposals import ProposalService
from worklog.services import WorkService

ORIGIN = "http://127.0.0.1:8877"
HUMAN = {"id": "human", "label": "Rudy", "kind": "human"}
AGENT = {"id": "agent", "label": "Engineer", "kind": "agent"}
# From version 15 to version 14, with Worklog stopped and after a manual backup. The README gave this way back
# while version 15 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
PRAGMA foreign_keys=ON;
BEGIN;
PRAGMA defer_foreign_keys=ON;
UPDATE work_items SET status='blocked' WHERE status='waiting';
CREATE TEMP TABLE work_items_v15 AS SELECT rowid AS kept_rowid,* FROM work_items;
DROP TABLE work_items;
CREATE TABLE work_items(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','doing','blocked','done')),ado_url TEXT,step_id TEXT REFERENCES steps(id),backlog_order INTEGER NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, roadmap_id TEXT REFERENCES roadmaps(id));
INSERT INTO work_items(rowid,id,title,description,status,ado_url,step_id,backlog_order,archived,revision,created_at,updated_at,roadmap_id) SELECT kept_rowid,id,title,description,status,ado_url,step_id,backlog_order,archived,revision,created_at,updated_at,roadmap_id FROM temp.work_items_v15 ORDER BY kept_rowid;
DROP TABLE temp.work_items_v15;
CREATE INDEX work_items_step ON work_items(step_id);
CREATE INDEX work_items_roadmap ON work_items(roadmap_id);
PRAGMA user_version=14;
COMMIT;
"""
# The tables whose rows name a work item, all of which the v15 upgrade must leave pointing at it.
# Tasks named one too, until v26 dropped work item checklists.
REFERRING = {("daily_entries", "item_id"), ("file_references", "item_id"),
             ("item_repositories", "item_id"), ("participations", "item_id"),
             ("coordination_updates", "item_id"), ("tickets", "item_id")}


def to_v14(path):
    """Take a current database back to the v14 shape the documented way: to version 15 first. The
    connection starts with foreign keys off, as the sqlite3 shell's does; the script turns them on itself."""
    from test_proposal_history import to_v15

    to_v15(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def scatter_rowids(path):
    """Give work items rowids with gaps and against the order they were added, as deletes and
    explicit rowids can leave them, so a rebuild that numbered its rows afresh would show."""
    with closing(sqlite3.connect(path)) as conn:
        rowids = [row[0] for row in conn.execute("SELECT rowid FROM work_items ORDER BY rowid").fetchall()]
        conn.executemany("UPDATE work_items SET rowid=? WHERE rowid=?",
                         [(1000 + 37 * (len(rowids) - place), rowid) for place, rowid in enumerate(rowids)])
        conn.commit()


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


def write(c, method, path, payload=None, agent=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]


def item(c, title, **extra):
    return ok(write(c, "POST", "/work-items", {"title": title, **extra}))


def set_status(c, record, status):
    return ok(write(c, "PATCH", f"/work-items/{record['id']}", {"revision": record["revision"], "status": status}))


def ids(listing):
    return [record["id"] for record in listing["items"]]


def populate(c):
    """Work in every v14 state, and a record of each kind that names a work item."""
    roadmap = ok(write(c, "POST", "/roadmaps", {"title": "Release", "steps": [{"title": "Ship"}]}))
    repository = ok(write(c, "POST", "/repositories", {"name": "Worklog"}))
    work = {"planned": item(c, "Write the notes", day=today())["id"],
            "stepped": item(c, "Ship the build", stepId=roadmap["steps"][0]["id"], repositoryIds=[repository["id"]])["id"],
            "roadmapped": item(c, "Plan the next one", roadmapId=roadmap["id"])["id"]}
    for state in ("doing", "blocked", "done"):
        work[state] = set_status(c, item(c, f"Work that is {state}"), state)["id"]
    old = item(c, "Old work")
    ok(write(c, "POST", f"/work-items/{old['id']}/archive", {"revision": old["revision"]}))
    ok(write(c, "POST", "/references", {"itemId": work["planned"], "path": "C:/notes/release.md"}))
    ok(write(c, "POST", "/tickets", {"title": "The build fails", "itemId": work["stepped"]}))
    credential = ok(write(c, "POST", "/credentials", {"label": "Builder"}))
    running = ok(write(c, "POST", "/agent-runs", {"clientRunKey": str(uuid.uuid4()), "displayName": "Build"}, credential))
    participation = ok(write(c, "POST", "/participations", {"runId": running["id"], "itemId": work["stepped"],
                                                            "contexts": [{"repositoryId": repository["id"]}]}, credential))
    ok(write(c, "POST", f"/participations/{participation['id']}/updates", {"kind": "update", "text": "Halfway"}, credential))
    return work


def contents(conn):
    """Every row of every table, with its rowid where the table has one (FTS5's own two have none)."""
    found = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        try:
            rows = conn.execute(f'SELECT rowid,* FROM "{name}"').fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
        found[name] = sorted(map(tuple, rows), key=repr)
    return found


def references(conn):
    """Each (table, column) whose foreign key names work_items, with its ON UPDATE and ON DELETE
    actions and its MATCH (PRAGMA foreign_key_list: id, seq, table, from, to, on_update, on_delete, match)."""
    found = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for key in conn.execute(f'PRAGMA foreign_key_list("{name}")').fetchall():
            if key[2] == "work_items":
                found[(name, key[3])] = (key[5], key[6], key[7])
    return found


def version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def test_work_waits_on_someone_else_and_stays_ranked_unfinished_work(client):
    waiting, other = item(client, "Hear back from review"), item(client, "Write the notes")
    changed = set_status(client, waiting, "waiting")
    assert changed["status"] == "waiting"
    assert ids(ok(client.get("/api/v1/work-items?status=waiting"))) == [waiting["id"]]
    assert set(ids(ok(client.get("/api/v1/work-items?status=waiting&status=open")))) == {waiting["id"], other["id"]}
    assert ids(ok(client.get("/api/v1/work-items?status=blocked"))) == []
    # Unfinished work keeps its stack rank and its place in the backlog, and can be ranked.
    detail = ok(client.get(f"/api/v1/work-items/{waiting['id']}"))
    assert (detail["status"], detail["rank"], detail["rankedCount"]) == ("waiting", 1, 2)
    assert waiting["id"] in ids(ok(client.get("/api/v1/work-items?view=backlog")))
    assert ok(write(client, "POST", f"/work-items/{waiting['id']}/rank", {"revision": changed["revision"], "rank": 2}))["rank"] == 2
    history = ok(client.get(f"/api/v1/history?entityId={waiting['id']}"))["items"]
    update = next(event for event in history if event["kind"] == "item.update")
    assert (update["before"]["status"], update["after"]["status"]) == ("open", "waiting")
    # Work goes back from waiting like any other state.
    assert set_status(client, ok(client.get(f"/api/v1/work-items/{waiting['id']}")), "doing")["status"] == "doing"


def test_today_neither_continues_waiting_work_nor_counts_it_blocked(client):
    day = today()
    waiting = set_status(client, item(client, "Hear back from review", day=day), "waiting")
    blocked = set_status(client, item(client, "Fix the pipeline", day=day), "blocked")
    planned = item(client, "Write the notes", day=day)
    shown = ok(client.get(f"/api/v1/days/{day}"))
    assert [entry["item"]["status"] for entry in shown["entries"]] == ["waiting", "blocked", "open"]
    # The waiting entry comes first, but the next work to continue is the open one after it.
    assert shown["entries"][0]["itemId"] == waiting["id"]
    assert shown["nextItemId"] == planned["id"]
    assert [record["id"] for record in shown["summary"]["blocked"]] == [blocked["id"]]


def test_waiting_is_a_work_items_status_only(client):
    failure = error(client.get("/api/v1/work-items?status=paused"), 422, "validation")
    assert failure["message"] == "Choose Open, Doing, Waiting, Blocked, or Done"
    record = item(client, "Ship it")
    error(write(client, "PATCH", f"/work-items/{record['id']}", {"revision": record["revision"], "status": "paused"}), 422, "validation")
    # Tickets keep their four states, refused as invalid input rather than by the database.
    error(write(client, "POST", "/tickets", {"title": "The build fails", "status": "waiting"}), 422, "validation")
    ticket = ok(write(client, "POST", "/tickets", {"title": "The build fails", "itemId": record["id"]}))
    error(write(client, "PATCH", f"/tickets/{ticket['id']}", {"revision": ticket["revision"], "status": "waiting"}), 422, "validation")
    error(client.get("/api/v1/tickets?status=waiting"), 422, "validation")
    # A ticket shows its work item's status, waiting included.
    set_status(client, ok(client.get(f"/api/v1/work-items/{record['id']}")), "waiting")
    assert ok(client.get(f"/api/v1/tickets/{ticket['id']}"))["item"]["status"] == "waiting"
    # So do roadmap steps.
    roadmap = ok(write(client, "POST", "/roadmaps", {"title": "Release", "steps": [{"title": "Ship"}]}))
    step = roadmap["steps"][0]
    error(write(client, "PATCH", f"/steps/{step['id']}/progress", {"revision": roadmap["revision"], "status": "waiting"}), 422, "validation")
    # A proposal can set a work item waiting, and is held to the same rules.
    operations.validate("item.update", {"id": record["id"], "revision": 1, "status": "waiting"})
    for action, status in (("item.update", "paused"), ("progress.set", "waiting")):
        with pytest.raises(AppError) as refused:
            operations.validate(action, {"id": record["id"], "revision": 1, "status": status})
        assert refused.value.status == 422, action


def test_an_agent_proposes_waiting_work_and_the_person_approves_it(tmp_path):
    db = Database(tmp_path / "worklog.sqlite3")
    db.initialize()
    plans = PlanService(db)
    work = WorkService(db, plans)
    service = ProposalService(db, work.apply)
    work.proposals = service
    with db.transaction() as conn:
        plan = plans.apply(conn, HUMAN, "roadmap.create", {"title": "Release", "steps": [{"title": "Ship"}]})
        record = work.apply(conn, HUMAN, "item.create", {"title": "Hear back from review"})
    proposal = service.create({"title": "Wait on review", "operations": [
        {"action": "item.update", "payload": {"id": record["id"], "revision": 1, "status": "waiting"}}]}, AGENT)
    proposal = service.transition(proposal["id"], "submit", {"revision": proposal["revision"]}, AGENT)
    service.transition(proposal["id"], "approve", {"revision": proposal["revision"]}, HUMAN)
    assert work.items.detail(record["id"])["status"] == "waiting"
    # A step's progress can't wait: the proposal is refused when it is submitted.
    step = plan["steps"][0]
    refused = service.create({"title": "Wait on the step", "operations": [
        {"action": "progress.set", "payload": {"id": step["id"], "revision": step["revision"], "status": "waiting"}}]}, AGENT)
    with pytest.raises(AppError) as failure:
        service.transition(refused["id"], "submit", {"revision": refused["revision"]}, AGENT)
    assert failure.value.status == 422
    assert plans.detail(plan["id"])["steps"][0]["status"] == "open"


def test_the_database_admits_waiting_and_no_status_it_does_not_know(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    insert = "INSERT INTO work_items(id,title,status,backlog_order,created_at,updated_at) VALUES(?,?,?,1,'2026-11-05','2026-11-05')"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(insert, (str(uuid.uuid4()), "Hear back", "waiting"))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(insert, (str(uuid.uuid4()), "Pause", "paused"))


def test_the_v15_upgrade_keeps_every_work_item_and_every_reference_to_one(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        work = populate(c)
    to_v14(path)
    scatter_rowids(path)
    with closing(sqlite3.connect(path)) as conn:
        # Each table that names work items has a row that does, so the upgrade meets every kind of
        # reference; dropping the table to build it again deletes none of them. Tasks name work
        # items too at v14, but none can be added since v26 took them away, and v26 drops them.
        assert references(conn) == {key: ("NO ACTION", "NO ACTION", "NONE") for key in REFERRING | {("tasks", "item_id")}}
        for table, column in REFERRING:
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL").fetchone()[0] > 0, table
        kept = contents(conn)
    recovery.validate_database(path)
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert version(conn) == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        # Every row of every table is as it was, work items' rowids included. The upgrade goes on
        # to v16, whose table names no proposal here, none having been approved, to v17, whose
        # agent ties start empty, to v18, whose registration directory is unknown for every
        # identity here, none having registered, to v19, whose releases start empty, to v20,
        # whose reminders start empty too, to v21, which starts each day's reflection revision
        # at its day's revision, to v22, which pins no discussion, and to v25 and v26, which drop
        # the plan import and task tables, empty here.
        upgraded = contents(conn)
        assert kept.pop("plan_imports") == []
        assert kept.pop("tasks") == []
        assert upgraded.pop("proposal_targets") == []
        assert upgraded.pop("agent_links") == []
        assert upgraded.pop("reminders") == []
        for table in ("releases", "release_contents", "release_checks"):
            assert upgraded.pop(table) == []
        assert {row[-1] for row in upgraded["communication_credentials"]} == {None}
        upgraded["communication_credentials"] = [row[:-1] for row in upgraded["communication_credentials"]]
        assert upgraded["days"] and all(row[-1] == row[-2] for row in upgraded["days"])
        upgraded["days"] = [row[:-1] for row in upgraded["days"]]
        # v22 gives each discussion a pin, none here, and gives the repository back the discussion it
        # opened under v22 before the way back, rather than opening another.
        assert {row[-1] for row in upgraded["discussions"]} == {None}
        upgraded["discussions"] = [row[:-1] for row in upgraded["discussions"]]
        [home] = upgraded.pop("discussion_contexts")
        assert kept.pop("discussion_contexts") == []
        assert (home[1], home[2:5]) == (kept["discussions"][0][1], ("repository", kept["repositories"][0][1], 1))
        assert upgraded == kept
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    snapshots = list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert version(conn) == 14
    with person(tmp_path) as c:
        planned = ok(c.get(f"/api/v1/work-items/{work['planned']}"))
        assert set_status(c, planned, "waiting")["status"] == "waiting"
        assert ids(ok(c.get("/api/v1/work-items?status=waiting"))) == [work["planned"]]


def test_an_upgrade_that_fails_part_way_leaves_version_14_as_it_was(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        populate(c)
    to_v14(path)
    with closing(sqlite3.connect(path)) as conn:
        kept, shape = contents(conn), recovery._schema_signature(conn)
    # The table is dropped by then: the failure has to bring it back, rows and all.
    monkeypatch.setattr(migrations, "WORK_ITEMS_TABLE", "CREATE TABLE work_items(")
    with pytest.raises(sqlite3.OperationalError):
        Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert version(conn) == 14
        assert recovery._schema_signature(conn) == shape
        assert contents(conn) == kept
    recovery.validate_database(path)


def test_an_upgrade_that_lost_a_named_work_item_cannot_commit(tmp_path, monkeypatch):
    # What the deferred checks are for: a work item a task or a day still names must come back.
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        work = populate(c)
    to_v14(path)
    with closing(sqlite3.connect(path)) as conn:
        kept = contents(conn)
    restore_indexes = migrations.execute_schema

    def lose_one(conn, schema):
        conn.execute("DELETE FROM work_items WHERE id=?", (work["planned"],))
        restore_indexes(conn, schema)

    monkeypatch.setattr(migrations, "execute_schema", lose_one)
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        migrations.upgrade(conn)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute("COMMIT")
        conn.execute("ROLLBACK")
    with closing(sqlite3.connect(path)) as conn:
        assert version(conn) == 14
        assert contents(conn) == kept


def test_restoring_a_v14_backup_upgrades_it_and_keeps_its_work(tmp_path):
    source, target = tmp_path / "source" / "worklog.sqlite3", tmp_path / "target" / "worklog.sqlite3"
    with person(source.parent) as c:
        populate(c)
    to_v14(source)
    scatter_rowids(source)
    # Restore ends the backup's runs and participations, so the rest is what stays as it was.
    unchanged = ("work_items", "daily_entries", "file_references", "item_repositories", "tickets", "coordination_updates")
    with closing(sqlite3.connect(source)) as conn:
        everything = contents(conn)
        kept = {table: everything[table] for table in unchanged}
    Database(target).initialize()
    recovery.restore_database(target, source, tmp_path / "restore-backups")
    with closing(sqlite3.connect(target)) as conn:
        assert version(conn) == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        restored = contents(conn)
        assert {table: restored[table] for table in kept} == kept
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_the_way_back_to_v14_gives_its_exact_shape_and_upgrades_again(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        populate(c)
        waiting = set_status(c, item(c, "Hear back from review"), "waiting")
    scatter_rowids(path)
    with closing(sqlite3.connect(path)) as conn:
        before = contents(conn)
    # The ways back to v21, v20, v19, v18, v17, v16 and v15, which to_v14 takes first, drop v22's pins
    # and its tags of kinds v21 lacks, v21's reflection revision, v20's and v19's tables, v18's
    # registration directory and v17's and v16's tables, and nothing else; this test holds no release
    # or reminder. The repository's own discussion stays, as an ordinary one: its tag and the feed's
    # record of it go.
    before["discussions"] = [row[:-1] for row in before["discussions"]]
    assert [row[2] for row in before.pop("discussion_contexts")] == ["repository"]
    assert [row[2] for row in before.pop("discussion_change_contexts")] == ["repository"]
    before["discussion_contexts"] = before["discussion_change_contexts"] = []
    for table in ("releases", "release_contents", "release_checks"):
        before.pop(table)
    before["communication_credentials"] = [row[:-1] for row in before["communication_credentials"]]
    before["days"] = [row[:-1] for row in before["days"]]
    before.pop("agent_links")
    before.pop("proposal_targets")
    before.pop("reminders")
    # The ways back to v25 and v24, which to_v14 takes first of all, make the task and plan import
    # tables again, empty.
    before["tasks"] = []
    before["plan_imports"] = []
    to_v14(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert version(conn) == 14
        assert recovery._schema_signature(conn) == sealed.shape(14)
        # Waiting work goes back to Blocked, and nothing else changes: not even its revision.
        status = [column[1] for column in conn.execute("PRAGMA table_info(work_items)")].index("status") + 1
        before["work_items"] = sorted((row[:status] + ("blocked",) + row[status + 1:] if row[1] == waiting["id"] else row
                                       for row in before["work_items"]), key=repr)
        assert contents(conn) == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert version(conn) == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    with person(tmp_path) as c:
        assert ok(c.get(f"/api/v1/work-items/{waiting['id']}"))["status"] == "blocked"


def test_a_way_back_that_fails_part_way_commits_nothing_even_without_bail(tmp_path):
    # Pasted into the shell without -bail, a statement that fails is skipped and the rest run. The
    # COMMIT then finds work items that other tables name gone, and refuses; the shell's exit
    # closes the connection, which rolls the open transaction back.
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        populate(c)
    with closing(sqlite3.connect(path)) as conn:
        kept, shape = contents(conn), recovery._schema_signature(conn)
    mangled = WAY_BACK.replace("CREATE TABLE work_items(", "CREATE TABLE work_items(,")
    failures = []
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        for statement in filter(str.strip, mangled.split(";\n")):
            try:
                conn.execute(statement)
            except sqlite3.Error as exc:
                failures.append((statement.split("(")[0].split()[:3], str(exc)))
    assert failures[-1] == (["COMMIT"], "FOREIGN KEY constraint failed")
    with closing(sqlite3.connect(path)) as conn:
        assert version(conn) == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == shape
        assert contents(conn) == kept


def test_a_v14_shape_cannot_claim_version_15_and_a_v15_shape_cannot_claim_14(tmp_path):
    from test_proposal_history import to_v15

    older, current = tmp_path / "older.sqlite3", tmp_path / "current.sqlite3"
    Database(older).initialize()
    to_v14(older)
    recovery.validate_database(older)
    Database(current).initialize()
    to_v15(current)
    recovery.validate_database(current)
    for path, claimed in ((older, 15), (current, 14)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={claimed}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
