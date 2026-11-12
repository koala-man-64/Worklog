"""Claude Code sessions running on this machine, suggested on Chats until a record tracks them.

Claude Code keeps a record of each running session in <config>/sessions/<pid>.json. A record counts
only while its process runs and started when the record says it did. The reader's tests write
records for this test's own process and for processes that have ended; the API's tests stand in
for the operating system with a table of the processes they say are running.
"""

import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_chat_confirmations import error, issue, ok, post, register, run
from worklog import active_sessions
from worklog.active_sessions import (
    EPOCH_TICKS,
    process_start,
    read_record,
    running,
    running_sessions,
)
from worklog.app import create_app
from worklog.transcripts import TranscriptRoots

ORIGIN = "http://127.0.0.1:8877"
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Claude Code records a Windows process start")
STARTED_MS = 1_793_900_000_000


def write_record(folder: Path, pid: int | str, *, file: int | None = None, **fields) -> dict:
    """A record as Claude Code writes it, named after its process unless `file` names it; a field
    given as None is left out."""
    data = {"pid": pid, "sessionId": str(uuid.uuid4()), "cwd": "C:\\Projects\\worklog", "startedAt": STARTED_MS,
            "procStart": "133000000000000000", "version": "2.1.317", "kind": "interactive", "entrypoint": "claude-desktop",
            "hostSessionId": "local_" + str(uuid.uuid4()), "name": "Parser fix", "status": "busy",
            "updatedAt": STARTED_MS + 1000, "statusUpdatedAt": STARTED_MS + 1000, "messagingSocketPath": "\\\\.\\pipe\\x"}
    data.update(fields)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{file or pid}.json").write_text(json.dumps({k: v for k, v in data.items() if v is not None}), encoding="utf-8")
    return data


