"""Offline compaction, `worklog compact` (consolidation step 4.3, K6).

Compaction thins old display-only history, keeps each roadmap's last 20 versions, purges plan imports
never committed and old receipts, then vacuums. Every record is as it was, so Today and every table
but those four export the same; every event row stays, in its order, and the kinds that hold state
keep their payloads byte for byte. It refuses while Worklog runs, and a failure leaves the database
as it was."""

import hashlib
import io
import json
import sqlite3
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import cli, compaction, recovery
from worklog.app import create_app
from worklog.compaction import compact_database

ORIGIN = "http://127.0.0.1:8877"
LATER = datetime.now(UTC) + timedelta(days=365)
OLD = "2020-01-01T00:00:00+00:00"
# Events of kinds that hold state, are read at runtime or are history the guides promise, written
# long ago with payloads compaction would otherwise thin.
KEPT_KINDS = ["credential.register", "credential.revoke", "proposal.create", "proposal.approve", "chat.unassign",
              "chat.message", "discussion.message", "resource.update", "release.create", "check.update",
              "ticket.create", "reminder.update", "person.update", "repository.create", "agent.link",
              "coordination.update", "run.create", "participation.create", "settings.timezone"]
# What compaction may change; every other table exports as it was.
CHANGED = {"events", "roadmap_versions", "plan_imports", "receipts"}
HTML = b"<html><body><h1>Imported plan</h1><h2>First step</h2><p>Text</p></body></html>"


def connect(c):
    c.headers["Origin"] = ORIGIN
    boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
    c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
    return c


def call(c, method, path, body=None, **options):
    response = c.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())}, **options)
    assert response.status_code == 200, response.text
    return response.json()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exported(path):
    with zipfile.ZipFile(io.BytesIO(recovery.export_database(path))) as archive:
        return {name: archive.read(name) for name in archive.namelist() if name != "manifest.json"}


def canonical(files):
    """The export's members compaction must leave alone: every table's records but CHANGED's, the
    schema and the BLOBs. The Markdown holds every table, and imports/ the imports' originals."""
    return {name: data for name, data in files.items()
            if not (name.startswith("records/") and name[len("records/"):-len(".json")] in CHANGED)
            and name != "worklog.md" and not name.startswith("imports/")}


def rows(path, sql, *parameters):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql, parameters).fetchall()


@pytest.fixture
def world(tmp_path):
    """A stopped Worklog with a day of work, a roadmap of 25 versions, two plan imports and old events
    of every kept kind. Returns its data directory, database and what was read before compaction."""
    data = tmp_path / "data"
    with TestClient(create_app(data, testing=True), base_url=ORIGIN) as c:
        connect(c)
        done, blocked, doing = (call(c, "POST", "/work-items", {"title": f"{name} work"})
                                for name in ("Finished", "Stuck", "Ongoing"))
        for item, status in ((done, "done"), (blocked, "blocked"), (doing, "doing")):
            call(c, "PATCH", f"/work-items/{item['id']}", {"revision": item["revision"], "status": status,
                                                            "description": "A long description " * 20})
        (day,), = rows(data / "worklog.sqlite3", "SELECT local_date FROM events LIMIT 1")
        for item in (done, blocked, doing):
            call(c, "PUT", f"/days/{day}/entries/{item['id']}", {"revision": 0, "planned": True, "worked": True,
                                                                   "outcome": "Moved along"})
        current = call(c, "GET", f"/days/{day}")
        call(c, "POST", f"/days/{day}/reorder", {"revision": current["revision"], "ids": [e["id"] for e in reversed(current["entries"])]})
        call(c, "PUT", f"/days/{day}/reflection", {"revision": current["reflectionRevision"], "text": "A good day"})
        call(c, "POST", "/notes", {"ownerType": "item", "ownerId": done["id"], "title": "Scratch", "text": "Notes " * 50})
        step = str(uuid.uuid4())
        roadmap = call(c, "POST", "/roadmaps", {"title": "Plan 1", "steps": [{"id": step, "title": "Step"}]})
        for number in range(2, 26):
            roadmap = call(c, "POST", f"/roadmaps/{roadmap['id']}/revisions",
                           {"revision": roadmap["revision"], "title": f"Plan {number}", "steps": [{"id": step, "title": "Step"}]})
        small = call(c, "POST", "/roadmaps", {"title": "Small 1", "steps": []})
        for number in (2, 3):
            small = call(c, "POST", f"/roadmaps/{small['id']}/revisions", {"revision": small["revision"], "title": f"Small {number}", "steps": []})
        imports = [c.post(f"/api/v1/imports/preview?name={name}.html", content=HTML).json() for name in ("kept", "left")]
        committed = call(c, "POST", f"/imports/{imports[0]['id']}/commit", {"title": "Imported", "steps": []})
        before = {"day": day, "today": call(c, "GET", f"/days/{day}"), "roadmap": roadmap["id"], "step": step,
                  "committed": committed["id"], "imports": [i["id"] for i in imports], "done": done["id"], "small": small["id"],
                  "search": call(c, "GET", "/search?q=Finished")}
    database = data / "worklog.sqlite3"
    with closing(sqlite3.connect(database)) as conn:
        for position, kind in enumerate(KEPT_KINDS):
            payload = json.dumps({"id": f"kept-{position}", "title": "Kept", "detail": "x" * 200})
            conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,before_json,after_json) "
                         "VALUES(?,?,?,'agent-1','Builder','agent',?,'2020-01-01',?,?)",
                         (f"kept-{position}", f"entity-{position}", kind, OLD, payload, payload))
        conn.commit()
    before.update(events=rows(database, "SELECT id,kind,before_json,after_json FROM events ORDER BY rowid"),
                  files=exported(database))
    return data, database, before


