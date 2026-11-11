"""Schema v28 removes Releases (decision R1 in docs/ROADMAPS-RETHINK.md, step 0.5): their page, routes,
proposal operations and agent commands are gone, search finds none and a discussion can't be tagged
with one. The upgrade drops the releases, release_contents and release_checks tables, every row and
search row of them, and every release tag with the feed's record of it, and changes no other record.
The two tag CHECKs keep 'release' (decision R5). The pre-migration backup keeps the tables, and the
documented way back gives v27's exact shape."""

import io
import json
import sqlite3
import uuid
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
import sealed
from test_chat_label_links import call, session
from test_sealed_schemas import contents
from worklog import cli, migrations, recovery
from worklog.db import Database
from worklog.doctor import doctor_database

# docs/UPGRADING.md: from version 28 to version 27, with Worklog stopped and after a manual backup.
# The tables and their indexes as version 27 had them.
WAY_BACK = """\
CREATE TABLE releases(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',release_date TEXT CHECK(release_date IS NULL OR date(release_date) IS release_date),status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN('planned','released','cancelled')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE release_contents(release_id TEXT NOT NULL REFERENCES releases(id),target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap','step','github')),target_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(release_id,target_type,target_id));
CREATE INDEX release_contents_target ON release_contents(target_type,target_id,release_id);
CREATE TABLE release_checks(id TEXT PRIMARY KEY,release_id TEXT NOT NULL REFERENCES releases(id),kind TEXT NOT NULL CHECK(kind IN('notify','test','prepare')),text TEXT NOT NULL,details TEXT NOT NULL DEFAULT '',person_id TEXT REFERENCES people(id),due_date TEXT CHECK(due_date IS NULL OR date(due_date) IS due_date),status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','doing','waiting','blocked','done')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX release_checks_release ON release_checks(release_id,archived,kind);
PRAGMA user_version=27;
"""
RELEASE_TABLES = ("releases", "release_contents", "release_checks")
# Compared row by row on their own: each loses its release rows and nothing else.
SHRUNK = ("search_index", "search_keys", "discussion_contexts", "discussion_change_contexts")
STAMP = "2026-11-01T00:00:00+00:00"


