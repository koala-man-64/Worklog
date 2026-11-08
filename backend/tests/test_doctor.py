"""The read-only doctor, `worklog doctor` (consolidation step 4.4, B-09).

It checks what the schema cannot state: records named by type and id, a step's parent, a chat tracked
twice, the search index and its keys, besides SQLite's own integrity and foreign keys. It reads a
copy, so the database is never changed, and a clean one reports nothing."""

import hashlib
import json
import re
import sqlite3
import typing
import uuid
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import cli, doctor, record_kinds, search
from worklog.app import create_app
from worklog.doctor import doctor_database
from worklog.schema import BASELINE

ORIGIN = "http://127.0.0.1:8877"


def call(c, method, path, body=None):
    response = c.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def world(tmp_path):
    """A stopped Worklog whose records name one another every way the doctor checks."""
    data = tmp_path / "data"
    with TestClient(create_app(data, testing=True), base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        item = call(c, "POST", "/work-items", {"title": "Parser"})
        note = call(c, "POST", "/notes", {"ownerType": "item", "ownerId": item["id"], "title": "Scratch", "text": "Notes"})
        link = call(c, "POST", "/links", {"ownerType": "item", "ownerId": item["id"], "url": "https://example.com/spec"})
        chat = call(c, "POST", "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": "chat-1", "title": "Build"})
        task = call(c, "POST", "/tasks", {"itemId": item["id"], "text": "Write the tests"})
        parent, child = str(uuid.uuid4()), str(uuid.uuid4())
        roadmap = call(c, "POST", "/roadmaps", {"title": "Plan", "steps": [{"id": parent, "title": "Parent"},
                                                                           {"id": child, "title": "Child", "parentId": parent}]})
        call(c, "POST", "/reminders", {"title": "Check the parser", "date": date.today().isoformat(),
                                       "targetType": "item", "targetId": item["id"]})
    names = {"item": item["id"], "note": note["id"], "link": link["id"], "chat": chat["id"], "task": task["id"],
             "roadmap": roadmap["id"], "child": child}
    return data, data / "worklog.sqlite3", names