def test_compaction_thins_old_display_history_and_keeps_every_record_and_state(world):
    data, database, before = world
    result = compact_database(database, data / "backups", now=LATER)
    assert result["bytesAfter"] == database.stat().st_size

    # Every event row is there, in its order. Kinds that hold state keep their payloads byte for
    # byte; display-only ones keep their records' id, title, status and _statusChanged.
    events = rows(database, "SELECT id,kind,before_json,after_json FROM events ORDER BY rowid")
    assert [e[0] for e in events] == [e[0] for e in before["events"]]
    thinned = 0
    for (identity, kind, old_before, old_after), (_, _, new_before, new_after) in zip(before["events"], events, strict=True):
        if kind.split(".")[0] in compaction.THINNED:
            expected = (compaction.thinned(json.loads(old_before)), compaction.thinned(json.loads(old_after)))
            assert (json.loads(new_before), json.loads(new_after)) == expected, identity
            thinned += (new_before, new_after) != (old_before, old_after)
        else:
            assert (new_before, new_after) == (old_before, old_after), identity
    assert result["eventsThinned"] == thinned > 0
    # Against fixed expectations too: every kept kind is as it was, and the finished item's change
    # keeps exactly what Today replays.
    kept = {kind: (b, a) for _, kind, b, a in events if kind in KEPT_KINDS}
    assert kept == {kind: (b, a) for _, kind, b, a in before["events"] if kind in KEPT_KINDS}
    assert len(kept) == len(KEPT_KINDS)
    finished = [json.loads(a) for _, kind, _, a in events if kind == "item.update" and before["done"] in a]
    assert finished == [{"id": before["done"], "title": "Finished work", "status": "done", "_statusChanged": True}]
    # A day's reorder lists and its reflection's text are not records: they go.
    assert rows(database, "SELECT DISTINCT before_json,after_json FROM events WHERE kind IN ('day.reorder','day.reflection')") == [("null", "null")]

    # Every record is as it was: the canonical tables, the schema and the BLOBs export the same.
    after = exported(database)
    assert canonical(after) == canonical(before["files"])

    assert result["receiptsPruned"] > 0 and rows(database, "SELECT count(*) FROM receipts") == [(0,)]
    # Each roadmap keeps its 20 newest versions, and numbering goes on from the newest.
    assert result["versionsRemoved"] == 5
    with TestClient(create_app(data, testing=True), base_url=ORIGIN) as c:
        connect(c)
        assert call(c, "GET", f"/days/{before['day']}") == before["today"]
        assert call(c, "GET", "/search?q=Finished") == before["search"]
        roadmap = call(c, "GET", f"/roadmaps/{before['roadmap']}")
        assert [v["number"] for v in roadmap["versions"]] == list(range(6, 26))
        assert [v["number"] for v in call(c, "GET", f"/roadmaps/{before['small']}")["versions"]] == [1, 2, 3]
        assert c.get(f"/api/v1/roadmaps/{before['roadmap']}/revisions/5").status_code == 404
        assert call(c, "GET", f"/roadmaps/{before['roadmap']}/revisions/6")["title"] == "Plan 6"
        revised = call(c, "POST", f"/roadmaps/{before['roadmap']}/revisions",
                       {"revision": roadmap["revision"], "title": "Plan 26", "steps": [{"id": before["step"], "title": "Step"}]})
        assert revised["versions"][-1]["number"] == 26
        # An import committed to a roadmap stays; one never committed goes.
        assert c.get(f"/api/v1/imports/{before['imports'][0]}/download").status_code == 200
        assert c.get(f"/api/v1/imports/{before['imports'][1]}/download").status_code == 404
    assert result["importsPurged"] == 1
    # The search index still finds each record's row by its key.
    assert rows(database, "SELECT count(*) FROM search_keys k LEFT JOIN search_index s ON s.rowid=k.doc "
                          "WHERE s.rowid IS NULL OR s.entity_id<>k.entity_id") == [(0,)]
    assert result["bytesReclaimed"] == result["bytesBefore"] - result["bytesAfter"]
    assert [p.name for p in data.iterdir() if p.name.startswith(".compact-")] == []


