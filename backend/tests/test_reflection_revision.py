"""Schema v21 gives each day's reflection its own revision (consolidation step 4.1, REM-26).

A reorder or new work no longer conflicts with a reflection being written, and two edits of one
reflection still do. The upgrade starts each reflection at its day's revision and changes no record,
and the documented way back gives v20's exact shape."""

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import pytest
import sealed
from fastapi.testclient import TestClient
from test_sealed_schemas import contents
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
DAY = "2026-11-04"
# README: from version 21 to version 20, with Worklog stopped and after a manual backup.
WAY_BACK = """\
BEGIN;
ALTER TABLE days DROP COLUMN reflection_revision;
PRAGMA user_version=20;
COMMIT;
"""


def to_v20(path):
    """Take a current database back to the v20 shape the documented way: to version 21 first."""
    from test_discussion_tag_recovery import to_v21

    to_v21(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def send(c, method, path, body=None):
    return c.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})


def call(c, method, path, body=None):
    response = send(c, method, path, body)
    assert response.status_code == 200, response.text
    return response.json()


def test_new_work_and_a_reorder_leave_a_reflection_draft_able_to_save(client):
    first, second = (call(client, "POST", "/work-items", {"title": title}) for title in ("First", "Second"))
    call(client, "PUT", f"/days/{DAY}/entries/{first['id']}", {"revision": 0, "planned": True})
    opened = call(client, "GET", f"/days/{DAY}")
    # Another tab adds work to the day and reorders it while the reflection is being written.
    call(client, "PUT", f"/days/{DAY}/entries/{second['id']}", {"revision": 0, "planned": True})
    current = call(client, "GET", f"/days/{DAY}")
    call(client, "POST", f"/days/{DAY}/reorder", {"revision": current["revision"], "ids": [e["id"] for e in reversed(current["entries"])]})
    reordered = call(client, "GET", f"/days/{DAY}")
    saved = call(client, "PUT", f"/days/{DAY}/reflection", {"revision": opened["reflectionRevision"], "text": "A good day"})
    assert saved["revision"] == opened["reflectionRevision"] + 1
    after = call(client, "GET", f"/days/{DAY}")
    assert (after["reflection"], after["reflectionRevision"]) == ("A good day", saved["revision"])
    # The reflection leaves the day's own revision, which guards its entries and their order.
    assert after["revision"] == reordered["revision"]


def test_a_reflection_begun_on_an_empty_day_still_saves_after_work_is_first_planned_on_it(client):
    """From the review of #129: the day's row is made by the first entry, and its reflection
    revision starts at the 0 an empty day reads as."""
    opened = call(client, "GET", f"/days/{DAY}")
    assert opened["reflectionRevision"] == 0 and opened["entries"] == []
    item = call(client, "POST", "/work-items", {"title": "First of the day"})
    call(client, "PUT", f"/days/{DAY}/entries/{item['id']}", {"revision": 0, "planned": True})
    assert call(client, "GET", f"/days/{DAY}")["reflectionRevision"] == 0
    saved = call(client, "PUT", f"/days/{DAY}/reflection", {"revision": opened["reflectionRevision"], "text": "Started well"})
    assert saved["revision"] == 1


def test_two_edits_of_one_reflection_still_conflict(client):
    opened = call(client, "GET", f"/days/{DAY}")
    assert (opened["revision"], opened["reflectionRevision"]) == (0, 0)
    call(client, "PUT", f"/days/{DAY}/reflection", {"revision": 0, "text": "From one tab"})
    stale = send(client, "PUT", f"/days/{DAY}/reflection", {"revision": 0, "text": "From another"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "conflict"
    assert call(client, "GET", f"/days/{DAY}")["reflection"] == "From one tab"


def test_the_v21_upgrade_starts_each_reflection_at_its_days_revision_and_changes_no_record(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO days(date,reflection,revision) VALUES('2026-11-04','Busy',5),('2026-11-05','',1)")
        conn.commit()
    to_v20(path)
    recovery.validate_database(path)
    _, _, before = contents(path)
    Database(path).initialize()
    version, signature, after = contents(path)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    # A draft opened before the upgrade carries its day's revision, which its reflection now starts at.
    assert after.pop("days") == [(*row, row[2]) for row in before.pop("days")]
    assert after == before
    snapshot, = (tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20


def test_the_documented_way_back_to_v20_gives_its_exact_shape_and_upgrades_again(tmp_path):
    # The SQL tested here is the SQL the README gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO days(date,reflection,revision,reflection_revision) VALUES('2026-11-04','Kept',3,9)")
        conn.commit()
    to_v20(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
        assert recovery._schema_signature(conn) == sealed.shape(20)
        assert conn.execute("SELECT date,reflection,revision FROM days").fetchall() == [("2026-11-04", "Kept", 3)]
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
