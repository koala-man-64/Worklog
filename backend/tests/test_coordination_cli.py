"""Repository coordination CLI wire contracts and credential boundaries."""

import io
import json
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

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
        return io.BytesIO(b'{"items":[],"total":0}')

    monkeypatch.setattr(cli, "_open", respond)
    return captured


@pytest.mark.parametrize("arguments,route,query", [
    (["repositories", "list"], "/repositories", {"archived": ["false"], "limit": ["50"], "offset": ["0"]}),
    (["repositories", "list", "--archived"], "/repositories", {"archived": ["true"], "limit": ["50"], "offset": ["0"]}),
    (["repositories", "list", "--q", "one & two"], "/repositories", {"q": ["one & two"], "archived": ["false"], "limit": ["50"], "offset": ["0"]}),
    (["repositories", "show", ID], f"/repositories/{ID}", {}),
    (["runs", "show", ID], f"/agent-runs/{ID}", {}),
    (["runs", "list", "--include-ended"], "/agent-runs", {"includeEnded": ["true"], "limit": ["50"], "offset": ["0"]}),
    (["participation", "list", "--repo", ID, "--run", ID, "--include-released", "--limit", "2", "--offset", "3"],
     "/participations", {"repositoryId": [ID], "runId": [ID], "includeReleased": ["true"], "limit": ["2"], "offset": ["3"]}),
    (["participation", "show", ID], f"/participations/{ID}", {}),
    (["updates", "list", "--repo", ID, "--item", ID, "--participation", ID], "/coordination-updates",
     {"repositoryId": [ID], "itemId": [ID], "participationId": [ID], "limit": ["50"], "offset": ["0"]}),
    (["context", "--repo", ID, "--item", ID, "--date", "2026-11-01"], "/context",
     {"repositoryId": [ID], "itemId": [ID], "date": ["2026-11-01"]}),
])
def test_filtered_reads(requests, arguments, route, query):
    assert cli.main(["agent", *arguments]) == 0
    request = requests[-1]
    assert request.method == "GET"
    assert urlsplit(request.full_url).path == "/api/v1" + route
    assert parse_qs(urlsplit(request.full_url).query) == query


@pytest.mark.parametrize("arguments,route,payload", [
    (["runs", "start", "--client-run-key", "chat-a", "--display-name", "Implementation", "--provider", "codex", "--chat", "external-a"],
     "/agent-runs", {"clientRunKey": "chat-a", "displayName": "Implementation", "provider": "codex", "externalChatId": "external-a"}),
    (["runs", "end", ID, "--revision", "2"], f"/agent-runs/{ID}/end", {"revision": 2}),
    (["participation", "start", "--run", ID, "--repo", ID], "/participations", {"runId": ID, "contexts": [{"repositoryId": ID}]}),
    *[(["participation", verb, ID, "--revision", "3"], f"/participations/{ID}/{verb}", {"revision": 3})
      for verb in ("check-in", "release", "rejoin")],
    (["updates", "post", "--participation", ID, "--text", "Ready"], f"/participations/{ID}/updates", {"kind": "update", "text": "Ready"}),
    (["updates", "post", "--participation", ID, "--kind", "blocker", "--text", "Awaiting review"],
     f"/participations/{ID}/updates", {"kind": "blocker", "text": "Awaiting review"}),
])
def test_writes_preserve_explicit_operation_key(requests, arguments, route, payload):
    assert cli.main(["agent", *arguments, "--request-id", KEY]) == 0
    request = requests[-1]
    assert request.method == "POST"
    assert request.full_url.endswith(route)
    assert json.loads(request.data) == payload
    assert request.get_header("Idempotency-key") == KEY


def test_context_file_retains_paths_and_revision_update(requests, tmp_path):
    payload = {"runId": ID, "contexts": [{"repositoryId": ID, "checkoutPath": "C:/repo", "worktreePath": "C:/task", "branch": "codex/task"}]}
    source = tmp_path / "context.json"
    source.write_text(json.dumps(payload))
    assert cli.main(["agent", "participation", "start", "--file", str(source), "--request-id", KEY]) == 0
    assert json.loads(requests[-1].data) == payload
    source.write_text(json.dumps({"itemId": None, "contexts": payload["contexts"]}))
    assert cli.main(["agent", "participation", "update", ID, "--file", str(source), "--revision", "4", "--request-id", KEY]) == 0
    assert requests[-1].method == "PATCH"
    assert requests[-1].get_header("Idempotency-key") == KEY
    assert json.loads(requests[-1].data) == {"itemId": None, "contexts": payload["contexts"], "revision": 4}


def test_update_json_stdin_preserves_kind(requests, monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('{"kind":"handoff","text":"Continue here"}'))
    assert cli.main(["agent", "updates", "post", "--participation", ID, "--file", "-", "--request-id", KEY]) == 0
    assert json.loads(requests[-1].data) == {"kind": "handoff", "text": "Continue here"}


def test_description_can_be_updated_without_context_file(requests):
    assert cli.main(["agent", "participation", "update", ID, "--description", "Reviewing parser", "--revision", "2", "--request-id", KEY]) == 0
    assert json.loads(requests[-1].data) == {"revision": 2, "description": "Reviewing parser"}


def test_new_writes_require_stable_key_before_transport(requests):
    with pytest.raises(SystemExit):
        cli.main(["agent", "participation", "release", ID, "--revision", "1"])
    assert not requests


@pytest.mark.parametrize("method,path", [
    ("POST", "/repositories"), ("PATCH", f"/repositories/{ID}"),
    ("POST", f"/repositories/{ID}/archive"), ("POST", f"/repositories/{ID}/recover"),
    ("DELETE", f"/participations/{ID}"), ("POST", f"/participations/{ID}/reassign"),
    ("POST", "/participations/%2e%2e/updates"), ("POST", f"/participations/{ID}/release/"),
])
def test_catalog_and_unrecognized_actions_stay_unreachable(requests, method, path):
    with pytest.raises(ValueError, match="outside"):
        cli.AgentClient().request(method, path, {})
    assert not requests


def test_uncertain_patch_reports_retry_key_without_credentials(requests, monkeypatch, capsys):
    def unavailable(*args, **kwargs):
        raise URLError("private-agent-token")

    monkeypatch.setattr(cli, "_open", unavailable)
    with pytest.raises(cli.AgentRequestError, match=KEY) as error:
        cli.AgentClient().request("PATCH", f"/participations/{ID}", {"revision": 1}, request_id=KEY)
    assert "private-agent-token" not in str(error.value)
