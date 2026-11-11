"""Agent interface contracts without a personal server or database."""

import io
import json
import threading
import time
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from worklog import cli

DISCUSSION = "11111111-1111-4111-8111-111111111111"
MESSAGE = "22222222-2222-4222-8222-222222222222"
ROLE = "33333333-3333-4333-8333-333333333333"
AGENT = "44444444-4444-4444-8444-444444444444"
SLOT = "55555555-5555-4555-8555-555555555555"
TOKEN = "secret-discussion-token"


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", TOKEN)
    captured = []

    def respond(request, **kwargs):
        captured.append((request, kwargs))
        return io.BytesIO(b'{"id":"result"}')

    monkeypatch.setattr(cli, "_open", respond)
    return captured


def command(*args):
    return ["agent", "discussions", *args]


@pytest.mark.parametrize("args,route,query", [
    (["participants"], "/discussions/participants", {}),
    (["participant", AGENT], f"/discussions/participants/{AGENT}", {}),
    (["participant", "local-user"], "/discussions/participants/local-user", {}),
    (["avatars"], "/discussions/avatars", {}),
    (["list", "--context", f"item:{DISCUSSION}", "--repo", "one & two", "--q", "a/b", "--view", "awaiting", "--limit", "3", "--offset", "4"],
     "/discussions", {"contextType": ["item"], "contextId": [DISCUSSION], "repo": ["one & two"], "q": ["a/b"], "view": ["awaiting"], "limit": ["3"], "offset": ["4"]}),
    (["list", "--pinned", "--context", "ticket:T-12"], "/discussions",
     {"pinned": ["true"], "contextType": ["ticket"], "contextId": ["T-12"], "view": ["all"], "limit": ["50"], "offset": ["0"]}),
    (["list", "--context", f"repository:{DISCUSSION}"], "/discussions",
     {"contextType": ["repository"], "contextId": [DISCUSSION], "view": ["all"], "limit": ["50"], "offset": ["0"]}),
    (["read", DISCUSSION], f"/discussions/{DISCUSSION}", {"limit": ["50"], "offset": ["0"]}),
    (["read", MESSAGE, "--message"], f"/discussions/messages/{MESSAGE}", {}),
    (["changes", "--cursor", "dataset:cursor+token", "--context", f"roadmap:{DISCUSSION}"],
     "/discussions/changes", {"cursor": ["dataset:cursor+token"], "contextType": ["roadmap"], "contextId": [DISCUSSION], "limit": ["50"]}),
    (["resume", "--context", f"step:{DISCUSSION}"], "/discussions/resume",
     {"contextType": ["step"], "contextId": [DISCUSSION], "limit": ["50"], "offset": ["0"]}),
    (["roles", "list", "--context", f"item:{DISCUSSION}"], "/discussions/roles",
     {"contextType": ["item"], "contextId": [DISCUSSION], "limit": ["50"], "offset": ["0"]}),
    (["roles", "list", "--limit", "3", "--offset", "6"], "/discussions/roles",
     {"limit": ["3"], "offset": ["6"]}),
])
def test_bounded_reads_encode_context_and_discovery_cursor(transport, args, route, query):
    assert cli.main(command(*args)) == 0
    request, _ = transport[-1]
    url = urlsplit(request.full_url)
    assert url.path == "/api/v1" + route
    assert parse_qs(url.query) == query
    assert request.method == "GET"


def test_rich_post_retains_acknowledgement_and_context(transport, tmp_path):
    payload = {"text": "Review this", "acknowledgement": {"mode": "all", "targets": [{"kind": "role", "id": ROLE}]}}
    source = tmp_path / "post.json"
    source.write_text(json.dumps(payload))
    assert cli.main(command("post", "--file", str(source), "--as-role", ROLE, "--context", f"item:{DISCUSSION}",
                            "--title", "Question", "--context", f"repository:{AGENT}", "--context", "ticket:T-12",
                            "--chat", DISCUSSION, "--request-id", "post-request-key")) == 0
    request, _ = transport[-1]
    assert request.method == "POST"
    assert request.get_header("Idempotency-key") == "post-request-key"
    assert json.loads(request.data) == {**payload, "onBehalfOfRoleId": ROLE, "title": "Question", "chatId": DISCUSSION, "contexts": [
        {"type": "item", "id": DISCUSSION}, {"type": "repository", "id": AGENT}, {"type": "ticket", "id": "T-12"}]}


