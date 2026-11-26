"""Follow a real loopback event stream between disposable, independently registered agents."""

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager

import httpx
import uvicorn
from test_registration import bearer, issue, register, write
from worklog.app import create_app


@contextmanager
def running_worklog(tmp_path, monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        monkeypatch.setenv("WORKLOG_PORT", str(port))
        app = create_app(tmp_path / "push-service", testing=True)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started
            yield f"http://127.0.0.1:{port}", app
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def sponsor_for(url):
    with httpx.Client(base_url=url, trust_env=False, headers={"Origin": url}) as person:
        session = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        session.raise_for_status()
        person.headers["X-CSRF-Token"] = session.json()["csrfToken"]
        return issue(person)["token"]


def agent(api, sponsor, provider):
    chat_id = str(uuid.uuid4())
    registered, token = register(api, sponsor, provider.title(), chatId=chat_id)
    registered.raise_for_status()
    who = {"chatId": chat_id, "agentId": registered.json()["agentId"], "token": token}
    binding = post(api, who, "/push/enroll", {"chatId": chat_id, "provider": provider})
    who["enrollmentId"] = binding["id"]
    return who


def post(api, who, path, payload):
    result = write(api, "POST", path, payload, headers=bearer(who["token"]))
    result.raise_for_status()
    return result.json()


@contextmanager
def connected(api, who):
    with api.stream("GET", "/api/v1/push/stream", params={"enrollmentId": who["enrollmentId"]},
                    headers=bearer(who["token"])) as response:
        response.raise_for_status()
        lines = response.iter_lines()
        assert next(lines) == ": connected"
        assert next(lines) == ""
        yield lines


def frame(lines):
    name = ""
    for line in lines:
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            return name, json.loads(line.removeprefix("data: "))
    raise AssertionError("stream ended before the expected event")


def test_two_way_live_push_and_offline_task_delivery(tmp_path, monkeypatch):
    with running_worklog(tmp_path, monkeypatch) as (url, _), httpx.Client(base_url=url, timeout=8, trust_env=False) as api:
        sponsor = sponsor_for(url)
        claude, codex = agent(api, sponsor, "claude"), agent(api, sponsor, "codex")
        ticket = post(api, codex, "/tickets", {"title": "Verify a change", "kind": "task"})
        post(api, claude, "/push/subscriptions", {"enrollmentId": claude["enrollmentId"], "kind": "ticket",
                                                 "ref": ticket["key"], "enabled": True})
        with connected(api, codex) as codex_stream:
            request = post(api, claude, "/discussions", {
                "text": "Please report the result here.",
                "contexts": [{"type": "ticket", "id": ticket["id"]}],
                "acknowledgement": {"targets": [{"kind": "agent", "id": codex["agentId"]}]},
            })
            message_id = request["message"]["id"]
            name, delivery = frame(codex_stream)
            assert name == "delivery" and delivery["messageId"] == message_id
            assert "Please report" not in json.dumps(delivery)
            attempt = {"enrollmentId": codex["enrollmentId"], "attemptId": str(uuid.uuid4())}
            path = "/push/deliveries/" + delivery["id"]
            claim_headers = {**bearer(codex["token"]), "Idempotency-Key": str(uuid.uuid4())}
            claimed = write(api, "POST", path + "/claim", attempt, headers=claim_headers)
            assert claimed.json()["acquired"] is True
            receipt_headers = {**bearer(codex["token"]), "Idempotency-Key": str(uuid.uuid4())}
            receipt = {**attempt, "outcome": "queued", "resumeWarning": True}
            queued = write(api, "POST", path + "/receipt", receipt, headers=receipt_headers).json()
            assert queued["state"] == "queued" and queued["resumeWarning"] is True
            status = api.get("/api/v1/push/status?box=sent", headers=bearer(claude["token"])).json()
            assert status["items"][0]["recipientAgentId"] == codex["agentId"]
            assert status["items"][0]["resumeWarning"] is True
            assert write(api, "POST", path + "/receipt", receipt, headers=receipt_headers).json()["attemptCount"] == 1
            replay = write(api, "POST", path + "/claim", attempt, headers=claim_headers)
            assert replay.json()["acquired"] is False and replay.json()["delivery"]["state"] == "queued"
            # Queue acceptance leaves the original acknowledgement for the recipient agent.
            message = api.get("/api/v1/discussions/messages/" + message_id, headers=bearer(claude["token"]))
            assert message.json()["request"]["targets"][0]["delivery"] == "pending"

        topic = request["discussion"]["id"]
        reply = post(api, codex, f"/discussions/{topic}/messages",
                     {"text": "Verification complete.", "replyToMessageId": message_id})
        assert reply["replyToMessageId"] == message_id
        done = write(api, "PATCH", "/tickets/" + ticket["key"], {"revision": ticket["revision"], "status": "done",
                     "resolution": "Verified"}, headers=bearer(codex["token"]))
        done.raise_for_status()
        # Both commits happened while Claude was disconnected. Reading another agent's stream is refused.
        wrong = api.get("/api/v1/push/stream", params={"enrollmentId": claude["enrollmentId"]},
                        headers=bearer(codex["token"]))
        assert wrong.status_code in {403, 404}
        with connected(api, claude) as claude_stream:
            first, second = frame(claude_stream), frame(claude_stream)
            assert first[0] == second[0] == "delivery"
            assert first[1]["messageId"] == reply["id"]
            assert second[1]["subjectId"] == ticket["id"]
            assert first[1]["seq"] < second[1]["seq"]
            post(api, claude, "/push/disable", {"enrollmentId": claude["enrollmentId"]})
            assert frame(claude_stream)[0] == "closed"


def test_claude_channel_cli_delivers_a_codex_request(tmp_path, monkeypatch):
    # Exercise the idle connector's normal heartbeat shutdown without waiting 20 seconds in CI.
    monkeypatch.setattr("worklog.push_api.HEARTBEAT_SECONDS", 0.5)
    from worklog.cli import AgentClient
    from worklog.push_cli import run_receiver

    with running_worklog(tmp_path, monkeypatch) as (url, _), httpx.Client(base_url=url, trust_env=False) as api:
        sponsor = sponsor_for(url)
        claude, codex = agent(api, sponsor, "claude"), agent(api, sponsor, "codex")
        codex_notices: queue.Queue = queue.Queue()
        receiver_result: queue.Queue = queue.Queue(maxsize=1)
        receiver_stop = threading.Event()
        codex_connected = threading.Event()
        codex_client = AgentClient(url, codex["token"])
        open_stream = codex_client.open_push_stream

        def observe_stream(path):
            response = open_stream(path)
            codex_connected.set()
            return response

        codex_client.open_push_stream = observe_stream

        def queue_codex_notice(notice, metadata):
            codex_notices.put((notice, metadata))
            return "queued"

        def run_codex_receiver():
            try:
                receiver_result.put(run_receiver(codex_client, codex["enrollmentId"], queue_codex_notice,
                                                 stop_event=receiver_stop, max_events=1))
            except BaseException as exc:
                receiver_result.put(exc)

        receiver = threading.Thread(target=run_codex_receiver, daemon=True)
        receiver.start()
        # Start the actual Worklog MCP command with a disposable identity and pipes in place of Claude.
        command = [sys.executable, "-m", "worklog.cli", "agent", "--url", url, "push", "channel",
                   "--enrollment-id", claude["enrollmentId"], "--chat", claude["chatId"]]
        env = {**os.environ, "WORKLOG_AGENT_TOKEN": claude["token"], "CLAUDE_CODE_SESSION_ID": claude["chatId"]}
        try:
            assert codex_connected.wait(timeout=10), "Codex receiver did not connect to the Worklog stream"
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, text=True, encoding="utf-8") as channel:
                assert channel.stdin and channel.stdout
                output: queue.Queue[str] = queue.Queue()
                reader = threading.Thread(target=lambda: output_lines(channel.stdout, output), daemon=True)
                reader.start()

                def send(payload):
                    channel.stdin.write(json.dumps({"jsonrpc": "2.0", **payload}) + "\n")
                    channel.stdin.flush()

                try:
                    send({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25",
                          "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
                    init = json.loads(output.get(timeout=15))
                    assert "claude/channel" in init["result"]["capabilities"]["experimental"]
                    send({"method": "notifications/initialized"})
                    ask = post(api, codex, "/discussions", {"text": "This body stays in Worklog.",
                               "acknowledgement": {"targets": [{"kind": "agent", "id": claude["agentId"]}]}})
                    notice = json.loads(output.get(timeout=15))
                    assert notice["method"] == "notifications/claude/channel"
                    assert ask["message"]["id"] in json.dumps(notice["params"])
                    assert "This body stays in Worklog" not in json.dumps(notice)
                    # A successful write to the provider's pipe precedes its durable receipt.
                    deadline = time.monotonic() + 8
                    while True:
                        status = api.get("/api/v1/push/status", headers=bearer(codex["token"]),
                                         params={"box": "sent"})
                        delivery = next((row for row in status.json()["items"]
                                         if row["messageId"] == ask["message"]["id"]), None)
                        if delivery and delivery["state"] == "submitted" or time.monotonic() >= deadline:
                            break
                        time.sleep(0.05)
                    assert delivery and delivery["state"] == "submitted"

                    discussion_id = ask["discussion"]["id"]
                    reply = post(api, claude, f"/discussions/{discussion_id}/messages",
                                 {"text": "This reply body stays in Worklog.",
                                  "replyToMessageId": ask["message"]["id"]})
                    assert reply["replyToMessageId"] == ask["message"]["id"]
                    reply_notice, metadata = codex_notices.get(timeout=15)
                    assert reply_notice == (
                        f"Worklog reply. Read message {reply['id']} in discussion {discussion_id}.")
                    assert metadata["kind"] == "discussion.reply"
                    assert metadata["discussion_id"] == discussion_id and metadata["message_id"] == reply["id"]
                    assert "This reply body stays in Worklog" not in reply_notice
                    assert receiver_result.get(timeout=15) == 1
                    receiver.join(timeout=3)
                    assert not receiver.is_alive(), "Codex receiver did not stop after its queued receipt"

                    status = api.get("/api/v1/push/status", headers=bearer(codex["token"]),
                                     params={"box": "received"})
                    receipt = next((row for row in status.json()["items"] if row["messageId"] == reply["id"]), None)
                    assert receipt and receipt["state"] == "queued" and receipt["attemptCount"] == 1
                    assert receipt["id"] == metadata["delivery_id"]

                    original = api.get(f"/api/v1/discussions/messages/{ask['message']['id']}",
                                       headers=bearer(codex["token"]))
                    original.raise_for_status()
                    request = original.json()["request"]
                    assert request["state"] == "pending" and request["receipts"] == []
                    assert len(request["targets"]) == 1
                    assert request["targets"][0]["acknowledged"] is False
                    assert request["targets"][0]["acknowledgedAt"] is None
                    channel.stdin.close()
                    assert channel.wait(timeout=8) == 0
                finally:
                    if channel.poll() is None:
                        channel.kill()
                    reader.join(timeout=2)
        finally:
            receiver_stop.set()
            receiver.join(timeout=5)
            assert not receiver.is_alive(), "Codex receiver did not shut down within the bounded wait"


def output_lines(pipe, output):
    for line in pipe:
        output.put(line)


def test_retiring_the_recipient_closes_the_senders_outstanding_deliveries(tmp_path, monkeypatch):
    with running_worklog(tmp_path, monkeypatch) as (url, _), httpx.Client(base_url=url, trust_env=False) as api:
        sponsor = sponsor_for(url)
        sender, recipient = agent(api, sponsor, "codex"), agent(api, sponsor, "claude")
        with connected(api, recipient) as stream:
            request = {"text": "Report the next checkpoint.", "acknowledgement": {
                "targets": [{"kind": "agent", "id": recipient["agentId"]}]}}
            first = post(api, sender, "/discussions", request)["message"]["id"]
            delivery = frame(stream)[1]
            post(api, recipient, "/push/deliveries/" + delivery["id"] + "/claim",
                 {"enrollmentId": recipient["enrollmentId"], "attemptId": str(uuid.uuid4())})
            second = post(api, sender, "/discussions", request)["message"]["id"]
            assert frame(stream)[1]["messageId"] == second
            post(api, recipient, "/agent-registrations/retire", {})
            assert frame(stream)[0] == "closed"
        status = api.get("/api/v1/push/status", params={"box": "sent"}, headers=bearer(sender["token"]))
        assert {row["messageId"]: row["state"] for row in status.json()["items"]} == {
            first: "uncertain", second: "cancelled"}


def test_busy_worklog_still_sends_retry_heartbeats(tmp_path, monkeypatch):
    monkeypatch.setattr("worklog.push_api.HEARTBEAT_SECONDS", 0.2)
    with running_worklog(tmp_path, monkeypatch) as (url, app), httpx.Client(base_url=url, trust_env=False) as api:
        recipient = agent(api, sponsor_for(url), "codex")
        stop = threading.Event()

        def wake_on_unrelated_commits():
            # Simulate commit notifications from work in other enrollments with no new rows here.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not stop.wait(0.015):
                app.state.db.commits.notify()

        with connected(api, recipient) as stream:
            writer = threading.Thread(target=wake_on_unrelated_commits)
            writer.start()
            try:
                assert next(stream) == ": keepalive"
                assert writer.is_alive(), "the heartbeat was postponed until unrelated activity stopped"
            finally:
                stop.set()
                writer.join(timeout=2)
