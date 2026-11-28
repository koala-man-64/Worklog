import json
import sys
import types
import uuid
from pathlib import Path

import pytest
from worklog import cli

PARENT_ID = "2f10a9b4-e2fb-46db-b541-a8ab9dcf5c48"
PARENT_SPONSOR = "b764e951-0428-4276-a752-e68b3e4e5d11"
CHILD_ID = "d3fe38f2-47db-4c22-91bf-0ac0a66f3426"
CHILD_CHAT = "72b0ccf8-e8f4-4c44-a44f-74e8e224e408"
URL = "http://127.0.0.1:9991"


class FakeAgentClient:
    def __init__(self, base, token=None):
        self.base = base
        self.token = token
        self.requests = []
        self.parent = {
            "agentId": PARENT_ID,
            "chatId": "parent-chat-id",
            "registeredBy": {"id": PARENT_SPONSOR, "label": "Sponsor"},
        }
        self.registration_overrides = {}
        self.profile = {"kind": "agent", "active": True, "chatId": CHILD_CHAT}
        self.registration_error_once = False
        self.child_registration_posts = 0
        self.child_id = CHILD_ID

    def request(self, method, path, payload=None, **kwargs):
        self.requests.append((method, path, payload, kwargs))
        if method == "GET" and path == "/agent-registrations/self":
            return self.parent
        if method == "POST" and path == "/agent-registrations":
            self.child_registration_posts += 1
            if self.registration_error_once:
                self.registration_error_once = False
                raise cli.AgentRequestError("registration denied", 401, "authentication")
            return {
                "agentId": self.child_id,
                "chatId": payload["chatId"],
                "registeredBy": {"id": PARENT_ID, "label": "Parent"},
                "directory": payload.get("directory"),
                "label": payload["label"],
                **self.registration_overrides,
            }
        if method == "GET" and path.startswith("/discussions/participants/"):
            return self.profile
        if method == "POST" and path == "/chats/track":
            return {"id": "tracked-chat", **(payload or {})}
        raise AssertionError(f"Unexpected request: {method} {path}")


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    clients = []
    tokens = {"parent.json": "parent-secret"}

    def new_client(base, token=None):
        client = FakeAgentClient(base, token)
        clients.append(client)
        return client

    monkeypatch.setattr(cli, "AgentClient", new_client)
    monkeypatch.setattr(cli, "resolve_port", lambda value=None: value or 8877)
    monkeypatch.setattr(cli, "data_directory", lambda value=None: tmp_path)
    monkeypatch.setattr(cli, "_token_store", lambda args, base: tmp_path / "tokens" / str(cli.urlsplit(base).port or 80))
    monkeypatch.setattr(cli, "_stored_token", lambda path: tokens.get(Path(path).name))

    def store_token(path, name, token):
        tokens[Path(path).name] = token

    monkeypatch.setattr(cli, "_store_new_token", store_token)
    return clients, tokens


def _argv(tmp_path, *extra):
    return ["--data-dir", str(tmp_path), "agent", "--url", URL, "--as", "parent", *extra]


def _reuse_client(monkeypatch, client):
    monkeypatch.setattr(cli, "AgentClient", lambda base, token=None: client)


def _stub_local_sessions(monkeypatch, *, calls=None):
    calls = calls if calls is not None else []

    def start_local_session(**kwargs):
        calls.append(kwargs)
        kwargs["prepare_credential"]("child-agent")
        child = kwargs["register_child"]("child-agent", CHILD_CHAT, str(kwargs["cwd"].resolve()))
        assert set(child) == {"agentId", "name"}
        return {
            "state": "created",
            "provider": kwargs["provider"],
            "chatId": CHILD_CHAT,
            "cwd": str(kwargs["cwd"].resolve()),
            "worklogCommand": kwargs["worklog_command"],
            "name": child["name"],
            "agentId": child["agentId"],
        }

    monkeypatch.setitem(sys.modules, "worklog.local_sessions", types.SimpleNamespace(start_local_session=start_local_session))
    return calls


def test_parser_accepts_named_provider_chat_start_and_requires_request_id():
    parsed = cli.parser().parse_args([
        "agent", "--as", "parent", "chats", "start", "--provider", "codex", "--cwd", "C:/repo",
        "--label", "Worker", "--model", "gpt-6.1-sol", "--reasoning-effort", "low", "--request-id", "start-child-01",
    ])
    assert (parsed.agent_action, parsed.coordination_action, parsed.provider, parsed.cwd, parsed.label,
            parsed.request_id, parsed.as_name) == (
        "chats", "start", "codex", "C:/repo", "Worker", "start-child-01", "parent")
    assert (parsed.model, parsed.reasoning_effort) == ("gpt-6.1-sol", "low")
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["agent", "chats", "start", "--provider", "codex"])