def to_v27(path):
    """Take a current database back to v27 the documented way."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def worked(data):
    """A current database with a work item, a roadmap, a person and a discussion tagged with the item,
    and their IDs."""
    client = session(data)
    try:
        item = call(client, "POST", "/work-items", {"title": "Export button"})
        roadmap = call(client, "POST", "/roadmaps", {"title": "Exports"})
        person = call(client, "POST", "/people", {"name": "Ana"})
        thread = call(client, "POST", "/discussions", {"title": "Exports", "text": "Who ships them?",
                                                        "contexts": [{"type": "item", "id": item["id"]}]})
    finally:
        client.__exit__(None, None, None)
    ids = {"item": item["id"], "roadmap": roadmap["id"], "person": person["id"], "discussion": thread["discussion"]["id"]}
    return data / "worklog.sqlite3", ids


def index(conn, identity, kind, title, owner):
    doc = conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,?,?,'',?,0)",
                       (identity, kind, title, owner)).lastrowid
    conn.execute("INSERT INTO search_keys(entity_id,doc,type,owner_id) VALUES(?,?,?,?)", (identity, doc, kind, owner))


def release(conn, ids):
    """A release as v27 kept it: holding the work item and the roadmap, with a check that names the
    person and one due on a day, each in search, a discussion tag of it with the feed's record of
    that tag, and the history it wrote, which stays."""
    rid, checks = str(uuid.uuid4()), (str(uuid.uuid4()), str(uuid.uuid4()))
    conn.execute("INSERT INTO releases(id,title,release_date,created_at,updated_at) VALUES(?,'November','2026-11-20',?,?)",
                 (rid, STAMP, STAMP))
    for kind in ("item", "roadmap"):
        conn.execute("INSERT INTO release_contents(release_id,target_type,target_id,created_at) VALUES(?,?,?,?)",
                     (rid, kind, ids[kind], STAMP))
    conn.execute("INSERT INTO release_checks(id,release_id,kind,text,person_id,created_at,updated_at) "
                 "VALUES(?,?,'notify','Tell support',?,?,?)", (checks[0], rid, ids["person"], STAMP, STAMP))
    conn.execute("INSERT INTO release_checks(id,release_id,kind,text,due_date,created_at,updated_at) "
                 "VALUES(?,?,'prepare','Add the secret','2026-11-13',?,?)", (checks[1], rid, STAMP, STAMP))
    index(conn, rid, "release", "November", rid)
    for check, text in zip(checks, ("Tell support", "Add the secret"), strict=True):
        index(conn, check, "release_check", text, rid)
    conn.execute("INSERT INTO discussion_contexts(discussion_id,context_type,context_id,home,added_by,added_by_kind,"
                 "created_at) VALUES(?,'release',?,0,'local-user','human',?)", (ids["discussion"], rid, STAMP))
    seq, = conn.execute("SELECT max(seq) FROM discussion_changes").fetchone()
    conn.execute("INSERT INTO discussion_change_contexts(seq,context_type,context_id) VALUES(?,'release',?)", (seq, rid))
    conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,"
                 "before_json,after_json) VALUES('release-event',?,'release.create','human','You','human',?,"
                 "'2026-11-01','null',?)", (rid, STAMP, json.dumps({"id": rid, "title": "November"})))
    return rid


def test_the_upgrade_drops_every_release_its_search_rows_and_tags_and_changes_no_other_record(tmp_path):
    database, ids = worked(tmp_path / "data")
    to_v27(database)
    with closing(sqlite3.connect(database)) as conn:
        rid = release(conn, ids)
        conn.commit()
    recovery.validate_database(database)
    _, _, before = contents(database)
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    assert [len(before[table]) for table in RELEASE_TABLES] == [1, 2, 2]
    assert not set(RELEASE_TABLES) & set(after)
    # The index loses the release's row and its checks', the tables of tags the release's tag and the
    # feed's record of it; each keeps every other row.
    assert after["search_index"] == [row for row in before["search_index"] if row[1] not in ("release", "release_check")]
    assert after["search_keys"] == [row for row in before["search_keys"] if row[2] not in ("release", "release_check")]
    for table in ("discussion_contexts", "discussion_change_contexts"):
        assert [row for row in before[table] if rid in row] and not [row for row in after[table] if rid in row]
        assert after[table] == [row for row in before[table] if rid not in row]
    # Every other table keeps every row: the records the release held, the discussion, its feed and
    # the release's history among them. The index's own tables change with its rows.
    kept = [table for table in before if table not in (*RELEASE_TABLES, *SHRUNK) and not table.startswith("search_")]
    assert set(after) == set(before) - set(RELEASE_TABLES)
    assert {table: after[table] for table in kept} == {table: before[table] for table in kept}
    assert [row[2] for row in after["events"] if row[0] == "release-event"] == ["release.create"]
    # Nothing is left for the doctor to find, and an export holds no releases.
    assert doctor_database(database)["ok"] is True
    names = zipfile.ZipFile(io.BytesIO(recovery.export_database(database))).namelist()
    assert not [name for name in names if "release" in name]
    snapshot, = (tmp_path / "data" / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 27
        assert [conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in RELEASE_TABLES] == [1, 2, 2]


def test_the_way_back_gives_v27s_exact_shape_and_upgrades_again(tmp_path):
    database, _ = worked(tmp_path / "data")
    to_v27(database)
    recovery.validate_database(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 27
        assert recovery._schema_signature(conn) == sealed.shape(27)
    Database(database).initialize()
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    # The SQL tested here is the SQL docs/UPGRADING.md gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "docs" / "UPGRADING.md").read_text(encoding="utf-8")


def test_the_routes_are_gone_and_nothing_takes_a_release(tmp_path):
    _, ids = worked(tmp_path / "data")
    client = session(tmp_path / "data")
    try:
        identity = str(uuid.uuid4())
        for method, path, body in (
                ("GET", "/releases", None), ("POST", "/releases", {"title": "November"}),
                ("GET", f"/releases/{identity}", None), ("PATCH", f"/releases/{identity}", {"revision": 1}),
                ("POST", f"/releases/{identity}/archive", {"revision": 1}), ("POST", f"/releases/{identity}/recover", {"revision": 1}),
                ("POST", "/release-contents", {"releaseId": identity, "targetType": "item", "targetId": ids["item"]}),
                ("POST", "/release-contents/remove", {"releaseId": identity, "targetType": "item", "targetId": ids["item"]}),
                ("POST", "/release-checks", {"releaseId": identity, "kind": "test", "text": "Smoke test"}),
                ("GET", f"/release-checks/{identity}", None), ("PATCH", f"/release-checks/{identity}", {"revision": 1}),
                ("POST", f"/release-checks/{identity}/archive", {"revision": 1}),
                ("POST", f"/release-checks/{identity}/recover", {"revision": 1})):
            response = client.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code in (404, 405), (method, path, response.text)
        # Search can't be narrowed to releases, the browser counts no Releases page, a discussion takes
        # no release tag, a proposal no release change, and the context lists no releases.
        for kind in ("release", "release_check"):
            assert client.get("/api/v1/search", params={"q": "november", "type": kind}).status_code == 422
        for path, body in (("/page-views", {"page": "releases"}),
                           ("/discussions", {"title": "Ship", "text": "When?", "contexts": [{"type": "release", "id": identity}]}),
                           (f"/discussions/{ids['discussion']}/tags", {"type": "release", "id": identity})):
            response = client.post("/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code == 422, (path, response.text)
        token = call(client, "POST", "/credentials", {"label": "Coding agent"})["token"]
        agent = {"Authorization": "Bearer " + token}
        for action, payload in (("release.create", {"title": "November"}), ("check.create", {"releaseId": identity, "kind": "test", "text": "T"})):
            response = client.post("/api/v1/proposals", json={"title": "Plan the release", "rationale": "Ship it",
                                                              "operations": [{"action": action, "payload": payload}]},
                                   headers={"Idempotency-Key": str(uuid.uuid4()), **agent})
            assert response.status_code == 422, response.text
        assert "releases" not in call(client, "GET", "/context", headers=agent)
    finally:
        client.__exit__(None, None, None)


def test_the_agent_command_line_has_no_releases(capsys):
    with pytest.raises(SystemExit):
        cli.main(["agent", "releases", "list"])
    assert "invalid choice: 'releases'" in capsys.readouterr().err
    client = cli.AgentClient("http://127.0.0.1:8893", "token")
    for route in ("/releases", f"/releases/{uuid.uuid4()}"):
        with pytest.raises(ValueError, match="outside proposal/read scope"):
            client.request("GET", route)


def test_the_doctor_reports_a_release_tag_written_since_as_a_type_its_table_never_takes(tmp_path):
    # Decision R5: the tag CHECK still allows 'release', so the doctor finds one written behind
    # Worklog's back.
    database, ids = worked(tmp_path / "data")
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO discussion_contexts(discussion_id,context_type,context_id,home,added_by,added_by_kind,"
                     "created_at) VALUES(?,'release','gone',0,'local-user','human',?)", (ids["discussion"], STAMP))
        conn.commit()
    references = doctor_database(database)["checks"]["references"]
    assert references["examples"] == [{"table": "discussion_contexts", "type": "release", "rows": 1,
                                       "problem": "a type this table never takes"}]
