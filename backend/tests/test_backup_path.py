"""Backups stay off the request path (consolidation step 4.2, B-05).

The day's automatic backup is taken at startup and by the serve watcher, never by a write, and a
failure shows in Settings until one succeeds. Each backup's hash is worked out once and cached for
as long as the file is unchanged."""

import hashlib
import json
import os
import secrets
import uuid

from fastapi.testclient import TestClient
from worklog import app as app_module
from worklog import cli, recovery
from worklog.app import BACKUP_FAILED, create_app, daily_backup
from worklog.db import Database
from worklog.transcripts import TranscriptRoots

ORIGIN = "http://127.0.0.1:8877"


def session(app):
    client = TestClient(app, base_url=ORIGIN)
    client.headers["Origin"] = ORIGIN
    boot = client.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
    client.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
    return client


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_writes_take_no_backup_and_startup_still_takes_the_days_first(tmp_path, monkeypatch):
    taken = []
    monkeypatch.setattr(app_module, "automatic_backup", lambda db, backups: taken.append(db))
    # Not a test app, so the day's backup runs as it does when serving; empty transcript roots keep
    # this machine's Claude folders out of it.
    app = create_app(tmp_path, transcript_roots=TranscriptRoots())
    assert len(taken) == 1
    with session(app) as person:
        key = {"Idempotency-Key": str(uuid.uuid4())}
        ok(person.post("/api/v1/work-items", json={"title": "Write"}, headers=key))
        sponsor = ok(person.post("/api/v1/credentials", json={"label": "Agent"}, headers={"Idempotency-Key": str(uuid.uuid4())}))
        token = secrets.token_urlsafe(32)
        # Registration writes through its own path, which took the backup too.
        ok(person.post("/api/v1/agent-registrations", json={"label": "Helper", "token": token, "chatId": str(uuid.uuid4())},
                       headers={"Authorization": "Bearer " + sponsor["token"], "Origin": ""}))
    assert len(taken) == 1


def test_a_failed_daily_backup_shows_in_settings_until_one_succeeds(tmp_path, monkeypatch):
    app = create_app(tmp_path, testing=True)

    def full_disk(db, backups):
        raise OSError("No space left on device")

    monkeypatch.setattr(app_module, "automatic_backup", full_disk)
    assert daily_backup(app) is False
    with session(app) as person:
        assert ok(person.get("/api/v1/settings"))["storage"]["warning"] == BACKUP_FAILED
        monkeypatch.setattr(app_module, "automatic_backup", lambda db, backups: None)
        assert daily_backup(app) is True
        assert ok(person.get("/api/v1/settings"))["storage"]["warning"] is None


def test_any_failure_of_the_daily_backup_is_shown_not_raised(tmp_path, monkeypatch):
    # From the review of #131: the serve watcher's own catch-all used to leave Settings blank.
    app = create_app(tmp_path, testing=True)

    def broken(db, backups):
        raise KeyError("dataId")

    monkeypatch.setattr(app_module, "automatic_backup", broken)
    assert daily_backup(app) is False and app.state.backup_error == BACKUP_FAILED


def test_verify_hashes_every_backup_now_and_corrects_the_cache(tmp_path, monkeypatch):
    # From the review of #131: a cached hash is the one taken while the file kept its size and
    # modification time, so damage that keeps both shows only when the bytes are hashed again.
    db, backups = tmp_path / "worklog.sqlite3", tmp_path / "backups"
    Database(db).initialize()
    made = recovery.backup_database(db, backups)
    path = backups / made["name"]
    stat = path.stat()
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(bytes(data))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert recovery.list_backups(backups)[0]["sha256"] == made["sha256"]
    damaged = hashlib.sha256(bytes(data)).hexdigest()
    assert recovery.list_backups(backups, verify=True)[0]["sha256"] == damaged
    assert recovery.list_backups(backups)[0]["sha256"] == damaged


def test_the_backups_command_verifies_only_when_asked(tmp_path, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli, "list_backups", lambda directory, verify=False: seen.append(verify) or [])
    for argv in (["backups", "--verify"], ["backups"]):
        assert cli.main(["--data-dir", str(tmp_path / "data"), *argv]) == 0
    assert seen == [True, False]


def test_a_listing_hashes_each_backup_once_while_it_is_unchanged(tmp_path, monkeypatch):
    db, backups = tmp_path / "worklog.sqlite3", tmp_path / "backups"
    Database(db).initialize()
    first, second = recovery.backup_database(db, backups), recovery.backup_database(db, backups)
    hashed, real = [], recovery._file_hash
    monkeypatch.setattr(recovery, "_file_hash", lambda path: hashed.append(path.name) or real(path))
    # Each was hashed as it was made, and the listing reads the hashes back.
    listed = {backup["name"]: backup["sha256"] for backup in recovery.list_backups(backups)}
    assert hashed == [] and listed == {first["name"]: first["sha256"], second["name"]: second["sha256"]}
    # A file that changes is hashed again, and one that goes leaves the cache.
    changed = backups / first["name"]
    with changed.open("ab") as stream:
        stream.write(b"\0")
    (backups / second["name"]).unlink()
    (relisted,) = recovery.list_backups(backups)
    assert hashed == [first["name"]] and relisted["sha256"] == hashlib.sha256(changed.read_bytes()).hexdigest()
    assert set(json.loads((backups / recovery.HASH_CACHE).read_text(encoding="utf-8"))) == {first["name"]}
    recovery.list_backups(backups)
    assert hashed == [first["name"]]


def test_a_cache_that_cannot_be_read_is_worked_out_again(tmp_path):
    db, backups = tmp_path / "worklog.sqlite3", tmp_path / "backups"
    Database(db).initialize()
    made = recovery.backup_database(db, backups)
    (backups / recovery.HASH_CACHE).write_text("not json", encoding="utf-8")
    assert [backup["sha256"] for backup in recovery.list_backups(backups)] == [made["sha256"]]
    assert json.loads((backups / recovery.HASH_CACHE).read_text(encoding="utf-8"))[made["name"]]["sha256"] == made["sha256"]