def test_a_clean_database_reports_nothing_and_is_not_changed(world):
    data, database, _ = world
    original = digest(database)
    # A reader holding the database doesn't stop the doctor, which reads it as a backup does.
    with closing(sqlite3.connect(database, isolation_level=None)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM events").fetchone()
        report = doctor_database(database)
        reader.execute("ROLLBACK")
    assert report["ok"] is True
    assert {name: check["problems"] for name, check in report["checks"].items()} == {
        "integrity": 0, "references": 0, "steps": 0, "trackedChats": 0, "searchIndex": 0, "searchKeys": 0}
    assert digest(database) == original
    assert list(data.glob("*doctor*")) == []


def test_each_check_finds_what_the_schema_cannot_refuse(world):
    data, database, names = world
    with closing(sqlite3.connect(database)) as conn:
        conn.row_factory = sqlite3.Row
        # A supporting record of a type its table never takes, and one whose owner is gone.
        # Since v23 a CHECK refuses that type, so it is written as a tool ignoring CHECKs would write it.
        conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute("UPDATE notes SET owner_type='ticket' WHERE id=?", (names["note"],))
        conn.execute("PRAGMA ignore_check_constraints=OFF")
        conn.execute("UPDATE web_links SET owner_id='gone-item' WHERE id=?", (names["link"],))
        # A step whose parent is gone, and the chat tracked twice on its work item.
        conn.execute("UPDATE steps SET parent_id='gone-step' WHERE id=?", (names["child"],))
        tracked = dict(conn.execute("SELECT * FROM agent_chats WHERE id=?", (names["chat"],)).fetchone(), id="chat-again")
        conn.execute(f"INSERT INTO agent_chats({','.join(tracked)}) VALUES({','.join('?' for _ in tracked)})", tuple(tracked.values()))
        # A task whose work item is gone breaks a foreign key.
        conn.execute("UPDATE tasks SET item_id='gone-item' WHERE id=?", (names["task"],))
        # A key whose row is gone, a row with no key, and a key whose record is gone.
        (link_doc,), = conn.execute("SELECT doc FROM search_keys WHERE entity_id=?", (names["link"],))
        conn.execute("DELETE FROM search_index WHERE rowid=?", (link_doc,))
        conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES('stray','item','Stray','','',0)")
        conn.execute("DELETE FROM reminders")
        conn.commit()
    report = doctor_database(database)
    checks = report["checks"]
    assert report["ok"] is False
    references = checks["references"]["examples"]
    assert {"table": "notes", "type": "ticket", "rows": 1, "problem": "a type this table never takes"} in references
    assert {"table": "web_links", "type": "item", "id": "gone-item", "problem": "the record it names is gone"} in references
    assert checks["references"]["problems"] == 2
    assert checks["steps"]["examples"] == [{"step": names["child"], "parent": "gone-step",
                                           "problem": "its parent is gone or on another roadmap"}]
    assert checks["trackedChats"]["examples"] == [{"ownerType": "item", "ownerId": names["item"], "chatId": "chat-1", "times": 2}]
    # SQLite's own check finds the CHECK the note breaks, and the task's broken foreign key.
    integrity = checks["integrity"]["examples"]
    assert [e for e in integrity if isinstance(e, str) and "CHECK constraint failed in notes" in e]
    assert [(e["table"], e["references"]) for e in integrity if isinstance(e, dict)] == [("tasks", "work_items")]
    keys = {(e.get("entityId"), e["problem"]) for e in checks["searchKeys"]["examples"]}
    assert (names["link"], "its row is gone") in keys
    assert ("stray", "a row with no key") in keys
    assert any(problem == "its record is gone" for _, problem in keys)
    assert checks["searchKeys"]["problems"] == 3
    assert checks["searchIndex"]["problems"] == 0


def test_fts_damage_shows_in_the_index_check(world):
    data, database, names = world
    with closing(sqlite3.connect(database)) as conn:
        (doc,), = conn.execute("SELECT doc FROM search_keys WHERE entity_id=?", (names["item"],))
        conn.execute("DELETE FROM search_index_content WHERE id=?", (doc,))
        conn.commit()
    report = doctor_database(database)
    assert report["checks"]["searchIndex"]["problems"] == 1
    assert report["ok"] is False


def test_the_cli_prints_the_report_and_fails_when_it_finds_problems(world, capsys):
    data, database, names = world
    assert cli.main(["--data-dir", str(data), "doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("UPDATE web_links SET owner_id='gone-item' WHERE id=?", (names["link"],))
        conn.commit()
    assert cli.main(["--data-dir", str(data), "doctor"]) == 1
    assert json.loads(capsys.readouterr().out)["checks"]["references"]["problems"] == 1


def test_the_doctor_asks_for_a_database_worklog_has_upgraded(tmp_path):
    database = tmp_path / "worklog.sqlite3"
    sealed.build(database, 21)
    with pytest.raises(ValueError, match="schema version 21"):
        doctor_database(database)


def test_the_doctor_knows_every_kind_the_schema_and_the_index_name():
    # Every search type, and the table each kind names.
    assert set(doctor.SEARCHED) == set(typing.get_args(search.SearchType))
    for kind, sql in record_kinds.SUMMARIES.items():
        if kind in doctor.TABLES:
            assert doctor.TABLES[kind] in re.findall(r"FROM (\w+)", sql), kind
    # Each reference takes the types its table's CHECK allows, where it has one.
    for table, kind_column, _, kinds in doctor.REFERENCES:
        assert set(kinds) <= set(doctor.TABLES)
        definition = re.search(rf"CREATE TABLE {table}\s*\((.*?)\);", BASELINE, re.S).group(1)
        check = re.search(rf"{kind_column} TEXT[^,]*CHECK\({kind_column} IN ?\(([^)]*)\)\)", definition)
        if check:
            assert set(kinds) == {value.strip().strip("'") for value in check.group(1).split(",")}, table
