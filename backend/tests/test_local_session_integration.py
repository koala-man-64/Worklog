"""The local chats-start command creates and registers one addressable Worklog recipient."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from test_registration import bearer, issue, write
from worklog import cli, local_sessions
from worklog.app import create_app


@contextmanager
def running_worklog(data_dir: Path, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        monkeypatch.setenv("WORKLOG_PORT", str(port))
        app = create_app(data_dir, testing=True)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started, "Worklog test server did not start"
            yield f"http://127.0.0.1:{port}", app, port
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "Worklog test server did not stop"


def invoke_cli(capsys, data_dir: Path, port: int, *arguments: str) -> dict:
    status = cli.main(["--data-dir", str(data_dir), "--port", str(port), *arguments])
    captured = capsys.readouterr()
    assert status == 0, captured.err or captured.out
    return json.loads(captured.out)


def token_for(data_dir: Path, port: int, name: str) -> str:
    path = data_dir / cli.AGENT_TOKENS / str(port) / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))["token"]


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_chats_start_prepares_once_registers_child_and_delivers_parent_ask(
    tmp_path, monkeypatch, capsys, provider
):
    cli_data = tmp_path / "cli-data"
    cwd = tmp_path / f"{provider}-workspace"
    cwd.mkdir()
    chat_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"worklog-local-session-{provider}"))
    model = f"{provider}-test-model"
    prepare_calls = []

    def prepare(actual_provider, actual_cwd, prompt, *, allow_non_git=False):
        prepare_calls.append((actual_provider, actual_cwd, prompt, allow_non_git))
        return chat_id, model

    monkeypatch.setattr(local_sessions, "_prepare_session", prepare)
    with running_worklog(tmp_path / "worklog-data", monkeypatch) as (url, app, port):
        with httpx.Client(base_url=url, trust_env=False, headers={"Origin": url}) as person:
            session = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
            session.raise_for_status()
            person.headers["X-CSRF-Token"] = session.json()["csrfToken"]
            sponsor_token = issue(person)["token"]

        monkeypatch.setenv("WORKLOG_AGENT_TOKEN", sponsor_token)
        parent = invoke_cli(
            capsys,
            cli_data,
            port,
            "agent", "register", "--chat", str(uuid.uuid4()), "--name", "parent", "--label", "Parent",
        )
        parent_token = token_for(cli_data, port, "parent")
        with httpx.Client(base_url=url, trust_env=False) as api:
            parent_self = api.get("/api/v1/agent-registrations/self", headers=bearer(parent_token))
            parent_self.raise_for_status()
            parent_identity = parent_self.json()
        assert parent_identity["agentId"] == parent["agentId"]

        start_args = (
            "agent", "--as", "parent", "chats", "start",
            "--provider", provider,
            "--cwd", str(cwd),
            "--label", f"Child {provider.title()}",
            "--request-id", f"start-{provider}-request-1",
        )
        created = invoke_cli(capsys, cli_data, port, *start_args)
        repeated = invoke_cli(capsys, cli_data, port, *start_args)

        assert created == repeated
        assert created["state"] == "created"
        assert created["provider"] == provider
        assert created["chatId"] == chat_id
        assert created["cwd"] == str(cwd.resolve())
        assert created["name"].startswith("session-")
        assert created["agentId"]
        assert created["model"] == model
        assert len(prepare_calls) == 1
        prepared_provider, prepared_cwd, prompt, allow_non_git = prepare_calls[0]
        assert (prepared_provider, prepared_cwd, allow_non_git) == (provider, str(cwd.resolve()), True)
        assert created["name"] in prompt and "WORKLOG_READY" in prompt
        worklog_command = [
            sys.executable, "-m", "worklog.cli", "--data-dir", str(cli_data),
            "agent", "--url", url, "--as", created["name"],
        ]
        assert created["worklogCommand"] == worklog_command
        assert json.dumps(worklog_command, ensure_ascii=True) in prompt
        assert sponsor_token not in prompt and parent_token not in prompt

        child_token = token_for(cli_data, port, created["name"])
        assert child_token not in prompt
        with httpx.Client(base_url=url, trust_env=False) as api:
            child_self = api.get("/api/v1/agent-registrations/self", headers=bearer(child_token))
            child_self.raise_for_status()
            registration = child_self.json()
            assert registration["agentId"] == created["agentId"]
            assert registration["chatId"] == created["chatId"]
            assert registration["directory"] == str(cwd.resolve())
            assert registration["registeredBy"] == {
                "id": parent_identity["agentId"], "label": parent_identity["label"],
            }

            with app.state.db.read() as connection:
                recipients = connection.execute(
                    "SELECT agent_id FROM communication_credentials WHERE registered_by=? AND chat_id=?",
                    (parent_identity["agentId"], chat_id),
                ).fetchall()
            assert [row["agent_id"] for row in recipients] == [created["agentId"]]

            ask = write(
                api,
                "POST",
                "/discussions",
                {
                    "text": "Please check this new session's Worklog access.",
                    "acknowledgement": {"targets": [{"kind": "agent", "id": created["agentId"]}]},
                },
                headers=bearer(parent_token),
            )
            assert ask.status_code == 200, ask.text
            message = ask.json()["message"]

        child_environment = {key: value for key, value in os.environ.items() if not key.startswith("WORKLOG_")}
        assert not any(key.startswith("WORKLOG_") for key in child_environment)
        completed = subprocess.run(
            [*created["worklogCommand"], "inbox"],
            cwd=cwd,
            env=child_environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        inbox = json.loads(completed.stdout)
        assert inbox["unseen"] == 1
        [received] = inbox["received"]
        assert received["message"]["id"] == message["id"]
        assert received["message"]["text"] == "Please check this new session's Worklog access."
