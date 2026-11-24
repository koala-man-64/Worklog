"""start cannot open an empty data set by accident (audit finding O2, roadmap step 1.5).

The data folder is --data-dir, else the process's WORKLOG_DATA_DIR, else (on Windows) the user-level
WORKLOG_DATA_DIR, else the default folder. A shell started without the variable used to land on the
default folder, so a start while the real server was down opened a new, empty database on port 8877.
Now a folder nobody named that holds no database is refused while another candidate holds one;
a named folder may always be created, as every isolated test instance is."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from worklog import cli, locations


def database_in(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder / "worklog.sqlite3")) as conn:
        conn.execute("CREATE TABLE marker(x)")
    return folder


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine whose default folder is tmp_path/local/Worklog, with no WORKLOG_DATA_DIR in the
    process and none at user level until a test sets `user["value"]`."""
    monkeypatch.delenv("WORKLOG_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(locations.Path, "home", lambda: tmp_path / "home")
    user: dict[str, str | None] = {"value": None}
    monkeypatch.setattr(locations, "user_environment_variable", lambda name: user["value"])
    return {"default": locations.default_folder(), "user": user}


@pytest.fixture
def launches(tmp_path, monkeypatch):
    """start against a fake server: what it launched, by data folder."""
    health = {"app": "worklog", "ready": True, "instanceId": "instance", "dataId": "data"}
    process = {"created": 123, "executable": "python"}
    launched: list[Path] = []

    class Launched:
        def poll(self):
            return None

    def popen(args, **options):
        folder = Path(args[args.index("--data-dir") + 1])
        launched.append(folder)
        cli._write_json(folder / "server-state.json",
                        {**health, "dataDir": str(folder.resolve()), "pid": 4321, "process": process})
        return Launched()

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    monkeypatch.setattr(cli, "_health", lambda port: health if launched else None)
    monkeypatch.setattr(cli, "_port_busy", lambda port: False)
    monkeypatch.setattr(cli, "_process_identity", lambda pid: process)
    return launched


def test_a_process_without_the_variable_reads_the_user_level_one(machine, tmp_path):
    machine["user"]["value"] = str(tmp_path / "personal")
    folder = locations.locate(None)
    assert folder.path == (tmp_path / "personal").resolve()
    assert folder.named is False
    assert cli.data_directory() == folder.path


def test_the_process_variable_and_data_dir_still_win(machine, tmp_path, monkeypatch):
    machine["user"]["value"] = str(tmp_path / "personal")
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "process"))
    assert locations.locate(None) == locations.DataFolder((tmp_path / "process").resolve(), True, ())
    assert locations.locate(str(tmp_path / "given")).path == (tmp_path / "given").resolve()


def test_an_unnamed_empty_folder_is_refused_beside_one_that_holds_a_database(machine, tmp_path, launches, capsys):
    database_in(machine["default"])
    machine["user"]["value"] = str(tmp_path / "typo")
    assert cli.main(["--port", "8890", "start"]) == 1
    error = capsys.readouterr().err
    assert str((tmp_path / "typo").resolve()) in error and str(machine["default"]) in error
    assert not (tmp_path / "typo").exists()
    assert launches == []


def test_start_with_data_dir_creates_a_fresh_folder_beside_the_default_one(machine, tmp_path, launches, capsys):
    database_in(machine["default"])
    fresh = tmp_path / "isolated"
    assert cli.main(["--data-dir", str(fresh), "--port", "8890", "start"]) == 0, capsys.readouterr().err
    result = json.loads(capsys.readouterr().out)
    assert result["created"] is True and result["dataDir"] == str(fresh.resolve())
    assert launches == [fresh.resolve()]


def test_a_first_start_on_a_machine_with_no_data_creates_it_and_says_so(machine, launches, capsys):
    assert cli.main(["--port", "8890", "start"]) == 0, capsys.readouterr().err
    result = json.loads(capsys.readouterr().out)
    assert result["created"] is True
    assert launches == [machine["default"]]


def test_a_start_on_existing_data_reports_nothing_created(machine, launches, capsys):
    database_in(machine["default"])
    assert cli.main(["--port", "8890", "start"]) == 0, capsys.readouterr().err
    assert json.loads(capsys.readouterr().out)["created"] is False
