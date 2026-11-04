import io
import json
import os
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
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
    for verb in ("submit", "reopen", "withdraw"):
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


def test_cli_messages_are_ascii():
    # Every message reaches an agent intact whatever code page its console or pipe uses.
    source = Path(cli.__file__).read_bytes()
    assert source.isascii(), [line for line in source.splitlines() if not line.isascii()]


def _rejecting(status, body):
    def rejected(*args, **kwargs):
        raise HTTPError("http://127.0.0.1", status, "Rejected", {}, io.BytesIO(json.dumps(body).encode()))
    return rejected


def test_agent_http_error_names_the_api_code_and_fields(requests, monkeypatch, capsys):
    """Agents act on the code, and on fields such as a conflict's currentRevision."""
    monkeypatch.setattr(cli, "_open", _rejecting(409, {"error": {
        "code": "conflict", "message": "This record changed. Reload and review your draft.",
        "fields": {"expected": 3, "currentRevision": 4}}}))
    assert cli.main(["agent", "tickets", "update", "T-12", "--revision", "3", "--status", "doing"]) == 1
    assert capsys.readouterr().err.strip() == (
        'Worklog: HTTP 409 conflict: This record changed. Reload and review your draft.'
        ' {"currentRevision":4,"expected":3}')


@pytest.mark.parametrize("body,expected", [
    ({"detail": "Not Found"}, "Worklog: HTTP 404: Request rejected"),
    ({"error": {"code": "Not a code!", "message": "Odd"}}, "Worklog: HTTP 404: Odd"),
    ({"error": {"code": "not_found", "message": "Record not found", "fields": {}}},
     "Worklog: HTTP 404 not_found: Record not found"),
    ({"error": {"code": "validation", "message": "Invalid", "fields": {"text": "x" * 2000}}},
     "Worklog: HTTP 404 validation: Invalid (fields omitted)"),
])
def test_agent_http_error_shows_only_a_well_formed_code_and_short_fields(requests, monkeypatch, capsys, body, expected):
    monkeypatch.setattr(cli, "_open", _rejecting(404, body))
    assert cli.main(["agent", "context"]) == 1
    assert capsys.readouterr().err.strip() == expected


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
    monkeypatch.setattr(cli, "_health", lambda port: health)
    monkeypatch.setattr(cli, "_process_identity", lambda pid: process)
    assert cli.start(tmp_path)["reused"] is True
    monkeypatch.setattr(cli, "_port_busy", lambda port: True)
    for field in ("instanceId", "dataId"):
        changed = dict(health)
        changed[field] = "other"
        monkeypatch.setattr(cli, "_health", lambda port, value=changed: value)
        with pytest.raises(RuntimeError, match="unverified"):
            cli.start(tmp_path)
        with pytest.raises(RuntimeError, match="nothing was stopped"):
            cli.stop(tmp_path)


def test_pid_reuse_and_unrelated_listener_never_signal(tmp_path, monkeypatch):
    health = {"app": "worklog", "ready": True, "instanceId": "instance", "dataId": "data"}
    cli._write_json(tmp_path / "server-state.json", {**health, "dataDir": str(tmp_path.resolve()),
                    "pid": 10, "process": {"created": 123, "executable": "python"}})
    monkeypatch.setattr(cli, "_health", lambda port: health)
    monkeypatch.setattr(cli, "_process_identity", lambda pid: {"created": 999, "executable": "python"})
    monkeypatch.setattr(cli, "_port_busy", lambda port: True)
    with pytest.raises(RuntimeError, match="nothing was stopped"):
        cli.stop(tmp_path)
    assert not list(tmp_path.glob("stop-*.json"))


def test_data_directory_override_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "environment"))
    assert cli.data_directory() == (tmp_path / "environment").resolve()
    assert cli.data_directory(str(tmp_path / "argument")) == (tmp_path / "argument").resolve()


def test_port_precedence_and_validation(monkeypatch):
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    assert cli.resolve_port() == 8877
    monkeypatch.setenv("WORKLOG_PORT", "8878")
    assert cli.resolve_port() == 8878
    assert cli.resolve_port(9000) == 9000
    for invalid in ("0", "65536", "port"):
        monkeypatch.setenv("WORKLOG_PORT", invalid)
        with pytest.raises(ValueError, match="1-65535"):
            cli.resolve_port()


def test_agent_commands_follow_the_selected_port(requests, monkeypatch):
    monkeypatch.setenv("WORKLOG_PORT", "8878")
    assert cli.main(["agent", "context"]) == 0
    assert requests[-1].full_url.startswith("http://127.0.0.1:8878/api/v1/context")
    assert cli.main(["--port", "8879", "agent", "context"]) == 0
    assert requests[-1].full_url.startswith("http://127.0.0.1:8879/api/v1/context")


