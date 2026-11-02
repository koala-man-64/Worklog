"""Agent chat history read from the transcripts Claude Code and Codex write, through the HTTP boundary."""

import json
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog import transcripts
from worklog.app import create_app
from worklog.transcripts import TranscriptReader, TranscriptRoots

ORIGIN = "http://127.0.0.1:8877"
CLAUDE = "6f48a3b8-48c0-4576-9c88-376444018e1b"
DESKTOP = "local_3bad7a04-7345-4b8d-a4db-9f450b876f45"
REVIEWER = "local_b95c2fb7-0000-4000-8000-000000000001"
CODEX = "01a194b7-153b-7f41-83ed-80f5e52479d1"
CHILD = "01a194b8-58f4-7991-98c5-4cfacd3ea5ea"


def at(minute: int) -> str:
    return f"2026-11-01T08:{minute:02d}:00.000Z"


def lines(path, records, partial=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
        if partial is not None:
            f.write(json.dumps(partial)[:-5])
    return path


def human(minute, content):
    return {"type": "user", "timestamp": at(minute), "origin": {"kind": "human"}, "message": {"role": "user", "content": content}}


def reply(minute, message_id, blocks, stop="tool_use"):
    return {"type": "assistant", "timestamp": at(minute), "message": {"id": message_id, "model": "claude-opus-5-5", "content": blocks, "stop_reason": stop}}


@pytest.fixture
def roots(tmp_path):
    claude = tmp_path / "claude" / "projects"
    session = lines(claude / "C--repo" / f"{CLAUDE}.jsonl", [
        {"type": "custom-title", "customTitle": "Build the API", "sessionId": CLAUDE},
        human(1, "Please build the API"),
        human(2, [{"type": "text", "text": "<system-reminder>private context</system-reminder>"}, {"type": "text", "text": "and add tests"}]),
        reply(3, "m1", [{"type": "text", "text": "On it."}]),
        reply(4, "m1", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest", "description": "Run tests"}}]),
        {"type": "user", "timestamp": at(5), "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "tool output"}]}},
        reply(6, "m3", [{"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "api.py"}}]),
        reply(7, "m4", [{"type": "tool_use", "id": "t3", "name": "SendMessage", "input": {"to": REVIEWER, "message": "API is ready for review"}}]),
        reply(8, "m5", [{"type": "tool_use", "id": "t4", "name": "Agent", "input": {"description": "Review the API", "prompt": "Review it", "subagent_type": "Explore"}}]),
        {"type": "user", "timestamp": at(9), "isMeta": True, "origin": {"kind": "peer", "from": REVIEWER}, "message": {"role": "user", "content": "Looks good, ship it"}},
        {"type": "user", "timestamp": at(10), "origin": {"kind": "task-notification"}, "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
        human(11, "<command-name>/review</command-name><command-message>review</command-message><command-args>PR 12</command-args>"),
        {"type": "assistant", "isSidechain": True, "timestamp": at(12), "message": {"id": "s1", "content": [{"type": "text", "text": "inline sidechain"}], "stop_reason": "end_turn"}},
        reply(13, "m6", [{"type": "text", "text": "Done."}], stop="end_turn"),
    ])
    sub = lines(session.parent / CLAUDE / "subagents" / "agent-a1b2c3.jsonl", [
        {"type": "user", "timestamp": at(8), "isSidechain": True, "message": {"role": "user", "content": "Review the API endpoints"}},
        {"type": "assistant", "timestamp": at(9), "isSidechain": True, "message": {"id": "x1", "content": [{"type": "text", "text": "Found one issue"}], "stop_reason": "end_turn"}},
    ])
    sub.with_name("agent-a1b2c3.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Review the API"}), encoding="utf-8")
    sessions = tmp_path / "desktop" / "Claude-Data" / "claude_x" / "claude-code-sessions" / "acct" / "org"
    sessions.mkdir(parents=True)
    (sessions / f"{DESKTOP}.json").write_text(json.dumps({"sessionId": DESKTOP, "cliSessionId": CLAUDE, "title": "API session"}), encoding="utf-8")
    (sessions / f"{REVIEWER}.json").write_text(json.dumps({"sessionId": REVIEWER, "cliSessionId": str(uuid.uuid4()), "title": "Reviewer session"}), encoding="utf-8")

    codex = tmp_path / "codex"
    def text(kind, value):
        return {"type": kind, "text": value}
    def item(minute, payload):
        return {"type": "response_item", "timestamp": at(minute), "payload": payload}
    def envelope(kind, task, sender, payload=""):
        return text("input_text", f"Message Type: {kind}\nTask name: {task}\nSender: {sender}\nPayload:\n{payload}")
    sealed = {"type": "encrypted_content", "encrypted_content": "gAAAAABsealed"}
    lines(codex / "sessions" / "2026" / "11" / "01" / f"rollout-2026-11-01T08-00-00-{CODEX}.jsonl", [
        {"type": "session_meta", "timestamp": at(0), "payload": {"id": CODEX, "session_id": CODEX, "base_instructions": "x" * 1000}},
        {"type": "turn_context", "timestamp": at(0), "payload": {"model": "gpt-6.1-sol"}},
        {"type": "event_msg", "timestamp": at(1), "payload": {"type": "task_started"}},
        item(1, {"type": "message", "role": "developer", "content": [text("input_text", "developer rules")]}),
        item(1, {"type": "message", "role": "user", "content": [text("input_text", "# AGENTS.md instructions for C:\\repo"), text("input_text", "<environment_context>\n</environment_context>")]}),
        item(2, {"type": "message", "role": "user", "content": [text("input_text", "Review the release")]}),
        item(3, {"type": "function_call", "namespace": "collaboration", "name": "spawn_agent", "arguments": json.dumps({"task": "Check the pipeline"})}),
        item(4, {"type": "function_call", "namespace": "collaboration", "name": "send_message", "arguments": json.dumps({"target": "pipeline_check", "message": "sent side"})}),
        item(5, {"type": "agent_message", "author": "/root/pipeline_check", "recipient": "/root", "content": [envelope("MESSAGE", "/root", "/root/pipeline_check"), sealed]}),
        item(6, {"type": "agent_message", "author": "/root/pipeline_check", "recipient": "/root", "content": [envelope("FINAL_ANSWER", "/root", "/root/pipeline_check", "Pipeline is green"), sealed]}),
        item(7, {"type": "message", "role": "assistant", "phase": "final_answer", "content": [text("output_text", "Release looks good")]}),
        {"type": "event_msg", "timestamp": at(7), "payload": {"type": "task_complete"}},
    ])
    lines(codex / "archived_sessions" / f"rollout-2026-11-01T08-03-00-{CHILD}.jsonl", [
        {"type": "session_meta", "timestamp": at(3), "payload": {"id": CHILD, "session_id": CODEX, "agent_nickname": "Lorentz", "agent_path": "/root/pipeline_check", "parent_thread_id": CODEX}},
        item(4, {"type": "agent_message", "author": "/root", "recipient": "/root/pipeline_check", "content": [envelope("NEW_TASK", "/root/pipeline_check", "/root"), sealed]}),
        item(5, {"type": "custom_tool_call", "name": "exec", "input": "git status\nls"}),
    ])
    lines(codex / "archived_sessions" / "rollout-2026-11-01T09-00-00-01a19999-0000-7000-8000-000000000000.jsonl", [
        {"type": "session_meta", "timestamp": at(30), "payload": {"id": "01a19999-0000-7000-8000-000000000000", "session_id": "01a19999-0000-7000-8000-000000000000"}},
    ])
    (codex / "session_index.jsonl").write_text(json.dumps({"id": CODEX, "thread_name": "Draft name"}) + "\n" + json.dumps({"id": CODEX, "thread_name": "Release review"}) + "\n", encoding="utf-8")
    return TranscriptRoots((claude,), (tmp_path / "desktop" / "Claude-Data",), (codex,))


@pytest.fixture
def client(tmp_path, roots):
    app = create_app(tmp_path / "data", testing=True, transcript_roots=roots)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def write(c, method, path, payload=None, **kwargs):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **kwargs.pop("headers", {})}
    return c.request(method, "/api/v1" + path, headers=headers, json=payload, **kwargs)


def chat(c, owner_type, owner_id, chat_id, title, **fields):
    r = write(c, "POST", "/chats", {"ownerType": owner_type, "ownerId": owner_id, "chatId": chat_id, "title": title, **fields})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_chat_list_shows_who_works_on_what_across_roadmaps_and_items(client):
    roadmap = write(client, "POST", "/roadmaps", {"title": "Platform", "steps": [{"title": "API"}, {"title": "Docs"}]}).json()
    api = roadmap["steps"][0]["id"]
    work = write(client, "POST", "/work-items", {"title": "Release 3.2"}).json()
    claude = chat(client, "roadmap", roadmap["id"], CLAUDE, "API builder", provider="Anthropic", model="claude-opus-5-5", stepIds=[api])
    codex = chat(client, "item", work["id"], CODEX.upper(), "Release review", provider="OpenAI")
    untracked = chat(client, "item", work["id"], "codex-7", "Untracked chat")

    items = {c["id"]: c for c in client.get("/api/v1/chats").json()["items"]}
    assert items[claude["id"]]["owner"] == {"type": "roadmap", "id": roadmap["id"], "title": "Platform", "archived": False}
    assert items[claude["id"]]["steps"] == [{"id": api, "title": "API", "status": "open", "retired": False}]
    summary = items[claude["id"]]["transcript"]
    assert summary["found"] and summary["source"] == "claude" and summary["lastActivityAt"] == at(13)
    assert [(t["name"], t["title"], t["task"], t["parent"]) for t in summary["threads"]] == [
        ("Claude", "Build the API", "", None), ("Explore", "", "Review the API", CLAUDE)]
    assert summary["threads"][0]["model"] == "claude-opus-5-5"
    # Upper-case IDs still find the transcript; the session index names the thread.
    codex_summary = items[codex["id"]]["transcript"]
    assert codex_summary["source"] == "codex" and codex_summary["state"] == "idle"
    assert [(t["name"], t["title"], t["task"]) for t in codex_summary["threads"]] == [
        ("Codex", "Release review", ""), ("Lorentz", "", "pipeline_check")]
    assert items[codex["id"]]["owner"]["title"] == "Release 3.2"
    assert items[untracked["id"]]["transcript"] == {"found": False, "source": None, "lastActivityAt": None, "state": "idle", "threads": []}

    write(client, "POST", f"/chats/{untracked['id']}/archive", {"revision": untracked["revision"]})
    assert untracked["id"] not in {c["id"] for c in client.get("/api/v1/chats").json()["items"]}
    assert [c["id"] for c in client.get("/api/v1/chats?archived=true").json()["items"]] == [untracked["id"]]


def test_each_chat_names_the_roadmaps_its_record_sits_within(client):
    platform = write(client, "POST", "/roadmaps", {"title": "Platform"}).json()
    api = write(client, "POST", "/roadmaps", {"title": "API", "parentId": platform["id"], "steps": [{"title": "Auth"}]}).json()
    on_step = write(client, "POST", "/work-items", {"title": "Token refresh", "stepId": api["steps"][0]["id"]}).json()
    on_roadmap = write(client, "POST", "/work-items", {"title": "Rate limits", "roadmapId": api["id"]}).json()
    loose = write(client, "POST", "/work-items", {"title": "Laptop setup"}).json()
    chats = {
        "top": chat(client, "roadmap", platform["id"], CLAUDE, "Platform lead"),
        "nested": chat(client, "roadmap", api["id"], "c-1", "API lead"),
        "step": chat(client, "item", on_step["id"], "c-2", "Refresh tokens"),
        "item": chat(client, "item", on_roadmap["id"], "c-3", "Limit requests"),
        "loose": chat(client, "item", loose["id"], "c-4", "Install tools"),
    }
    items = {c["id"]: c for c in client.get("/api/v1/chats").json()["items"]}
    top, nested = {"id": platform["id"], "title": "Platform", "archived": False}, {"id": api["id"], "title": "API", "archived": False}
    assert {name: items[c["id"]]["roadmaps"] for name, c in chats.items()} == {
        "top": [top], "nested": [top, nested], "step": [top, nested], "item": [top, nested], "loose": []}

    # An archived roadmap still holds its chats; a parent chain that loops stops before repeating.
    write(client, "POST", f"/roadmaps/{platform['id']}/archive", {"revision": platform["revision"]})
    archived = {c["id"]: c["roadmaps"] for c in client.get("/api/v1/chats").json()["items"]}
    assert [r["archived"] for r in archived[chats["item"]["id"]]] == [True, False]
    with client.app.state.db.transaction() as conn:
        conn.execute("UPDATE roadmaps SET parent_id=? WHERE id=?", (api["id"], platform["id"]))
    looped = {c["id"]: c["roadmaps"] for c in client.get("/api/v1/chats").json()["items"]}
    assert [r["title"] for r in looped[chats["top"]["id"]]] == ["API", "Platform"]
    assert [r["title"] for r in looped[chats["nested"]["id"]]] == ["Platform", "API"]


def test_a_claude_transcript_shows_prompts_replies_tools_and_messages_between_agents(client):
    work = write(client, "POST", "/work-items", {"title": "API"}).json()
    record = chat(client, "item", work["id"], CLAUDE, "API builder")
    body = client.get(f"/api/v1/chats/{record['id']}/transcript").json()
    assert body["chat"]["id"] == record["id"] and body["found"] and not body["more"]
    shown = [(m["kind"], m["author"], m.get("recipient"), m.get("text")) for m in body["messages"]]
    assert shown == [
        ("user", "You", None, "Please build the API"),
        ("user", "You", None, "and add tests"),
        ("assistant", "Claude", None, "On it."),
        ("tools", "Claude", None, None),
        ("agent", "Claude", "Reviewer session", "API is ready for review"),
        ("tools", "Claude", None, None),
        ("agent", "Claude", "Explore", "Review the API endpoints"),
        ("agent", "Reviewer session", "Claude", "Looks good, ship it"),
        ("assistant", "Explore", None, "Found one issue"),
        ("user", "You", None, "/review PR 12"),
        ("assistant", "Claude", None, "Done."),
    ]
    tools = [m for m in body["messages"] if m["kind"] == "tools"]
    assert tools[0]["count"] == 2 and tools[0]["tools"] == [{"name": "Bash", "summary": "Run tests"}, {"name": "Read", "summary": "api.py"}]
    assert tools[1]["tools"] == [{"name": "Agent", "summary": "Review the API"}]
    every = json.dumps(body)
    assert "private context" not in every and "inline sidechain" not in every and "tool output" not in every
    assert body["messages"][6]["authorThread"] == CLAUDE and body["messages"][8]["authorThread"] == "a1b2c3"

    # One agent alone, and the latest messages first when there are more.
    sub = client.get(f"/api/v1/chats/{record['id']}/transcript?thread=a1b2c3").json()["messages"]
    assert [m["text"] for m in sub] == ["Review the API endpoints", "Found one issue"]
    page = client.get(f"/api/v1/chats/{record['id']}/transcript?limit=2").json()
    assert page["more"] and page["total"] == 11 and [m["text"] for m in page["messages"]] == ["/review PR 12", "Done."]
    assert client.get(f"/api/v1/chats/{record['id']}/transcript?limit=0").status_code == 422

    # A tracked chat's own title names it in another chat's messages.
    chat(client, "item", work["id"], REVIEWER, "Code reviewer")
    named = client.get(f"/api/v1/chats/{record['id']}/transcript").json()["messages"]
    assert named[4]["recipient"] == "Code reviewer" and named[7]["author"] == "Code reviewer"


def test_desktop_session_ids_read_the_claude_code_session_they_run(client):
    work = write(client, "POST", "/work-items", {"title": "API"}).json()
    record = chat(client, "item", work["id"], DESKTOP, "API builder")
    body = client.get(f"/api/v1/chats/{record['id']}/transcript").json()
    assert body["source"] == "claude" and body["messages"][0]["text"] == "Please build the API"
    # The transcript's own title wins over the desktop record's.
    assert body["threads"][0]["title"] == "Build the API"


def test_a_codex_thread_and_the_agents_it_started_read_as_one_conversation(client):
    work = write(client, "POST", "/work-items", {"title": "Release"}).json()
    record = chat(client, "item", work["id"], CODEX, "Release review")
    body = client.get(f"/api/v1/chats/{record['id']}/transcript").json()
    shown = [(m["kind"], m["author"], m.get("recipient"), m.get("text")) for m in body["messages"]]
    assert shown == [
        ("user", "You", None, "Review the release"),
        ("tools", "Codex", None, None),
        ("agent", "Codex", "Lorentz", ""),
        ("agent", "Lorentz", "Codex", ""),
        ("tools", "Lorentz", None, None),
        ("agent", "Lorentz", "Codex", "Pipeline is green"),
        ("assistant", "Codex", None, "Release looks good"),
    ]
    # Codex's header is dropped; a message it kept only encrypted says so, and readable text always shows.
    assert [(m["messageType"], m.get("encrypted")) for m in body["messages"] if m["kind"] == "agent"] == [
        ("NEW_TASK", True), ("MESSAGE", True), ("FINAL_ANSWER", None)]
    assert body["messages"][1]["tools"] == [{"name": "spawn_agent", "summary": "Check the pipeline"}]
    assert body["messages"][4]["tools"] == [{"name": "exec", "summary": "git status ls"}]
    assert body["threads"][0]["model"] == "gpt-6.1-sol" and body["threads"][1]["parent"] == CODEX
    for hidden in ("developer rules", "sent side", "Message Type", "gAAAA"):
        assert hidden not in json.dumps(body), hidden

    # One agent's view includes what it sent, though the agent it sent to recorded it.
    lorentz = client.get(f"/api/v1/chats/{record['id']}/transcript?thread={CHILD}").json()["messages"]
    assert [(m["kind"], m.get("text")) for m in lorentz] == [("agent", ""), ("agent", ""), ("tools", None), ("agent", "Pipeline is green")]
    assert client.get(f"/api/v1/chats/{record['id']}/transcript?thread=nobody").json()["messages"] == []

    # A subagent's own ID shows that agent alone.
    alone = chat(client, "item", work["id"], CHILD, "Pipeline check")
    assert [t["name"] for t in client.get(f"/api/v1/chats/{alone['id']}/transcript").json()["threads"]] == ["Lorentz"]


def test_new_lines_are_read_as_they_are_written_and_an_open_turn_counts_as_working(client, roots):
    work = write(client, "POST", "/work-items", {"title": "API"}).json()
    record = chat(client, "item", work["id"], CLAUDE, "API builder")
    path = roots.claude_projects[0] / "C--repo" / f"{CLAUDE}.jsonl"
    url = f"/api/v1/chats/{record['id']}/transcript"
    assert client.get(url).json()["state"] == "idle"

    late = reply(20, "m7", [{"type": "text", "text": "Starting the docs"}])
    lines(path, [human(19, "Now the docs")], partial=late)
    body = client.get(url).json()
    assert [m["text"] for m in body["messages"][-1:]] == ["Now the docs"]
    assert body["state"] == "working" and body["threads"][0]["state"] == "working"
    # The half-written line is read once it is complete.
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(late)[-5:] + "\n")
    assert client.get(url).json()["messages"][-1]["text"] == "Starting the docs"
    listed = next(c for c in client.get("/api/v1/chats").json()["items"] if c["id"] == record["id"])
    assert listed["transcript"]["state"] == "working"

    # An open turn nobody has touched for a while is idle, not working.
    stale = time.time() - 3600
    os.utime(path, (stale, stale))
    assert client.get(url).json()["state"] == "idle"


def test_a_summary_reads_only_the_end_of_an_unopened_transcript(roots):
    reader = TranscriptReader(roots)
    path = roots.claude_projects[0] / "C--repo" / f"{CLAUDE}.jsonl"
    lines(path, [human(30, "x" * 1000)] * 700 + [reply(40, "m9", [{"type": "text", "text": "Still going"}])])
    summary = reader.summary(CLAUDE)
    assert summary["lastActivityAt"] == at(40) and summary["state"] == "working"
    assert path not in reader.cache


def test_transcripts_are_for_the_person_only_and_ids_never_name_paths(client):
    work = write(client, "POST", "/work-items", {"title": "API"}).json()
    record = chat(client, "item", work["id"], CLAUDE, "API builder")
    cred = write(client, "POST", "/credentials", {"label": "Agent"}).json()
    agent = {"Authorization": "Bearer " + cred["token"]}
    assert client.get("/api/v1/chats", headers=agent).status_code == 403
    assert client.get(f"/api/v1/chats/{record['id']}/transcript", headers=agent).json()["error"]["code"] == "agent_scope"
    assert client.get(f"/api/v1/chats/{record['id']}", headers=agent).status_code == 200
    assert client.get(f"/api/v1/chats/{uuid.uuid4()}/transcript").status_code == 404

    for chat_id in ("../C--repo/" + CLAUDE, f"C--repo/{CLAUDE}", f"*{CLAUDE[1:]}", CLAUDE + ".jsonl", "local_../../x"):
        probe = chat(client, "item", work["id"], chat_id, "Probe")
        body = client.get(f"/api/v1/chats/{probe['id']}/transcript").json()
        assert not body["found"] and body["messages"] == [], chat_id


def test_testing_apps_never_read_this_machines_transcripts(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN):
        assert app.state.transcripts.roots == TranscriptRoots()


def test_a_line_that_cannot_be_parsed_is_skipped_not_fatal(client, roots):
    work = write(client, "POST", "/work-items", {"title": "API"}).json()
    record = chat(client, "item", work["id"], CLAUDE, "API builder")
    path = roots.claude_projects[0] / "C--repo" / f"{CLAUDE}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write("[" * 200_000 + "\n")
    lines(path, [human(30, "After the bad line")])
    assert client.get("/api/v1/chats").status_code == 200
    body = client.get(f"/api/v1/chats/{record['id']}/transcript").json()
    assert body["messages"][-1]["text"] == "After the bad line"


def test_an_oversized_line_is_passed_over_once_it_is_complete(roots, monkeypatch):
    monkeypatch.setattr(transcripts, "LINE_BYTES", 1000)
    reader = TranscriptReader(roots)
    path = roots.claude_projects[0] / "C--repo" / f"{CLAUDE}.jsonl"
    huge = json.dumps(human(30, "x" * 5000))
    with path.open("a", encoding="utf-8") as f:
        f.write(huge[:3000])
    assert reader.messages(CLAUDE, limit=50)["messages"][-1]["text"] == "Done."
    with path.open("a", encoding="utf-8") as f:
        f.write(huge[3000:] + "\n")
    lines(path, [human(31, "After the long line")])
    texts = [m.get("text", "") for m in reader.messages(CLAUDE, limit=50)["messages"]]
    assert texts[-1] == "After the long line" and not any(t.startswith("xxx") for t in texts)


def test_each_transcript_keeps_its_latest_messages_and_counts_the_rest(roots, monkeypatch):
    monkeypatch.setattr(transcripts, "MESSAGE_LIMIT", 3)
    reader = TranscriptReader(roots)
    body = reader.messages(CLAUDE, limit=2)
    assert [m["text"] for m in body["messages"]] == ["/review PR 12", "Done."]
    # Eleven messages were written, and the subagent's two are kept in full.
    assert body["total"] == 11 and body["more"]
    assert [m["text"] for m in reader.messages(CLAUDE, limit=3, thread=CLAUDE)["messages"]] == ["Looks good, ship it", "/review PR 12", "Done."]
    # Only kept messages can be paged to; the rest are counted, not offered.
    kept = reader.messages(CLAUDE, limit=5)
    assert len(kept["messages"]) == 5 and kept["total"] == 11 and not kept["more"]


def test_one_unreadable_transcript_does_not_fail_the_list(client, monkeypatch):
    work = write(client, "POST", "/work-items", {"title": "API"}).json()
    chat(client, "item", work["id"], CLAUDE, "API builder")
    codex = chat(client, "item", work["id"], CODEX, "Release review")
    reader = client.app.state.transcripts
    original = reader.threads

    def threads(chat_id):
        if chat_id == CLAUDE:
            raise PermissionError("denied")
        return original(chat_id)

    monkeypatch.setattr(reader, "threads", threads)
    items = {c["chatId"]: c["transcript"] for c in client.get("/api/v1/chats").json()["items"]}
    assert items[CLAUDE]["error"] and not items[CLAUDE]["found"]
    assert items[codex["chatId"]]["found"] and "error" not in items[codex["chatId"]]


def test_agents_are_refused_transcripts_with_a_trailing_slash_too(client):
    work = write(client, "POST", "/work-items", {"title": "API"}).json()
    record = chat(client, "item", work["id"], CLAUDE, "API builder")
    agent = {"Authorization": "Bearer " + write(client, "POST", "/credentials", {"label": "Agent"}).json()["token"]}
    for path in ("/api/v1/chats/", f"/api/v1/chats/{record['id']}/transcript/"):
        response = client.get(path, headers=agent, follow_redirects=False)
        assert response.status_code == 403 and response.json()["error"]["code"] == "agent_scope", path


def test_a_tool_call_too_nested_to_decode_is_still_listed(client, roots):
    work = write(client, "POST", "/work-items", {"title": "Release"}).json()
    record = chat(client, "item", work["id"], CODEX, "Release review")
    root = next((roots.codex[0] / "sessions").rglob(f"*{CODEX}.jsonl"))
    nested = '{"a":' + "[" * 200_000
    lines(root, [{"type": "response_item", "timestamp": at(8), "payload": {"type": "function_call", "name": "shell", "arguments": nested}}])
    assert client.get("/api/v1/chats").status_code == 200
    last = client.get(f"/api/v1/chats/{record['id']}/transcript").json()["messages"][-1]
    assert last["kind"] == "tools" and last["tools"][0]["name"] == "shell"