def test_a_post_takes_no_free_text_repository_label(transport, capsys):
    # K8: a thread names a catalog repository with --context repository:ID instead.
    with pytest.raises(SystemExit) as exited:
        cli.main(command("post", "--text", "Labelled", "--repo", "worklog"))
    assert exited.value.code == 2
    assert not transport


def test_ask_is_refused_with_a_file_which_names_its_own_acknowledgement(transport, tmp_path, capsys):
    unread = tmp_path / "missing.json"
    assert cli.main(command("reply", DISCUSSION, "--file", str(unread), "--ask", AGENT, "--request-id", "key")) == 1
    assert "Give --ask with --text" in capsys.readouterr().err
    assert not transport


def test_reply_from_stdin_keeps_role_and_uses_message_route(transport, monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('{"text":"Reply","acknowledgement":{"mode":"any_agent","targets":[]}}'))
    assert cli.main(command("reply", DISCUSSION, "--file", "-", "--as-role", ROLE)) == 0
    request, _ = transport[-1]
    assert request.full_url.endswith(f"/discussions/{DISCUSSION}/messages")
    assert json.loads(request.data)["acknowledgement"]["mode"] == "any_agent"
    assert json.loads(request.data)["onBehalfOfRoleId"] == ROLE


@pytest.mark.parametrize("args,route,payload", [
    (["post", "--text", "Hello"], "/discussions", {"text": "Hello"}),
    (["avatar", "teal-visor-antenna"], "/discussions/avatar", {"avatar": "teal-visor-antenna"}),
    (["reply", DISCUSSION, "--text", "Response"], f"/discussions/{DISCUSSION}/messages", {"text": "Response"}),
    # --ask names who must acknowledge, each once: an agent by its identity, or the person by either name.
    (["reply", DISCUSSION, "--text", "We both change the lexer", "--ask", AGENT, "--ask", "person", "--ask", AGENT, "--ask", "local-user"],
     f"/discussions/{DISCUSSION}/messages", {"text": "We both change the lexer", "acknowledgement": {"mode": "all", "targets": [
         {"kind": "agent", "id": AGENT}, {"kind": "human", "id": "local-user"}]}}),
    (["post", "--text", "Who owns the lexer?", "--ask", "person", "--context", f"item:{DISCUSSION}"], "/discussions",
     {"text": "Who owns the lexer?", "acknowledgement": {"mode": "all", "targets": [{"kind": "human", "id": "local-user"}]},
      "contexts": [{"type": "item", "id": DISCUSSION}]}),
    (["ack", MESSAGE, "--target", SLOT, "--target", ROLE], f"/discussions/messages/{MESSAGE}/acknowledge", {"targetIds": [SLOT, ROLE]}),
    (["ack", MESSAGE], f"/discussions/messages/{MESSAGE}/acknowledge", {}),
    (["cancel", MESSAGE], f"/discussions/messages/{MESSAGE}/cancel", {}),
    (["roles", "create", "--context", f"item:{DISCUSSION}", "--label", "implementation"],
     "/discussions/roles", {"contextType": "item", "contextId": DISCUSSION, "label": "implementation"}),
    *[(["roles", verb, ROLE, "--revision", "7"], f"/discussions/roles/{ROLE}/{verb}", {"revision": 7})
      for verb in ("claim", "release", "accept", "decline", "withdraw")],
    (["roles", "offer", ROLE, "--revision", "8", "--to", AGENT, "--note", "handoff"],
     f"/discussions/roles/{ROLE}/offer", {"revision": 8, "successorId": AGENT, "note": "handoff"}),
    (["tag", DISCUSSION, "--context", f"repository:{AGENT}"], f"/discussions/{DISCUSSION}/tags", {"type": "repository", "id": AGENT}),
    (["tag", DISCUSSION, "--context", "ticket:12"], f"/discussions/{DISCUSSION}/tags", {"type": "ticket", "id": "12"}),
    (["untag", DISCUSSION, "--context", f"resource:{AGENT}"], f"/discussions/{DISCUSSION}/tags/remove", {"type": "resource", "id": AGENT}),
])
def test_writes_use_exact_routes_payload_and_supplied_key(transport, args, route, payload):
    assert cli.main(command(*args, "--request-id", "same-retry-key")) == 0
    request, _ = transport[-1]
    assert request.method == "POST"
    assert request.full_url.endswith(route)
    assert json.loads(request.data) == payload
    assert request.get_header("Idempotency-key") == "same-retry-key"