def test_start_launches_and_verifies_the_selected_port(tmp_path, monkeypatch):
    health = {"app": "worklog", "ready": True, "instanceId": "instance", "dataId": "data"}
    process = {"created": 123, "executable": "python"}
    launched, probed = [], []

    class Launched:
        def poll(self):
            return None

    def popen(args, **kwargs):
        launched.append(args)
        cli._write_json(tmp_path / "server-state.json",
                        {**health, "dataDir": str(tmp_path.resolve()), "pid": 4321, "process": process})
        return Launched()

    def health_on(port):
        probed.append(port)
        return health if launched else None

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    monkeypatch.setattr(cli, "_health", health_on)
    monkeypatch.setattr(cli, "_port_busy", lambda port: False)
    monkeypatch.setattr(cli, "_process_identity", lambda pid: process)
    result = cli.start(tmp_path, 8878)
    assert result["url"] == "http://127.0.0.1:8878" and result["reused"] is False
    assert launched[0][-3:] == ["--port", "8878", "serve"]
    assert set(probed) == {8878}


@pytest.fixture
def launches(tmp_path, monkeypatch):
    """start against a fake Popen: each attempt's options, and errors that refuse the next attempts."""
    health = {"app": "worklog", "ready": True, "instanceId": "instance", "dataId": "data"}
    process = {"created": 123, "executable": "python"}
    state = tmp_path / "server-state.json"
    attempts, refusals = [], []

    class Launched:
        def poll(self):
            return None

    def popen(args, **options):
        attempts.append(options)
        if refusals:
            raise refusals.pop(0)
        cli._write_json(state, {**health, "dataDir": str(tmp_path.resolve()), "pid": 4321, "process": process})
        return Launched()

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    monkeypatch.setattr(cli, "_health", lambda port: health if state.exists() else None)
    monkeypatch.setattr(cli, "_port_busy", lambda port: False)
    monkeypatch.setattr(cli, "_process_identity", lambda pid: process)
    return attempts, refusals


windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows process creation flags")


@windows_only
def test_start_launches_the_hidden_server_breaking_away_from_its_windows_job(tmp_path, launches):
    attempts, _ = launches
    result = cli.start(tmp_path, 8878)
    assert [attempt["creationflags"] for attempt in attempts] == [
        subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB]
    assert result["running"] is True and "warning" not in result
    assert not (tmp_path / "server.log").read_bytes()


@windows_only
def test_start_stays_in_a_job_that_forbids_breakaway_and_says_so(tmp_path, launches, capsys):
    attempts, refusals = launches
    # What CreateProcess raises when the job lacks JOB_OBJECT_LIMIT_BREAKAWAY_OK.
    refusals.append(OSError(None, "Access is denied", None, cli.ERROR_ACCESS_DENIED))
    assert cli.main(["--data-dir", str(tmp_path), "--port", "8878", "start"]) == 0
    hidden = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    assert [attempt["creationflags"] for attempt in attempts] == [hidden | subprocess.CREATE_BREAKAWAY_FROM_JOB, hidden]
    result = json.loads(capsys.readouterr().out)
    assert result["running"] is True and result["warning"] == cli.JOB_WARNING
    # Whoever reads server.log after the server vanished learns why.
    assert (tmp_path / "server.log").read_text(encoding="ascii") == f"worklog start: {cli.JOB_WARNING}\n"


@windows_only
def test_start_reports_any_other_launch_failure_without_a_second_attempt(tmp_path, launches, capsys):
    attempts, refusals = launches
    refusals.append(OSError(None, "The system cannot find the file specified", None, 2))
    assert cli.main(["--data-dir", str(tmp_path), "--port", "8878", "start"]) == 1
    assert len(attempts) == 1
    assert "cannot find the file" in capsys.readouterr().err
    assert not (tmp_path / "server.log").read_bytes()


def _export_args(tmp_path, target):
    return ["--data-dir", str(tmp_path / "data"), "export", str(target)]


def test_failed_export_leaves_no_target_or_temporary_file(tmp_path, monkeypatch, capsys):
    def explode(database):
        raise RuntimeError("injected export failure")

    monkeypatch.setattr(cli, "export_database", explode)
    target = tmp_path / "out" / "export.zip"
    target.parent.mkdir()
    assert cli.main(_export_args(tmp_path, target)) == 1
    assert "injected export failure" in capsys.readouterr().err
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_export_refuses_existing_target_and_leaves_no_temporary_file(tmp_path, monkeypatch, capsys):
    def unexpected(database):
        raise AssertionError("export must not start when the target exists")

    monkeypatch.setattr(cli, "export_database", unexpected)
    target = tmp_path / "export.zip"
    target.write_bytes(b"precious")
    assert cli.main(_export_args(tmp_path, target)) == 1
    assert "already exists" in capsys.readouterr().err
    assert target.read_bytes() == b"precious"
    assert [path.name for path in tmp_path.iterdir() if path.name != "data"] == ["export.zip"]


def test_successful_export_publishes_target_without_temporary_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "export_database", lambda database: b"zip-bytes")
    target = tmp_path / "export.zip"
    assert cli.main(_export_args(tmp_path, target)) == 0
    assert target.read_bytes() == b"zip-bytes"
    assert [path.name for path in tmp_path.iterdir() if path.name != "data"] == ["export.zip"]
    assert json.loads(capsys.readouterr().out)["size"] == len(b"zip-bytes")
