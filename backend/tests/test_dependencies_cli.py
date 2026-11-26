"""Typed dependency CLI wire contracts and agent scope boundaries."""

import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from worklog import cli

ID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
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


def test_list_uses_one_typed_scope_and_canonical_record_id(requests):
    identity = ID
    assert cli.main(["agent", "dependencies", "list", "--type", "ticket", "--id", identity]) == 0
    request = requests[-1]
    url = urlsplit(request.full_url)
    assert request.method == "GET"
    assert url.path == "/api/v1/dependencies"
    assert parse_qs(url.query) == {"type": ["ticket"], "id": [identity]}


def test_report_sends_agent_prerequisite_context_and_stable_key(requests):
    args = [
        "agent",
        "dependencies",
        "report",
        "--prerequisite-type",
        "agent",
        "--prerequisite-id",
        OTHER,
        "--context-type",
        "step",
        "--context-id",
        ID,
        "--condition",
        "Wait for review",
        "--request-id",
        KEY,
    ]
    assert cli.main(args) == 0
    request = requests[-1]
    assert request.method == "POST"
    assert urlsplit(request.full_url).path == "/api/v1/dependencies/waits"
    assert json.loads(request.data) == {
        "prerequisite": {"type": "agent", "id": OTHER},
        "context": {"type": "step", "id": ID},
        "condition": "Wait for review",
    }
    assert request.get_header("Idempotency-key") == KEY


@pytest.mark.parametrize(
    "arguments,route,payload",
    [
        (
            ["confirm", ID, "--revision", "4", "--note", "The review is complete"],
            f"/dependencies/{ID}/confirm",
            {"revision": 4, "note": "The review is complete"},
        ),
        (["confirm", ID, "--revision", "4"], f"/dependencies/{ID}/confirm", {"revision": 4}),
        (["archive", ID, "--revision", "5"], f"/dependencies/{ID}/archive", {"revision": 5}),
    ],
)
def test_confirm_and_archive_send_current_revision_and_optional_note(
    requests, arguments, route, payload
):
    assert cli.main(["agent", "dependencies", *arguments, "--request-id", KEY]) == 0
    request = requests[-1]
    assert request.method == "POST"
    assert urlsplit(request.full_url).path == "/api/v1" + route
    assert json.loads(request.data) == payload
    assert request.get_header("Idempotency-key") == KEY


def test_dependency_write_needs_request_id_before_transport(requests):
    with pytest.raises(SystemExit):
        cli.main(
            [
                "agent",
                "dependencies",
                "report",
                "--prerequisite-type",
                "item",
                "--prerequisite-id",
                ID,
                "--context-type",
                "item",
                "--context-id",
                OTHER,
                "--condition",
                "Wait for item",
            ]
        )
    assert not requests


def test_context_scope_does_not_accept_agent(requests):
    with pytest.raises(SystemExit):
        cli.main(
            [
                "agent",
                "dependencies",
                "report",
                "--prerequisite-type",
                "item",
                "--prerequisite-id",
                ID,
                "--context-type",
                "agent",
                "--context-id",
                OTHER,
                "--condition",
                "Wait for item",
                "--request-id",
                KEY,
            ]
        )
    assert not requests


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/dependencies"),
        ("PATCH", "/dependencies"),
        ("GET", "/dependencies/waits"),
        ("GET", f"/dependencies/{ID}"),
        ("POST", "/dependencies/not-a-uuid/confirm"),
        ("POST", f"/dependencies/{ID}/recover"),
        ("DELETE", f"/dependencies/{ID}/archive"),
    ],
)
def test_route_allowlist_excludes_shared_writes_and_other_dependency_routes(requests, method, path):
    client = cli.AgentClient(token="private-agent-token")
    with pytest.raises(ValueError, match="outside proposal/read scope"):
        client.request(method, path)
    assert not requests