def ended_pid() -> int:
    """The ID of a process that has exited."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


# The reader, against this machine's processes ------------------------------------------------


def test_a_record_counts_only_while_its_own_process_runs(tmp_path):
    folder = tmp_path / "sessions"
    own = os.getpid()
    start = process_start(own)
    assert start is not None
    mine = write_record(folder, own, procStart=str(start) if start else None)
    gone = write_record(folder, ended_pid())
    assert process_start(gone["pid"]) is None
    assert [s["sessionId"] for s in running_sessions([folder])] == [mine["sessionId"]]
    # Files that are not records, and a folder that does not exist, are passed over.
    (folder / f"{own}.0123abcd.key").write_text("secret", encoding="utf-8")
    (folder / "notes.json").write_text("{}", encoding="utf-8")
    assert len(running_sessions([folder, tmp_path / "missing"])) == 1


@WINDOWS
def test_a_reused_process_id_is_not_the_session_that_recorded_it(tmp_path):
    own = os.getpid()
    start = process_start(own)
    assert start and start > EPOCH_TICKS
    created_ms = (start - EPOCH_TICKS) // 10_000
    assert created_ms <= time.time() * 1000

    def recorded(**fields):
        write_record(tmp_path, own, **fields)
        found = read_record(tmp_path / f"{own}.json")
        assert found is not None
        return found

    assert running(recorded(procStart=str(start)))
    assert not running(recorded(procStart=str(start + 1)))
    # Without a recorded start, the process must be no younger than the session.
    assert running(recorded(procStart=None, startedAt=created_ms + 1))
    assert not running(recorded(procStart=None, startedAt=created_ms - 60_000))
    assert running(recorded(procStart=None, startedAt=None))


def test_a_record_is_read_as_claude_code_writes_it(tmp_path):
    session, host = str(uuid.uuid4()), "local_" + str(uuid.uuid4())
    write_record(tmp_path, 4242, sessionId=session.upper(), hostSessionId=host.upper(), status="waiting",
                 waitingFor="permission prompt", cwd="C:\\Projects\\api", name="  Build   the API  ")
    record = read_record(tmp_path / "4242.json")
    assert record is not None
    assert {k: record[k] for k in ("pid", "procStart", "sessionId", "hostSessionId", "name", "cwd", "status", "waitingFor")} == {
        "pid": 4242, "procStart": 133000000000000000, "sessionId": session, "hostSessionId": host, "name": "Build the API",
        "cwd": "C:\\Projects\\api", "status": "waiting", "waitingFor": "permission prompt"}
    # A status this reader doesn't know reads as idle, and only a waiting session says what for.
    write_record(tmp_path, 4243, status="compacting", waitingFor="nothing", hostSessionId="local_nope", cwd="C:\\a\nb")
    other = read_record(tmp_path / "4243.json")
    assert other is not None
    assert (other["status"], other["waitingFor"], other["hostSessionId"], other["cwd"]) == ("idle", "", None, "")
    # Not records: a file named for another process, a session ID that is not a UUID, a PID that is not a number.
    write_record(tmp_path, 4245, file=4244)
    write_record(tmp_path, 4246, sessionId="../../etc")
    write_record(tmp_path, "4247", file=4247)
    (tmp_path / "4248.json").write_text("[1,2]", encoding="utf-8")
    (tmp_path / "4249.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "4250.json").write_text(json.dumps({"pid": 4250, "sessionId": str(uuid.uuid4()), "pad": "x" * 70_000}), encoding="utf-8")
    assert [read_record(tmp_path / f"{pid}.json") for pid in range(4244, 4251) if pid != 4245] == [None] * 6
    # A process ID no system gives is no process, and asking about one never fails the list.
    write_record(tmp_path, 9_999_999_999)
    assert read_record(tmp_path / "9999999999.json") is None
    assert [process_start(pid) for pid in (0, -4, 2**31, 9_999_999_999)] == [None] * 4


@WINDOWS
def test_a_record_from_another_systems_processes_is_not_this_ones(tmp_path):
    # As when WSL shares the folder: its process IDs mean nothing to Windows.
    for pid, domain in [(4301, "win32:msi"), (4302, None), (4303, "linux:4026531836"), (4304, "darwin")]:
        write_record(tmp_path, pid, pidDomain=domain)
    assert [read_record(tmp_path / f"{pid}.json") is not None for pid in range(4301, 4305)] == [True, True, False, False]


def test_running_sessions_put_those_waiting_on_you_first_and_count_each_once(tmp_path):
    starts = {101: 1, 102: 2, 103: 3, 104: 4, 105: 5}
    shared = str(uuid.uuid4())
    for pid, status, changed in [(101, "idle", 9), (102, "busy", 5), (103, "waiting", 1), (104, "busy", 7)]:
        write_record(tmp_path, pid, procStart=str(starts[pid]), status=status, statusUpdatedAt=STARTED_MS + changed,
                     name=f"pid {pid}", sessionId=shared if pid in {102, 104} else str(uuid.uuid4()))
    write_record(tmp_path, 105, procStart="999", name="reused")
    found = running_sessions([tmp_path], starts.get)
    # One session recorded twice counts once, as its latest record.
    assert [(s["name"], s["status"]) for s in found] == [("pid 103", "waiting"), ("pid 104", "working"), ("pid 101", "idle")]
    assert found[0]["statusAt"] == "2026-11-05T17:33:20.001Z"
    assert set(found[0]) == {"sessionId", "hostSessionId", "name", "cwd", "entrypoint", "kind", "status", "waitingFor",
                             "startedAt", "statusAt", "version"}


# The API, with the operating system's processes stood in for --------------------------------


class Machine:
    """Claude Code's folders under tmp_path, and the processes said to be running."""

    def __init__(self, root: Path):
        self.root = root
        self.starts: dict[int, int] = {}
        self.sessions = root / "claude" / "sessions"
        self.projects = root / "claude" / "projects"
        self.desktop = root / "desktop"
        self.sessions.mkdir(parents=True)

    def roots(self) -> TranscriptRoots:
        return TranscriptRoots((self.projects,), (self.desktop,), (), (self.sessions,))

    def start(self, pid: int, **fields) -> dict:
        """A session whose process runs now."""
        self.starts[pid] = 133_000_000_000_000_000 + pid
        return write_record(self.sessions, pid, procStart=str(self.starts[pid]), **fields)

    def end(self, pid: int) -> None:
        del self.starts[pid]

    def transcript(self, session: str, *records: dict) -> None:
        path = self.projects / "C--Projects-worklog" / f"{session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


@pytest.fixture
def machine(tmp_path, monkeypatch):
    found = Machine(tmp_path)
    monkeypatch.setattr(active_sessions, "process_start", lambda pid: found.starts.get(pid))
    return found


@contextmanager
def person(directory: Path, roots: TranscriptRoots):
    app = create_app(directory, testing=True, transcript_roots=roots)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


