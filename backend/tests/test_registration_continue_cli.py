"""`worklog agent register --continue NAME` carries an identity on in a new chat, with a token it
generates, keeps and never prints, and finishes what an uncertain earlier run left."""

import io
import json
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from worklog import cli
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
SPONSOR = "sponsor-token-that-must-stay-private"
OLD_CHAT = "3f0c6a1e-8d2b-4c5a-9e7f-1a2b3c4d5e6f"
NEW_CHAT = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
OLD_TOKEN = "o" * 21 + "ld-token-of-the-old-chat"
RECORD = {"id": "credential-2", "agentId": "agent", "label": "Parser fix", "avatar": "teal-visor-antenna", "chatId": NEW_CHAT,
          "registeredBy": {"id": "sponsor", "label": "Shared agents"}, "directory": None}
CONTINUED = {**RECORD, "continued": {"credentialId": "credential-1", "chatId": OLD_CHAT, "runsEnded": 1, "chats": [], "roles": []}}
OLD_RECORD = {**RECORD, "id": "credential-1", "chatId": OLD_CHAT}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", SPONSOR)
    return tmp_path / "data" / "agent-tokens" / "8877"


@pytest.fixture
def transport(home, monkeypatch):
    sent = []
    replies: list = []

    def respond(request, **kwargs):
        sent.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr(cli, "_open", respond)
    return sent, replies


def rejected(status, code, message="Rejected"):
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    return HTTPError(ORIGIN, status, message, {}, io.BytesIO(body))


def store(home, name, token, **noted):
    home.mkdir(parents=True, exist_ok=True)
    (home / f"{name}.json").write_text(json.dumps({"name": name, "token": token, **noted}), encoding="utf-8")


def stored(home, file):
    return json.loads((home / file).read_text(encoding="utf-8"))


def calls(sent):
    return [(r.method, urlsplit(r.full_url).path, r.get_header("Authorization").removeprefix("Bearer ")) for r in sent]


def continue_(*extra):
    return cli.main(["agent", "register", "--chat", NEW_CHAT, "--continue", "parser-fix", *extra])


def test_it_checks_the_old_token_then_sends_a_new_one_stored_first_and_never_printed(home, transport, monkeypatch, capsys):
    sent, replies = transport
    store(home, "parser-fix", OLD_TOKEN)
    path = home / f"{NEW_CHAT}.json"
    seen = []
    respond = cli._open

    def note_disk(request, **kwargs):
        seen.append(path.exists())
        return respond(request, **kwargs)

    monkeypatch.setattr(cli, "_open", note_disk)
    replies.extend([OLD_RECORD, CONTINUED])
    assert continue_() == 0
    new = stored(home, f"{NEW_CHAT}.json")
    assert calls(sent) == [("GET", "/api/v1/agent-registrations/self", OLD_TOKEN),
                           ("POST", "/api/v1/agent-registrations/continue", OLD_TOKEN)]
    body = json.loads(sent[-1].data)
    assert (body["token"], body["chatId"], body.get("directory")) == (new["token"], NEW_CHAT, os.getcwd())
    # Stored before it was first sent, beside what it continues; the old name's file is gone.
    assert seen == [False, True]
    assert (new["name"], new["continues"], new["chatId"]) == (NEW_CHAT, "parser-fix", NEW_CHAT)
    assert not (home / "parser-fix.json").exists()
    out, err = capsys.readouterr()
    assert json.loads(out) == {"name": NEW_CHAT, **CONTINUED}
    assert new["token"] not in out + err and OLD_TOKEN not in out + err and SPONSOR not in out + err


def test_an_uncertain_result_is_finished_by_running_it_again(home, transport, capsys):
    sent, replies = transport
    store(home, "parser-fix", OLD_TOKEN)
    replies.extend([OLD_RECORD, URLError("connection reset")])
    assert continue_() == 1
    assert "result uncertain; run the same register command again" in capsys.readouterr().err
    new = stored(home, f"{NEW_CHAT}.json")["token"]
    assert (home / "parser-fix.json").exists()
    # Worklog had continued it: the new token works now, so the rerun only finishes.
    sent.clear()
    replies.append(RECORD)
    assert continue_() == 0
    assert calls(sent) == [("GET", "/api/v1/agent-registrations/self", new)]
    assert json.loads(capsys.readouterr().out) == {"name": NEW_CHAT, **RECORD}
    assert not (home / "parser-fix.json").exists() and stored(home, f"{NEW_CHAT}.json")["token"] == new
    # And a rerun after that finds the same.
    replies.append(RECORD)
    assert continue_() == 0
    assert json.loads(capsys.readouterr().out)["name"] == NEW_CHAT


