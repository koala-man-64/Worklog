"""Who owns this, can I reach them now, and did my message land (v33, decision R11).

The first test replays the 2026-11-19 T-4 incident end to end through the agent command line, against
an application on a disposable data directory: a Codex agent's pull request broke production, and the
Claude agent reverting it had to find its owner, reach it and know whether its message landed. The
repository is public, so the replay keeps the incident's shape (T-4, the branch, pull request 4849) with
an invented Azure DevOps organization and invented IDs. The tests after it pin each command and each
state on its own.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError

import pytest
import sealed
from fastapi.testclient import TestClient
from test_discussion_tags import credential, error, ok, person, read, write
from test_repository_discussions import repository
from test_sealed_schemas import contents
from worklog import cli, migrations
from worklog.app import create_app
from worklog.coordination import commit_sha, pull_request
from worklog.db import Database
from worklog.discussions import DiscussionService

# Version 33's documented downgrade to version 32: what agents reported producing, the seen marks, and
# each request's deadline go; nothing else changes.
WAY_BACK = """\
DROP INDEX discussion_requests_deadline;
ALTER TABLE discussion_requests DROP COLUMN escalated_at;
ALTER TABLE discussion_requests DROP COLUMN urgent;
ALTER TABLE discussion_requests DROP COLUMN deadline_at;
DROP TABLE discussion_seen;
DROP TABLE participation_outputs;
PRAGMA user_version=32;
"""


def to_v32(path):
    """Take a current database back to v32 the documented way."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)

ORIGIN = "http://127.0.0.1:8877"
REMOTE = "https://dev.azure.com/example-org/Platform/_git/control-plane"
BRANCH = "codex/t4-producer-close-20261118"
PR_URL = REMOTE + "/pullrequest/4849"
CODEX_THREAD = "0190f4c2-7a5e-7d31-9b8a-3c6e2f1d4a07"
CLAUDE_CHAT = "local_9d3b1c2e-5f4a-4e8b-a7c6-1b2d3e4f5a6b"


@contextmanager
def instance(tmp_path, monkeypatch, capsys):
    """The application on a disposable data directory, with the agent command line wired to it as it
    sends requests (no browser origin, no session), and the person's own browser client beside it."""
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "cli-data"))
    app = create_app(tmp_path / "server", testing=True)
    with TestClient(app, base_url=ORIGIN) as browser, TestClient(app, base_url=ORIGIN) as wire:
        browser.headers["Origin"] = ORIGIN
        browser.headers["X-CSRF-Token"] = browser.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"},
                                                       json={}).json()["csrfToken"]
        sponsor = ok(browser.post("/api/v1/credentials", json={"label": "Shared agents"},
                                  headers={"Idempotency-Key": "issue-shared-agents"}))
        monkeypatch.setenv("WORKLOG_AGENT_TOKEN", sponsor["token"])

        def bridge(request, **kwargs):
            response = wire.request(request.get_method(), request.full_url, content=request.data,
                                    headers=dict(request.header_items()))
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, "rejected", {}, io.BytesIO(response.content))
            return io.BytesIO(response.content)

        monkeypatch.setattr(cli, "_open", bridge)

        def run(*args, fails=False):
            code = cli.main(["agent", *args])
            out, err = capsys.readouterr()
            if fails:
                assert code != 0, out
                return err
            assert code == 0, err
            assert sponsor["token"] not in out + err
            return json.loads(out)

        yield app, browser, run


def register(run, monkeypatch, folder, chat, name, label):
    """Register from the agent's own project folder, as the guide asks."""
    folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(folder)
    return run("register", "--chat", chat, "--name", name, "--label", label)