@pytest.fixture
def client(tmp_path, machine):
    with person(tmp_path / "data", machine.roots()) as c:
        yield c


def sessions(c) -> dict[str, dict]:
    return {s["sessionId"]: s for s in ok(c.get("/api/v1/claude-sessions"))["items"]}


def test_running_sessions_are_offered_with_the_id_to_track_them_by(client, machine):
    parser = machine.start(201, name="Parser fix", status="busy")
    docs = machine.start(202, name=None, status="waiting", waitingFor="permission prompt", hostSessionId=None, entrypoint="cli")
    machine.transcript(docs["sessionId"],
                       {"type": "custom-title", "customTitle": "Docs pass"},
                       {"type": "assistant", "timestamp": "2026-11-05T20:00:00Z",
                        "message": {"id": "m1", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "On it"}], "stop_reason": "end_turn"}})
    gone = machine.start(203, name="Finished")
    machine.end(203)

    found = ok(client.get("/api/v1/claude-sessions"))["items"]
    assert [s["sessionId"] for s in found] == [docs["sessionId"], parser["sessionId"]]
    assert gone["sessionId"] not in {s["sessionId"] for s in found}
    waiting, working = found
    # A session Claude Code has not named goes by its transcript's title; the model comes from it too.
    assert (waiting["name"], waiting["status"], waiting["waitingFor"], waiting["model"], waiting["entrypoint"]) == (
        "Docs pass", "waiting", "permission prompt", "claude-opus-5-5", "cli")
    assert (working["name"], working["status"], working["model"], working["hostSessionId"]) == (
        "Parser fix", "working", "", parser["hostSessionId"])
    # Nothing tracks or ties them yet, so each is tracked by its own session ID.
    assert [(s["chatId"], s["tracked"], s["agents"]) for s in found] == [
        (docs["sessionId"], [], []), (parser["sessionId"], [], [])]


def test_an_unnamed_session_goes_by_its_desktop_title_or_else_its_folder(client, machine):
    titled = machine.start(211, name="", cwd="C:\\Projects\\alpha")
    folder = machine.desktop / "claude-code-sessions" / "acct" / "org"
    folder.mkdir(parents=True)
    (folder / f"{titled['hostSessionId']}.json").write_text(json.dumps({"title": "Alpha review"}), encoding="utf-8")
    plain = machine.start(212, name="", cwd="C:\\Projects\\beta\\", hostSessionId=None)
    found = sessions(client)
    assert (found[titled["sessionId"]]["name"], found[plain["sessionId"]]["name"]) == ("Alpha review", "beta")


def test_a_tracked_session_names_its_chats_and_keeps_their_id(client, machine):
    session = machine.start(221)
    item = post(client, "/work-items", {"title": "Import format"})
    roadmap = post(client, "/roadmaps", {"title": "Platform"})
    # Tracked by its desktop ID, as typed: case and surrounding space don't hide the session.
    chat = post(client, "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": " " + session["hostSessionId"].upper(), "title": "Parser"})
    found = sessions(client)[session["sessionId"]]
    assert found["chatId"] == session["hostSessionId"].upper()
    assert found["tracked"] == [{"id": chat["id"], "title": "Parser", "archived": False, "ownerType": "item",
                                 "ownerId": item["id"], "ownerTitle": "Import format"}]
    post(client, f"/chats/{chat['id']}/archive", {"revision": chat["revision"]})
    on_roadmap = post(client, "/chats", {"ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": session["sessionId"], "title": "Lead"})
    tracked = sessions(client)[session["sessionId"]]["tracked"]
    # Records tracking it now come before archived ones.
    assert [(t["id"], t["archived"], t["ownerTitle"]) for t in tracked] == [(on_roadmap["id"], False, "Platform"), (chat["id"], True, "Import format")]


def test_agents_tied_to_a_session_and_the_id_they_are_tied_by(client, machine):
    session = machine.start(231)
    sponsor = issue(client, "Settings credential")
    register(client, sponsor, "Parser agent", chat=session["sessionId"])
    claimant = issue(client, "Helper")
    run(client, claimant, chat_id=session["hostSessionId"], name="Helper run")

    found = sessions(client)[session["sessionId"]]
    assert [(a["label"], a["binding"], a["chatId"], a["active"]) for a in found["agents"]] == [
        ("Parser agent", "registered", session["sessionId"], True), ("Helper", "run", session["hostSessionId"], True)]
    # The registered identity's ID wins: its runs report it, and a tracked chat ties to them by it exactly.
    assert found["chatId"] == session["sessionId"]


