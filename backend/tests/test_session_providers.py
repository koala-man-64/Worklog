"""Provider contracts that cannot safely be exercised with real paid sessions in CI."""

import json
import subprocess
import uuid

import pytest
from worklog import session_launches as sessions

SESSION = "903fb118-fbe8-4241-85bf-d6270a0bfaee"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_preparation_keeps_prompt_out_of_arguments_and_requires_a_saved_session(monkeypatch, provider):
    prompt = "Keep literal text: $(private) & `command`\nContinue later"
    monkeypatch.setattr(sessions, "_codex_executable", lambda: ["codex.exe"])
    monkeypatch.setattr(sessions, "_cli_executable", lambda name: name + ".exe")
    monkeypatch.setattr(sessions.uuid, "uuid4", lambda: uuid.UUID(SESSION))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if provider == "codex":
            output = '\n'.join(json.dumps(event) for event in [
                {"type": "thread.started", "thread_id": SESSION}, {"type": "turn.completed"}])
        else:
            output = json.dumps({"session_id": SESSION, "is_error": False})
        return subprocess.CompletedProcess(command, 0, output.encode(), b"")

    monkeypatch.setattr(sessions, "_run_cli", run)
    assert sessions._prepare_session(provider, "C:\\work", prompt) == (SESSION, "")
    command, options = calls[0]
    assert prompt not in command
    assert options["input_bytes"] == prompt.encode()
    if provider == "claude":
        assert command[command.index("--tools") + 1] == ""
    else:
        assert command[command.index("--sandbox") + 1] == "read-only"


@pytest.mark.parametrize("provider,output", [
    ("codex", '{"type":"turn.completed"}'),
    ("codex", json.dumps({"type": "thread.started", "thread_id": SESSION})),
    ("claude", '{"is_error":false}'),
    ("claude", json.dumps({"session_id": SESSION, "is_error": True})),
])
def test_incomplete_provider_receipt_is_not_reported_as_ready(monkeypatch, provider, output):
    monkeypatch.setattr(sessions, "_codex_executable", lambda: ["codex.exe"])
    monkeypatch.setattr(sessions, "_cli_executable", lambda name: name + ".exe")
    monkeypatch.setattr(sessions, "_run_cli", lambda command, **kw:
                        subprocess.CompletedProcess(command, 0, output.encode(), b""))
    with pytest.raises(sessions._ProviderFailure):
        sessions._prepare_session(provider, "C:\\work", "Prepare only")


def test_claude_handoff_has_real_console_and_uses_subscription_without_changing_parent(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("WORKLOG_UNRELATED_SETTING", "kept")
    monkeypatch.setattr(sessions, "_cli_executable", lambda name: "claude.exe")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, None, None)

    monkeypatch.setattr(sessions, "_run_cli", run)
    sessions._open_desktop("claude", "C:\\work", SESSION)
    command, options = calls[0]
    assert command == ["claude.exe", "--desktop", "--resume", SESSION]
    assert options["new_console"] and not options["capture_output"]
    assert options["input_bytes"] is None
    assert "ANTHROPIC_API_KEY" not in options["env"]
    assert "ANTHROPIC_BASE_URL" not in options["env"]
    assert options["env"]["WORKLOG_UNRELATED_SETTING"] == "kept"
    assert sessions.os.environ["ANTHROPIC_API_KEY"] == "test-only-key"


def test_timeout_terminates_owned_process_tree_and_releases_tracking(monkeypatch):
    class Process:
        returncode = None

        def communicate(self, **kwargs):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("provider", 1)
            return b"", b""

    process = Process()
    killed = []

    def kill_tree(target):
        killed.append(target)
        target.returncode = 1

    monkeypatch.setattr(sessions.subprocess, "Popen", lambda command, **kw: process)
    monkeypatch.setattr(sessions, "_kill_owned_tree", kill_tree)
    with pytest.raises(subprocess.TimeoutExpired):
        sessions._run_cli(["provider"], cwd="C:\\work", env={}, input_bytes=b"Prepare",
                          timeout=1, capture_output=True)
    assert killed == [process]
    assert process not in sessions._ACTIVE_PROCESSES
