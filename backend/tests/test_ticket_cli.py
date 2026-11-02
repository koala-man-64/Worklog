"""The agent CLI's tickets commands: wire contracts, reachable routes, and the whole path."""

import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from worklog import cli
from worklog.app import create_app

ID = "11111111-1111-4111-8111-111111111111"
KEY = "stable-operation-key"
SELF = {"items": [], "self": {"id": "agent-self", "label": "Implementer", "kind": "agent"}}


@pytest.fixture
def requests(monkeypatch):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", "private-agent-token")
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    captured = []

    def respond(request, **kwargs):
        captured.append(request)
        body = SELF if urlsplit(request.full_url).path.endswith("/discussions/participants") else {"items": [], "total": 0}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(cli, "_open", respond)
    return captured


def sent(request):
    return request.method, urlsplit(request.full_url).path.removeprefix("/api/v1"), parse_qs(urlsplit(request.full_url).query)


@pytest.mark.parametrize("arguments,route,query", [
    (["tickets", "list"], "/tickets", {"archived": ["false"], "limit": ["50"], "offset": ["0"]}),
    (["tickets", "list", "--status", "all", "--q", "locked database", "--kind", "incident", "--priority", "high",
      "--label", "sqlite", "--repo", ID, "--item", ID, "--assignee", "none", "--sort", "recent", "--archived",
      "--limit", "5", "--offset", "10"], "/tickets",
     {"status": ["all"], "q": ["locked database"], "kind": ["incident"], "priority": ["high"], "label": ["sqlite"],
      "repositoryId": [ID], "itemId": [ID], "assigneeId": ["none"], "sort": ["recent"], "archived": ["true"],
      "limit": ["5"], "offset": ["10"]}),
    (["tickets", "show", "T-12"], "/tickets/T-12", {}),
    (["tickets", "show", ID, "--comments", "20"], f"/tickets/{ID}", {"comments": ["20"]}),
])
def test_reads(requests, arguments, route, query):
    assert cli.main(["agent", *arguments]) == 0
    assert sent(requests[-1]) == ("GET", route, query)


def test_me_resolves_to_the_callers_identity_before_listing_or_assigning(requests):
    assert cli.main(["agent", "tickets", "list", "--assignee", "me"]) == 0
    assert [sent(r)[1] for r in requests] == ["/discussions/participants", "/tickets"]
    assert sent(requests[-1])[2]["assigneeId"] == ["agent-self"]
    assert cli.main(["agent", "tickets", "create", "--title", "Mine", "--assign", "me", "--request-id", KEY]) == 0
    assert json.loads(requests[-1].data) == {"title": "Mine", "assigneeId": "agent-self"}


@pytest.mark.parametrize("arguments,method,route,payload", [
    (["tickets", "create", "--title", "Parser drops rows", "--description", "Last row lost", "--kind", "bug",
      "--priority", "high", "--label", "parser", "--label", "import", "--repo", ID, "--item", ID],
     "POST", "/tickets", {"title": "Parser drops rows", "description": "Last row lost", "kind": "bug", "priority": "high",
                          "labels": ["parser", "import"], "repositoryIds": [ID], "itemId": ID}),
    (["tickets", "create", "--title", "Past outage", "--kind", "incident", "--status", "done", "--resolution", "Restarted"],
     "POST", "/tickets", {"title": "Past outage", "kind": "incident", "status": "done", "resolution": "Restarted"}),
    (["tickets", "update", "T-3", "--revision", "2", "--status", "doing", "--assign", "none", "--no-item", "--no-labels", "--no-repos"],
     "PATCH", "/tickets/T-3", {"status": "doing", "assigneeId": None, "itemId": None, "labels": [], "repositoryIds": [], "revision": 2}),
    (["tickets", "update", "3", "--revision", "4", "--title", "Renamed", "--label", "ci"],
     "PATCH", "/tickets/3", {"title": "Renamed", "labels": ["ci"], "revision": 4}),
    (["tickets", "close", ID, "--revision", "5", "--resolution", "Fixed in #51"],
     "PATCH", f"/tickets/{ID}", {"resolution": "Fixed in #51", "status": "done", "revision": 5}),
    (["tickets", "comment", "T-3", "--text", "Reproduced with an empty file"],
     "POST", "/tickets/T-3/comments", {"text": "Reproduced with an empty file"}),
])
def test_writes_carry_the_explicit_operation_key(requests, arguments, method, route, payload):
    assert cli.main(["agent", *arguments, "--request-id", KEY]) == 0
    request = requests[-1]
    assert sent(request)[:2] == (method, route)
    assert json.loads(request.data) == payload
    assert request.get_header("Idempotency-key") == KEY


