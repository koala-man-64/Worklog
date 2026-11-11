"""Schema v27 removes Reminders (decision R1 in docs/ROADMAPS-RETHINK.md, step 0.4): their page, routes
and agent commands are gone, search finds none, and the upgrade drops the reminders table, every
reminder and its search row with it, and changes no other record. The pre-migration backup keeps the
table, and the documented way back gives v26's exact shape."""

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

# docs/UPGRADING.md: from version 27 to version 26, with Worklog stopped and after a manual backup.
# The table and its indexes as version 26 had them.
WAY_BACK = """\
CREATE TABLE reminders(id TEXT PRIMARY KEY,title TEXT NOT NULL,due_date TEXT NOT NULL,due_time TEXT,state TEXT NOT NULL DEFAULT 'open' CHECK(state IN('open','done')),target_type TEXT CHECK(target_type IN('item','ticket','roadmap','person','repository','chat')),target_id TEXT,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,done_at TEXT,notes TEXT NOT NULL DEFAULT '',CHECK((target_type IS NULL)=(target_id IS NULL)));
CREATE INDEX reminders_due ON reminders(archived,state,due_date,due_time);
CREATE INDEX reminders_target ON reminders(target_type,target_id);
PRAGMA user_version=26;
"""
STAMP = "2026-11-01T00:00:00+00:00"


def to_v26(path):
    """Take a current database back to v26 the documented way."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def worked(data):
    """A current database with a work item, and the item's ID."""
    client = session(data)
    try:
        item = call(client, "POST", "/work-items", {"title": "Ship the release"})
    finally:
        client.__exit__(None, None, None)
    return data / "worklog.sqlite3", item["id"]


def remind(conn, title, notes, *, target=(None, None), archived=0):
    """A reminder as v26 kept it, with the search row and key its service wrote: the notes as the
    row's text, and the reminder as its own owner."""
    identity = str(uuid.uuid4())
    conn.execute("INSERT INTO reminders(id,title,due_date,due_time,target_type,target_id,author_id,author_label,"
                 "author_kind,archived,created_at,updated_at,notes) VALUES(?,?,'2026-11-06','14:25',?,?,'local-user',"
                 "'You','human',?,?,?,?)", (identity, title, *target, archived, STAMP, STAMP, notes))
    doc = conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,'reminder',?,?,?,?)",
                       (identity, title, notes, identity, archived)).lastrowid
    conn.execute("INSERT INTO search_keys(entity_id,doc,type,owner_id) VALUES(?,?,'reminder',?)", (identity, doc, identity))


def found(path, words):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT count(*) FROM search_index WHERE search_index MATCH ?", (words,)).fetchone()[0]


def test_the_upgrade_drops_every_reminder_and_its_search_row_and_changes_no_other_record(tmp_path):
    database, item = worked(tmp_path / "data")
    to_v26(database)
    # Reminders as v26 kept them, one naming the work item and one archived, and the history one
    # wrote, which stays.
    with closing(sqlite3.connect(database)) as conn:
        remind(conn, "Renew the certificate", "Before Friday", target=("item", item))
        remind(conn, "Call the vendor", "About the invoice", archived=1)
        conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,"
                     "before_json,after_json) VALUES('reminder-event','r','reminder.create','human','You','human',?,"
                     "'2026-11-01','null',?)", (STAMP, json.dumps({"id": "r", "title": "Renew the certificate"})))
        conn.commit()
    recovery.validate_database(database)
    assert (found(database, "certificate"), found(database, "vendor"), found(database, "release")) == (1, 1, 1)
    _, _, before = contents(database)
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    assert "reminders" not in after and len(before["reminders"]) == 2
    # The search index loses each reminder's row and key, and only those: the work item's stay.
    assert after["search_index"] == [row for row in before["search_index"] if row[1] != "reminder"]
    assert after["search_keys"] == [row for row in before["search_keys"] if row[2] != "reminder"]
    assert (found(database, "certificate"), found(database, "vendor"), found(database, "release")) == (0, 0, 1)
    # Every other table keeps every row, the reminder's history among them; the index's own tables
    # change with its rows.
    kept = [table for table in before if table != "reminders" and not table.startswith("search_")]
    assert set(after) == set(before) - {"reminders"}
    assert {table: after[table] for table in kept} == {table: before[table] for table in kept}
    assert [row[2] for row in after["events"] if row[0] == "reminder-event"] == ["reminder.create"]
    # Nothing is left for the doctor to find, and an export holds no reminders.
    assert doctor_database(database)["ok"] is True
    assert "records/reminders.json" not in zipfile.ZipFile(io.BytesIO(recovery.export_database(database))).namelist()
    snapshot, = (tmp_path / "data" / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 26
        assert conn.execute("SELECT count(*) FROM reminders").fetchone()[0] == 2


def test_the_way_back_gives_v26s_exact_shape_and_upgrades_again(tmp_path):
    database, _ = worked(tmp_path / "data")
    to_v26(database)
    recovery.validate_database(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 26
        assert recovery._schema_signature(conn) == sealed.shape(26)
    Database(database).initialize()
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    # The SQL tested here is the SQL docs/UPGRADING.md gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "docs" / "UPGRADING.md").read_text(encoding="utf-8")


def test_the_routes_are_gone_and_neither_search_nor_usage_knows_reminders(tmp_path):
    worked(tmp_path / "data")
    client = session(tmp_path / "data")
    try:
        identity = str(uuid.uuid4())
        for method, path, body in (("GET", "/reminders", None), ("POST", "/reminders", {"title": "Renew", "date": "2026-11-06"}),
                                   ("GET", f"/reminders/{identity}", None), ("PATCH", f"/reminders/{identity}", {"revision": 1}),
                                   ("POST", f"/reminders/{identity}/archive", {"revision": 1}),
                                   ("POST", f"/reminders/{identity}/recover", {"revision": 1})):
            response = client.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code in (404, 405), (method, path, response.text)
        # Search can't be narrowed to reminders, and the browser counts no Reminders page.
        assert client.get("/api/v1/search", params={"q": "renew", "type": "reminder"}).status_code == 422
        response = client.post("/api/v1/page-views", json={"page": "reminders"}, headers={"Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 422, response.text
    finally:
        client.__exit__(None, None, None)


def test_the_agent_command_line_has_no_reminders(capsys):
    with pytest.raises(SystemExit):
        cli.main(["agent", "reminders", "list"])
    assert "invalid choice: 'reminders'" in capsys.readouterr().err
    client = cli.AgentClient("http://127.0.0.1:8893", "token")
    for method, route in (("GET", "/reminders"), ("POST", "/reminders"), ("GET", f"/reminders/{uuid.uuid4()}"),
                          ("PATCH", f"/reminders/{uuid.uuid4()}")):
        with pytest.raises(ValueError, match="outside proposal/read scope"):
            client.request(method, route, {})