def test_an_uncertain_result_worklog_never_applied_is_sent_again_with_the_same_token(home, transport, capsys):
    sent, replies = transport
    store(home, "parser-fix", OLD_TOKEN)
    replies.extend([OLD_RECORD, rejected(503, "database_busy")])
    assert continue_() == 1
    new = stored(home, f"{NEW_CHAT}.json")["token"]
    sent.clear()
    replies.extend([rejected(401, "authentication"), OLD_RECORD, CONTINUED])
    assert continue_() == 0
    assert calls(sent) == [("GET", "/api/v1/agent-registrations/self", new), ("GET", "/api/v1/agent-registrations/self", OLD_TOKEN),
                           ("POST", "/api/v1/agent-registrations/continue", OLD_TOKEN)]
    assert json.loads(sent[-1].data)["token"] == new
    assert json.loads(capsys.readouterr().out) == {"name": NEW_CHAT, **CONTINUED}


def test_a_refusal_forgets_the_new_token_and_says_why(home, transport, capsys):
    sent, replies = transport
    store(home, "parser-fix", OLD_TOKEN)
    replies.extend([OLD_RECORD, rejected(429, "agent_limit", "Agents registered or continued 200 identities today")])
    assert continue_() == 1
    assert "HTTP 429 agent_limit" in capsys.readouterr().err
    assert not (home / f"{NEW_CHAT}.json").exists() and (home / "parser-fix.json").exists()
    # A revoked identity: Worklog refuses its token, and the person decides.
    replies.append(rejected(401, "authentication"))
    assert continue_() == 1
    assert "The credential stored for parser-fix was revoked; tell the person" in capsys.readouterr().err
    assert not (home / f"{NEW_CHAT}.json").exists()


def test_the_name_can_stay_the_same_across_chats(home, transport, capsys):
    sent, replies = transport
    store(home, "parser-fix", OLD_TOKEN)
    replies.extend([OLD_RECORD, URLError("connection reset")])
    assert continue_("--name", "Parser-Fix") == 1
    capsys.readouterr()
    # The new token waits beside the old one until Worklog confirms it.
    new = stored(home, "parser-fix.next.json")["token"]
    assert stored(home, "parser-fix.json")["token"] == OLD_TOKEN
    replies.append(RECORD)
    assert continue_("--name", "parser-fix") == 0
    assert json.loads(capsys.readouterr().out) == {"name": "parser-fix", **RECORD}
    assert stored(home, "parser-fix.json")["token"] == new and not (home / "parser-fix.next.json").exists()
    # Once it is in this chat, running it again changes nothing.
    sent.clear()
    replies.append(RECORD)
    assert continue_("--name", "parser-fix") == 0
    assert calls(sent) == [("GET", "/api/v1/agent-registrations/self", new)]
    assert json.loads(capsys.readouterr().out) == {"name": "parser-fix", **RECORD}


def test_a_token_left_for_another_chat_that_never_registered_is_replaced(home, transport, capsys):
    sent, replies = transport
    store(home, "parser-fix", OLD_TOKEN)
    store(home, "fresh", "p" * 43, continues="parser-fix", chatId="another-chat")
    replies.extend([rejected(401, "authentication"), OLD_RECORD, CONTINUED])
    assert continue_("--name", "fresh") == 0
    new = stored(home, "fresh.json")
    assert calls(sent) == [("GET", "/api/v1/agent-registrations/self", "p" * 43), ("GET", "/api/v1/agent-registrations/self", OLD_TOKEN),
                           ("POST", "/api/v1/agent-registrations/continue", OLD_TOKEN)]
    assert new["token"] != "p" * 43 and new["chatId"] == NEW_CHAT and json.loads(sent[-1].data)["token"] == new["token"]
    assert json.loads(capsys.readouterr().out)["name"] == "fresh"