def test_writes_without_a_request_id_still_send_a_fresh_key(requests):
    assert cli.main(["agent", "tickets", "create", "--title", "One"]) == 0
    assert cli.main(["agent", "tickets", "update", "T-1", "--revision", "1", "--priority", "low"]) == 0
    keys = [r.get_header("Idempotency-key") for r in requests]
    assert all(keys) and len(set(keys)) == 2


def test_json_files_and_standard_input_supply_fields(requests, tmp_path, monkeypatch):
    source = tmp_path / "ticket.json"
    source.write_text(json.dumps({"title": "From a file", "description": "Long **Markdown**", "labels": ["docs"]}))
    assert cli.main(["agent", "tickets", "create", "--file", str(source), "--priority", "urgent", "--request-id", KEY]) == 0
    assert json.loads(requests[-1].data) == {"title": "From a file", "description": "Long **Markdown**", "labels": ["docs"], "priority": "urgent"}
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('{"text":"A long log excerpt"}'))
    assert cli.main(["agent", "tickets", "comment", "T-1", "--file", "-", "--request-id", KEY]) == 0
    assert json.loads(requests[-1].data) == {"text": "A long log excerpt"}


def test_close_needs_a_resolution_before_anything_is_sent(requests):
    with pytest.raises(SystemExit):
        cli.main(["agent", "tickets", "close", "T-1", "--revision", "1"])
    assert not requests


@pytest.mark.parametrize("method,path", [
    ("POST", f"/tickets/{ID}/archive"), ("POST", "/tickets/T-1/recover"), ("DELETE", "/tickets/T-1"),
    ("PATCH", "/tickets"), ("POST", "/tickets/T-1"), ("GET", "/tickets/T-1/comments"),
    ("POST", "/tickets/%2e%2e/comments"), ("POST", "/tickets/T-1/comments/"), ("GET", "/tickets/not-a-ticket"),
    ("GET", "/tickets/T-0"), ("POST", f"/tickets/{ID}/comments/extra"),
])
def test_archive_recover_and_unrecognized_routes_stay_unreachable(requests, method, path):
    with pytest.raises(ValueError, match="outside"):
        cli.AgentClient().request(method, path, {})
    assert not requests


def test_an_uncertain_comment_names_its_key_but_never_the_token(requests, monkeypatch):
    def unavailable(*args, **kwargs):
        raise URLError("private-agent-token")

    monkeypatch.setattr(cli, "_open", unavailable)
    with pytest.raises(cli.AgentRequestError, match=KEY) as error:
        cli.AgentClient().request("POST", "/tickets/T-1/comments", {"text": "x"}, request_id=KEY)
    assert "private-agent-token" not in str(error.value)


def test_an_agent_files_works_and_closes_a_ticket_against_the_application(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    app = create_app(tmp_path / "server", testing=True)
    with TestClient(app, base_url="http://127.0.0.1:8877") as person, TestClient(app, base_url="http://127.0.0.1:8877") as wire:
        person.headers["Origin"] = "http://127.0.0.1:8877"
        csrf = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={}).json()["csrfToken"]
        agent = person.post("/api/v1/credentials", json={"label": "Implementer"},
                            headers={"X-CSRF-Token": csrf, "Idempotency-Key": "issue-implementer"}).json()
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

        filed = run("tickets", "create", "--title", "Backup holds a lock", "--kind", "incident", "--label", "SQLite",
                    "--assign", "me", "--request-id", "file-backup-lock")
        assert (filed["key"], filed["labels"], filed["assigneeId"], filed["author"]["id"]) == ("T-1", ["sqlite"], agent["agentId"], agent["agentId"])
        assert run("tickets", "create", "--title", "Backup holds a lock", "--kind", "incident", "--label", "SQLite",
                   "--assign", "me", "--request-id", "file-backup-lock")["id"] == filed["id"]
        run("tickets", "comment", "T-1", "--text", "Happens while the nightly snapshot runs.")
        assert [t["key"] for t in run("tickets", "list", "--assignee", "me")["items"]] == ["T-1"]
        assert "422" in run("tickets", "update", "T-1", "--revision", str(filed["revision"]), "--status", "done", code=1)
        closed = run("tickets", "close", "T-1", "--revision", str(filed["revision"]), "--resolution", "Snapshot with the online backup API.")
        assert closed["status"] == "done"
        found = run("tickets", "list", "--status", "all", "--q", "nightly snapshot")["items"]
        assert [(t["key"], t["resolution"]) for t in found] == [("T-1", "Snapshot with the online backup API.")]
        shown = run("tickets", "show", "1")
        assert [c["text"] for c in shown["comments"]["items"]] == ["Happens while the nightly snapshot runs."]
        assert [e["kind"] for e in shown["history"]] == ["ticket.close", "ticket.comment", "ticket.create"]
