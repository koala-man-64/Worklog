"""The inbox command's wire contract: one route, an operation key, and the chat filter."""

import io
import json
from urllib.error import URLError
from urllib.parse import urlsplit

import pytest
from worklog import cli

ID = "11111111-1111-4111-8111-111111111111"
KEY = "stable-operation-key"


@pytest.fixture
def requests(monkeypatch):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", "private-agent-token")
    captured = []

    def respond(request, **kwargs):
        captured.append(request)
        return io.BytesIO(b'{"items":[{"id":"message","text":"Hello"}],"total":1}')

    monkeypatch.setattr(cli, "_open", respond)
    return captured


def test_inbox_receives_under_a_fresh_operation_key_each_time(requests, capsys):
    assert cli.main(["agent", "inbox"]) == 0
    request = requests[-1]
    assert request.method == "POST"
    assert urlsplit(request.full_url).path == "/api/v1/inbox/receive"
    assert json.loads(request.data) == {}
    key = request.get_header("Idempotency-key")
    assert key and 8 <= len(key) <= 200
    assert json.loads(capsys.readouterr().out)["items"][0]["text"] == "Hello"
    assert cli.main(["agent", "inbox"]) == 0
    assert requests[-1].get_header("Idempotency-key") != key


def test_inbox_chat_filter_and_explicit_key(requests):
    assert cli.main(["agent", "inbox", "--chat", "session-1", "--request-id", KEY]) == 0
    assert json.loads(requests[-1].data) == {"chatId": "session-1"}
    assert requests[-1].get_header("Idempotency-key") == KEY


def test_an_uncertain_receive_names_the_key_to_retry_without_the_token(requests, monkeypatch, capsys):
    def unavailable(*args, **kwargs):
        raise URLError("private-agent-token")

    monkeypatch.setattr(cli, "_open", unavailable)
    assert cli.main(["agent", "inbox", "--request-id", KEY]) == 1
    message = capsys.readouterr().err
    assert "--request-id " + KEY in message and "private-agent-token" not in message


@pytest.mark.parametrize("method,path", [
    ("GET", "/inbox/receive"), ("POST", "/inbox"), ("POST", "/inbox/receive/"),
    ("GET", f"/chats/{ID}/messages"), ("POST", f"/chats/{ID}/messages"),
])
def test_other_inbox_and_chat_message_routes_stay_unreachable(requests, method, path):
    with pytest.raises(ValueError, match="outside"):
        cli.AgentClient().request(method, path, {})
    assert not requests