def test_start_requires_named_parent_before_making_requests(fake_cli, monkeypatch, tmp_path, capsys):
    clients, _ = fake_cli
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", "shared-setting-token")
    assert cli.main(["--data-dir", str(tmp_path), "agent", "chats", "start", "--provider", "codex",
                     "--request-id", "start-child-01"]) == 1
    assert clients == []
    captured = capsys.readouterr()
    assert "named Worklog agent" in captured.err
    assert "shared-setting-token" not in captured.out + captured.err


def test_start_registers_exact_child_and_uses_url_port_and_trimmed_label(fake_cli, monkeypatch, tmp_path, capsys):
    clients, _ = fake_cli
    data_directory_calls = []

    def resolve_data_directory(value=None):
        data_directory_calls.append(value)
        return tmp_path

    monkeypatch.setattr(cli, "data_directory", resolve_data_directory)
    cwd = tmp_path / "project"
    cwd.mkdir()
    starts = _stub_local_sessions(monkeypatch)

    assert cli.main(_argv(
        tmp_path, "--port", "8877", "chats", "start", "--provider", "codex", "--cwd", str(cwd),
        "--label", "  Worker  ", "--request-id", "start-child-02",
    )) == 0

    [client] = clients
    assert len(starts) == 1
    kwargs = starts[0]
    assert kwargs["receipt_dir"] == tmp_path / "agent-session-launches" / "9991"
    assert kwargs["parent_id"] == PARENT_ID
    assert kwargs["provider"] == "codex"
    assert kwargs["cwd"] == cwd
    assert kwargs["label"] == "Worker"
    assert kwargs["request_id"] == "start-child-02"
    assert kwargs["worklog_command"] == [
        sys.executable, "-m", "worklog.cli", "--data-dir", str(tmp_path), "agent", "--url", URL,
    ]
    assert data_directory_calls == [str(tmp_path)]
    _, registration_path, payload, _ = next(r for r in client.requests if r[0] == "POST")
    assert registration_path == "/agent-registrations"
    assert payload["chatId"] == CHILD_CHAT
    assert payload["directory"] == str(cwd.resolve())
    assert payload["label"] == "Worker"
    assert "token" in payload
    assert client.requests[-1][1] == f"/discussions/participants/{CHILD_ID}"
    output = capsys.readouterr().out
    assert "parent-secret" not in output
    decoded = json.loads(output)
    assert "token" not in decoded
    assert decoded["worklogCommand"] == kwargs["worklog_command"]


def test_claude_start_uses_provider_default_label(fake_cli, monkeypatch, tmp_path):
    clients, _ = fake_cli
    starts = _stub_local_sessions(monkeypatch)
    assert cli.main(_argv(tmp_path, "chats", "start", "--provider", "claude", "--request-id", "start-child-09")) == 0
    [client] = clients
    assert starts[0]["label"] == "Claude Worklog agent"
    registration = next(payload for method, path, payload, _ in client.requests
                        if method == "POST" and path == "/agent-registrations")
    assert registration["label"] == "Claude Worklog agent"


@pytest.mark.parametrize("field,value", [
    ("registeredBy", None),
    ("chatId", None),
    ("agentId", "not-an-agent-id"),
])
def test_start_rejects_unregistered_or_incomplete_parent(fake_cli, monkeypatch, tmp_path, capsys, field, value):
    clients, _ = fake_cli
    starts = _stub_local_sessions(monkeypatch)
    original_client = cli.AgentClient

    def client_with_bad_parent(base, token=None):
        client = original_client(base, token)
        assert isinstance(client, FakeAgentClient)
        client.parent[field] = value
        return client

    monkeypatch.setattr(cli, "AgentClient", client_with_bad_parent)

    assert cli.main(_argv(tmp_path, "chats", "start", "--provider", "codex", "--request-id", "start-child-03")) == 1
    assert starts == []
    assert "registered Worklog parent" in capsys.readouterr().err


@pytest.mark.parametrize("overrides,agent_id", [
    ({"registeredBy": {"id": "another-agent", "label": "Other"}}, CHILD_ID),
    ({"chatId": "another-chat"}, CHILD_ID),
    ({"directory": "C:/another-project"}, CHILD_ID),
    ({"label": "Other label"}, CHILD_ID),
    ({}, "invalid"),
])
def test_start_rejects_child_registration_not_matching_launch(
    fake_cli, monkeypatch, tmp_path, capsys, overrides, agent_id
):
    clients, _ = fake_cli
    starts = _stub_local_sessions(monkeypatch)
    original_client = cli.AgentClient

    def client_with_bad_child(base, token=None):
        client = original_client(base, token)
        assert isinstance(client, FakeAgentClient)
        client.registration_overrides = overrides
        client.child_id = agent_id
        return client

    monkeypatch.setattr(cli, "AgentClient", client_with_bad_child)
    cwd = tmp_path / "project"
    cwd.mkdir()
    args = _argv(tmp_path, "chats", "start", "--provider", "codex", "--cwd", str(cwd), "--label", "Worker",
                 "--request-id", "start-child-04")

    assert cli.main(args) == 1
    assert len(starts) == 1
    assert "does not match this launch" in capsys.readouterr().err
    [client] = clients
    assert not any(path.startswith("/discussions/participants/") for _, path, _, _ in client.requests)