def test_the_t4_incident_answers_who_owns_it_whether_it_is_reachable_and_whether_the_ask_landed(tmp_path, monkeypatch, capsys):
    with instance(tmp_path, monkeypatch, capsys) as (app, browser, run):
        project = tmp_path / "Projects" / "control-plane"
        codex = register(run, monkeypatch, project, CODEX_THREAD, "execution-delivery", "Execution delivery (Codex)")
        started = run("--as", "execution-delivery", "runs", "start", "--client-run-key", "t4", "--display-name", "T-4 producer close",
                      "--provider", "codex", "--request-id", "codex-run-t4")
        worktree = str(project / ".worktrees" / "t4")
        context = tmp_path / "context.json"
        context.write_text(json.dumps({"runId": started["id"], "description": "T-4: producer close for the attempt journal",
                                       "contexts": [{"repositoryUrl": REMOTE, "checkoutPath": str(project), "worktreePath": worktree,
                                                     "branch": BRANCH}]}), encoding="utf-8")
        reported = run("--as", "execution-delivery", "participation", "start", "--file", str(context), "--request-id", "codex-part-t4")
        for title in ("Earlier ticket", "Another ticket", "A third ticket"):
            run("--as", "execution-delivery", "tickets", "create", "--title", title, "--request-id", "filler-" + title.split()[0])
        t4 = run("--as", "execution-delivery", "tickets", "create", "--title", "Verify the attempt journal covers every outbound call",
                 "--kind", "task", "--priority", "high", "--assign", "me", "--request-id", "codex-t4")
        assert t4["key"] == "T-4"
        produced = run("--as", "execution-delivery", "participation", "output", reported["id"], "--pr", PR_URL,
                       "--commit", "43B69AE9", "--request-id", "codex-output-4849")
        assert [(o["kind"], o["reference"], o["url"]) for o in produced["outputs"]] == [
            ("pr", "4849", PR_URL), ("commit", "43b69ae9", "")]
        repo = ok(browser.get("/api/v1/repositories/" + produced["participation"]["contexts"][0]["repositoryId"]))

        register(run, monkeypatch, tmp_path / "Projects" / "remediation", CLAUDE_CHAT, "canary-remediation",
                 "Canary permit remediation (Claude)")
        run("--as", "canary-remediation", "runs", "start", "--client-run-key", "revert", "--display-name", "Revert 4849",
            "--provider", "claude", "--request-id", "claude-run-revert")

        # 1. Who owns it: one command each, for the ticket, the branch and the pull request.
        for ref, relation in (("T-4", "assignee"), (BRANCH, "branch"), (PR_URL, "producer"), ("4849", "producer"),
                              ("43b69ae9", "producer"), (worktree, "checkout")):
            found = run("--as", "canary-remediation", "owners", ref)
            [owner] = [agent for agent in found["agents"] if agent["owner"]]
            assert (owner["id"], owner["label"], owner["provider"]) == (codex["agentId"], "Execution delivery (Codex)", "codex"), ref
            assert (owner["chatId"], owner["directory"]) == (CODEX_THREAD, str(project)), ref
            assert relation in {r["relation"] for r in owner["relations"]}, ref
            assert [run_["chatId"] for run_ in owner["runs"]] == [CODEX_THREAD]
            assert [(w["contexts"][0]["branch"], w["contexts"][0]["worktreePath"]) for w in owner["working"]] == [(BRANCH, worktree)]
            assert found["person"] == {"id": "local-user", "label": "You", "kind": "human"}
        assert {m["kind"] for m in run("--as", "canary-remediation", "owners", "T-4")["matches"]} == {"ticket"}

        # 2. Can I reach it: the agent's own profile says where it works and whether it is still at it.
        profile = run("--as", "canary-remediation", "agents", "show", codex["agentId"])
        assert (profile["chatId"], profile["directory"], profile["provider"], profile["stale"]) == (
            CODEX_THREAD, str(project), "codex", False)
        assert profile == run("--as", "canary-remediation", "discussions", "participant", codex["agentId"])

        # 3. Did my message land: pending, seen, acknowledged, answered.
        asked = run("--as", "canary-remediation", "discussions", "reply", repo["discussionId"], "--text",
                    "Production regression from T-4 PR 4849; your input is needed.", "--ask", codex["agentId"],
                    "--deadline", "30m", "--request-id", "ask-codex-1")

        def slot():
            [sent] = run("--as", "canary-remediation", "requests", "list", "--sent", "--state", "all")["items"]
            assert sent["message"]["id"] == asked["id"]
            return sent["message"]["request"]["targets"][0]

        assert slot()["delivery"] == "pending"
        assert slot()["presence"] == {"active": True, "lastSeenAt": slot()["presence"]["lastSeenAt"], "stale": False, "openRuns": 1}
        assert asked["request"]["deadlineAt"] is not None and asked["request"]["escalatedAt"] is None
        cursor = run("--as", "canary-remediation", "inbox")["cursor"]

        waiting = run("--as", "execution-delivery", "inbox")
        assert ([item["message"]["id"] for item in waiting["received"]], waiting["unseen"]) == ([asked["id"]], 1)
        assert slot()["delivery"] == "seen" and slot()["seenAt"] is not None
        assert run("--as", "execution-delivery", "inbox")["unseen"] == 0

        slot_id = waiting["received"][0]["message"]["request"]["actionableTargetIds"][0]
        run("--as", "execution-delivery", "discussions", "ack", asked["id"], "--target", slot_id, "--request-id", "ack-codex-1")
        assert slot()["delivery"] == "acknowledged"
        # The request ended, so --sent lists it under ended now.
        [ended] = run("--as", "canary-remediation", "requests", "list", "--sent", "--state", "ended")["items"]
        assert ended["message"]["request"]["state"] == "satisfied"

        answer = run("--as", "execution-delivery", "discussions", "reply", repo["discussionId"], "--text",
                     "Reverting is right; the journal is not deployed.", "--request-id", "answer-codex-1")
        [after] = run("--as", "canary-remediation", "requests", "list", "--sent", "--state", "ended")["items"]
        target = after["message"]["request"]["targets"][0]
        assert (target["delivery"], target["answerMessageId"]) == ("answered", answer["id"])
        back = run("--as", "canary-remediation", "inbox", "--since", cursor)
        assert [item["message"]["id"] for item in back["answers"]] == [answer["id"]]
        assert [change["kind"] for change in back["changes"]] == ["discussion.acknowledge"]

        # Unanswered by its deadline, a request escalates to the person, and Rudy's Live counts it as his.
        before = ok(browser.get("/api/v1/live"))["summary"]
        second = run("--as", "canary-remediation", "discussions", "reply", repo["discussionId"], "--text",
                     "Confirm the journal rollout plan.", "--ask", codex["agentId"], "--deadline", "10s", "--request-id", "ask-codex-2")
        service = DiscussionService(app.state.db)
        with app.state.db.transaction() as c:
            assert service.escalate_overdue(c) == []  # not yet due
        later = datetime.now(UTC) + timedelta(seconds=11)
        with app.state.db.transaction() as c:
            assert service.overdue(c, later)
            assert service.escalate_overdue(c, later) == [second["id"]]
            assert service.escalate_overdue(c, later) == []  # once
        [pending] = run("--as", "canary-remediation", "requests", "list", "--sent")["items"]
        assert (pending["message"]["id"], pending["message"]["request"]["overdue"]) == (second["id"], True)
        assert pending["message"]["request"]["escalatedAt"] is not None
        escalated = run("--as", "canary-remediation", "inbox", "--since", back["cursor"])
        assert [change["kind"] for change in escalated["changes"]] == ["discussion.request.escalate"]
        summary = ok(browser.get("/api/v1/live"))["summary"]
        assert summary["pendingRequests"] == before["pendingRequests"] + 1
        assert summary["waitingOnAgents"] == before["waitingOnAgents"]
        mine = ok(browser.get("/api/v1/discussions/requests?box=received"))["items"]
        assert second["id"] in [item["message"]["id"] for item in mine]

        # Waking without a push channel: inbox --wait returns as soon as the answer arrives.
        replies = []

        def answer_while_waiting(seconds):
            if not replies:
                replies.append(run("--as", "execution-delivery", "discussions", "reply", repo["discussionId"], "--text",
                                   "Rollout plan: journal first, then the hook.", "--request-id", "answer-codex-2"))

        monkeypatch.setattr(cli.time, "sleep", answer_while_waiting)
        woke = run("--as", "canary-remediation", "inbox", "--wait", "--timeout", "60")
        assert woke["timedOut"] is False
        assert [item["message"]["id"] for item in woke["answers"]] == [replies[0]["id"]]