@pytest.mark.parametrize("method,route", [
    ("POST", f"/discussions/messages/{MESSAGE}/release"),
    ("POST", f"/discussions/{DISCUSSION}/archive"),
    ("POST", f"/discussions/{DISCUSSION}/pin"),
    ("POST", "/discussions/not-an-id/tags"), ("POST", f"/discussions/{DISCUSSION}/tags/remove/"),
    ("POST", f"/discussions/roles/{ROLE}/reassign"),
    ("GET", "/chats"), ("GET", f"/chats/{DISCUSSION}/transcript"),
    ("GET", "/discussions/roles/"), ("POST", "/discussions/%2e%2e/messages"),
    ("POST", f"/discussions/messages/{MESSAGE}/acknowledge/"),
    ("PATCH", f"/discussions/messages/{MESSAGE}"),
    ("GET", "/discussions/participants/not-an-id"), ("GET", f"/discussions/participants/{AGENT}/"),
    ("POST", f"/discussions/participants/{AGENT}"),
])
def test_agent_cannot_reach_human_actions_or_transcripts(transport, method, route):
    with pytest.raises(ValueError, match="outside"):
        cli.AgentClient().request(method, route, {})
    assert not transport


@pytest.mark.parametrize("failure", [URLError("offline"), TimeoutError(), IncompleteRead(b"partial"),
    HTTPError("http://127.0.0.1", 503, "Unavailable", {}, io.BytesIO(b'{"error":{"message":"retry"}}'))])
def test_uncertain_write_exposes_generated_key_reused_by_explicit_retry(transport, monkeypatch, capsys, failure):
    attempted = []
    def fail(request, **kwargs):
        attempted.append(request)
        raise failure
    monkeypatch.setattr(cli, "_open", fail)
    assert cli.main(command("post", "--text", "Preserve me")) == 1
    key = attempted[0].get_header("Idempotency-key")
    error = capsys.readouterr().err
    assert "--request-id " + key in error
    assert TOKEN not in error
    def succeed(request, **kwargs):
        attempted.append(request)
        return io.BytesIO(b'{}')
    monkeypatch.setattr(cli, "_open", succeed)
    assert cli.main(command("post", "--text", "Preserve me", "--request-id", key)) == 0
    assert attempted[1].get_header("Idempotency-key") == key
    assert attempted[1].data == attempted[0].data


