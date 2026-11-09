"""Schema v8: each work item keeps one scratchpad, its only active note."""

import sqlite3
import uuid
from contextlib import closing

import pytest
import sealed
from fastapi.testclient import TestClient
from test_coordination_recovery import legacy_database
from test_people_recovery import to_v9
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.common import NOTE_LIMIT, AppError
from worklog.db import Database
from worklog.search import global_search

ORIGIN = "http://127.0.0.1:8877"
HUMAN = {"id": "local-user", "label": "You", "kind": "human"}
AGENT = {"id": "agent", "label": "Engineer", "kind": "agent"}
DAY = "2026-01-0{}T00:00:00+00:00"
# id, owner type, owner, title, text, archived, created
NOTES = [
    ("first", "item", "many", "New note", "First thoughts", 0, DAY.format(1)),
    ("third", "item", "many", "", "Loose text", 0, DAY.format(3)),
    ("second", "item", "many", "Decisions", "- Use SQLite\n- Keep it local\n", 0, DAY.format(2)),
    # Oldest of all, but archived, so it is no scratchpad and is left alone.
    ("old", "item", "many", "Discarded", "Archived before v8", 1, "2025-12-31T00:00:00+00:00"),
    ("titled", "item", "single", "Design", "Use the index", 0, DAY.format(1)),
    ("untitled", "item", "plain", "New note", "Just text", 0, DAY.format(1)),
    # Created together: the id breaks the tie.
    ("tie-b", "item", "tie", "", "Second by id", 0, DAY.format(1)),
    ("tie-a", "item", "tie", "", "First by id", 0, DAY.format(1)),
    ("plan-a", "roadmap", "plan", "A", "Roadmap note A", 0, DAY.format(1)),
    ("plan-b", "roadmap", "plan", "B", "Roadmap note B", 0, DAY.format(2)),
]


def v7_database(path, notes=NOTES):
    """The v7 shape, v9 without resources (v9) or the scratchpad index (v8), holding notes as v7 kept them."""
    Database(path).initialize()
    to_v9(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TABLE resources")
        conn.execute("DROP INDEX notes_item_scratchpad")
        for owner in sorted({note[2] for note in notes if note[1] == "item"}):
            conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES(?,?,0,?,?)",
                         (owner, owner.title(), DAY.format(1), DAY.format(1)))
        for identity, owner_type, owner, title, text, archived, created in notes:
            conn.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,1,?,?)", (identity, owner_type, owner, title, text, archived, created, created))
            conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,?,?,?,?,?)",
                         (identity, "note", title, text, owner, archived))
        conn.execute("PRAGMA user_version=7")
        conn.commit()
    recovery.validate_database(path)


