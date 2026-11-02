"""The agent CLI registers identities with tokens it generates, keeps and never prints."""

import io
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from worklog import cli
from worklog.app import create_app

SPONSOR = "sponsor-token-that-must-stay-private"
REGISTERED = {"id": "credential", "agentId": "agent", "label": "Parser fix", "avatar": "teal-visor-antenna",
              "registeredBy": {"id": "sponsor", "label": "Shared agents"}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", SPONSOR)
    return tmp_path / "data" / "agent-tokens"


@pytest.fixture
def transport(home, monkeypatch):
    sent = []
    replies: list = []

    def respond(request, **kwargs):
        sent.append(request)
        reply = replies.pop(0) if replies else REGISTERED
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr(cli, "_open", respond)
    return sent, replies


def rejected(status, code, message="Rejected"):
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    return HTTPError("http://127.0.0.1:8877", status, message, {}, io.BytesIO(body))


def stored(home, name, port=8877):
    return json.loads((home / str(port) / f"{name}.json").read_text(encoding="utf-8"))["token"]


def store(home, name, token, port=8877):
    (home / str(port)).mkdir(parents=True, exist_ok=True)
    (home / str(port) / f"{name}.json").write_text(json.dumps({"name": name, "token": token}), encoding="utf-8")


def test_register_stores_a_generated_token_before_sending_it_and_never_prints_it(home, transport, monkeypatch, capsys):
    sent, replies = transport
    path = home / "8877" / "parser-fix.json"
    seen_on_disk = []
    respond = cli._open

    def check_first(request, **kwargs):
        seen_on_disk.append(path.exists())
        return respond(request, **kwargs)

    monkeypatch.setattr(cli, "_open", check_first)
    assert cli.main(["agent", "register", "--name", "Parser-Fix", "--label", "Parser fix", "--avatar", "teal-visor-antenna"]) == 0
    request = sent[-1]
    assert (request.method, urlsplit(request.full_url).path) == ("POST", "/api/v1/agent-registrations")
    assert request.get_header("Authorization") == "Bearer " + SPONSOR
    body = json.loads(request.data)
    token = stored(home, "parser-fix")
    assert body == {"label": "Parser fix", "token": token, "avatar": "teal-visor-antenna"}
    assert len(token) == 43 and seen_on_disk == [True]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    out, err = capsys.readouterr()
    assert json.loads(out) == {"name": "parser-fix", **REGISTERED}
    assert token not in out + err and SPONSOR not in out + err


def test_uncertain_registration_is_retried_with_the_same_token(home, transport, capsys):
    sent, replies = transport
    replies.append(URLError("connection reset"))
    assert cli.main(["agent", "register", "--name", "parser-fix", "--label", "Parser fix"]) == 1
    assert "result uncertain; run the same register command again" in capsys.readouterr().err
    first = json.loads(sent[-1].data)["token"]
    assert cli.main(["agent", "register", "--name", "parser-fix", "--label", "Parser fix"]) == 0
    assert json.loads(sent[-1].data)["token"] == first == stored(home, "parser-fix")


def test_a_revoked_stored_token_says_how_to_register_again(home, transport, capsys):
    sent, replies = transport
    store(home, "parser-fix", "r" * 43)
    replies.append(rejected(409, "token_revoked", "This token was revoked"))
    assert cli.main(["agent", "register", "--name", "parser-fix", "--label", "Parser fix"]) == 1
    err = capsys.readouterr().err
    assert "worklog agent --as parser-fix retire" in err and "r" * 43 not in err


def test_as_acts_with_the_registered_token_and_redacts_it(home, transport, capsys):
    sent, replies = transport
    token = "s" * 43
    store(home, "parser-fix", token)
    replies.append({"echo": "server said " + token})
    assert cli.main(["agent", "--as", "PARSER-FIX", "discussions", "post", "--text", "Starting"]) == 0
    assert sent[-1].get_header("Authorization") == "Bearer " + token
    out, err = capsys.readouterr()
    assert token not in out + err and "[redacted]" in out


def test_as_needs_a_registration_for_that_instance(home, transport, monkeypatch, capsys):
    sent, replies = transport
    store(home, "parser-fix", "s" * 43, port=8878)
    monkeypatch.delenv("WORKLOG_AGENT_TOKEN")
    assert cli.main(["agent", "--as", "parser-fix", "context"]) == 1
    assert "worklog agent register --name parser-fix" in capsys.readouterr().err
    assert cli.main(["agent", "--port", "8878", "--as", "parser-fix", "context"]) == 0
    assert urlsplit(sent[-1].full_url).port == 8878
    assert sent[-1].get_header("Authorization") == "Bearer " + "s" * 43


def test_register_keeps_tokens_per_port(home, transport):
    sent, replies = transport
    assert cli.main(["agent", "--port", "8878", "register", "--name", "tester", "--label", "Test instance"]) == 0
    assert urlsplit(sent[-1].full_url).port == 8878
    assert (home / "8878" / "tester.json").exists() and not (home / "8877").exists()


@pytest.mark.parametrize("name", ["../escape", "con", "Lpt1", "-leading", "a" * 65, "", "with space", "dot.name"])
def test_agent_names_cannot_escape_or_collide_with_device_names(home, transport, name, capsys):
    sent, replies = transport
    assert cli.main(["agent", "register", "--name=" + name, "--label", "Bad"]) == 1
    # An empty name never falls back to WORKLOG_AGENT_TOKEN.
    assert cli.main(["agent", "--as=" + name, "context"]) == 1
    assert "agent name" in capsys.readouterr().err
    assert not sent and not home.exists()


def test_an_unreadable_token_file_is_never_sent(home, transport, capsys):
    sent, replies = transport
    (home / "8877").mkdir(parents=True)
    (home / "8877" / "parser-fix.json").write_text("not json", encoding="utf-8")
    assert cli.main(["agent", "--as", "parser-fix", "context"]) == 1
    assert "does not hold an agent token" in capsys.readouterr().err
    assert not sent


def test_retire_revokes_and_forgets_only_registered_agents(home, transport, capsys):
    sent, replies = transport
    assert cli.main(["agent", "retire"]) == 1
    assert "--as NAME retire" in capsys.readouterr().err and not sent
    store(home, "parser-fix", "s" * 43)
    replies.append({"id": "credential", "agentId": "agent", "retired": True})
    assert cli.main(["agent", "--as", "parser-fix", "retire"]) == 0
    assert urlsplit(sent[-1].full_url).path == "/api/v1/agent-registrations/retire"
    assert sent[-1].get_header("Authorization") == "Bearer " + "s" * 43
    assert json.loads(capsys.readouterr().out) == {"name": "parser-fix", "id": "credential", "agentId": "agent", "retired": True}
    assert not (home / "8877" / "parser-fix.json").exists()
    # A token the server no longer accepts is forgotten too; an outage keeps it for a retry.
    store(home, "revoked", "t" * 43)
    replies.append(rejected(401, "authentication"))
    assert cli.main(["agent", "--as", "revoked", "retire"]) == 0
    assert json.loads(capsys.readouterr().out)["alreadyRevoked"] is True
    assert not (home / "8877" / "revoked.json").exists()
    store(home, "offline", "u" * 43)
    replies.append(rejected(503, "database_busy"))
    assert cli.main(["agent", "--as", "offline", "retire"]) == 1
    assert (home / "8877" / "offline.json").exists()


def test_register_post_and_retire_against_the_application(tmp_path, home, monkeypatch, capsys):
    """The whole path: a shared credential registers an identity that then speaks for itself."""
    app = create_app(tmp_path / "server", testing=True)
    with TestClient(app, base_url="http://127.0.0.1:8877") as person, TestClient(app, base_url="http://127.0.0.1:8877") as wire:
        person.headers["Origin"] = "http://127.0.0.1:8877"
        csrf = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={}).json()["csrfToken"]
        sponsor = person.post("/api/v1/credentials", json={"label": "Shared agents"},
                              headers={"X-CSRF-Token": csrf, "Idempotency-Key": "issue-shared-agents"}).json()
        monkeypatch.setenv("WORKLOG_AGENT_TOKEN", sponsor["token"])

        def bridge(request, **kwargs):
            # The CLI's request as sent: no browser origin, no session cookie.
            response = wire.request(request.get_method(), request.full_url, content=request.data, headers=dict(request.header_items()))
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, "rejected", {}, io.BytesIO(response.content))
            return io.BytesIO(response.content)

        monkeypatch.setattr(cli, "_open", bridge)

        def run(*args):
            code = cli.main(["agent", *args])
            out, err = capsys.readouterr()
            assert code == 0, err
            assert sponsor["token"] not in out + err
            return json.loads(out)

        registered = run("register", "--name", "parser-fix", "--label", "Parser fix")
        token = stored(home, "parser-fix")
        assert registered["registeredBy"] == {"id": sponsor["agentId"], "label": "Shared agents"}
        assert run("register", "--name", "parser-fix", "--label", "Parser fix")["id"] == registered["id"]
        posted = run("--as", "parser-fix", "discussions", "post", "--title", "Parser plan", "--text", "Starting the parser")
        assert (posted["message"]["authorId"], posted["message"]["authorLabel"]) == (registered["agentId"], "Parser fix")
        reply = run("discussions", "reply", posted["discussion"]["id"], "--text", "From the shared credential")
        assert reply["authorId"] == sponsor["agentId"] != registered["agentId"]

        assert run("--as", "parser-fix", "retire")["retired"] is True
        assert not (home / "8877" / "parser-fix.json").exists()
        assert wire.get("/api/v1/context", headers={"Authorization": "Bearer " + token}).status_code == 401
        with app.state.db.read() as conn:
            assert token not in "\n".join(conn.iterdump())