def test_the_pre_compaction_backup_holds_the_database_as_it_was(world):
    data, database, before = world
    result = compact_database(database, data / "backups", now=LATER)
    backup = data / "backups" / result["preCompactBackup"]["name"]
    assert backup.name.startswith("worklog-pre-compact-")
    recovery.validate_database(backup)
    assert rows(backup, "SELECT id,kind,before_json,after_json FROM events ORDER BY rowid") == before["events"]
    assert canonical(exported(backup)) == canonical(before["files"])


def test_recent_history_and_recent_imports_are_left_alone(world):
    data, database, before = world
    # Six days on, nothing is 90 days old, and the uncommitted import is not yet 7 days old.
    result = compact_database(database, data / "backups", now=datetime.now(UTC) + timedelta(days=6))
    assert (result["eventsThinned"], result["importsPurged"]) == (0, 0)
    assert result["versionsRemoved"] == 5
    events = rows(database, "SELECT id,kind,before_json,after_json FROM events ORDER BY rowid")
    assert events == before["events"]
    assert rows(database, "SELECT count(*) FROM plan_imports") == [(2,)]
    # Compacting again lets nothing more go.
    again = compact_database(database, data / "backups", now=datetime.now(UTC) + timedelta(days=6))
    assert (again["eventsThinned"], again["versionsRemoved"], again["importsPurged"]) == (0, 0, 0)


def test_compaction_refuses_while_worklog_runs_or_a_write_or_restore_is_unfinished(world):
    data, database, _ = world
    original = digest(database)
    with recovery.InstanceLock(data), pytest.raises(RuntimeError, match="Worklog is running"):
        compact_database(database, data / "backups", now=LATER)
    for name, message in (("worklog.sqlite3-journal", "a connection is open"), (".restore-in-progress.json", "restore was interrupted")):
        (data / name).write_bytes(b"")
        with pytest.raises(RuntimeError, match=message):
            compact_database(database, data / "backups", now=LATER)
        (data / name).unlink()
    assert digest(database) == original
    assert list((data / "backups").glob("worklog-pre-compact-*")) == []


def test_compaction_refuses_a_database_worklog_has_not_upgraded(tmp_path):
    database = tmp_path / "worklog.sqlite3"
    sealed.build(database, 21)
    original = digest(database)
    with pytest.raises(ValueError, match="schema version 21"):
        compact_database(database, tmp_path / "backups", now=LATER)
    assert digest(database) == original