def test_tracking_a_session_names_the_agents_whose_runs_report_it(client, machine):
    session = machine.start(241)
    helper = issue(client, "Helper")
    run(client, helper, chat_id=session["sessionId"], name="Helper run")
    item = post(client, "/work-items", {"title": "Import format"})
    chat = post(client, "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": session["sessionId"], "title": "Parser"})
    listed = {c["id"]: c for c in ok(client.get("/api/v1/chats"))["items"]}
    assert [(a["label"], a["binding"]) for a in listed[chat["id"]]["agents"]] == [("Helper", "run")]
    # Once tracked, the session is no longer only a suggestion: it names the chat.
    assert [t["id"] for t in sessions(client)[session["sessionId"]]["tracked"]] == [chat["id"]]


def test_a_session_is_offered_under_the_id_an_agents_run_reports(client, machine):
    # Tracking the session by the ID offered ties the chat to that agent's run, which reports the
    # desktop session's ID.
    session = machine.start(245)
    helper = issue(client, "Helper")
    run(client, helper, chat_id=session["hostSessionId"], name="Helper run")
    offered = sessions(client)[session["sessionId"]]
    claim = next(a for a in offered["agents"] if a["id"] == helper["agentId"])
    assert (claim["binding"], claim["chatId"]) == ("run", session["hostSessionId"])
    assert offered["chatId"] == session["hostSessionId"]

    item = post(client, "/work-items", {"title": "Import format"})
    chat = post(client, "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": offered["chatId"], "title": "Parser"})
    listed = {c["id"]: c for c in ok(client.get("/api/v1/chats"))["items"]}
    assert [(a["label"], a["binding"]) for a in listed[chat["id"]]["agents"]] == [("Helper", "run")]


def test_a_tracked_chat_names_only_the_agents_tied_to_its_own_chat_id(client, machine):
    # A tracked chat compares chat IDs exactly, as runs report them, so a tie under another form of
    # the ID is not this chat's, though the running session, which goes by both, still finds it.
    session = machine.start(247)
    exact, other = issue(client, "Exact"), issue(client, "Other case")
    item = post(client, "/work-items", {"title": "Import format"})
    chat = post(client, "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": session["sessionId"], "title": "Parser"})
    run(client, exact, chat_id=session["sessionId"])
    run(client, other, chat_id=session["sessionId"].upper())
    listed = {c["id"]: c for c in ok(client.get("/api/v1/chats"))["items"]}
    assert [a["label"] for a in listed[chat["id"]]["agents"]] == ["Exact"]
    assert [a["label"] for a in sessions(client)[session["sessionId"]]["agents"]] == ["Exact", "Other case"]


def test_agents_never_see_the_persons_sessions(client, machine):
    helper = issue(client, "Helper")
    machine.start(251)
    error(client.get("/api/v1/claude-sessions", headers={"Authorization": "Bearer " + helper["token"]}), 403, "agent_scope")
    error(client.get("/api/v1/claude-sessions/", headers={"Authorization": "Bearer " + helper["token"]}), 403, "agent_scope")


def test_an_agent_page_shows_the_chats_its_runs_report(client, machine):
    helper = issue(client, "Parser helper")
    issue(client, "Docs writer")
    item = post(client, "/work-items", {"title": "Import format"})
    chat_id = str(uuid.uuid4())
    chat = post(client, "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": chat_id, "title": "Parser"})
    run(client, helper, chat_id=chat_id)
    agent = ok(client.get(f"/api/v1/agents/{helper['agentId']}"))
    assert [(c["id"], c["via"], c["current"]) for c in agent["chats"]["items"]] == [(chat["id"], ["run"], True)]
    assert [(w["id"], w["via"]) for w in agent["workItems"]["items"]] == [(item["id"], ["chat"])]
    # The agent list finds agents by label, for the picker that ties them.
    assert [a["label"] for a in ok(client.get("/api/v1/agents", params={"q": "PARSER"}))["items"]] == ["Parser helper"]
    assert len(ok(client.get("/api/v1/agents"))["items"]) == 2