@pytest.fixture
def client(tmp_path):
    with person(tmp_path) as c:
        yield c


def agent_run(c, agent, repositories=(), branch="claude/work", **fields):
    running = ok(write(c, "POST", "/agent-runs", {"clientRunKey": str(uuid.uuid4()), "displayName": "Work"}, agent))
    return ok(write(c, "POST", "/participations", {"runId": running["id"], "contexts": [
        {"repositoryId": r["id"], "branch": branch, **fields} for r in repositories]}, agent))


def outputs(c, agent, participation, **payload):
    return write(c, "POST", f"/participations/{participation['id']}/outputs", payload, agent)


def test_pull_requests_and_commits_are_named_by_address_number_or_sha():
    assert pull_request("https://github.com/Team/Repo/pull/12") == ("12", "https://github.com/Team/Repo/pull/12", "github.com/team/repo")
    assert pull_request(PR_URL + "?_a=files") == ("4849", PR_URL, "dev.azure.com/example-org/platform/_git/control-plane")
    assert pull_request("https://example-org.visualstudio.com/Platform/_git/control-plane/pullrequest/7")[2] == \
        "dev.azure.com/example-org/platform/_git/control-plane"
    assert pull_request("https://gitlab.com/g/p/-/merge_requests/3")[0] == "3"
    assert pull_request(" 4849 ") == ("4849", "", "")
    assert commit_sha("43B69AE9") == "43b69ae9"
    for wrong in ("https://github.com/Team/Repo", "PR-12", "0", "ftp://host/pull/1"):
        with pytest.raises(Exception, match="pull request"):
            pull_request(wrong)
    with pytest.raises(Exception, match="SHA"):
        commit_sha("43b6")


