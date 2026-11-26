"""A local launch and its Worklog registration recover independently."""

import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest
from worklog import local_sessions, session_launches
from worklog.common import AgentRequestError

CHAT = "d83c2389-5c89-4b11-a893-c6df1b688436"
AGENT = "a861be6f-98b1-4e1b-ab10-3e67aafc0ed7"


@pytest.fixture
def arguments(tmp_path):
    return dict(receipt_dir=tmp_path / "receipts", parent_id="parent-one", provider="codex", cwd=tmp_path,
                label="Review agent", request_id="review-request-1", worklog_command=["worklog", "agent"],
                prepare_credential=lambda name: None,
                register_child=lambda name, chat, cwd: {"name": name, "agentId": AGENT})


def test_concurrent_launch_and_replay_create_one_chat(arguments, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    calls = []

    def prepare(provider, cwd, prompt, *, allow_non_git):
        calls.append((provider, cwd, prompt))
        assert allow_non_git
        started.set()
        assert finish.wait(8)
        return CHAT, "example-model"

    monkeypatch.setattr(local_sessions, "_prepare_session", prepare)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(local_sessions.start_local_session, **arguments)
        try:
            assert started.wait(4)
            with pytest.raises(ValueError, match="in progress"):
                local_sessions.start_local_session(**arguments)
        finally:
            finish.set()
        created = first.result(timeout=8)
    assert created["chatId"] == CHAT and created["agentId"] == AGENT
    assert created["name"] in calls[0][2] and created["state"] == "created"
    assert created == local_sessions.start_local_session(**arguments)
    assert len(calls) == 1
    for key, other in (("provider", "claude"), ("cwd", Path(arguments["cwd"]).parent), ("label", "A new label")):
        with pytest.raises(ValueError, match="different"):
            local_sessions.start_local_session(**{**arguments, key: other})


def test_retry_registration_reuses_the_persisted_provider_id(arguments, monkeypatch):
    created, prepared_credentials, registration_attempts = [], [], []

    def prepare(provider, cwd, prompt, **kwargs):
        created.append(cwd)
        return CHAT, ""

    def register(name, chat, cwd):
        registration_attempts.append((name, chat, cwd))
        if len(registration_attempts) == 1:
            raise AgentRequestError("Worklog is unreachable")
        return {"name": name, "agentId": AGENT}

    monkeypatch.setattr(local_sessions, "_prepare_session", prepare)
    arguments["prepare_credential"] = prepared_credentials.append
    arguments["register_child"] = register
    with pytest.raises(AgentRequestError, match=f"created chat {CHAT}"):
        local_sessions.start_local_session(**arguments)
    assert local_sessions.start_local_session(**arguments)["agentId"] == AGENT
    assert len(created) == 1
    assert registration_attempts[0] == registration_attempts[1]
    assert registration_attempts[0][1:] == (CHAT, str(arguments["cwd"]))
    assert prepared_credentials == [registration_attempts[0][0]]


def test_unknown_provider_outcome_is_not_sent_again(arguments, monkeypatch):
    count = 0

    def timeout(*args, **kwargs):
        nonlocal count
        count += 1
        raise subprocess.TimeoutExpired("private provider invocation", 1)

    monkeypatch.setattr(local_sessions, "_prepare_session", timeout)
    with pytest.raises(ValueError, match="did not confirm") as failed:
        local_sessions.start_local_session(**arguments)
    assert "private provider" not in str(failed.value)
    with pytest.raises(ValueError, match="may already have created"):
        local_sessions.start_local_session(**arguments)
    assert count == 1


def test_incomplete_saved_receipt_never_starts_another_chat(arguments, monkeypatch):
    def prepare(*args, **kwargs):
        return CHAT, ""

    monkeypatch.setattr(local_sessions, "_prepare_session", prepare)
    local_sessions.start_local_session(**arguments)
    path, = arguments["receipt_dir"].glob("*.json")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    del receipt["chatId"]
    path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(local_sessions, "_prepare_session", lambda *a, **kw: pytest.fail("second provider launch"))
    with pytest.raises(ValueError, match="invalid ID"):
        local_sessions.start_local_session(**arguments)


def test_missing_executable_can_be_retried_and_another_parent_gets_its_own_chat(arguments, monkeypatch):
    attempts = 0

    def prepare(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FileNotFoundError()
        return str(UUID(int=attempts)), ""

    monkeypatch.setattr(local_sessions, "_prepare_session", prepare)
    with pytest.raises(ValueError, match="unavailable"):
        local_sessions.start_local_session(**arguments)
    first = local_sessions.start_local_session(**arguments)
    other = local_sessions.start_local_session(**{**arguments, "parent_id": "parent-two"})
    assert first["chatId"] != other["chatId"]
    assert first["name"] != other["name"]
    assert attempts == 3


def test_codex_local_preparation_supports_an_ordinary_folder_and_keeps_the_desktop_default(tmp_path, monkeypatch):
    commands = []
    events = [json.dumps({"type": "thread.started", "thread_id": CHAT}), json.dumps({"type": "turn.completed"})]

    def cli(command, **options):
        commands.append(command)
        assert options["cwd"] == str(tmp_path)
        return subprocess.CompletedProcess(command, 0, ("\n".join(events) + "\n").encode(), b"")

    monkeypatch.setattr(session_launches, "_codex_executable", lambda: ["codex"])
    monkeypatch.setattr(session_launches, "_run_cli", cli)
    for local in (True, False):
        assert session_launches._prepare_session("codex", str(tmp_path), "Prepare", allow_non_git=local) == (CHAT, "")
    assert "--skip-git-repo-check" in commands[0]
    assert "--skip-git-repo-check" not in commands[1]
    assert all(command[-1] == "-" and command[command.index("--sandbox") + 1] == "read-only" for command in commands)
