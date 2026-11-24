"""Manual backups are pruned by the person's rule; Settings shows the space (audit finding B2, roadmap
step 2.2, Rudy's answer of 2026-11-24).

85 manual backups held 26.5 GB and were never pruned; each deploy adds one. Now the newest 10 manual
backups are kept, with any the person marks to keep, after a manual backup and never inside a backup
of another kind. The first pass removes nothing by itself: the listing and Settings show what the
rule would remove, and the person confirms once. The list is in time order across kinds, and
Settings shows the backups by kind and the disk's free space, warning on either."""

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from worklog import recovery
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


def backups_of(directory: Path, kind: str, days: range) -> list[Path]:
    """Backups of a kind from those days of January 2026, oldest first."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / f"worklog-{kind}-202601{day:02d}T000000000000Z-{day:08x}.sqlite3" for day in days]
    for path in paths:
        path.write_bytes(b"an earlier backup")
    return paths


@contextmanager
def held(path: Path, monkeypatch):
    """`path` held open by another handle (on Windows), or refused to Path.unlink elsewhere."""
    if os.name == "nt":
        with path.open("rb"):
            yield
        return
    unlink = Path.unlink

    def refused(self, *args, **kwargs):
        if self == path:
            raise PermissionError(13, "The process cannot access the file", str(self))
        return unlink(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", refused)
        yield


@pytest.fixture
def source(tmp_path):
    from worklog.db import Database

    path = tmp_path / "live.sqlite3"
    Database(path).initialize()
    return path


def test_the_first_pass_lists_what_it_would_remove_and_removes_nothing(tmp_path, source):
    folder = tmp_path / "backups"
    old = backups_of(folder, "manual", range(1, 14))
    recovery.backup_database(source, folder)
    assert all(path.exists() for path in old)
    # 14 manual backups: the newest 10 stay, the 4 oldest are what the rule would remove.
    assert recovery.manual_prunable(folder) == [path.name for path in reversed(old[:4])]
    listed = {b["name"]: b for b in recovery.list_backups(folder, hashed=False)}
    assert {name for name, b in listed.items() if b["prunable"]} == {path.name for path in old[:4]}


def test_once_confirmed_the_rule_keeps_the_newest_ten_and_those_marked(tmp_path, source):
    folder = tmp_path / "backups"
    old = backups_of(folder, "manual", range(1, 14))
    recovery.mark_backup(folder, old[0].name, True)
    removed = recovery.confirm_manual_pruning(folder)
    assert removed == [path.name for path in reversed(old[1:3])]
    assert old[0].exists() and not old[1].exists() and not old[2].exists()
    assert [path.exists() for path in old[3:]] == [True] * 10
    # Later manual backups prune by themselves; the mark still holds.
    recovery.backup_database(source, folder)
    assert old[0].exists() and not old[3].exists()
    assert len(list(folder.glob("worklog-manual-*.sqlite3"))) == 11
    recovery.mark_backup(folder, old[0].name, False)
    recovery.backup_database(source, folder)
    assert not old[0].exists()


def test_a_backup_that_cannot_be_deleted_is_skipped(tmp_path, monkeypatch):
    folder = tmp_path / "backups"
    old = backups_of(folder, "manual", range(1, 14))
    with held(old[0], monkeypatch):
        removed = recovery.confirm_manual_pruning(folder)
    assert old[0].exists() and removed == [old[2].name, old[1].name]


@pytest.mark.parametrize("kind", ["pre-migration", "pre-restore", "pre-compact", "daily"])
def test_no_backup_of_another_kind_prunes_manual_ones(tmp_path, source, kind):
    folder = tmp_path / "backups"
    old = backups_of(folder, "manual", range(1, 16))
    recovery.confirm_manual_pruning(folder)
    remaining = sorted(folder.glob("worklog-manual-*.sqlite3"))
    backups_of(folder, "manual", range(16, 19))
    recovery.backup_database(source, folder, kind)
    assert len(list(folder.glob("worklog-manual-*.sqlite3"))) == len(remaining) + 3
    assert not old[0].exists()


def test_the_list_is_in_time_order_across_kinds(tmp_path):
    folder = tmp_path / "backups"
    backups_of(folder, "manual", range(1, 3))
    backups_of(folder, "daily", range(3, 5))
    backups_of(folder, "pre-migration", range(5, 6))
    listed = recovery.list_backups(folder, hashed=False)
    assert [b["kind"] for b in listed] == ["pre-migration", "daily", "daily", "manual", "manual"]


def test_a_mark_needs_a_backup_in_the_folder(tmp_path):
    folder = tmp_path / "backups"
    backups_of(folder, "manual", range(1, 2))
    for name in ("worklog-manual-20260109T000000000000Z-00000009.sqlite3", "../worklog.sqlite3", "notes.txt"):
        with pytest.raises(ValueError):
            recovery.mark_backup(folder, name, True)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        c.data = tmp_path
        yield c


def post(c, path, body):
    response = c.post("/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def test_settings_show_the_backups_by_kind_the_free_space_and_the_rule(client):
    old = backups_of(client.data / "backups", "manual", range(1, 13))
    backups_of(client.data / "backups", "daily", range(13, 15))
    storage = client.get("/api/v1/settings").json()["storage"]
    assert storage["backupCount"] == 14
    assert storage["backupKinds"] == {"manual": {"count": 12, "bytes": 12 * 17}, "daily": {"count": 2, "bytes": 34}}
    assert storage["diskFreeBytes"] > 0
    assert storage["pruning"] == {"keep": 10, "confirmed": False, "candidates": [old[1].name, old[0].name],
                                  "candidateBytes": 34}
    assert post(client, f"/backups/{old[0].name}/keep", {"kept": True}) == {"name": old[0].name, "kept": True}
    assert post(client, "/backups/pruning", {}) == {"removed": [old[1].name]}
    storage = client.get("/api/v1/settings").json()["storage"]
    assert storage["pruning"]["confirmed"] is True and storage["pruning"]["candidates"] == []
    missing = client.post("/api/v1/backups/worklog-manual-20990101T000000000000Z-00000001.sqlite3/keep",
                          json={"kept": True}, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert missing.status_code == 404


def test_settings_warn_when_backups_outgrow_the_database_or_the_disk_fills(client, monkeypatch):
    import shutil
    from collections import namedtuple

    from worklog import app as app_module

    assert client.get("/api/v1/settings").json()["storage"]["warning"] is None
    size = (client.data / "worklog.sqlite3").stat().st_size
    huge = client.data / "backups" / "worklog-manual-20260101T000000000000Z-00000001.sqlite3"
    huge.parent.mkdir(exist_ok=True)
    huge.write_bytes(b"\0" * (app_module.BACKUP_RATIO * size + 1))
    assert "times the database" in client.get("/api/v1/settings").json()["storage"]["warning"]
    huge.unlink()
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(10 * size, 9 * size, size))
    assert "Free disk space" in client.get("/api/v1/settings").json()["storage"]["warning"]


def test_the_backups_command_marks_what_the_rule_removes_and_confirms_it(tmp_path, source, capsys):
    import json

    from worklog import cli

    folder = tmp_path / "data" / "backups"
    old = backups_of(folder, "manual", range(1, 13))
    assert cli.main(["--data-dir", str(tmp_path / "data"), "backups"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {b["name"] for b in listed if b["prunable"]} == {old[0].name, old[1].name}
    assert cli.main(["--data-dir", str(tmp_path / "data"), "backups", "--confirm-pruning"]) == 0
    assert json.loads(capsys.readouterr().out) == {"removed": [old[1].name, old[0].name]}



def test_a_hand_named_copy_neither_counts_among_the_newest_nor_goes(tmp_path):
    folder = tmp_path / "backups"
    old = backups_of(folder, "manual", range(1, 12))
    copy = folder / "worklog-manual-before-upgrade.sqlite3"
    copy.write_bytes(b"a copy the person made")
    assert recovery.manual_prunable(folder) == [old[0].name]
    assert recovery.confirm_manual_pruning(folder) == [old[0].name]
    assert copy.exists() and all(path.exists() for path in old[1:])
    assert {b["name"]: b["kind"] for b in recovery.list_backups(folder, hashed=False)}[copy.name] is None


@pytest.mark.parametrize("text", ['{"confirmed": true, "kept": "everything"}', "not json", "[1, 2]"])
def test_a_pruning_file_that_cannot_be_read_never_prunes(tmp_path, text):
    folder = tmp_path / "backups"
    old = backups_of(folder, "manual", range(1, 14))
    (folder / recovery.PRUNING).write_text(text, encoding="utf-8")
    assert recovery.prune_manual_backups(folder) == []
    assert all(path.exists() for path in old)
