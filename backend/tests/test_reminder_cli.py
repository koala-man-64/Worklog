"""The agent CLI's reminders commands: wire contracts, reachable routes, and the whole path."""

import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from worklog import cli, reminders
from worklog.app import create_app

ID = "11111111-1111-4111-8111-111111111111"
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


@pytest.mark.parametrize("arguments,route,query", [
    (["reminders", "list"], "/reminders", {"archived": ["false"], "limit": ["50"], "offset": ["0"]}),
    (["reminders", "list", "--state", "due", "--date", "2026-11-06", "--until", "2026-11-07", "--ticket", "T-12",
      "--q", "merge", "--archived", "--limit", "5", "--offset", "10"], "/reminders",
     {"state": ["due"], "date": ["2026-11-06"], "until": ["2026-11-07"], "targetType": ["ticket"], "targetId": ["T-12"],
      "q": ["merge"], "archived": ["true"], "limit": ["5"], "offset": ["10"]}),
    (["reminders", "list", "--chat", ID], "/reminders",
     {"targetType": ["chat"], "targetId": [ID], "archived": ["false"], "limit": ["50"], "offset": ["0"]}),
    (["reminders", "show", ID], f"/reminders/{ID}", {}),
])
def test_reads(requests, arguments, route, query):
    assert cli.main(["agent", *arguments]) == 0
    assert sent(requests[-1]) == ("GET", route, query)


def test_a_reminder_names_one_record_at_most(requests):
    for arguments in (["list", "--item", ID, "--person", ID],
                      ["add", "--title", "Two", "--on", "2026-11-06", "--roadmap", ID, "--chat", ID],
                      ["update", ID, "--revision", "1", "--repository", ID, "--unlink"],
                      ["update", ID, "--revision", "1", "--at", "09:00", "--all-day"],
                      ["add", "--title", "No day"], ["done", ID]):
        with pytest.raises(SystemExit):
            cli.main(["agent", "reminders", *arguments])
    assert not requests


@pytest.mark.parametrize("method,path", [
    ("POST", f"/reminders/{ID}/archive"), ("POST", f"/reminders/{ID}/recover"), ("DELETE", f"/reminders/{ID}"),
    ("PATCH", "/reminders"), ("POST", f"/reminders/{ID}"), ("GET", "/reminders/not-an-id"),
    ("GET", "/reminders/%2e%2e"), ("GET", f"/reminders/{ID}/"), ("GET", f"/reminders/{ID}/history"),
])
def test_archive_recover_and_unrecognized_routes_stay_unreachable(requests, method, path):
    with pytest.raises(ValueError, match="outside"):
        cli.AgentClient().request(method, path, {})
    assert not requests


@pytest.mark.parametrize("arguments,method,route,payload", [
    (["reminders", "add", "--title", "Merge #113", "--on", "2026-11-06", "--at", "14:25", "--notes", "Once CI is green",
      "--ticket", "T-12"],
     "POST", "/reminders", {"title": "Merge #113", "date": "2026-11-06", "time": "14:25", "notes": "Once CI is green",
                            "targetType": "ticket", "targetId": "T-12"}),
    (["reminders", "add", "--title", "Renew the certificate", "--on", "2026-11-09"],
     "POST", "/reminders", {"title": "Renew the certificate", "date": "2026-11-09"}),
    (["reminders", "update", ID, "--revision", "2", "--on", "2026-11-07", "--at", "09:00"],
     "PATCH", f"/reminders/{ID}", {"date": "2026-11-07", "time": "09:00", "revision": 2}),
    (["reminders", "update", ID, "--revision", "3", "--title", "Renamed", "--all-day", "--unlink"],
     "PATCH", f"/reminders/{ID}", {"title": "Renamed", "time": None, "targetType": None, "targetId": None, "revision": 3}),
    (["reminders", "update", ID, "--revision", "4", "--item", ID],
     "PATCH", f"/reminders/{ID}", {"targetType": "item", "targetId": ID, "revision": 4}),
    (["reminders", "done", ID, "--revision", "5"], "PATCH", f"/reminders/{ID}", {"state": "done", "revision": 5}),
    (["reminders", "reopen", ID, "--revision", "6"], "PATCH", f"/reminders/{ID}", {"state": "open", "revision": 6}),
])
def test_agent_writes_carry_the_explicit_operation_key(requests, arguments, method, route, payload):
    assert cli.main(["agent", *arguments, "--request-id", KEY]) == 0
    request = requests[-1]
    assert sent(request)[:2] == (method, route)
    assert json.loads(request.data) == payload
    assert request.get_header("Idempotency-key") == KEY


def test_agent_writes_without_a_request_id_still_send_a_fresh_key(requests):
    assert cli.main(["agent", "reminders", "add", "--title", "One", "--on", "2026-11-06"]) == 0
    assert cli.main(["agent", "reminders", "done", ID, "--revision", "1"]) == 0
    keys = [r.get_header("Idempotency-key") for r in requests]
    assert all(keys) and len(set(keys)) == 2


def test_agent_writes_set_snooze_and_complete_a_reminder_against_the_application(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    monkeypatch.setattr(reminders, "local_now", lambda tz: reminders.datetime(2026, 11, 6, 15, 0, tzinfo=reminders.ZoneInfo(tz)))
    app = create_app(tmp_path / "server", testing=True)
    with TestClient(app, base_url="http://127.0.0.1:8877") as person, TestClient(app, base_url="http://127.0.0.1:8877") as wire:
        person.headers["Origin"] = "http://127.0.0.1:8877"
        csrf = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={}).json()["csrfToken"]
        agent = person.post("/api/v1/credentials", json={"label": "Implementer"},
                            headers={"X-CSRF-Token": csrf, "Idempotency-Key": "issue-implementer"}).json()
        ticket = person.post("/api/v1/tickets", json={"title": "Parser drops rows"},
                             headers={"X-CSRF-Token": csrf, "Idempotency-Key": "file-parser-ticket"}).json()
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

        set_ = run("reminders", "add", "--title", "Merge the parser fix", "--on", "2026-11-06", "--at", "14:25",
                   "--ticket", "T-1", "--request-id", "remind-merge")
        assert (set_["due"], set_["target"]["id"], set_["author"]["id"]) == (True, ticket["id"], agent["agentId"])
        assert run("reminders", "add", "--title", "Merge the parser fix", "--on", "2026-11-06", "--at", "14:25",
                   "--ticket", "T-1", "--request-id", "remind-merge")["id"] == set_["id"]
        assert [r["id"] for r in run("reminders", "list", "--state", "due", "--ticket", "T-1")["items"]] == [set_["id"]]
        snoozed = run("reminders", "update", set_["id"], "--revision", str(set_["revision"]), "--at", "16:00")
        assert (snoozed["time"], snoozed["due"]) == ("16:00", False)
        assert "422" in run("reminders", "update", set_["id"], "--revision", str(snoozed["revision"]), "--at", "25:00", code=1)
        done = run("reminders", "done", set_["id"], "--revision", str(snoozed["revision"]))
        assert done["state"] == "done"
        shown = run("reminders", "show", set_["id"])
        assert [e["kind"] for e in shown["history"]] == ["reminder.done", "reminder.update", "reminder.create"]