def test_a_failure_before_the_swap_leaves_the_database_as_it_was(world, monkeypatch):
    data, database, _ = world
    original = digest(database)
    real_compact, real_replace = compaction._compact, compaction.os.replace

    def compacted_then_failed(stage, moment):
        real_compact(stage, moment)
        raise OSError("No space left on device")

    monkeypatch.setattr(compaction, "_compact", compacted_then_failed)
    with pytest.raises(OSError, match="No space"):
        compact_database(database, data / "backups", now=LATER)
    monkeypatch.setattr(compaction, "_compact", real_compact)

    def swap_refused(source, destination):
        if Path(source).name.startswith(".compact-"):
            raise PermissionError("The file is in use")
        return real_replace(source, destination)

    monkeypatch.setattr(compaction.os, "replace", swap_refused)
    with pytest.raises(RuntimeError, match="open in another program"):
        compact_database(database, data / "backups", now=LATER)
    assert digest(database) == original
    assert [p.name for p in data.iterdir() if p.name.startswith(".compact-")] == []
    # Each run's backup only repeated the database, so it went again.
    assert list((data / "backups").glob("worklog-pre-compact-*")) == []


def test_only_finished_compactions_keep_backups_and_the_three_newest_stay(world, monkeypatch):
    # From the review of step 4.3: failed runs used to push out the backup holding the history.
    data, database, _ = world
    backups = data / "backups"
    names = [compact_database(database, backups, now=LATER)["preCompactBackup"]["name"] for _ in range(4)]
    assert sorted(p.name for p in backups.glob("worklog-pre-compact-*")) == sorted(names[1:])
    real_validate = compaction.validate_database
    calls = []

    def fails_once_swapped(path, **options):
        calls.append(path)
        if len(calls) == 3:  # the database itself, the copy, then the database after the swap
            raise ValueError("Database integrity check failed")
        return real_validate(path, **options)

    monkeypatch.setattr(compaction, "validate_database", fails_once_swapped)
    with pytest.raises(RuntimeError, match="restore worklog-pre-compact-") as failure:
        compact_database(database, backups, now=LATER)
    # The swap happened, so the backup it names is the way back, and stays.
    kept = str(failure.value).split("restore ")[1].split(" ")[0]
    assert (backups / kept).is_file() and len(list(backups.glob("worklog-pre-compact-*"))) == 4


def test_a_payload_that_is_not_json_stops_compaction_and_changes_nothing(world):
    data, database, _ = world
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("UPDATE events SET after_json='{not json' WHERE kind='item.create'")
        conn.commit()
    original = digest(database)
    with pytest.raises(ValueError, match="not JSON"):
        compact_database(database, data / "backups", now=LATER)
    assert digest(database) == original
    assert list((data / "backups").glob("worklog-pre-compact-*")) == []


def test_compaction_refuses_a_database_another_connection_is_reading(world):
    data, database, _ = world
    with closing(sqlite3.connect(database, isolation_level=None)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM events").fetchone()
        with pytest.raises(RuntimeError, match="before compacting"):
            compact_database(database, data / "backups", now=LATER)
        reader.execute("ROLLBACK")


def test_a_run_killed_part_way_leaves_nothing_the_next_one_keeps(world):
    data, database, _ = world
    for name in (".compact-left.sqlite3", ".compact-left.sqlite3-journal"):
        (data / name).write_bytes(b"left by a killed run")
    compact_database(database, data / "backups", now=LATER)
    assert [p.name for p in data.iterdir() if p.name.startswith(".compact-")] == []


def test_vacuum_must_keep_the_order_history_is_read_in(world, monkeypatch):
    data, database, _ = world
    original = digest(database)
    orders = iter(["before", "after"])
    monkeypatch.setattr(compaction, "_event_order", lambda conn: next(orders))
    with pytest.raises(ValueError, match="changed the order of events"):
        compact_database(database, data / "backups", now=LATER)
    assert digest(database) == original


def test_the_cli_compacts_and_reports_or_explains_a_refusal(world, capsys):
    data, database, _ = world
    with recovery.InstanceLock(data):
        assert cli.main(["--data-dir", str(data), "compact"]) == 1
    assert "Worklog is running" in capsys.readouterr().err
    assert cli.main(["--data-dir", str(data), "compact"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"compacted", "eventsThinned", "versionsRemoved", "importsPurged", "receiptsPruned",
                           "bytesBefore", "bytesAfter", "bytesReclaimed", "preCompactBackup"}
    assert report["versionsRemoved"] == 5