def test_under_the_same_name_it_carries_on_from_a_chat_an_earlier_run_moved_it_to(home, transport, capsys):
    sent, replies = transport
    moved = "m" * 43
    store(home, "parser-fix", OLD_TOKEN)
    (home / "parser-fix.next.json").write_text(json.dumps({"name": "parser-fix", "token": moved, "continues": "parser-fix",
                                                           "chatId": "another-chat"}), encoding="utf-8")
    elsewhere = {**OLD_RECORD, "id": "credential-moved", "chatId": "another-chat"}
    replies.extend([elsewhere, elsewhere, CONTINUED])
    assert continue_("--name", "parser-fix") == 0
    assert calls(sent) == [("GET", "/api/v1/agent-registrations/self", moved), ("GET", "/api/v1/agent-registrations/self", moved),
                           ("POST", "/api/v1/agent-registrations/continue", moved)]
    new = stored(home, "parser-fix.json")["token"]
    assert new not in {moved, OLD_TOKEN} and json.loads(sent[-1].data)["token"] == new
    assert not (home / "parser-fix.next.json").exists()
    assert json.loads(capsys.readouterr().out) == {"name": "parser-fix", **CONTINUED}


def test_an_outage_while_checking_a_waiting_token_keeps_it(home, transport, capsys):
    sent, replies = transport
    store(home, "parser-fix", OLD_TOKEN)
    store(home, NEW_CHAT, "w" * 43, continues="parser-fix", chatId=NEW_CHAT)
    replies.append(rejected(503, "database_busy"))
    assert continue_() == 1
    assert "HTTP 503 database_busy" in capsys.readouterr().err
    assert stored(home, f"{NEW_CHAT}.json")["token"] == "w" * 43 and (home / "parser-fix.json").exists()
    assert len(sent) == 1


def test_it_refuses_what_it_cannot_continue_before_sending_anything(home, transport, capsys):
    sent, replies = transport
    # No identity of that name here.
    assert continue_() == 1
    assert "No agent named parser-fix was registered" in capsys.readouterr().err
    # A name another agent holds here.
    store(home, "parser-fix", OLD_TOKEN)
    store(home, "taken", "t" * 43)
    assert continue_("--name", "taken") == 1
    assert "taken names another agent here" in capsys.readouterr().err
    # It acts as the identity it continues, which keeps its label and avatar.
    assert cli.main(["agent", "--as", "parser-fix", "register", "--chat", NEW_CHAT, "--continue", "parser-fix"]) == 1
    assert "leave out --as" in capsys.readouterr().err
    assert continue_("--avatar", "teal-visor-antenna") == 1
    assert "leave out --avatar" in capsys.readouterr().err
    assert cli.main(["agent", "register", "--chat", "thread.with.dots", "--continue", "parser-fix"]) == 1
    assert "give --name as well" in capsys.readouterr().err
    assert not sent
    with pytest.raises(SystemExit) as exit_:
        cli.main(["agent", "register", "--chat", NEW_CHAT, "--continue", "parser-fix", "--label", "Parser fix"])
    assert exit_.value.code == 2
    with pytest.raises(SystemExit) as exit_:
        cli.main(["agent", "register", "--chat", NEW_CHAT])
    assert exit_.value.code == 2
    assert "--label" in capsys.readouterr().err and not sent


def test_an_identity_already_in_this_chat_carries_on(home, transport, capsys):
    sent, replies = transport
    store(home, "parser-fix", OLD_TOKEN)
    replies.append({**OLD_RECORD, "chatId": NEW_CHAT})
    assert continue_() == 1
    assert "parser-fix is registered with this chat already; carry on with --as parser-fix" in capsys.readouterr().err
    assert not (home / f"{NEW_CHAT}.json").exists()