def test_outputs_belong_to_a_reported_repository_once_each_and_follow_a_merge(client):
    repo, other = repository(client), repository(client, "Other")
    builder = credential(client, "Builder")
    reported = agent_run(client, builder, [repo])
    first = ok(outputs(client, builder, reported, pullRequests=["12"], commits=["abcdef1"]))
    assert (first["added"], first["participation"]["revision"]) == (2, 2)
    assert ok(outputs(client, builder, reported, pullRequests=["12"]))["added"] == 0
    error(outputs(client, builder, reported, repositoryId=other["id"], commits=["abcdef2"]), 422, "validation")
    error(outputs(client, builder, reported), 422, "validation")
    error(outputs(client, credential(client, "Someone else"), reported, commits=["abcdef3"]), 403, "coordination_scope")
    # The person merges the repository: its outputs follow, and owners still finds the producer.
    ok(write(client, "POST", f"/repositories/{repo['id']}/merge", {"intoId": other["id"], "revision": repo["revision"],
                                                                   "intoRevision": other["revision"]}))
    found = read(client, "/owners?ref=abcdef1")
    assert [(a["label"], a["owner"]) for a in found["agents"]] == [("Builder", True)]
    assert found["agents"][0]["relations"][0]["repositoryId"] == other["id"]
    released = ok(write(client, "POST", f"/participations/{reported['id']}/release",
                        {"revision": read(client, f"/participations/{reported['id']}")["revision"]}, builder))
    error(outputs(client, builder, released, commits=["abcdef4"]), 409, "released")


