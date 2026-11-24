"""start runs only the checkout that serves the data (roadmap step 1.6).

The person's data folder is named by a user-level variable every shell inherits, so a start run
from an agent's worktree used to stop the live server and serve that worktree's code on the
person's data: a branch with a higher schema version upgraded the live file. serve now records
which checkout served the folder, start refuses a folder another checkout last served unless
--force, and check reports the record."""

import json
from pathlib import Path

import pytest
from worklog import cli


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


def served_by(folder: Path) -> str | None:
    path = folder / cli.SERVED_BY
    return json.loads(path.read_text(encoding="utf-8"))["checkout"] if path.exists() else None


def other_checkout(folder: Path, tmp_path: Path) -> str:
    other = str((tmp_path / "other-worktree").resolve())
    folder.mkdir(parents=True, exist_ok=True)
    cli._write_json(folder / cli.SERVED_BY, {"checkout": other})
    return other


def test_a_folder_another_checkout_served_is_refused(tmp_path, launches):
    data = tmp_path / "data"
    other = other_checkout(data, tmp_path)
    with pytest.raises(RuntimeError) as refused:
        cli.start(data, 8890)
    assert other in str(refused.value) and str(cli.CHECKOUT) in str(refused.value)
    assert "--force" in str(refused.value)
    assert launches == []
    assert served_by(data) == other


def test_force_serves_it_and_the_record_names_this_checkout(tmp_path, launches):
    data = tmp_path / "data"
    other_checkout(data, tmp_path)
    assert cli.start(data, 8890, force=True)["running"] is True
    assert launches == [data]
    assert served_by(data) == str(cli.CHECKOUT)


def test_a_folder_with_no_record_is_served_and_recorded(tmp_path, launches):
    data = tmp_path / "data"
    assert cli.start(data, 8890)["running"] is True
    assert launches == [data]
    assert served_by(data) == str(cli.CHECKOUT)


def test_a_folder_this_checkout_served_is_served(tmp_path, launches):
    data = tmp_path / "data"
    cli.record_checkout(data)
    assert cli.start(data, 8890)["running"] is True
    assert launches == [data]


def test_check_reports_the_checkout_that_served_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_health", lambda port: None)
    monkeypatch.setattr(cli, "_port_busy", lambda port: False)
    data = tmp_path / "data"
    data.mkdir()
    assert cli.check(data, 8890)["servedBy"] is None
    cli.record_checkout(data)
    report = cli.check(data, 8890)
    assert (report["servedBy"], report["servedByThisCheckout"]) == (str(cli.CHECKOUT), True)
    other = other_checkout(data, tmp_path)
    report = cli.check(data, 8890)
    assert (report["servedBy"], report["servedByThisCheckout"]) == (other, False)


def test_start_with_force_from_the_command_line(tmp_path, launches, capsys):
    data = tmp_path / "data"
    other_checkout(data, tmp_path)
    assert cli.main(["--data-dir", str(data), "--port", "8890", "start"]) == 1
    assert "--force" in capsys.readouterr().err
    assert cli.main(["--data-dir", str(data), "--port", "8890", "start", "--force"]) == 0, capsys.readouterr().err
    assert served_by(data) == str(cli.CHECKOUT)
