import io
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError

import pytest
from worklog import cli

PROPOSAL_ID = str(uuid.uuid4())


@pytest.fixture
def requests(monkeypatch):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", "test-sensitive-token")
    captured = []

    def open_request(request, **kwargs):
        captured.append(request)
        return io.BytesIO(b'{"id":"result","revision":1}')

    monkeypatch.setattr(cli, "_open", open_request)
    return captured


def test_agent_context_search_and_credential_not_printed(requests, capsys):
    assert cli.main(["agent", "context", "--date", "2026-10-28"]) == 0
    assert requests[-1].full_url.endswith("/api/v1/context?date=2026-10-28")
    assert requests[-1].get_header("Authorization") == "Bearer test-sensitive-token"
    assert cli.main(["agent", "search", "one & two"]) == 0
    assert "q=one+%26+two" in requests[-1].full_url
    assert "test-sensitive-token" not in capsys.readouterr().out


def test_draft_update_submit_and_withdraw_use_exact_proposal_routes(requests, tmp_path):
    payload = tmp_path / "proposal.json"
    payload.write_text(json.dumps({"title": "Test", "rationale": "Why", "operations": []}))
    assert cli.main(["agent", "proposal", "draft", "--file", str(payload)]) == 0
    assert requests[-1].method == "POST"
    assert requests[-1].get_header("Idempotency-key")
    assert requests[-1].full_url.endswith("/api/v1/proposals")
    assert cli.main(["agent", "proposal", "update", PROPOSAL_ID, "--revision", "3", "--file", str(payload)]) == 0
    assert requests[-1].method == "PATCH"
    assert json.loads(requests[-1].data)["revision"] == 3
    for verb in ("submit", "withdraw"):
        assert cli.main(["agent", "proposal", verb, PROPOSAL_ID, "--revision", "4"]) == 0
        assert requests[-1].full_url.endswith(f"/proposals/{PROPOSAL_ID}/{verb}")
        assert json.loads(requests[-1].data) == {"revision": 4}


def test_evidence_is_raw_upload_owned_by_proposal(requests, tmp_path):
    path = tmp_path / "evidence.txt"
    path.write_bytes(b"actual evidence")
    assert cli.main(["agent", "proposal", "evidence", PROPOSAL_ID, "--file", str(path)]) == 0
    request = requests[-1]
    assert request.data == b"actual evidence"
    assert "ownerType=proposal" in request.full_url
    assert f"ownerId={PROPOSAL_ID}" in request.full_url
    assert request.get_header("Content-type") == "text/plain"


@pytest.mark.parametrize("method,path", [
    ("POST", "/work-items"), ("PATCH", "/settings"), ("POST", "/credentials"),
    ("POST", f"/proposals/{PROPOSAL_ID}/approve"),
    ("POST", f"/proposals/{PROPOSAL_ID}/reject"),
    ("PATCH", "/proposals/%2e%2e%2fwork-items"),
    ("POST", "/attachments?ownerType=item&ownerId=" + PROPOSAL_ID),
])
def test_agent_client_rejects_canonical_writes_and_approval(requests, method, path):
    client = cli.AgentClient()
    with pytest.raises(ValueError, match="outside"):
        client.request(method, path, {})
    assert requests == []


def test_approve_command_does_not_exist(requests):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["agent", "proposal", "approve", PROPOSAL_ID, "--revision", "1"])


@pytest.mark.parametrize("url", ["https://127.0.0.1:8877", "http://example.com", "http://localhost:8877",
                                 "http://127.0.0.1:8877/path", "http://x:y@127.0.0.1:8877"])
def test_remote_or_ambiguous_agent_url_is_rejected(requests, url):
    with pytest.raises(ValueError, match="literal"):
        cli.AgentClient(url)


def test_missing_agent_credential_is_actionable(monkeypatch, capsys):
    monkeypatch.delenv("WORKLOG_AGENT_TOKEN", raising=False)
    assert cli.main(["agent", "context"]) == 1
    assert "WORKLOG_AGENT_TOKEN" in capsys.readouterr().err


def test_agent_http_error_redacts_credential(requests, monkeypatch, capsys):
    def rejected(*args, **kwargs):
        raise HTTPError("http://127.0.0.1", 403, "Forbidden", {},
                        io.BytesIO(b'{"error":{"message":"test-sensitive-token was rejected"}}'))

    monkeypatch.setattr(cli, "_open", rejected)
    assert cli.main(["agent", "context"]) == 1
    result = capsys.readouterr()
    assert "test-sensitive-token" not in result.out + result.err
    assert "[redacted]" in result.err


def test_agent_ignores_proxy_and_refuses_redirect(monkeypatch):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", "local-token")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")

    class Handler(BaseHTTPRequestHandler):
        redirect = False

        def do_GET(self):
            if self.redirect:
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/collect")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = cli.AgentClient(f"http://127.0.0.1:{server.server_port}")
        assert client.request("GET", "/context") == {"ok": True}
        Handler.redirect = True
        with pytest.raises(RuntimeError, match="HTTP 302"):
            client.request("GET", "/context")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_lifecycle_reuses_only_matching_health_process_and_data(tmp_path, monkeypatch):
    health = {"app": "worklog", "ready": True, "instanceId": "instance", "dataId": "data"}
    process = {"created": 123, "executable": "python"}
    state = {**health, "dataDir": str(tmp_path.resolve()), "pid": 4321, "process": process}
    cli._write_json(tmp_path / "server-state.json", state)
    monkeypatch.setattr(cli, "_health", lambda: health)
    monkeypatch.setattr(cli, "_process_identity", lambda pid: process)
    assert cli.start(tmp_path)["reused"] is True
    monkeypatch.setattr(cli, "_port_busy", lambda: True)
    for field in ("instanceId", "dataId"):
        changed = dict(health)
        changed[field] = "other"
        monkeypatch.setattr(cli, "_health", lambda value=changed: value)
        with pytest.raises(RuntimeError, match="unverified"):
            cli.start(tmp_path)
        with pytest.raises(RuntimeError, match="nothing was stopped"):
            cli.stop(tmp_path)


def test_pid_reuse_and_unrelated_listener_never_signal(tmp_path, monkeypatch):
    health = {"app": "worklog", "ready": True, "instanceId": "instance", "dataId": "data"}
    cli._write_json(tmp_path / "server-state.json", {**health, "dataDir": str(tmp_path.resolve()),
                    "pid": 10, "process": {"created": 123, "executable": "python"}})
    monkeypatch.setattr(cli, "_health", lambda: health)
    monkeypatch.setattr(cli, "_process_identity", lambda pid: {"created": 999, "executable": "python"})
    monkeypatch.setattr(cli, "_port_busy", lambda: True)
    with pytest.raises(RuntimeError, match="nothing was stopped"):
        cli.stop(tmp_path)
    assert not list(tmp_path.glob("stop-*.json"))


def test_data_directory_override_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "environment"))
    assert cli.data_directory() == (tmp_path / "environment").resolve()
    assert cli.data_directory(str(tmp_path / "argument")) == (tmp_path / "argument").resolve()