def test_owners_tells_owners_from_contributors_and_narrows_by_repository(client):
    repo, other = repository(client), repository(client, "Other")
    owner, helper, commenter = credential(client, "Owner"), credential(client, "Helper"), credential(client, "Commenter")
    work = ok(write(client, "POST", "/work-items", {"title": "Parser"}))
    role = ok(write(client, "POST", "/discussions/roles", {"contextType": "item", "contextId": work["id"], "label": "Implementation"}, owner))
    ok(write(client, "POST", f"/discussions/roles/{role['id']}/claim", {"revision": role["revision"]}, owner))
    running = ok(write(client, "POST", "/agent-runs", {"clientRunKey": "h", "displayName": "Help"}, helper))
    ok(write(client, "POST", "/participations", {"runId": running["id"], "itemId": work["id"]}, helper))
    ticket = ok(write(client, "POST", "/tickets", {"title": "Parser crash", "itemId": work["id"]}, helper))
    ok(write(client, "POST", f"/tickets/{ticket['key']}/comments", {"text": "Seen it too"}, commenter))

    found = read(client, f"/owners?ref={ticket['key']}")
    relations = {a["label"]: (a["owner"], sorted(r["relation"] for r in a["relations"])) for a in found["agents"]}
    assert relations == {"Owner": (True, ["role"]), "Helper": (False, ["author", "participant"]),
                         "Commenter": (False, ["commenter"])}
    assert found["agents"][0]["label"] == "Owner"
    assert read(client, f"/owners?ref=item:{work['id']}")["matches"] == [
        {"kind": "item", "id": work["id"], "label": "Parser", "matchedBy": "id"}]

    agent_run(client, owner, [repo], branch="feature/x")
    agent_run(client, helper, [other], branch="feature/x")
    assert {a["label"] for a in read(client, "/owners?ref=feature/x")["agents"]} == {"Owner", "Helper"}
    assert {a["label"] for a in read(client, "/owners?ref=refs/heads/feature/x")["agents"]} == {"Owner", "Helper"}
    narrowed = read(client, f"/owners?ref=branch:feature/x&repositoryId={other['id']}")["agents"]
    assert [a["label"] for a in narrowed] == ["Helper"]
    assert read(client, "/owners?ref=no-such-branch") == {"ref": "no-such-branch", "matches": [],
                                                          "person": {"id": "local-user", "label": "You", "kind": "human"},
                                                          "agents": []}
    error(client.get("/api/v1/owners?ref=https://github.com/Team/Repo"), 422, "validation")


def test_a_path_names_the_checkout_or_worktree_that_holds_it(client):
    repo = repository(client)
    builder = credential(client, "Builder")
    agent_run(client, builder, [repo], checkoutPath="C:\\Projects\\Worklog", worktreePath="C:/Projects/Worklog/.claude/worktrees/x")
    for ref in ("C:/projects/worklog/.claude/worktrees/x/backend", "C:\\Projects\\Worklog\\.claude\\worktrees\\x", "path:C:/Projects/Worklog"):
        [found] = read(client, "/owners?" + "ref=" + ref.replace("\\", "%5C"))["agents"]
        assert (found["label"], [r["relation"] for r in found["relations"]][0]) == ("Builder", "checkout"), ref


def test_the_profile_says_where_to_reach_an_agent_and_the_person_stays_as_before(client):
    builder = credential(client, "Builder")
    profile = read(client, f"/discussions/participants/{builder['agentId']}", builder)
    # A credential issued in Settings registered from no chat or directory.
    assert (profile["chatId"], profile["directory"], profile["provider"], profile["runs"], profile["outputs"]) == (
        None, None, "", [], [])
    assert profile["stale"] is True
    running = ok(write(client, "POST", "/agent-runs", {"clientRunKey": "r", "displayName": "Work", "provider": "codex",
                                                       "externalChatId": CODEX_THREAD}, builder))
    profile = read(client, f"/discussions/participants/{builder['agentId']}")
    assert profile["runs"] == [{"id": running["id"], "displayName": "Work", "provider": "codex", "chatId": CODEX_THREAD,
                                "createdAt": running["createdAt"]}]
    assert (profile["provider"], profile["stale"]) == ("codex", False)
    assert read(client, "/discussions/participants/local-user") == {"id": "local-user", "label": "You", "kind": "human",
                                                                    "avatar": None, "active": True}