def test_under_the_same_name_an_identity_in_this_chat_forgets_a_token_never_registered(home, transport, capsys):
    sent, replies = transport
    store(home, "parser-fix", OLD_TOKEN)
    (home / "parser-fix.next.json").write_text(json.dumps({"name": "parser-fix", "token": "n" * 43, "continues": "parser-fix",
                                                           "chatId": NEW_CHAT}), encoding="utf-8")
    replies.extend([rejected(401, "authentication"), {**OLD_RECORD, "chatId": NEW_CHAT}])
    assert continue_("--name", "parser-fix") == 0
    assert json.loads(capsys.readouterr().out)["chatId"] == NEW_CHAT
    assert not (home / "parser-fix.next.json").exists() and stored(home, "parser-fix.json")["token"] == OLD_TOKEN


def test_registering_a_name_of_another_chat_says_how_to_continue_it(home, transport, capsys):
    sent, replies = transport
    store(home, "parser-fix", OLD_TOKEN)
    replies.append(rejected(409, "chat_mismatch"))
    assert cli.main(["agent", "register", "--chat", NEW_CHAT, "--name", "parser-fix", "--label", "Parser fix"]) == 1
    err = capsys.readouterr().err
    assert "parser-fix is registered for another chat" in err and "--continue parser-fix" in err


def test_the_whole_path_against_the_application(tmp_path, home, monkeypatch, capsys):
    """A chat registers, tracks its chat on a step and owns a role there; the next chat continues it."""
    app = create_app(tmp_path / "server", testing=True)
    with TestClient(app, base_url=ORIGIN) as person, TestClient(app, base_url=ORIGIN) as wire:
        person.headers["Origin"] = ORIGIN
        person.headers["X-CSRF-Token"] = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"},
                                                     json={}).json()["csrfToken"]
        sponsor = person.post("/api/v1/credentials", json={"label": "Shared agents"},
                              headers={"Idempotency-Key": "issue-shared-agents"}).json()
        monkeypatch.setenv("WORKLOG_AGENT_TOKEN", sponsor["token"])
        step = str(uuid.uuid4())
        roadmap = person.post("/api/v1/roadmaps", json={"title": "Parser", "steps": [{"id": step, "title": "Parse"}]},
                              headers={"Idempotency-Key": "create-parser-roadmap"}).json()

        def bridge(request, **kwargs):
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

        first = run("register", "--chat", OLD_CHAT, "--name", "parser-fix", "--label", "Parser fix")
        old_token = stored(home, "parser-fix.json")["token"]
        run("--as", "parser-fix", "runs", "start", "--client-run-key", "run-1", "--display-name", "Parser fix", "--request-id", "run-1-key")
        run("--as", "parser-fix", "chats", "track", "--roadmap", roadmap["id"], "--step", step, "--request-id", "track-key")
        role = run("--as", "parser-fix", "discussions", "roles", "create", "--context", f"step:{step}", "--label", "Implementation")
        run("--as", "parser-fix", "discussions", "roles", "claim", role["id"], "--revision", str(role["revision"]))

        continued = run("register", "--chat", NEW_CHAT, "--continue", "parser-fix")
        assert (continued["name"], continued["agentId"], continued["label"], continued["chatId"]) == (
            NEW_CHAT, first["agentId"], "Parser fix", NEW_CHAT)
        assert [s["id"] for s in continued["continued"]["chats"][0]["steps"]] == [step]
        assert [r["id"] for r in continued["continued"]["roles"]] == [role["id"]]
        assert not (home / "parser-fix.json").exists()
        assert wire.get("/api/v1/context", headers={"Authorization": "Bearer " + old_token}).status_code == 401
        # The new name acts as the same identity, and its runs report the new chat.
        started = run("--as", NEW_CHAT, "runs", "start", "--client-run-key", "run-2", "--display-name", "Parser fix", "--request-id", "run-2-key")
        assert (started["agentId"], started["externalChatId"]) == (first["agentId"], NEW_CHAT)
        # Running it again changes nothing, and the old name is gone.
        assert run("register", "--chat", NEW_CHAT, "--continue", "parser-fix")["id"] == continued["id"]
        assert cli.main(["agent", "--as", "parser-fix", "context"]) == 1
        assert "No agent named parser-fix" in capsys.readouterr().err
        with app.state.db.read() as conn:
            dump = "\n".join(conn.iterdump())
        assert stored(home, f"{NEW_CHAT}.json")["token"] not in dump and old_token not in dump
