"""Open agent's chat (decision R11): the person's handoff of a pending request to the agent it waits on.

Worklog opens the agent's own chat in its desktop app, as Open in app does for a launched chat, and gives
the person a message to paste there. It never sends into a chat, so the tests stand in for the desktop
handoff and check what it was asked to open, from which folder, and what the person is told.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

import pytest
from test_discussion_tags import error, ok
from test_ownership_reachability import instance, register
from worklog import session_launches
from worklog.common import AppError
from worklog.transcripts import TranscriptReader, TranscriptRoots

CODEX_THREAD = "0190f4c2-7a5e-7d31-9b8a-3c6e2f1d4a07"
CLAUDE_DESKTOP = "local_9d3b1c2e-5f4a-4e8b-a7c6-1b2d3e4f5a6b"
CLAUDE_SESSION = "5b0e7a51-2c4d-4f6e-8a9b-0c1d2e3f4a5b"
NOWHERE_THREAD = "7d1c2b3a-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
SECRET = "Revert PR 4849 and post the rollback plan"


def roots(tmp_path):
    """One Codex thread, and one Claude desktop session with the Claude Code session it runs."""
    rollout = tmp_path / "codex" / "sessions" / "2026" / "11" / "20" / f"rollout-2026-11-20T08-00-00-{CODEX_THREAD}.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": CODEX_THREAD, "session_id": CODEX_THREAD}}) + "\n",
                       encoding="utf-8")
    transcript = tmp_path / "claude" / "projects" / "C--Projects-docs" / f"{CLAUDE_SESSION}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"type": "user", "sessionId": CLAUDE_SESSION}) + "\n", encoding="utf-8")
    record = tmp_path / "desktop" / "claude_x" / "claude-code-sessions" / "acct" / "org" / f"{CLAUDE_DESKTOP}.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"sessionId": CLAUDE_DESKTOP, "cliSessionId": CLAUDE_SESSION, "title": "Docs"}), encoding="utf-8")
    return TranscriptRoots((tmp_path / "claude" / "projects",), (tmp_path / "desktop",), (tmp_path / "codex",))


@contextmanager
def handoff(tmp_path, monkeypatch, capsys):
    """The application with a Codex agent and a Claude agent registered from their project folders, and
    the desktop handoff replaced by a record of what it was asked to open."""
    with instance(tmp_path, monkeypatch, capsys) as (app, browser, run):
        app.state.request_chats.transcripts = TranscriptReader(roots(tmp_path))
        opened: list[tuple] = []
        monkeypatch.setattr(session_launches, "_open_desktop", lambda *args: opened.append(args))
        codex = register(run, monkeypatch, tmp_path / "Projects" / "control-plane", CODEX_THREAD, "execution-delivery",
                         "Execution delivery (Codex)")
        claude = register(run, monkeypatch, tmp_path / "Projects" / "docs", CLAUDE_DESKTOP, "docs-writer", "Docs writer (Claude)")
        yield app, browser, run, opened, codex, claude


def ask(browser, *targets, title="T-4 regression"):
    """The person asks in a new thread; returns the thread ID, the message and its slots by recipient."""
    started = ok(browser.post("/api/v1/discussions", headers={"Idempotency-Key": str(uuid.uuid4())}, json={
        "title": title, "text": SECRET,
        "acknowledgement": {"mode": "all", "targets": [{"kind": kind, "id": identity} for kind, identity in targets]}}))
    message = started["message"]
    return started["discussion"]["id"], message, {t["recipientId"]: t["id"] for t in message["request"]["targets"]}


def open_chat(browser, message_id, target_id):
    return browser.post(f"/api/v1/discussions/messages/{message_id}/open-chat", json={"targetId": target_id})


def test_it_opens_a_codex_thread_and_gives_the_person_the_commands_to_paste(tmp_path, monkeypatch, capsys):
    with handoff(tmp_path, monkeypatch, capsys) as (app, browser, run, opened, codex, claude):
        thread, message, slots = ask(browser, ("agent", codex["agentId"]))
        target = slots[codex["agentId"]]
        result = ok(open_chat(browser, message["id"], target))

        assert opened == [("codex", "", CODEX_THREAD)]
        assert {key: result[key] for key in ("agentId", "agentLabel", "chatId", "provider", "sessionId", "opened")} == {
            "agentId": codex["agentId"], "agentLabel": "Execution delivery (Codex)", "chatId": CODEX_THREAD,
            "provider": "codex", "sessionId": CODEX_THREAD, "opened": True}
        assert result["directory"] == str(tmp_path / "Projects" / "control-plane")
        assert result["message"] == ("Asked the Codex app to open Execution delivery (Codex)'s chat. "
                                     "Paste the message below into it and send it.")
        paste = result["paste"]
        assert f"discussions read {message['id']} --message" in paste
        assert f"discussions ack {message['id']} --target {target}" in paste
        assert f"discussions reply {thread} --text 'YOUR ANSWER'" in paste
        assert 'The person asked in the thread "T-4 regression"' in paste
        # The request's own text stays in Worklog, where it carries who wrote it.
        assert SECRET not in paste

        # Nothing was stored: the slot is still unseen, and the agent's inbox reads it as before.
        [received] = run("--as", "execution-delivery", "inbox")["received"]
        assert received["message"]["id"] == message["id"]


def test_a_claude_desktop_chat_opens_its_claude_code_session_from_the_folder_it_registered_from(tmp_path, monkeypatch, capsys):
    with handoff(tmp_path, monkeypatch, capsys) as (app, browser, run, opened, codex, claude):
        _, message, slots = ask(browser, ("agent", claude["agentId"]), ("agent", codex["agentId"]))
        result = ok(open_chat(browser, message["id"], slots[claude["agentId"]]))

        assert opened == [("claude", str((tmp_path / "Projects" / "docs").resolve()), CLAUDE_SESSION)]
        assert (result["chatId"], result["provider"], result["sessionId"], result["opened"]) == (
            CLAUDE_DESKTOP, "claude", CLAUDE_SESSION, True)

        # Claude resumes a session from its folder; once that folder is gone, nothing is opened.
        monkeypatch.chdir(tmp_path)
        (tmp_path / "Projects" / "docs").rmdir()
        result = ok(open_chat(browser, message["id"], slots[claude["agentId"]]))
        assert len(opened) == 1 and result["opened"] is False
        assert result["message"] == ("Claude resumes a session from its folder, and the folder Docs writer (Claude) registered "
                                     "from is missing. Paste the message below where it works.")
        assert f"--target {slots[claude['agentId']]}" in result["paste"]


def test_a_role_slot_opens_the_chat_of_the_agent_that_owns_the_role(tmp_path, monkeypatch, capsys):
    with handoff(tmp_path, monkeypatch, capsys) as (app, browser, run, opened, codex, claude):
        work = ok(browser.post("/api/v1/work-items", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"title": "T-4"}))
        role = ok(browser.post("/api/v1/discussions/roles", headers={"Idempotency-Key": str(uuid.uuid4())},
                               json={"contextType": "item", "contextId": work["id"], "label": "Producer"}))
        _, message, slots = ask(browser, ("role", role["id"]))
        error(open_chat(browser, message["id"], slots[role["id"]]), 409, "not_an_agent")

        run("--as", "execution-delivery", "discussions", "roles", "claim", role["id"], "--revision", str(role["revision"]),
            "--request-id", "claim-producer")
        result = ok(open_chat(browser, message["id"], slots[role["id"]]))
        assert (result["agentId"], result["opened"]) == (codex["agentId"], True)
        assert opened == [("codex", "", CODEX_THREAD)]


@pytest.mark.parametrize("failure, says", [
    (FileNotFoundError("codex.exe at C:/secret"), "The Codex command line could not be found."),
    (OSError("handler crashed: C:/secret"), "The Codex app did not open the chat."),
])
def test_a_failed_handoff_says_so_without_the_failure_s_own_text(tmp_path, monkeypatch, capsys, failure, says):
    with handoff(tmp_path, monkeypatch, capsys) as (app, browser, run, opened, codex, claude):
        def fail(*args):
            raise failure
        monkeypatch.setattr(session_launches, "_open_desktop", fail)
        _, message, slots = ask(browser, ("agent", codex["agentId"]))
        result = ok(open_chat(browser, message["id"], slots[codex["agentId"]]))
        assert result["opened"] is False
        assert result["message"] == says + " Paste the message below where it works."
        assert "secret" not in json.dumps(result)
        assert result["paste"]


def test_an_agent_with_no_chat_here_still_gets_the_message_to_paste(tmp_path, monkeypatch, capsys):
    with handoff(tmp_path, monkeypatch, capsys) as (app, browser, run, opened, codex, claude):
        elsewhere = register(run, monkeypatch, tmp_path / "Projects" / "elsewhere", NOWHERE_THREAD, "elsewhere", "Elsewhere (Codex)")
        _, message, slots = ask(browser, ("agent", elsewhere["agentId"]))
        result = ok(open_chat(browser, message["id"], slots[elsewhere["agentId"]]))
        assert (result["opened"], result["provider"], result["sessionId"]) == (False, None, None)
        assert result["message"] == (f"Worklog found no Claude or Codex chat {NOWHERE_THREAD} on this computer. "
                                     "Paste the message below where it works.")

        # An identity registered with no chat falls back to the chat its open run reports.
        shared = ok(browser.post("/api/v1/credentials", json={"label": "Release bot"}, headers={"Idempotency-Key": "release-bot"}))
        monkeypatch.setenv("WORKLOG_AGENT_TOKEN", shared["token"])
        _, message, slots = ask(browser, ("agent", shared["agentId"]))
        result = ok(open_chat(browser, message["id"], slots[shared["agentId"]]))
        assert (result["chatId"], result["opened"]) == (None, False)
        assert result["message"].startswith("Release bot reported no chat, so there is none to open.")
        run("runs", "start", "--client-run-key", "release", "--chat", CODEX_THREAD, "--display-name", "Release",
            "--provider", "codex", "--request-id", "release-run")
        result = ok(open_chat(browser, message["id"], slots[shared["agentId"]]))
        assert (result["chatId"], result["opened"]) == (CODEX_THREAD, True)
        assert opened == [("codex", "", CODEX_THREAD)]


def test_only_a_pending_agent_slot_can_be_handed_off_and_only_by_the_person(tmp_path, monkeypatch, capsys):
    with handoff(tmp_path, monkeypatch, capsys) as (app, browser, run, opened, codex, claude):
        _, message, slots = ask(browser, ("agent", codex["agentId"]))
        error(open_chat(browser, message["id"], "no-such-slot"), 404, "not_found")
        error(open_chat(browser, str(uuid.uuid4()), slots[codex["agentId"]]), 404, "not_found")
        error(browser.post(f"/api/v1/discussions/messages/{message['id']}/open-chat", json={}), 422, "validation")

        # An agent's credential never reaches it: opening a chat starts a local process.
        stored = json.loads(next((tmp_path / "cli-data").rglob("docs-writer.json")).read_text(encoding="utf-8"))
        refused = browser.post(f"/api/v1/discussions/messages/{message['id']}/open-chat", json={"targetId": slots[codex["agentId"]]},
                               headers={"Authorization": "Bearer " + stored["token"], "Origin": "", "X-CSRF-Token": ""})
        error(refused, 403, "agent_scope")
        with pytest.raises(AppError) as refused_inside:
            app.state.request_chats.open(message["id"], slots[codex["agentId"]], {"id": codex["agentId"], "kind": "agent"})
        assert refused_inside.value.code == "agent_scope"

        ok(browser.post(f"/api/v1/discussions/messages/{message['id']}/release", headers={"Idempotency-Key": "release-ask"},
                        json={"reason": "Handled on a call"}))
        error(open_chat(browser, message["id"], slots[codex["agentId"]]), 409, "request_terminal")
        assert opened == []