def ask(c, sender, discussion, targets, **acknowledgement):
    return ok(write(c, "POST", f"/discussions/{discussion}/messages",
                    {"text": "Please confirm", "acknowledgement": {"targets": targets, **acknowledgement}}, sender))


def test_only_a_target_reading_marks_its_slot_seen_and_the_person_answers_a_slot_of_theirs(client):
    asker, target, bystander = credential(client, "Asker"), credential(client, "Target"), credential(client, "Bystander")
    thread = ok(write(client, "POST", "/discussions", {"text": "Start"}, asker))["discussion"]["id"]
    asked = ask(client, asker, thread, [{"kind": "agent", "id": target["agentId"]}, {"kind": "human", "id": "local-user"}])
    for reader in (asker, bystander):
        read(client, f"/discussions/{thread}", reader)
        read(client, f"/discussions/messages/{asked['id']}", reader)
    slots = {t["kind"]: t for t in read(client, f"/discussions/messages/{asked['id']}", asker)["request"]["targets"]}
    assert (slots["agent"]["delivery"], slots["human"]["delivery"]) == ("pending", "pending")
    read(client, "/discussions?view=awaiting", target)
    read(client, f"/discussions/{thread}")  # the person opens the thread
    slots = {t["kind"]: t for t in read(client, f"/discussions/messages/{asked['id']}", asker)["request"]["targets"]}
    assert (slots["agent"]["delivery"], slots["human"]["delivery"]) == ("seen", "seen")
    assert slots["human"]["presence"] is None and slots["agent"]["presence"]["active"] is True
    ok(write(client, "POST", f"/discussions/{thread}/messages", {"text": "Answered by the person"}))
    slots = {t["kind"]: t for t in read(client, f"/discussions/messages/{asked['id']}", asker)["request"]["targets"]}
    assert (slots["agent"]["delivery"], slots["human"]["delivery"]) == ("seen", "answered")
    with client.app.state.db.read() as c:
        assert c.execute("SELECT count(*) FROM discussion_seen").fetchone()[0] == 2


def test_a_deadline_escalates_only_an_unanswered_request_and_urgent_means_fifteen_minutes(client):
    asker, target = credential(client, "Asker"), credential(client, "Target")
    thread = ok(write(client, "POST", "/discussions", {"text": "Start"}, asker))["discussion"]["id"]
    named = [{"kind": "agent", "id": target["agentId"]}]
    answered = ask(client, asker, thread, named, deadlineSeconds=10)
    # A message the target posts after a request answers it, and every earlier request in the thread.
    ok(write(client, "POST", f"/discussions/{thread}/messages", {"text": "Here is my answer"}, target))
    urgent = ask(client, asker, thread, named, urgent=True)["request"]
    wait = datetime.fromisoformat(urgent["deadlineAt"]) - datetime.now(UTC)
    assert urgent["urgent"] is True and timedelta(minutes=14) < wait <= timedelta(minutes=15)
    error(write(client, "POST", f"/discussions/{thread}/messages",
                {"text": "x", "acknowledgement": {"targets": named, "deadlineSeconds": 5}}, asker), 422, "validation")
    service = DiscussionService(client.app.state.db)
    with client.app.state.db.transaction() as c:
        # The answered one is due but has its answer; the urgent one is not due yet.
        assert service.escalate_overdue(c, datetime.now(UTC) + timedelta(seconds=11)) == []
        assert service.escalate_overdue(c, datetime.now(UTC) + timedelta(minutes=16)) == [urgent["id"]]
    message = read(client, f"/discussions/messages/{answered['id']}", asker)["request"]
    assert (message["escalatedAt"], message["targets"][0]["delivery"]) == (None, "answered")