def test_invalid_context_and_request_id_fail_before_transport(transport, capsys):
    assert cli.main(command("list", "--context", "project:abc")) == 1
    assert cli.main(command("post", "--text", "hello", "--request-id", "bad\nheader")) == 1
    # Roles sit only on work items, roadmaps and steps; a proposal is no kind of tag.
    assert cli.main(command("roles", "create", "--context", f"repository:{AGENT}", "--label", "Review")) == 1
    assert cli.main(command("tag", DISCUSSION, "--context", f"proposal:{AGENT}")) == 1
    assert not transport


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(cli.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(cli.time, "sleep", clock.sleep)
    monkeypatch.setattr(cli.random, "uniform", lambda *_: 1)
    return clock


def waiting_transport(monkeypatch, clock, states, durations=None):
    calls = []
    def read(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if durations:
            clock.now += durations[min(len(calls) - 1, len(durations) - 1)]
        value = states[min(len(calls) - 1, len(states) - 1)]
        if isinstance(value, Exception):
            raise value
        return {"id": MESSAGE, "request": {"state": value}}
    monkeypatch.setattr(cli.AgentClient, "request", read)
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", TOKEN)
    return calls


def test_wait_polls_with_backoff_bounded_deadline_and_no_discovery_cursor(monkeypatch, clock, capsys):
    calls = waiting_transport(monkeypatch, clock, ["pending"])
    assert cli.main(command("wait", MESSAGE)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "pending" and result["timedOut"]
    assert "cursor" not in result
    assert clock.now == 30
    assert clock.sleeps == [2, 5, 10, 10, 3]
    assert all(method == "GET" and path == f"/discussions/messages/{MESSAGE}" for method, path, _ in calls)
    assert [kwargs["timeout"] for _, _, kwargs in calls] == [30, 28, 23, 13, 3]


@pytest.mark.parametrize("state", ["satisfied", "cancelled", "released_by_human"])
def test_wait_observes_terminal_state_before_timeout(monkeypatch, clock, capsys, state):
    waiting_transport(monkeypatch, clock, ["pending", state], durations=[0, 3])
    assert cli.main(command("wait", MESSAGE, "--timeout", "5")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == state
    assert result["timedOut"] is False
    assert clock.now == 5


@pytest.mark.parametrize("status,outcome", [(401, "authentication_failed"), (403, "forbidden")])
def test_wait_auth_failure_stops_without_retry(monkeypatch, clock, capsys, status, outcome):
    calls = waiting_transport(monkeypatch, clock, [cli.AgentRequestError("Denied", status)])
    assert cli.main(command("wait", MESSAGE)) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == outcome
    assert len(calls) == 1 and not clock.sleeps


def test_wait_transport_failure_has_finite_retries(monkeypatch, clock, capsys):
    calls = waiting_transport(monkeypatch, clock, [cli.AgentRequestError("Unavailable")])
    assert cli.main(command("wait", MESSAGE)) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "unavailable"
    assert len(calls) == 3 and clock.sleeps == [2, 5]


def test_wait_can_recover_transport_failure(monkeypatch, clock, capsys):
    waiting_transport(monkeypatch, clock, [cli.AgentRequestError("Unavailable"), "satisfied"])
    assert cli.main(command("wait", MESSAGE)) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "satisfied"


@pytest.mark.parametrize("timeout", ["0", "-1", "61", "nan", "inf"])
def test_wait_rejects_unbounded_or_invalid_timeout(timeout):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(command("wait", MESSAGE, "--timeout", timeout))


@pytest.mark.parametrize("verb", ["claim", "release", "accept", "decline", "withdraw"])
def test_role_note_is_only_supported_for_offer(verb):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(command("roles", verb, ROLE, "--revision", "1", "--note", "unsupported"))


@pytest.mark.parametrize("part", ["body", "headers", "error"])
def test_real_trickled_response_obeys_total_wait_deadline(monkeypatch, part):
    """Every byte arrives before the socket inactivity timeout would expire."""
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", TOKEN)
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if part == "headers":
                    content = b"HTTP/1.1 200 OK\r\nContent-Length: 60\r\n\r\n"
                else:
                    self.send_response(503 if part == "error" else 200)
                    self.end_headers()
                    content = json.dumps({"id": MESSAGE, "request": {"state": "pending"}}).encode()
                for byte in content:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    if stop.wait(0.03):
                        break
            except OSError:
                pass

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    thread.start()
    try:
        client = cli.AgentClient(f"http://127.0.0.1:{server.server_port}")
        started = time.monotonic()
        result = cli._wait_for_discussion(client, MESSAGE, 0.20)
        elapsed = time.monotonic() - started
        assert result["outcome"] == "unavailable"
        # Allows loaded CI overhead while rejecting the reproduced ~0.99s failure.
        assert elapsed < 0.65, elapsed
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_wait_response_size_is_bounded_without_reading_entire_body(transport, monkeypatch):
    class LargeResponse(io.BytesIO):
        def __init__(self):
            super().__init__(b"x" * (cli.WAIT_RESPONSE_LIMIT + 10))
            self.requested = []
        def read(self, size=-1):
            self.requested.append(size)
            return super().read(size)
    response = LargeResponse()
    monkeypatch.setattr(cli, "_open", lambda *args, **kwargs: response)
    with pytest.raises(cli.AgentRequestError, match="Invalid response"):
        cli.AgentClient().request("GET", f"/discussions/messages/{MESSAGE}", deadline=time.monotonic() + 1)
    assert response.requested == [cli.WAIT_RESPONSE_LIMIT + 1]
    assert response.closed


def test_wait_accepts_maximum_message_with_json_escaping(transport, monkeypatch):
    from worklog.common import NOTE_LIMIT

    payload = {"id": MESSAGE, "text": "\u0001" * NOTE_LIMIT, "request": {"state": "satisfied"}}
    response = io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(cli, "_open", lambda *args, **kwargs: response)
    actual = cli.AgentClient().request("GET", f"/discussions/messages/{MESSAGE}", deadline=time.monotonic() + 5)
    assert actual == payload


def test_real_deadline_transport_ignores_proxy_and_refuses_redirect(monkeypatch):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")

    class Handler(BaseHTTPRequestHandler):
        redirect = False

        def do_GET(self):
            if self.redirect:
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/collect")
                body = b"{}"
            else:
                self.send_response(200)
                body = json.dumps({"id": MESSAGE, "request": {"state": "satisfied"}}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    thread.start()
    try:
        client = cli.AgentClient(f"http://127.0.0.1:{server.server_port}")
        assert cli._wait_for_discussion(client, MESSAGE, 1)["outcome"] == "satisfied"
        Handler.redirect = True
        with pytest.raises(cli.AgentRequestError, match="HTTP 302"):
            cli._wait_for_discussion(client, MESSAGE, 1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()