def notes(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return {row["id"]: dict(row) for row in conn.execute("SELECT * FROM notes")}


def test_v8_folds_each_items_active_notes_into_its_oldest_and_archives_the_rest(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    v7_database(path)
    before = notes(path)
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    after = notes(path)
    # The oldest active note holds every active note in order, a titled one under its heading.
    assert after["first"]["title"] == "Scratchpad"
    assert after["first"]["text"] == "First thoughts\n\n## Decisions\n\n- Use SQLite\n- Keep it local\n\nLoose text"
    assert (after["first"]["archived"], after["first"]["revision"]) == (0, 2)
    # The rest are archived whole, as the archive command leaves them.
    for identity in ("second", "third", "tie-b"):
        assert after[identity] == {**before[identity], "archived": 1, "revision": 2}
    assert after["tie-a"]["text"] == "First by id\n\nSecond by id"
    # A lone titled note keeps its title as a heading; the rest are as they were.
    assert (after["titled"]["title"], after["titled"]["text"], after["titled"]["revision"]) == ("Scratchpad", "## Design\n\nUse the index", 2)
    for identity in ("old", "untitled", "plan-a", "plan-b"):
        assert after[identity] == before[identity]
    snapshot, = (path.parent / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert recovery._schema_signature(conn) == sealed.shape(7)
    assert notes(snapshot) == before


def test_search_finds_folded_text_in_the_scratchpad_and_a_second_active_note_is_refused(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    v7_database(path)
    db = create_app(tmp_path, testing=True).state.db

    def found(query, archived=False):
        with db.read() as c:
            return [hit["id"] for hit in global_search(c, query, archived)["items"]]

    assert found("Loose") == ["first"]
    assert found("Loose", archived=True) == ["third"]
    assert found("Design") == ["titled"]
    with closing(sqlite3.connect(path)) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE notes SET archived=0 WHERE id='second'")


def test_a_fold_that_would_pass_the_note_limit_keeps_what_fits_and_the_archive_keeps_the_rest(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    large = 600_000
    v7_database(path, [("big-1", "item", "big", "", "a" * large, 0, DAY.format(1)),
                       ("big-2", "item", "big", "Second", "b" * large, 0, DAY.format(2)),
                       ("big-3", "item", "big", "", "small", 0, DAY.format(3))])
    before = notes(path)
    Database(path).initialize()
    after = notes(path)
    pad = after["big-1"]
    assert pad["text"] == "a" * large + "\n\n*2 more notes did not fit here and stay whole under Archived notes.*"
    assert len(pad["text"].encode("utf-8")) <= NOTE_LIMIT
    # Folding stops at the first note that does not fit, so the order stays as written.
    for identity in ("big-2", "big-3"):
        assert after[identity] == {**before[identity], "archived": 1, "revision": 2}


def test_restoring_a_v7_backup_folds_its_notes(tmp_path):
    source = tmp_path / "source" / "worklog.sqlite3"
    source.parent.mkdir()
    v7_database(source)
    target = tmp_path / "target" / "worklog.sqlite3"
    recovery.restore_database(target, source, tmp_path / "backups")
    with Database(target).read() as conn:
        assert recovery._schema_signature(conn) == sealed.current()
    restored = notes(target)
    assert {i for i, n in restored.items() if n["owner_type"] == "item" and not n["archived"]} == {"first", "titled", "untitled", "tie-a"}
    assert restored["first"]["text"].startswith("First thoughts\n\n## Decisions")


def test_restoring_a_v1_backup_folds_its_notes_through_the_whole_upgrade(tmp_path):
    source = tmp_path / "source" / "worklog.sqlite3"
    source.parent.mkdir()
    legacy_database(source)
    with closing(sqlite3.connect(source)) as conn:
        for identity, title, text, day in (("v1-a", "Plan", "Start here", 1), ("v1-b", "", "Then this", 2)):
            conn.execute("INSERT INTO notes VALUES(?,'item','item',?,?,0,1,?,?)", (identity, title, text, DAY.format(day), DAY.format(day)))
        conn.commit()
    target = tmp_path / "target" / "worklog.sqlite3"
    recovery.restore_database(target, source, tmp_path / "backups")
    with Database(target).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    restored = notes(target)
    assert (restored["v1-a"]["archived"], restored["v1-a"]["text"]) == (0, "## Plan\n\nStart here\n\nThen this")
    assert restored["v1-b"]["archived"] == 1


def test_a_failed_v8_upgrade_leaves_v7_and_its_notes_as_they_were(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    v7_database(path)
    before = notes(path)
    # The notes are folded before the index fails, so only a rollback restores them.
    monkeypatch.setattr(migrations, "SCRATCHPAD_INDEX", "CREATE UNIQUE INDEX broken ON missing_table(x)")
    with pytest.raises(sqlite3.OperationalError, match="missing_table"):
        Database(path).initialize()
    assert notes(path) == before
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert recovery._schema_signature(conn) == sealed.shape(7)
    recovery.validate_database(path)


def test_a_v7_shape_cannot_claim_v8(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP INDEX notes_item_scratchpad")
    with pytest.raises(ValueError, match="schema definitions"):
        recovery.validate_database(path)


def test_the_way_back_to_v7_keeps_the_folded_notes_and_upgrades_again_unchanged(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    v7_database(path)
    Database(path).initialize()
    folded = notes(path)
    # From version 10 to version 9, then to version 8, then to version 7, with Worklog stopped.
    to_v9(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("DELETE FROM search_index WHERE type='resource'; DROP TABLE resources; PRAGMA user_version=8;")
        conn.executescript("DROP INDEX notes_item_scratchpad; PRAGMA user_version=7;")
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert recovery._schema_signature(conn) == sealed.shape(7)
    Database(path).initialize()
    assert notes(path) == folded


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def write(c, method, path, payload):
    return c.request(method, "/api/v1" + path, json=payload, headers={"Idempotency-Key": str(uuid.uuid4())})


def test_a_work_item_keeps_one_scratchpad_and_roadmaps_keep_their_notes(client):
    item = write(client, "POST", "/work-items", {"title": "Scratch"}).json()
    pad = write(client, "POST", "/notes", {"ownerType": "item", "ownerId": item["id"], "title": "Scratchpad", "text": "First"})
    assert pad.status_code == 200, pad.text
    second = write(client, "POST", "/notes", {"ownerType": "item", "ownerId": item["id"], "text": "Second"})
    assert (second.status_code, second.json()["error"]["code"]) == (409, "conflict")
    # A malformed note is refused as malformed, scratchpad or not.
    assert write(client, "POST", "/notes", {"ownerType": "item", "ownerId": item["id"], "text": 5}).status_code == 422
    # Recovering the scratchpad itself is no second note.
    assert write(client, "POST", f"/notes/{pad.json()['id']}/recover", {"revision": 1}).status_code == 200
    # A scratchpad may be emptied.
    emptied = write(client, "PATCH", f"/notes/{pad.json()['id']}", {"revision": 2, "text": ""})
    assert emptied.status_code == 200, emptied.text
    # Archived, it makes room for a new one, and can then not come back beside it.
    assert write(client, "POST", f"/notes/{pad.json()['id']}/archive", {"revision": 3}).status_code == 200
    fresh = write(client, "POST", "/notes", {"ownerType": "item", "ownerId": item["id"], "title": "Scratchpad", "text": "Fresh"})
    assert fresh.status_code == 200, fresh.text
    refused = write(client, "POST", f"/notes/{pad.json()['id']}/recover", {"revision": 4})
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "conflict")
    assert write(client, "POST", f"/notes/{fresh.json()['id']}/archive", {"revision": 1}).status_code == 200
    assert write(client, "POST", f"/notes/{pad.json()['id']}/recover", {"revision": 4}).status_code == 200
    roadmap = write(client, "POST", "/roadmaps", {"title": "Plan", "steps": [{"title": "Step"}]}).json()
    for text in ("One", "Two"):
        assert write(client, "POST", "/notes", {"ownerType": "roadmap", "ownerId": roadmap["id"], "text": text}).status_code == 200


@pytest.fixture
def approvals(tmp_path):
    app = create_app(tmp_path, testing=True)
    with app.state.db.transaction() as conn:
        item = app.state.work.apply(conn, HUMAN, "item.create", {"title": "Investigate"})

    def approve(*notes, owner=item["id"]):
        operations = [{"action": "note.create", "payload": {"ownerType": "item", "ownerId": owner, **note}} for note in notes]
        draft = app.state.proposals.create({"title": "Findings", "rationale": "What I found", "operations": operations}, AGENT)
        submitted = app.state.proposals.transition(draft["id"], "submit", {"revision": draft["revision"]}, AGENT)
        return app.state.proposals.transition(draft["id"], "approve", {"revision": submitted["revision"]}, HUMAN)

    def pads():
        with app.state.db.read() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM notes WHERE owner_id=? AND archived=0", (item["id"],))]

    return app, item, approve, pads


def test_an_approved_note_for_a_work_item_lands_in_its_scratchpad(approvals):
    app, item, approve, pads = approvals
    # The first note starts the scratchpad, its title as a heading.
    assert approve({"title": "Root cause", "text": "Lock contention"})["status"] == "approved"
    first, = pads()
    assert (first["title"], first["text"]) == ("Scratchpad", "## Root cause\n\nLock contention")
    # Later ones are appended in order, each landing on the scratchpad the one before left.
    approve({"text": "Retry fixed it"}, {"title": "Follow-up", "text": "Add a test"})
    pad, = pads()
    assert pad["id"] == first["id"]
    assert pad["text"] == "## Root cause\n\nLock contention\n\nRetry fixed it\n\n## Follow-up\n\nAdd a test"
    assert pad["revision"] == 3
    with app.state.db.read() as conn:
        kinds = [row[0] for row in conn.execute("SELECT kind FROM events WHERE entity_id=? ORDER BY created_at,rowid", (first["id"],))]
    assert kinds == ["note.create", "note.update", "note.update"]


def test_a_note_that_cannot_land_stops_the_approval_and_names_why(approvals):
    app, item, approve, pads = approvals
    approve({"text": "x" * (NOTE_LIMIT - 10)})
    before, = pads()
    refusals = [
        # Malformed and missing-item notes pass through for dispatch to refuse as before.
        ({"text": 5}, {}, 422, "Invalid proposed operation"),
        ({"text": "Lost"}, {"owner": str(uuid.uuid4())}, 404, "Owner does not exist"),
        # A full scratchpad is named as the reason, not the note.
        ({"text": "Over the limit"}, {}, 422, "scratchpad is full"),
    ]
    for note, where, status, message in refusals:
        with pytest.raises(AppError) as refused:
            approve(note, **where)
        assert refused.value.status == status
        assert refused.value.message.startswith("Change 1 of 1, note.create: ") and message in refused.value.message
    assert pads() == [before]