def test_the_command_line_checks_deadlines_and_names_a_folder_by_its_path(tmp_path, monkeypatch, capsys):
    with instance(tmp_path, monkeypatch, capsys) as (_, _, run):
        assert "go with --ask" in run("discussions", "reply", str(uuid.uuid4()), "--text", "x", "--deadline", "5m", fails=True)
        for usage in (["discussions", "reply", "x", "--text", "x", "--ask", "a", "--deadline", "5s"],
                      ["inbox", "--wait", "--timeout", "3601"]):
            with pytest.raises(SystemExit) as exited:
                cli.main(["agent", *usage])
            assert exited.value.code == 2
        capsys.readouterr()
        assert "--pr" in run("participation", "output", str(uuid.uuid4()), "--request-id", "output-nothing", fails=True)
        monkeypatch.chdir(tmp_path)
        assert run("owners", ".")["ref"] == "path:" + os.path.abspath(".")
        assert run("owners", "main")["ref"] == "main"
    assert cli._deadline("90s") == 90 and cli._deadline("2h") == 7200 and cli._deadline("1d") == 86400


def test_inbox_wait_times_out_quietly_when_nothing_arrives(tmp_path, monkeypatch, capsys):
    with instance(tmp_path, monkeypatch, capsys) as (_, _, run):
        register(run, monkeypatch, tmp_path / "work", "chat-1", "waiter", "Waiter")
        clock = [0.0]
        monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(cli.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        waited = run("--as", "waiter", "inbox", "--wait", "--timeout", "30")
        assert (waited["timedOut"], waited["received"], waited["answers"], waited["changes"]) == (True, [], [], [])


def worked(directory):
    """A current database holding a reported output, a seen mark and an escalated request."""
    with person(directory) as c:
        repo, asker, target = repository(c), credential(c, "Asker"), credential(c, "Target")
        reported = agent_run(c, target, [repo])
        ok(outputs(c, target, reported, pullRequests=["12"]))
        thread = ok(write(c, "POST", "/discussions", {"text": "Start"}, asker))["discussion"]["id"]
        asked = ask(c, asker, thread, [{"kind": "agent", "id": target["agentId"]}], deadlineSeconds=10)
        read(c, f"/discussions/messages/{asked['id']}", target)
        with c.app.state.db.transaction() as conn:
            DiscussionService(c.app.state.db).escalate_overdue(conn, datetime.now(UTC) + timedelta(minutes=1))
    return directory / "worklog.sqlite3"


def test_the_v33_upgrade_changes_no_record_and_the_way_back_gives_v32s_exact_shape(tmp_path):
    database = worked(tmp_path / "data")
    to_v32(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 32
    _, signature, before = contents(database)
    assert signature == sealed.shape(32)
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    # Each request keeps its row, with no deadline, not urgent and not escalated; the new tables start empty.
    assert after["discussion_requests"] == sorted((row + (None, 0, None) for row in before["discussion_requests"]), key=repr)
    assert (after["participation_outputs"], after["discussion_seen"]) == ([], [])
    changed = {"discussion_requests", "participation_outputs", "discussion_seen"}
    assert {t: rows for t, rows in after.items() if t not in changed} == {t: rows for t, rows in before.items() if t not in changed}
    snapshot, = (tmp_path / "data" / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 32


# What v33 adds to a database that an older upgrade test, comparing every table across its upgrade, has
# to leave out: the two tables, empty after an upgrade, and each request's three new columns.
V33_TABLES = ("participation_outputs", "discussion_seen")


def before_v33(rows):
    """A database's rows as they read before v33: its new tables left out while empty, and each request's
    row cut back to its ten older columns."""
    kept = {table: values for table, values in rows.items() if table not in V33_TABLES or values}
    if "discussion_requests" in kept:
        kept["discussion_requests"] = [row[:10] for row in kept["discussion_requests"]]
    return kept