def test_existing_chat_track_still_uses_its_original_route(fake_cli, tmp_path, capsys):
    clients, _ = fake_cli
    assert cli.main(_argv(tmp_path, "chats", "track", "--item", str(uuid.uuid4()),
                          "--request-id", "track-child-01")) == 0
    [client] = clients
    method, path, payload, kwargs = client.requests[-1]
    assert (method, path) == ("POST", "/chats/track")
    assert payload["ownerType"] == "item"
    assert payload["ownerId"]
    assert kwargs["request_id"] == "track-child-01"
    assert "tracked-chat" in capsys.readouterr().out


def test_registration_failure_retry_does_not_start_another_provider_chat(fake_cli, monkeypatch, tmp_path, capsys):
    import worklog.local_sessions as local_sessions

    _, _ = fake_cli
    client = FakeAgentClient(URL)
    client.registration_error_once = True
    _reuse_client(monkeypatch, client)
    calls = []

    def prepare(provider, cwd, prompt, *, requested_options, allow_non_git):
        calls.append((provider, cwd, prompt, allow_non_git))
        return CHILD_CHAT, "test-model"

    monkeypatch.setattr(local_sessions, "_prepare_session", prepare)
    cwd = tmp_path / "project"
    cwd.mkdir()
    args = _argv(tmp_path, "chats", "start", "--provider", "codex", "--cwd", str(cwd),
                 "--request-id", "start-child-05")

    assert cli.main(args) == 1
    assert "registration denied" in capsys.readouterr().err
    assert cli.main(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["chatId"] == CHILD_CHAT
    assert output["agentId"] == CHILD_ID
    assert len(calls) == 1
    assert client.child_registration_posts == 2


def test_removed_child_credential_after_possible_registration_is_not_recreated(
    fake_cli, monkeypatch, tmp_path, capsys
):
    import worklog.local_sessions as local_sessions

    _, tokens = fake_cli
    client = FakeAgentClient(URL)
    client.registration_error_once = True
    _reuse_client(monkeypatch, client)
    calls = []

    def prepare(provider, cwd, prompt, *, requested_options, allow_non_git):
        calls.append((provider, cwd, prompt, allow_non_git))
        return CHILD_CHAT, "test-model"

    monkeypatch.setattr(local_sessions, "_prepare_session", prepare)
    cwd = tmp_path / "project"
    cwd.mkdir()
    args = _argv(tmp_path, "chats", "start", "--provider", "codex", "--cwd", str(cwd),
                 "--request-id", "start-child-07")

    assert cli.main(args) == 1
    capsys.readouterr()
    child_credentials = [key for key in tokens if key != "parent.json"]
    assert len(child_credentials) == 1
    del tokens[child_credentials[0]]

    assert cli.main(args) == 1
    assert "saved credential" in capsys.readouterr().err
    assert len(calls) == 1
    assert client.child_registration_posts == 1


@pytest.mark.parametrize("profile", [
    {"kind": "agent", "active": False, "chatId": CHILD_CHAT},
    {"kind": "agent", "active": True, "chatId": "another-chat"},
])
def test_stale_registered_recipient_replay_does_not_launch_again(fake_cli, monkeypatch, tmp_path, capsys, profile):
    import worklog.local_sessions as local_sessions

    _, _ = fake_cli
    client = FakeAgentClient(URL)
    _reuse_client(monkeypatch, client)
    calls = []

    def prepare(provider, cwd, prompt, *, requested_options, allow_non_git):
        calls.append((provider, cwd, prompt, allow_non_git))
        return CHILD_CHAT, "test-model"

    monkeypatch.setattr(local_sessions, "_prepare_session", prepare)
    cwd = tmp_path / "project"
    cwd.mkdir()
    args = _argv(tmp_path, "chats", "start", "--provider", "codex", "--cwd", str(cwd),
                 "--request-id", "start-child-08")

    assert cli.main(args) == 0
    capsys.readouterr()
    client.profile = profile
    assert cli.main(args) == 1
    assert "no longer active in chat" in capsys.readouterr().err
    assert len(calls) == 1
    assert client.child_registration_posts == 1
