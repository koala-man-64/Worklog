"""Schema v10 gives people a title, a description and contact details beside their team, which
v9 kept as `role`, and ties them to repositories: upgrade, rollback, the documented way back
and restore keep every person and tie."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_search_keys_recovery import to_v10
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.db import Database

STAMP = "2026-01-01T00:00:00+00:00"
ORIGIN = "http://127.0.0.1:8877"
# README: from version 10 to version 9, with Worklog stopped and after a manual backup.
WAY_BACK = """\
BEGIN;
DELETE FROM person_links WHERE target_type='repository';
ALTER TABLE person_links RENAME TO person_links_v10;
CREATE TABLE person_links(id TEXT PRIMARY KEY,person_id TEXT NOT NULL REFERENCES people(id),target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap')),target_id TEXT NOT NULL,relationship TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(person_id,target_type,target_id));
INSERT INTO person_links SELECT * FROM person_links_v10;
DROP TABLE person_links_v10;
CREATE INDEX person_links_target ON person_links(target_type,target_id);
CREATE INDEX person_links_person ON person_links(person_id);
ALTER TABLE people DROP COLUMN title;
ALTER TABLE people DROP COLUMN description;
ALTER TABLE people DROP COLUMN email;
ALTER TABLE people DROP COLUMN phone;
ALTER TABLE people DROP COLUMN chat;
ALTER TABLE people DROP COLUMN location;
ALTER TABLE people RENAME COLUMN team TO role;
UPDATE search_index SET body=(SELECT role || char(10) || notes FROM people WHERE people.id=search_index.entity_id) WHERE type='person';
PRAGMA user_version=9;
COMMIT;
"""
LINKS = [("ann-item", "ann", "item", "item", "Owner", STAMP), ("ann-road", "ann", "roadmap", "road", "Stakeholder", STAMP),
         ("bo-item", "bo", "item", "item", "", STAMP)]
OPEN_UPDATE = [{"action": "person.update", "payload": {"id": "ann", "revision": 1, "role": "Payments"}}]
DRAFT_CREATE = [{"action": "person.create", "payload": {"name": "Cy", "role": "Data"}},
                {"action": "item.update", "payload": {"id": "item", "revision": 1, "title": "Work"}}]
APPROVED_CREATE = [{"action": "person.create", "payload": {"name": "Di", "role": "Ops"}}]


def to_v9(path):
    """Take a current database back to the v9 shape the documented way, as older shapes' helpers do:
    to version 10 first, then to version 9."""
    to_v10(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def v9_database(path):
    """The shape main shipped as v9, with two people tied to work and a roadmap, and proposals for them."""
    Database(path).initialize()
    to_v9(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES('item','Work',0,?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO roadmaps(id,title,overview,revision,created_at,updated_at) VALUES('road','Plan','',1,?,?)", (STAMP, STAMP))
        for identity, name, role, notes in (("ann", "Ann Lee", "Platform", "Kafka lead"), ("bo", "Bo Ruiz", "", "")):
            conn.execute("INSERT INTO people(id,name,role,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                         (identity, name, role, notes, STAMP, STAMP))
            conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,'person',?,?,?,0)",
                         (identity, name, (role + "\n" + notes).strip(), identity))
        conn.executemany("INSERT INTO person_links VALUES(?,?,?,?,?,?)", LINKS)
        for identity, status, operations in (("open", "submitted", OPEN_UPDATE), ("draft", "draft", DRAFT_CREATE),
                                             ("done", "approved", APPROVED_CREATE)):
            conn.execute("INSERT INTO proposals VALUES(?,?,'',?,1,'credential','Agent','agent',?,NULL,?,?)",
                         (identity, identity.title(), status, json.dumps(operations), STAMP, STAMP))
        conn.commit()
    recovery.validate_database(path)


def proposals(path):
    with closing(sqlite3.connect(path)) as conn:
        return {r[0]: (r[1], json.loads(r[2])) for r in conn.execute("SELECT id,revision,operations FROM proposals")}


def test_v9_upgrade_makes_role_the_team_keeps_every_tie_and_restores(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    v9_database(path)
    kept = {t: rows(path, t) for t in ("work_items", "roadmaps", "person_links", "search_index")}
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == recovery._expected_schema()
        people = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM people")}
    assert {t: rows(path, t) for t in kept} == kept
    assert people["ann"] == {"id": "ann", "name": "Ann Lee", "team": "Platform", "notes": "Kafka lead", "archived": 0,
                             "revision": 1, "created_at": STAMP, "updated_at": STAMP, "title": "", "description": "",
                             "email": "", "phone": "", "chat": "", "location": ""}
    assert people["bo"]["team"] == ""
    # Open proposals set the same field by its new name, under a new revision; approved ones stay as made.
    assert proposals(path) == {
        "open": (2, [{"action": "person.update", "payload": {"id": "ann", "revision": 1, "team": "Payments"}}]),
        "draft": (2, [{"action": "person.create", "payload": {"name": "Cy", "team": "Data"}}, DRAFT_CREATE[1]]),
        "done": (1, APPROVED_CREATE)}
    snapshot, = (path.parent / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert recovery._schema_signature(conn) == recovery._expected_schema(people=False)
    # A second start finds nothing to upgrade and takes no second snapshot.
    Database(path).initialize()
    assert len(list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))) == 1
    restored = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(restored, snapshot, tmp_path / "restore-backups")
    with Database(restored).read() as conn:
        assert recovery._schema_signature(conn) == recovery._expected_schema()
        assert conn.execute("SELECT team FROM people WHERE id='ann'").fetchone()[0] == "Platform"
        assert conn.execute("SELECT count(*) FROM person_links").fetchone()[0] == len(LINKS)


def test_an_agent_proposal_made_for_role_still_applies_after_the_upgrade(tmp_path):
    v9_database(tmp_path / "worklog.sqlite3")
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers["Origin"] = ORIGIN
        client.headers["X-CSRF-Token"] = client.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={}).json()["csrfToken"]
        approved = client.post("/api/v1/proposals/open/approve", json={"revision": 2}, headers={"Idempotency-Key": "approve-open"})
        assert approved.status_code == 200, approved.text
        ann = client.get("/api/v1/people/ann").json()
        assert (ann["team"], ann["title"], ann["revision"]) == ("Payments", "", 2)
        # A repository tie now fits beside the work and roadmap ties.
        repo = client.post("/api/v1/repositories", json={"name": "api", "url": "https://example.com/acme/api"},
                           headers={"Idempotency-Key": "register-api"}).json()
        tie = client.post("/api/v1/person-links", json={"personId": "ann", "targetType": "repository", "targetId": repo["id"],
                                                        "relationship": "Maintainer"}, headers={"Idempotency-Key": "tie-ann-api"})
        assert tie.status_code == 200, tie.text
        assert [(r["repository"]["name"], r["relationship"]) for r in client.get("/api/v1/people/ann").json()["repositories"]] == [
            ("api", "Maintainer")]


def test_a_failed_person_upgrade_leaves_the_v9_database_as_it_was(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    v9_database(path)
    before = {t: rows(path, t) for t in ("people", "person_links", "proposals")}

    def fail(conn):
        raise RuntimeError("Injected person upgrade failure")

    # The links are rebuilt and the columns renamed and added before this runs.
    monkeypatch.setattr(migrations, "_propose_team", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert recovery._schema_signature(conn) == recovery._expected_schema(people=False)
    assert {t: rows(path, t) for t in before} == before
    recovery.validate_database(path)


def test_a_v9_shape_cannot_claim_version_10_and_a_v10_shape_cannot_claim_9(tmp_path):
    older = tmp_path / "older.sqlite3"
    v9_database(older)
    current = tmp_path / "current.sqlite3"
    Database(current).initialize()
    for path, version in ((older, 10), (current, 9)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)


def test_the_documented_way_back_to_v9_keeps_people_and_their_work_ties_and_upgrades_again(tmp_path):
    # The SQL tested here is the SQL the README gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    path = tmp_path / "worklog.sqlite3"
    v9_database(path)
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE people SET title='Staff Engineer',description='Owns ingest',email='ann@example.com',"
                     "phone='+1 312 555 0100',chat='@ann',location='Chicago' WHERE id='ann'")
        conn.execute("INSERT INTO repositories(id,name,created_at,updated_at) VALUES('repo','api',?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO person_links VALUES('ann-repo','ann','repository','repo','Maintainer',?)", (STAMP,))
        conn.execute("UPDATE search_index SET body='Staff Engineer\nPlatform\nOwns ingest\nann@example.com\nKafka lead' WHERE entity_id='ann'")
        conn.commit()
    to_v9(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert recovery._schema_signature(conn) == recovery._expected_schema(people=False)
        assert conn.execute("SELECT role,notes FROM people WHERE id='ann'").fetchone() == ("Platform", "Kafka lead")
        # Version 9's search finds a person by what version 9 shows of them, and nothing it dropped.
        assert conn.execute("SELECT body FROM search_index WHERE entity_id='ann'").fetchone()[0] == "Platform\nKafka lead"
        assert not conn.execute("SELECT 1 FROM search_index WHERE search_index MATCH 'ingest'").fetchall()
    assert rows(path, "person_links") == sorted(LINKS)
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert tuple(conn.execute("SELECT team,title,email FROM people WHERE id='ann'").fetchone()) == ("Platform", "", "")


def test_a_way_back_that_fails_part_way_changes_nothing(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    v9_database(path)
    Database(path).initialize()
    to_v10(path)  # README: from version 11 to version 10 first
    before = {t: rows(path, t) for t in ("people", "person_links", "search_index")}
    # As SQLite before 3.35 refuses DROP COLUMN: the statement fails once the links are rebuilt.
    broken = WAY_BACK.replace("DROP COLUMN title;", "DROP COLUMN no_such_column;")
    with closing(sqlite3.connect(path)) as conn, pytest.raises(sqlite3.OperationalError):
        conn.executescript(broken)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        assert recovery._schema_signature(conn) == recovery._expected_schema(search_keys=False)
    assert {t: rows(path, t) for t in before} == before
    recovery.validate_database(path)


def test_a_malformed_open_proposal_is_left_for_review_to_refuse(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    v9_database(path)
    odd = ["not json", json.dumps({"action": "person.update"}), json.dumps([1, None]),
           json.dumps([{"action": ["person.update"], "payload": {"role": "x"}}]), json.dumps([{"action": "person.create", "payload": "role"}]),
           json.dumps([{"action": "person.update", "payload": {"role": "x", "team": "y"}}]),
           # Proposals are written without NaN, so carrying it over would store what they refuse.
           json.dumps([{"action": "person.update", "payload": {"id": "ann", "revision": 1, "role": float("nan")}}])]
    with closing(sqlite3.connect(path)) as conn:
        for number, operations in enumerate(odd):
            conn.execute("INSERT INTO proposals VALUES(?,'Odd','','draft',1,'credential','Agent','agent',?,NULL,?,?)",
                         (f"odd-{number}", operations, STAMP, STAMP))
        conn.commit()
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT operations,revision FROM proposals WHERE id LIKE 'odd-%' ORDER BY id").fetchall() == [
            (operations, 1) for operations in odd]
