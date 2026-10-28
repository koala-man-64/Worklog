import hashlib
import io
import json
import os
import sqlite3
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from worklog import recovery
from worklog.db import Database


def make_database(path: Path, title: str = "Original") -> Path:
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES ('item',?,0,'2026-01-01','2026-01-01')", (title,))
        conn.execute("INSERT INTO credentials VALUES ('agent','Agent','secret-hash','2026-01-01',0)")
        conn.execute("INSERT INTO attachments(id,owner_type,owner_id,name,mime,size,sha256,content,archived,created_at) VALUES ('blob','item','item','archived.bin','application/octet-stream',10,?,?,1,'2026-01-01')",
                     (hashlib.sha256(b"\x00archived\xff").hexdigest(), b"\x00archived\xff"))
        conn.commit()
    return path


def read_title(path: Path) -> str:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT title FROM work_items").fetchone()[0]


def test_backup_includes_live_wal_and_publishes_valid_snapshot(tmp_path):
    source = make_database(tmp_path / "live.sqlite3")
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("UPDATE work_items SET title='Committed WAL'")
        conn.commit()
        result = recovery.backup_database(source, tmp_path / "backups")
        backup = tmp_path / "backups" / result["name"]
        assert read_title(backup) == "Committed WAL"
        recovery.validate_database(backup)
        assert result["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
        assert recovery.list_backups(tmp_path / "backups")[0]["name"] == backup.name


def test_retention_daily_14_migration_3_manual_unpruned(tmp_path):
    source = make_database(tmp_path / "live.sqlite3")
    directory = tmp_path / "backups"
    for kind, count in [("daily", 16), ("pre-migration", 5), ("manual", 4)]:
        for _ in range(count):
            recovery.backup_database(source, directory, kind)
    assert len(list(directory.glob("worklog-daily-*.sqlite3"))) == 14
    assert len(list(directory.glob("worklog-pre-migration-*.sqlite3"))) == 3
    assert len(list(directory.glob("worklog-manual-*.sqlite3"))) == 4


def test_automatic_daily_uses_configured_calendar_and_skips_existing(tmp_path):
    source = make_database(tmp_path / "live.sqlite3")
    directory = tmp_path / "backups"
    first = recovery.automatic_backup(source, directory)
    assert first
    assert recovery.automatic_backup(source, directory) is None
    state = json.loads((directory / "daily-backup-state.json").read_text())
    assert state["timezone"] == "America/Chicago"
    assert len(state["date"]) == 10
    (directory / first["name"]).unlink()
    assert recovery.automatic_backup(source, directory)


def test_automatic_daily_uses_local_day_across_utc_midnight(tmp_path, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 29, 2, 30, tzinfo=UTC).astimezone(tz)

    source = make_database(tmp_path / "live.sqlite3")
    directory = tmp_path / "backups"
    monkeypatch.setattr(recovery, "datetime", FixedDatetime)
    recovery.automatic_backup(source, directory)
    state = json.loads((directory / "daily-backup-state.json").read_text())
    assert state["date"] == "2026-10-28"
    assert recovery.automatic_backup(source, directory) is None


def test_concurrent_daily_backup_owner_is_reused(tmp_path):
    source = make_database(tmp_path / "live.sqlite3")
    directory = tmp_path / "backups"
    with recovery.InstanceLock(directory, ".daily.lock"):
        assert recovery.automatic_backup(source, directory) is None


def test_restore_preserves_readonly_candidate_and_revokes_credentials(tmp_path):
    target = make_database(tmp_path / "worklog.sqlite3")
    candidate = make_database(tmp_path / "candidate.sqlite3", "Recovered")
    before = candidate.read_bytes()
    with closing(sqlite3.connect(candidate)) as conn:
        original_data_id = json.loads(conn.execute("SELECT value FROM settings WHERE key='dataId'").fetchone()[0])
    candidate.chmod(0o444)
    try:
        result = recovery.restore_database(target, candidate, tmp_path / "backups")
        assert read_title(target) == "Recovered"
        assert candidate.read_bytes() == before
        assert result["credentialsRevoked"] is True
        assert read_title(tmp_path / "backups" / result["preRestoreBackup"]["name"]) == "Original"
        with closing(sqlite3.connect(target)) as conn:
            assert conn.execute("SELECT revoked FROM credentials").fetchone() == (1,)
            restored_data_id = json.loads(conn.execute("SELECT value FROM settings WHERE key='dataId'").fetchone()[0])
            assert restored_data_id == result["dataId"] != original_data_id
    finally:
        candidate.chmod(0o644)


@pytest.mark.parametrize("problem", ["corrupt", "wrong-version", "missing-table", "foreign-key", "trigger", "column"])
def test_invalid_restore_leaves_original_intact(tmp_path, problem):
    target = make_database(tmp_path / "worklog.sqlite3")
    candidate = tmp_path / "candidate.sqlite3"
    if problem == "corrupt":
        candidate.write_bytes(b"not a sqlite database")
    else:
        make_database(candidate, "Bad")
        with closing(sqlite3.connect(candidate)) as conn:
            if problem == "wrong-version":
                conn.execute("PRAGMA user_version=999")
            elif problem == "missing-table":
                conn.execute("DROP TABLE notes")
            elif problem == "foreign-key":
                conn.execute("CREATE TABLE child(parent_id TEXT REFERENCES work_items(id))")
                conn.execute("INSERT INTO child VALUES ('missing')")
            elif problem == "trigger":
                conn.execute("CREATE TRIGGER tamper AFTER UPDATE ON credentials BEGIN DELETE FROM work_items; END")
            else:
                conn.execute("ALTER TABLE work_items ADD COLUMN unexpected TEXT")
            conn.commit()
    with pytest.raises(ValueError):
        recovery.restore_database(target, candidate, tmp_path / "backups")
    assert read_title(target) == "Original"
    assert not list(tmp_path.glob(".restore-*"))


def test_restore_blocks_active_server_lock(tmp_path):
    target = make_database(tmp_path / "worklog.sqlite3")
    candidate = make_database(tmp_path / "candidate.sqlite3", "Recovered")
    with recovery.InstanceLock(tmp_path), pytest.raises(RuntimeError, match="running"):
        recovery.restore_database(target, candidate, tmp_path / "backups")
    assert read_title(target) == "Original"


def test_replacement_failure_preserves_original_and_snapshot(tmp_path, monkeypatch):
    target = make_database(tmp_path / "worklog.sqlite3")
    candidate = make_database(tmp_path / "candidate.sqlite3", "Recovered")
    replace = os.replace

    def fail_restore(source, destination):
        if Path(destination) == target:
            raise PermissionError("Simulated replacement failure")
        return replace(source, destination)

    monkeypatch.setattr(recovery.os, "replace", fail_restore)
    with pytest.raises(PermissionError):
        recovery.restore_database(target, candidate, tmp_path / "backups")
    assert read_title(target) == "Original"
    assert len(list((tmp_path / "backups").glob("worklog-pre-restore-*.sqlite3"))) == 1
    assert not list(tmp_path.glob(".restore-*"))


def test_valid_backup_restores_corrupt_active_and_preserves_raw_sidecars(tmp_path):
    target = tmp_path / "worklog.sqlite3"
    candidate = make_database(tmp_path / "candidate.sqlite3", "Recovered")
    originals = {"": b"corrupt database", "-wal": b"damaged wal bytes",
                 "-shm": b"damaged shm bytes", "-journal": b"damaged journal bytes"}
    for suffix, content in originals.items():
        target.with_name(target.name + suffix).write_bytes(content)
    result = recovery.restore_database(target, candidate, tmp_path / "backups")
    assert read_title(target) == "Recovered"
    recovery.validate_database(target)
    assert result["preRestoreBackup"] == result["offlineEvidence"]
    with zipfile.ZipFile(tmp_path / "backups" / result["offlineEvidence"]["name"]) as archive:
        for suffix, content in originals.items():
            assert archive.read(target.name + suffix) == content
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "worklog-offline-evidence"
        for entry in manifest["files"]:
            assert entry["sha256"] == hashlib.sha256(archive.read(entry["path"])).hexdigest()
    assert all(not target.with_name(target.name + suffix).exists() for suffix in originals if suffix)


def test_failed_corrupt_active_replace_keeps_original_and_all_sidecars(tmp_path, monkeypatch):
    target = tmp_path / "worklog.sqlite3"
    candidate = make_database(tmp_path / "candidate.sqlite3", "Recovered")
    originals = {"": b"corrupt database", "-wal": b"damaged wal bytes",
                 "-shm": b"damaged shm bytes", "-journal": b"damaged journal bytes"}
    for suffix, content in originals.items():
        target.with_name(target.name + suffix).write_bytes(content)
    replace = os.replace

    def fail_main(source, destination):
        if Path(destination) == target:
            raise PermissionError("Simulated main replacement failure")
        return replace(source, destination)

    monkeypatch.setattr(recovery.os, "replace", fail_main)
    with pytest.raises(PermissionError, match="main replacement"):
        recovery.restore_database(target, candidate, tmp_path / "backups")
    for suffix, content in originals.items():
        assert target.with_name(target.name + suffix).read_bytes() == content
    assert not list(tmp_path.glob(".restore-*"))
    assert len(list((tmp_path / "backups").glob("worklog-pre-restore-raw-*.zip"))) == 1
    assert not (tmp_path / ".restore-in-progress.json").exists()


def test_durable_marker_exists_before_sidecar_or_main_mutation(tmp_path, monkeypatch):
    target = tmp_path / "worklog.sqlite3"
    target.write_bytes(b"corrupt active")
    target.with_name(target.name + "-wal").write_bytes(b"old wal")
    candidate = make_database(tmp_path / "candidate.sqlite3", "Recovered")
    marker = tmp_path / ".restore-in-progress.json"
    replace = os.replace
    mutations = []

    def checked_replace(source, destination):
        source, destination = Path(source), Path(destination)
        if destination == target or source.name == target.name + "-wal":
            assert marker.exists()
            transaction = json.loads(marker.read_text())
            assert transaction["phase"] == "prepared"
            assert transaction["offlineEvidence"]
            mutations.append(source.name)
        return replace(source, destination)

    monkeypatch.setattr(recovery.os, "replace", checked_replace)
    result = recovery.restore_database(target, candidate, tmp_path / "backups")
    assert len(mutations) == 2
    assert read_title(target) == "Recovered"
    assert result["dataId"]
    assert not marker.exists()


def test_interrupted_restore_marker_quarantines_preserved_on_explicit_retry(tmp_path):
    candidate = make_database(tmp_path / "candidate.sqlite3", "Recovered")
    target = tmp_path / "worklog.sqlite3"
    target.write_bytes(b"damaged original")
    quarantine = tmp_path / ".restore-old-interrupted-wal"
    quarantine.write_bytes(b"previous quarantined wal")
    marker = tmp_path / ".restore-in-progress.json"
    original_marker = b'{"version":1,"phase":"prepared","interrupted":true}'
    marker.write_bytes(original_marker)
    result = recovery.restore_database(target, candidate, tmp_path / "backups")
    assert not marker.exists()
    assert read_title(target) == "Recovered"
    with zipfile.ZipFile(tmp_path / "backups" / result["offlineEvidence"]["name"]) as archive:
        assert archive.read(marker.name) == original_marker
        assert archive.read(quarantine.name) == b"previous quarantined wal"


def test_failed_cleanup_keeps_marker_until_explicit_verified_retry(tmp_path, monkeypatch):
    target = tmp_path / "worklog.sqlite3"
    target.write_bytes(b"damaged original")
    target.with_name(target.name + "-wal").write_bytes(b"old wal")
    candidate = make_database(tmp_path / "candidate.sqlite3", "Recovered")
    marker = tmp_path / ".restore-in-progress.json"
    unlink = Path.unlink

    def fail_cleanup(path, *args, **kwargs):
        if path.name.startswith(".restore-old-"):
            raise PermissionError("Simulated interrupted cleanup")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    with pytest.raises(PermissionError, match="interrupted cleanup"):
        recovery.restore_database(target, candidate, tmp_path / "backups")
    assert marker.exists()
    assert read_title(target) == "Recovered"
    monkeypatch.setattr(Path, "unlink", unlink)
    recovery.restore_database(target, candidate, tmp_path / "backups")
    assert not marker.exists()
    assert len(list((tmp_path / "backups").glob("worklog-pre-restore-raw-*.zip"))) == 2


def test_restores_corrupt_body_with_valid_sqlite_header(tmp_path):
    target = make_database(tmp_path / "worklog.sqlite3")
    candidate = make_database(tmp_path / "candidate.sqlite3", "Recovered")
    damaged = bytearray(target.read_bytes())
    damaged[100:200] = b"\xff" * 100
    target.write_bytes(damaged)
    result = recovery.restore_database(target, candidate, tmp_path / "backups")
    assert read_title(target) == "Recovered"
    with zipfile.ZipFile(tmp_path / "backups" / result["offlineEvidence"]["name"]) as archive:
        assert archive.read(target.name) == bytes(damaged)


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_restore_busy_writer_fails_closed(tmp_path, journal_mode):
    target = make_database(tmp_path / "worklog.sqlite3")
    candidate = make_database(tmp_path / "candidate.sqlite3", "Recovered")
    with closing(sqlite3.connect(target)) as writer:
        writer.execute("PRAGMA journal_mode=" + journal_mode)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE work_items SET title='Uncommitted writer'")
        with pytest.raises(RuntimeError, match="busy"):
            recovery.restore_database(target, candidate, tmp_path / "backups")
        assert writer.execute("SELECT title FROM work_items").fetchone()[0] == "Uncommitted writer"
        writer.rollback()
    assert read_title(target) == "Original"


def test_backup_failed_validation_never_publishes(tmp_path, monkeypatch):
    source = make_database(tmp_path / "live.sqlite3")

    def invalid(*args, **kwargs):
        raise ValueError("Snapshot integrity failure")

    monkeypatch.setattr(recovery, "validate_database", invalid)
    with pytest.raises(ValueError):
        recovery.backup_database(source, tmp_path / "backups")
    assert not list((tmp_path / "backups").iterdir())


def test_export_contains_all_archived_blobs_and_valid_manifest_hashes(tmp_path):
    source = make_database(tmp_path / "live.sqlite3")
    output = recovery.export_database(source)
    with zipfile.ZipFile(io.BytesIO(output)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "worklog-export"
        for file in manifest["files"]:
            data = archive.read(file["path"])
            assert file["size"] == len(data)
            assert file["sha256"] == hashlib.sha256(data).hexdigest()
        attachments = json.loads(archive.read("records/attachments.json"))
        assert attachments[0]["archived"] == 1
        assert archive.read(attachments[0]["content"]["file"]) == b"\x00archived\xff"
        assert "Original" in archive.read("worklog.md").decode()
        assert b"secret-hash" not in archive.read("records/credentials.json")
        assert b"secret-hash" not in archive.read("worklog.md")


def test_candidate_same_as_target_rejected(tmp_path):
    target = make_database(tmp_path / "worklog.sqlite3")
    with pytest.raises(ValueError, match="differ"):
        recovery.restore_database(target, target, tmp_path / "backups")


def test_export_original_html_bytes_have_hashed_manifest_entry(tmp_path):
    source = make_database(tmp_path / "live.sqlite3")
    original = b"<!doctype html><title>Raw</title><script>raw & unchanged</script>\r\n"
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("INSERT INTO plan_imports(id,name,original,sha256,preview,actor_id,created_at) VALUES ('import','../../unsafe.html',?,?,'{}','human','2026-01-01')",
                     (original, hashlib.sha256(original).hexdigest()))
        conn.commit()
    with zipfile.ZipFile(io.BytesIO(recovery.export_database(source))) as archive:
        records = json.loads(archive.read("records/plan_imports.json"))
        member = records[0]["original"]["file"]
        assert member.startswith("imports/") and member.endswith(".html")
        assert ".." not in member
        assert archive.read(member) == original
        manifest = json.loads(archive.read("manifest.json"))
        entry = next(file for file in manifest["files"] if file["path"] == member)
        assert entry["sha256"] == hashlib.sha256(original).hexdigest()
