"""The agent CLI's ties commands: what each sends, the routes it can and cannot reach, and the whole path."""

import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from worklog import cli
from worklog.app import create_app

ID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
KEY = "stable-operation-key"


@pytest.fixture
def requests(monkeypatch):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", "private-agent-token")
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    captured = []

    def respond(request, **kwargs):
        captured.append(request)
        return io.BytesIO(json.dumps({"items": [], "total": 0}).encode())

    monkeypatch.setattr(cli, "_open", respond)
    return captured


def sent(request):
    return request.method, urlsplit(request.full_url).path.removeprefix("/api/v1"), parse_qs(urlsplit(request.full_url).query)


@pytest.mark.parametrize("arguments,query", [
    (["ties", "list"], {}),
    (["ties", "list", "--agent", ID], {"agentId": [ID]}),
    (["ties", "list", "--type", "question", "--id", ID, "--id", OTHER], {"targetType": ["question"], "targetId": [ID, OTHER]}),
])
def test_list_reads_your_own_ties_or_the_ones_it_names(requests, arguments, query):
    assert cli.main(["agent", *arguments]) == 0
    assert sent(requests[-1]) == ("GET", "/agent-links", query)


@pytest.mark.parametrize("arguments", [
    ["ties", "list", "--agent", ID, "--type", "item", "--id", OTHER],
    ["ties", "list", "--type", "item"],
    ["ties", "list", "--id", ID],
])
def test_list_refuses_a_mix_before_anything_is_sent(requests, arguments, capsys):
    assert cli.main(["agent", *arguments]) == 1
    assert "Worklog:" in capsys.readouterr().err
    assert not requests


@pytest.mark.parametrize("arguments,route,payload", [
    (["ties", "set", "--type", "repository", "--id", ID, "--relationship", "Maintainer"],
     "/agent-links", {"targetType": "repository", "targetId": ID, "relationship": "Maintainer"}),
    (["ties", "set", "--type", "ticket", "--id", "T-12"], "/agent-links", {"targetType": "ticket", "targetId": "T-12", "relationship": ""}),
    (["ties", "remove", "--type", "item", "--id", ID], "/agent-links/unlink", {"targetType": "item", "targetId": ID}),
])
def test_writes_name_no_agent_and_carry_the_request_id(requests, arguments, route, payload):
    assert cli.main(["agent", *arguments, "--request-id", KEY]) == 0
    request = requests[-1]
    assert sent(request)[:2] == ("POST", route)
    assert json.loads(request.data) == payload
    assert request.get_header("Idempotency-key") == KEY


def test_a_type_worklog_does_not_tie_is_a_usage_error(requests):
    with pytest.raises(SystemExit):
        cli.main(["agent", "ties", "set", "--type", "day", "--id", "2026-01-05"])
    assert not requests


@pytest.mark.parametrize("method,path", [
    ("PATCH", "/agent-links"), ("DELETE", "/agent-links"), ("GET", "/agent-links/unlink"),
    ("POST", "/agent-links/"), ("POST", f"/agent-links/{ID}"), ("POST", "/person-links"),
])
def test_other_routes_stay_unreachable(requests, method, path):
    with pytest.raises(ValueError, match="outside"):
        cli.AgentClient().request(method, path, {})
    assert not requests


def test_an_agent_ties_itself_reads_and_unties_against_the_application(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    app = create_app(tmp_path / "server", testing=True)
    with TestClient(app, base_url="http://127.0.0.1:8877") as person, TestClient(app, base_url="http://127.0.0.1:8877") as wire:
        person.headers["Origin"] = "http://127.0.0.1:8877"
        person.headers["X-CSRF-Token"] = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"},
                                                     json={}).json()["csrfToken"]

        def write(path, payload, key):
            response = person.post("/api/v1" + path, json=payload, headers={"Idempotency-Key": key})
            assert response.status_code == 200, response.text
            return response.json()

        agent = write("/credentials", {"label": "Implementer"}, "issue-implementer")
        work = write("/work-items", {"title": "Fix the parser"}, "create-parser-item")
        ticket = write("/tickets", {"title": "Parser drops the last row"}, "file-parser-ticket")
        write("/agent-links", {"agentId": agent["agentId"], "targetType": "item", "targetId": work["id"],
                               "relationship": "Owner"}, "tie-implementer-owner")
        monkeypatch.setenv("WORKLOG_AGENT_TOKEN", agent["token"])

        def bridge(request, **kwargs):
            # The CLI's request as sent: no browser origin, no session cookie.
            response = wire.request(request.get_method(), request.full_url, content=request.data, headers=dict(request.header_items()))
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, "rejected", {}, io.BytesIO(response.content))
            return io.BytesIO(response.content)

        monkeypatch.setattr(cli, "_open", bridge)

        def run(*args, code=0):
            result = cli.main(["agent", *args])
            out, err = capsys.readouterr()
            assert result == code, err
            assert agent["token"] not in out + err
            return json.loads(out) if code == 0 else err

        tied = run("ties", "set", "--type", "ticket", "--id", "T-1", "--relationship", "Fixer", "--request-id", "tie-ticket-1")
        assert (tied["targetId"], tied["agentId"], tied["setBy"]) == (ticket["id"], agent["agentId"], "agent")
        assert [(t["targetType"], t["relationship"]) for t in run("ties", "list")["items"]] == [("item", "Owner"), ("ticket", "Fixer")]
        assert [t["agentId"] for t in run("ties", "list", "--type", "item", "--id", work["id"])["items"]] == [agent["agentId"]]
        assert run("context")["ties"]["total"] == 2
        # The person's tie is theirs to change.
        assert "403 forbidden" in run("ties", "remove", "--type", "item", "--id", work["id"], code=1)
        assert run("ties", "remove", "--type", "ticket", "--id", "1")["targetId"] == ticket["id"]
        assert [t["targetType"] for t in run("ties", "list", "--agent", agent["agentId"])["items"]] == ["item"]
