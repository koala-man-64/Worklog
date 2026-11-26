"""Provider notification transport tests; all CLI traffic is replaced with fixtures."""

from __future__ import annotations

import subprocess

import pytest
from worklog import push_providers

THREAD_ID = "123e4567-e89b-12d3-a456-426614174000"
NOTICE = "Worklog has an update. Check Worklog for details."


def test_codex_queues_notice_with_explicit_thread_and_no_shell(monkeypatch, capsys):
    command = ["C:/tools/codex.exe"]
    observed = []
    monkeypatch.setattr(push_providers, "_codex_executable", lambda: command)

    def run(args):
        observed.append(args)
        return subprocess.CompletedProcess(args, 0, b"resume warning", b"sensitive stderr")

    monkeypatch.setattr(push_providers, "_run", run)

    result = push_providers.submit_notice("codex", THREAD_ID, NOTICE)

    assert result == "queued"
    assert observed == [
        [
            "C:/tools/codex.exe",
            "queue",
            "--thread",
            THREAD_ID,
            "--message",
            NOTICE,
        ]
    ]
    assert capsys.readouterr() == ("", "")


def test_codex_resume_is_an_explicit_argument(monkeypatch):
    observed = []
    monkeypatch.setattr(push_providers, "_codex_executable", lambda: ["codex"])
    monkeypatch.setattr(
        push_providers,
        "_run",
        lambda args: observed.append(args) or subprocess.CompletedProcess(args, 0, b"", b""),
    )

    assert push_providers.submit_notice("codex", THREAD_ID, NOTICE, resume=True) == "queued"
    assert observed[0][-1] == "--resume"


@pytest.mark.parametrize(
    ("resume", "returncode", "stderr", "expected"),
    [
        (
            True,
            0,
            f"Warning: message was queued, but could not resume {THREAD_ID}: thread/resume failed: probe".encode(),
            "queued_resume_warning",
        ),
        (
            False,
            0,
            f"Warning: message was queued, but could not resume {THREAD_ID}: thread/resume failed: probe".encode(),
            "queued",
        ),
        (
            True,
            0,
            b"Warning: message was queued, but could not resume 99999999-9999-4999-8999-999999999999: probe",
            "queued",
        ),
        (
            True,
            1,
            f"Warning: message was queued, but could not resume {THREAD_ID}: probe".encode(),
            "uncertain",
        ),
    ],
)
def test_codex_resume_warning_is_recognized_only_for_confirmed_queue(
    monkeypatch, capsys, resume, returncode, stderr, expected
):
    monkeypatch.setattr(push_providers, "_codex_executable", lambda: ["codex"])
    monkeypatch.setattr(
        push_providers,
        "_run",
        lambda args: subprocess.CompletedProcess(args, returncode, b"queued", stderr),
    )

    assert push_providers.submit_notice("codex", THREAD_ID, NOTICE, resume=resume) == expected
    assert capsys.readouterr() == ("", "")


def test_invalid_session_id_is_rejected_before_resolving_cli(monkeypatch):
    resolved = []
    monkeypatch.setattr(push_providers, "_codex_executable", lambda: resolved.append(True))

    assert push_providers.submit_notice("codex", "--thread injection", NOTICE) == "unavailable"
    assert resolved == []


@pytest.mark.parametrize("notice", ["", "line one\nline two", "x" * 4_001])
def test_non_fixed_shape_notice_is_rejected_before_cli(monkeypatch, notice):
    resolved = []
    monkeypatch.setattr(push_providers, "_codex_executable", lambda: resolved.append(True))

    assert push_providers.submit_notice("codex", THREAD_ID, notice) == "unavailable"
    assert resolved == []


def test_missing_cli_is_definitely_unavailable(monkeypatch):
    monkeypatch.setattr(push_providers, "_codex_executable", lambda: None)
    assert push_providers.submit_notice("codex", THREAD_ID, NOTICE) == "unavailable"


def test_spawn_failure_is_definitely_unavailable(monkeypatch):
    monkeypatch.setattr(push_providers, "_codex_executable", lambda: ["codex"])
    monkeypatch.setattr(
        push_providers, "_run", lambda args: (_ for _ in ()).throw(FileNotFoundError())
    )

    assert push_providers.submit_notice("codex", THREAD_ID, NOTICE) == "unavailable"


def test_nonzero_exit_is_uncertain_and_output_stays_private(monkeypatch, capsys):
    monkeypatch.setattr(push_providers, "_codex_executable", lambda: ["codex"])
    monkeypatch.setattr(
        push_providers,
        "_run",
        lambda args: subprocess.CompletedProcess(args, 1, b"secret stdout", b"secret stderr"),
    )

    assert push_providers.submit_notice("codex", THREAD_ID, NOTICE) == "uncertain"
    assert capsys.readouterr() == ("", "")


def test_timeout_is_uncertain(monkeypatch):
    monkeypatch.setattr(push_providers, "_codex_executable", lambda: ["codex"])

    def timeout(args):
        raise subprocess.TimeoutExpired(args, push_providers.TIMEOUT_SECONDS, output=b"private")

    monkeypatch.setattr(push_providers, "_run", timeout)
    assert push_providers.submit_notice("codex", THREAD_ID, NOTICE) == "uncertain"


def test_claude_fails_closed_without_an_installed_channel_route():
    assert push_providers.submit_notice("claude", THREAD_ID, NOTICE) == "unavailable"


def test_unknown_provider_is_unavailable():
    assert push_providers.submit_notice("other", THREAD_ID, NOTICE) == "unavailable"


def test_codex_child_process_is_bounded_hidden_and_uses_filtered_environment(monkeypatch):
    observed = {}
    monkeypatch.setenv("CODEX_HOME", "C:/fixture/codex-home")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret-must-not-pass")
    monkeypatch.setattr(push_providers.os, "name", "nt")

    def run(command, **options):
        observed["command"] = command
        observed.update(options)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(push_providers.subprocess, "run", run)

    result = push_providers._run(["codex", "queue"])

    assert result.returncode == 0
    assert observed["shell"] is False
    assert observed["timeout"] == push_providers.TIMEOUT_SECONDS
    assert observed["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert observed["stdin"] == subprocess.DEVNULL
    assert observed["stdout"] == subprocess.PIPE
    assert observed["stderr"] == subprocess.PIPE
    assert observed["env"]["CODEX_HOME"] == "C:/fixture/codex-home"
    assert "OPENAI_API_KEY" not in observed["env"]
