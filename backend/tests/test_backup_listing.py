"""Settings lists backups from their file metadata; the CLI's `backups` keeps each hash.

Hashing reads every byte of every backup, which made each Settings page load take
seconds once backups added up to a few gigabytes. Settings never shows the hashes.
"""

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog import recovery
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def test_settings_lists_backups_without_reading_them(client, monkeypatch):
    made = client.post("/api/v1/backups", json={}, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert made.status_code == 200, made.text

    def unread(path):
        raise AssertionError(f"Settings read the bytes of {path.name}")

    monkeypatch.setattr(recovery, "_file_hash", unread)
    settings = client.get("/api/v1/settings")
    assert settings.status_code == 200, settings.text
    [listed] = settings.json()["backups"]
    assert set(listed) == {"name", "size", "createdAt"}
    assert listed["name"] == made.json()["name"] and listed["size"] > 0
    assert settings.json()["storage"]["backupBytes"] == listed["size"]


def test_the_backups_command_still_lists_each_hash(tmp_path):
    database = tmp_path / "worklog.sqlite3"
    create_app(tmp_path, testing=True)
    made = recovery.backup_database(database, tmp_path / "backups")
    [listed] = recovery.list_backups(tmp_path / "backups")
    assert listed["sha256"] == made["sha256"] == hashlib.sha256((tmp_path / "backups" / made["name"]).read_bytes()).hexdigest()
