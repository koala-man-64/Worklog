"""Local lifecycle/recovery commands and the deliberately limited agent HTTP client."""

from __future__ import annotations

import argparse
import ctypes
import io
import json
import mimetypes
import os
import random
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import closing
from http.client import HTTPConnection, HTTPException, HTTPResponse
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from worklog import __version__
from worklog.recovery import (
    InstanceLock,
    automatic_backup,
    backup_database,
    export_database,
    list_backups,
    restore_database,
)

HOST = "127.0.0.1"
DEFAULT_PORT = 8877
# stop waits STOP_WAIT_S for the server process to exit. Shutdown spends at most SHUTDOWN_GRACE_S
# on requests still open, which leaves room to spare.
STOP_WAIT_S = 15
SHUTDOWN_GRACE_S = 5
WAIT_RESPONSE_LIMIT = 8_000_000
# Tokens of agents registered from this OS account, under the data directory; see _token_store.
AGENT_TOKENS = "agent-tokens"
AGENT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
AGENT_TOKEN = re.compile(r"[A-Za-z0-9_-]{43,200}")
# Windows device names cannot name a file, even with an extension.
RESERVED_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"{d}{n}" for d in ("com", "lpt") for n in range(10))})
# A ticket's key (T-12), number or ID, the only forms a ticket route may carry.
TICKET_REF = re.compile(r"[Tt]-[1-9][0-9]{0,8}|[1-9][0-9]{0,8}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
TICKET_KINDS = ("bug", "incident", "task")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")
TICKET_STATUSES = ("open", "doing", "blocked", "done")
# What agents read before their first command; `worklog agent guide` prints it. Resolved like the
# frontend build in app.py, from this checkout.
AGENT_GUIDE = Path(__file__).resolve().parents[2] / "docs" / "AGENT-GUIDE.md"
ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
ERROR_FIELDS_LIMIT = 1000
# What CreateProcess fails with when the caller's job does not allow CREATE_BREAKAWAY_FROM_JOB.
ERROR_ACCESS_DENIED = 5
# start's report, and its line in server.log, when the server has to stay in start's own job.
JOB_WARNING = ("This command runs in a Windows job that does not let the server leave it, so the server "
               "stops when that job ends, for example when the session or app that started this shell ends")


def resolve_port(value: int | None = None) -> int:
    """An explicit --port wins over WORKLOG_PORT; both fall back to 8877."""
    raw: Any = value if value is not None else os.environ.get("WORKLOG_PORT") or DEFAULT_PORT
    try:
        port = int(raw)
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid port {raw!r}; use a port number 1-65535")
    return port


def local_url(port: int) -> str:
    return f"http://{HOST}:{port}"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


class _DeadlineReader(io.RawIOBase):
    """Enforce total elapsed time even when each individual receive makes progress."""

    def __init__(self, sock: socket.socket, deadline: float) -> None:
        self.sock = sock
        # Keep the socket alive after HTTPConnection closes a nonpersistent connection.
        self.stream = sock.makefile("rb", buffering=0)
        self.deadline = deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int | None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Worklog response deadline exceeded")
        self.sock.settimeout(remaining)
        return self.stream.readinto(buffer)

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            super().close()


class _DeadlineHTTPHandler(HTTPHandler):
    def __init__(self, deadline: float) -> None:
        super().__init__()
        self.deadline = deadline

    def http_open(self, request: Any) -> Any:
        deadline = self.deadline

        class ResponseSocket:
            def __init__(self, sock: socket.socket) -> None:
                self.sock = sock

            def makefile(self, mode: str) -> io.BufferedReader:
                return io.BufferedReader(_DeadlineReader(self.sock, deadline))

        class Connection(HTTPConnection):
            def connect(self) -> None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Worklog request deadline exceeded")
                self.timeout = remaining
                super().connect()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Worklog request deadline exceeded")
                assert self.sock is not None
                self.sock.settimeout(remaining)

            @staticmethod
            def response_class(sock: Any, **kwargs: Any) -> HTTPResponse:  # type: ignore[override]
                return HTTPResponse(cast(Any, ResponseSocket(sock)), **kwargs)

        return self.do_open(Connection, request)


def _open(request: Any, *, timeout: float, deadline: float | None = None) -> Any:
    # Local credentials must never reach an environment proxy or a redirect target.
    handlers: list[Any] = [ProxyHandler({}), _NoRedirect()]
    if deadline is not None:
        handlers.append(_DeadlineHTTPHandler(deadline))
    return build_opener(*handlers).open(request, timeout=timeout)


def _response_json(response: Any, deadline: float | None) -> Any:
    if deadline is None:
        return json.load(response)
    # Wait reads one message, not an unbounded context/export response.
    # A valid message alone may contain one million UTF-8 bytes. Leave room
    # for JSON escaping, targets and receipts while retaining a hard limit.
    maximum = WAIT_RESPONSE_LIMIT
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f"Worklog wait response exceeds {maximum:,} bytes")
    if time.monotonic() > deadline:
        raise TimeoutError("Worklog response deadline exceeded")
    return json.loads(data)


def data_directory(value: str | None = None) -> Path:
    if value or os.environ.get("WORKLOG_DATA_DIR"):
        return Path(value or os.environ["WORKLOG_DATA_DIR"]).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise RuntimeError("LOCALAPPDATA is unavailable; supply --data-dir")
        return Path(base) / "Worklog"
    return Path.home() / ".local" / "share" / "Worklog"


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: Any) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _process_identity(pid: int) -> dict[str, Any] | None:
    """OS creation identity prevents PID reuse from granting stop authority."""
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(system), ctypes.byref(user)):
                return None
            size = wintypes.DWORD(32768)
            name = ctypes.create_unicode_buffer(size.value)
            if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                return None
            return {"created": (created.dwHighDateTime << 32) | created.dwLowDateTime,
                    "executable": str(Path(name.value).resolve()).casefold()}
        finally:
            kernel.CloseHandle(handle)
    try:
        # Linux support is useful for development; Windows is the supported launcher target.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {"created": fields[19], "executable": os.readlink(f"/proc/{pid}/exe")}
    except (OSError, IndexError):
        return None


def _health(port: int) -> dict[str, Any] | None:
    try:
        with _open(local_url(port) + "/health", timeout=0.6) as response:
            value = json.load(response)
            return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _port_busy(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex((HOST, port)) == 0


def _owned(directory: Path, health: dict[str, Any] | None,
           state: dict[str, Any]) -> bool:
    if not health or not state or health.get("app") != "worklog" or health.get("ready") is not True:
        return False
    if Path(str(state.get("dataDir", ""))).resolve() != directory.resolve():
        return False
    if health.get("version") != state.get("version"):
        return False
    if not all(health.get(key) == state.get(key) and state.get(key)
               for key in ("instanceId", "dataId")):
        return False
    try:
        return _process_identity(int(state["pid"])) == state["process"] and bool(state["process"])
    except (KeyError, TypeError, ValueError):
        return False


def check(directory: Path, port: int = DEFAULT_PORT) -> dict[str, Any]:
    state = _json_file(directory / "server-state.json")
    health = _health(port)
    if _owned(directory, health, state):
        return {"running": True, "url": local_url(port), **(health or {}), "pid": state["pid"],
                "dataDir": str(directory)}
    if _port_busy(port):
        raise RuntimeError(f"Port {port} belongs to an unverified instance. Nothing was stopped")
    return {"running": False, "url": local_url(port), "dataDir": str(directory)}


def _launch_server(command: list[str], log: Any,
                   environment: dict[str, str]) -> tuple[subprocess.Popen[bytes], str | None]:
    """Launch the hidden server so it outlives this command; return it and any warning for start's report."""
    def launch(**platform: Any) -> subprocess.Popen[bytes]:
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment,
                                close_fds=True, **platform)

    if os.name != "nt":
        return launch(start_new_session=True), None
    hidden = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    # A new process joins its creator's Windows jobs, and a job's owner can end every process in it at
    # once: a Claude Code session ends its shells' processes that way when it ends. Breaking away leaves
    # each job, from this command's own outwards, that allows it (a Claude Code session's does), so the
    # server outlives the command, its shell and the session. The first job that forbids it keeps the
    # server without an error; only this command's own job refusing makes the launch fail.
    try:
        return launch(creationflags=hidden | subprocess.CREATE_BREAKAWAY_FROM_JOB), None
    except OSError as exc:
        if getattr(exc, "winerror", None) != ERROR_ACCESS_DENIED:
            raise
    log.write(f"worklog start: {JOB_WARNING}\n".encode("ascii"))
    log.flush()
    return launch(creationflags=hidden), JOB_WARNING


def start(directory: Path, port: int = DEFAULT_PORT) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    with InstanceLock(directory, ".launcher.lock"):
        current = check(directory, port)
        if current["running"]:
            return {**current, "reused": True}
        # Refuse another managed instance still booting or running on a different port.
        with InstanceLock(directory):
            pass
        environment = os.environ.copy()
        environment["WORKLOG_DATA_DIR"] = str(directory)
        # The server never needs the invoking agent credential.
        environment.pop("WORKLOG_AGENT_TOKEN", None)
        command = [sys.executable, "-m", "worklog.cli", "--data-dir", str(directory), "--port", str(port), "serve"]
        with (directory / "server.log").open("ab") as log:
            process, warning = _launch_server(command, log, environment)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Worklog startup failed; inspect {directory / 'server.log'}")
            state = _json_file(directory / "server-state.json")
            health = _health(port)
            if _owned(directory, health, state):
                return {"running": True, "url": local_url(port), **(health or {}), "pid": state["pid"],
                        "dataDir": str(directory), "reused": False, **({"warning": warning} if warning else {})}
            time.sleep(0.1)
        raise RuntimeError(f"Startup did not become ready; inspect {directory / 'server.log'} and run check")


def stop(directory: Path, port: int = DEFAULT_PORT) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    with InstanceLock(directory, ".launcher.lock"):
        state = _json_file(directory / "server-state.json")
        health = _health(port)
        if not _owned(directory, health, state):
            if not _port_busy(port):
                # A stopped or stale PID is never a reason to signal a process.
                with InstanceLock(directory):
                    pass
                return {"running": False, "stopped": False}
            raise RuntimeError("Instance/process/data identity could not be verified; nothing was stopped")
        marker = directory / ("stop-" + state["instanceId"] + ".json")
        _write_json(marker, {"instanceId": state["instanceId"], "dataId": state["dataId"]})
        deadline = time.monotonic() + STOP_WAIT_S
        while time.monotonic() < deadline:
            if _process_identity(int(state["pid"])) != state["process"]:
                return {"running": False, "stopped": True}
            time.sleep(0.1)
        raise RuntimeError("Graceful shutdown did not finish; no process was forcefully terminated")


def _server(app: Any, port: int, grace: int = SHUTDOWN_GRACE_S) -> Any:
    """Uvicorn that cuts any request still open `grace` seconds into shutdown, so stop's wait can rely on an exit."""
    import uvicorn

    return uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, access_log=False, timeout_graceful_shutdown=grace))


def serve(directory: Path, port: int = DEFAULT_PORT) -> None:
    """Private foreground entry point; start launches this in one hidden process."""
    from worklog.app import create_app

    os.environ["WORKLOG_DATA_DIR"] = str(directory)
    os.environ["WORKLOG_PORT"] = str(port)
    os.environ["WORKLOG_INSTANCE_ID"] = instance_id = str(uuid.uuid4())
    with InstanceLock(directory):
        app = create_app()
        with closing(sqlite3.connect(directory / "worklog.sqlite3")) as conn:
            data_id = json.loads(conn.execute("SELECT value FROM settings WHERE key='dataId'").fetchone()[0])
        identity = _process_identity(os.getpid())
        if not identity:
            raise RuntimeError("Cannot establish OS process identity")
        state = {"pid": os.getpid(), "process": identity, "instanceId": instance_id,
                 "dataId": data_id, "dataDir": str(directory.resolve()), "version": __version__}
        _write_json(directory / "server-state.json", state)
        marker = directory / ("stop-" + instance_id + ".json")
        server = _server(app, port)
        finished = threading.Event()

        def watch() -> None:
            from worklog.maintenance import prune_receipts

            next_backup = time.monotonic() + 60
            next_prune = time.monotonic() + 3600
            while not finished.wait(0.2):
                request = _json_file(marker)
                if request.get("instanceId") == instance_id and request.get("dataId") == data_id:
                    server.should_exit = True
                    return
                if time.monotonic() >= next_backup and os.environ.get("WORKLOG_AUTO_BACKUP") != "0":
                    try:
                        automatic_backup(directory / "worklog.sqlite3", directory / "backups")
                    except Exception as exc:
                        # Preserve service availability but make a failed backup visible in server.log.
                        print(f"Automatic Worklog backup failed: {type(exc).__name__}", file=sys.stderr, flush=True)
                    next_backup = time.monotonic() + 60
                if time.monotonic() >= next_prune:
                    try:
                        with app.state.db.transaction() as conn:
                            prune_receipts(conn)
                    except Exception as exc:
                        # A busy database waits for the next hour; startup prunes as well.
                        print(f"Pruning Worklog receipts failed: {type(exc).__name__}", file=sys.stderr, flush=True)
                    next_prune = time.monotonic() + 3600

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            server.run()
        finally:
            finished.set()
            watcher.join(timeout=1)
            marker.unlink(missing_ok=True)
            state_path = directory / "server-state.json"
            if _json_file(state_path).get("instanceId") == instance_id:
                state_path.unlink(missing_ok=True)


class AgentRequestError(RuntimeError):
    """A safe transport error with enough classification for bounded waits."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _discussion_route_allowed(method: str, route: str) -> bool:
    """Keep the client independently unable to invoke human-only commands."""
    if route in {"/discussions", "/discussions/roles"}:
        return method in {"GET", "POST"}
    if route in {"/discussions/participants", "/discussions/avatars", "/discussions/changes", "/discussions/resume"}:
        return method == "GET"
    if route == "/discussions/avatar":
        return method == "POST"
    parts = route.split("/")
    def valid_id(value: str) -> bool:
        try:
            return str(uuid.UUID(value)) == value.lower()
        except ValueError:
            return False
    if len(parts) == 3 and parts[1] == "discussions" and valid_id(parts[2]):
        return method == "GET"
    if len(parts) == 4 and parts[1] == "discussions":
        if valid_id(parts[2]) and parts[3] == "messages":
            return method == "POST"
        if parts[2] == "messages" and valid_id(parts[3]):
            return method == "GET"
    if len(parts) == 5 and parts[1] == "discussions" and valid_id(parts[3]):
        if parts[2] == "messages" and parts[4] in {"acknowledge", "cancel"}:
            return method == "POST"
        if parts[2] == "roles" and parts[4] in {"claim", "release", "offer", "accept", "decline", "withdraw"}:
            return method == "POST"
    return False


def _coordination_route_allowed(method: str, route: str) -> bool:
    """Coordination writes are self-attributed; catalog edits remain reviewed."""
    if not route.startswith("/") or route.endswith("/"):
        return False
    if route == "/repositories/ensure":
        return method == "POST"
    if route in {"/repositories", "/agent-runs", "/participations", "/coordination-updates"}:
        return method == "GET" or (method == "POST" and route in {"/agent-runs", "/participations"})
    parts = route.strip("/").split("/")
    if len(parts) not in {2, 3}:
        return False
    try:
        if str(uuid.UUID(parts[1])) != parts[1].lower():
            return False
    except ValueError:
        return False
    if len(parts) == 2:
        return (method == "GET" and parts[0] in {"repositories", "agent-runs", "participations"}
                or method == "PATCH" and parts[0] == "participations")
    return method == "POST" and (
        parts[0] == "agent-runs" and parts[2] == "end"
        or parts[0] == "participations" and parts[2] in {"release", "rejoin", "check-in", "updates"})


def _ticket_route_allowed(method: str, route: str) -> bool:
    """Agents file, change and comment on tickets; archive and recover stay with the person."""
    if route == "/tickets":
        return method in {"GET", "POST"}
    parts = route.split("/")
    if len(parts) not in {3, 4} or parts[1] != "tickets" or not TICKET_REF.fullmatch(parts[2]):
        return False
    if len(parts) == 3:
        return method in {"GET", "PATCH"}
    return method == "POST" and parts[3] == "comments"


def _agent_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != HOST or parsed.username
            or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Agent URL must be plain HTTP on literal 127.0.0.1 without a path or credentials")
    return base_url.rstrip("/")


class AgentClient:
    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base = _agent_url(base_url or local_url(resolve_port()))
        self.token = token or os.environ.get("WORKLOG_AGENT_TOKEN", "")
        if not self.token:
            raise ValueError("Set WORKLOG_AGENT_TOKEN to an agent credential issued in Worklog settings, or pass --as NAME for an agent you registered")

    def request(self, method: str, path: str, payload: Any = None,
                *, raw: bytes | None = None, content_type: str | None = None,
                request_id: str | None = None, timeout: float = 30,
                deadline: float | None = None) -> Any:
        # Explicit route allowlist makes canonical writes and approval unreachable from this client.
        route = path.split("?", 1)[0]
        segments = route.strip("/").split("/")
        allowed = method == "GET" and route in {"/context", "/search", "/proposals", "/resources"}
        def valid_id(value: str) -> bool:
            try:
                return str(uuid.UUID(value)) == value.lower()
            except ValueError:
                return False

        if len(segments) == 2 and segments[0] == "proposals":
            allowed = method in {"GET", "PATCH"} and valid_id(segments[1])
        if len(segments) == 3 and segments[0] == "proposals" and segments[2] in {"submit", "withdraw", "reopen"}:
            allowed = method == "POST" and valid_id(segments[1])
        if len(segments) == 2 and segments[0] in {"roadmaps", "steps", "resources"}:
            allowed = method == "GET" and valid_id(segments[1])
        if route in {"/proposals", "/agent-registrations", "/agent-registrations/retire"} and method == "POST":
            allowed = True
        if route == "/attachments" and method == "POST":
            from urllib.parse import parse_qs

            query = parse_qs(urlsplit(path).query)
            ids = query.get("ownerId", [])
            allowed = query.get("ownerType") == ["proposal"] and len(ids) == 1 and valid_id(ids[0])
        allowed = (allowed or _discussion_route_allowed(method, route) or _coordination_route_allowed(method, route)
                   or _ticket_route_allowed(method, route) or (method == "POST" and route == "/inbox/receive"))
        if not allowed:
            raise ValueError("Agent command is outside proposal/read scope")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        data = raw
        if raw is not None:
            headers["Content-Type"] = content_type or "application/octet-stream"
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        coordination_write = method in {"POST", "PATCH"} and route.startswith(("/repositories", "/agent-runs", "/participations", "/tickets"))
        if method == "POST" or coordination_write:
            request_id = request_id or str(uuid.uuid4())
            if not 8 <= len(request_id) <= 200 or not request_id.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in request_id):
                raise ValueError("Request ID must contain 8-200 printable ASCII characters without spaces")
            headers["Idempotency-Key"] = request_id
        discussion_write = method == "POST" and route.startswith(("/discussions", "/inbox"))
        retry_hint = (f"; result uncertain; retry the same command and payload with --request-id {request_id}"
                      if discussion_write or coordination_write else "")
        request = Request(self.base + "/api/v1" + path, data=data, headers=headers, method=method)
        try:
            options = {"deadline": deadline} if deadline is not None else {}
            with _open(request, timeout=timeout, **options) as response:
                return _response_json(response, deadline)
        except HTTPError as exc:
            code = None
            fields: Any = None
            try:
                with exc:
                    error = _response_json(exc, deadline).get("error", {})
                message = str(error.get("message", "Request rejected"))
                code = error.get("code") if isinstance(error.get("code"), str) else None
                fields = error.get("fields")
            except (ValueError, AttributeError, OSError, HTTPException):
                message = "Request rejected"
            # The code and fields tell an agent how to respond, such as a conflict's currentRevision.
            # Do not reflect arbitrary server content containing a credential.
            named = f" {code}" if code and ERROR_CODE.fullmatch(code) else ""
            detail = f"HTTP {exc.code}{named}: " + message + _error_fields(fields)
            if exc.code >= 500:
                detail += retry_hint
            raise AgentRequestError(detail.replace(self.token, "[redacted]"), exc.code, code) from None
        except (URLError, TimeoutError, OSError, HTTPException):
            raise AgentRequestError(("Cannot reach Worklog; run worklog check" + retry_hint).replace(self.token, "[redacted]")) from None
        except (ValueError, UnicodeError):
            raise AgentRequestError(("Invalid response from Worklog" + retry_hint).replace(self.token, "[redacted]")) from None


def _error_fields(fields: Any) -> str:
    """An error's field details as compact ASCII JSON, or a note when they are too long to show."""
    if fields is None or fields == {} or fields == []:
        return ""
    try:
        text = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return ""
    return " " + text if len(text) <= ERROR_FIELDS_LIMIT else " (fields omitted)"


def _payload(path: str) -> dict[str, Any]:
    value = json.loads(sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Proposal file must contain a JSON object")
    return value


def _token_store(args: argparse.Namespace, base: str) -> Path:
    """Tokens of agents registered from this OS account, kept per instance (data directory and port)."""
    return data_directory(args.data_dir) / AGENT_TOKENS / str(urlsplit(base).port or 80)


def _agent_name(value: str) -> str:
    name = value.lower()
    if not AGENT_NAME.fullmatch(name) or name in RESERVED_NAMES:
        raise ValueError("An agent name is 1-64 letters, digits, '-' or '_', starting with a letter or digit")
    return name


def _stored_token(path: Path) -> str | None:
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        stored = None
    token = stored.get("token") if isinstance(stored, dict) else None
    if not isinstance(token, str) or not AGENT_TOKEN.fullmatch(token):
        raise ValueError(f"{path} does not hold an agent token; move it aside and register again")
    return token


def _store_new_token(path: Path, name: str, token: str) -> None:
    """Store a token before it is first sent, so an uncertain registration can be retried.

    As in _publish_new_file, the rename (Windows) or hard link (elsewhere) fails rather
    than replace a token another process stored under the same name.
    """
    for directory in (path.parent.parent, path.parent):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name_ = tempfile.mkstemp(dir=path.parent, prefix=".agent-token-", suffix=".tmp")
    temporary = Path(name_)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"name": name, "token": token}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if os.name == "nt":
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
        except FileExistsError:
            raise ValueError(f"Another command stored a token for {name} at the same time; run register again") from None
    finally:
        temporary.unlink(missing_ok=True)


def register_command(client: AgentClient, args: argparse.Namespace, redact: list[str]) -> dict:
    """Register a new identity for this chat, with a token generated here and never printed."""
    if args.name is None:
        # The chat's ID names the identity unless it cannot be a name.
        try:
            name = _agent_name(args.chat)
        except ValueError:
            raise ValueError("This chat ID cannot name the agent; give --name as well") from None
    else:
        name = _agent_name(args.name)
    path = _token_store(args, client.base) / (name + ".json")
    token = _stored_token(path)
    if token is None:
        token = secrets.token_urlsafe(32)
        _store_new_token(path, name, token)
    redact.append(token)
    payload = {"label": args.label, "token": token, "chatId": args.chat, **({"avatar": args.avatar} if args.avatar else {})}
    try:
        registration = client.request("POST", "/agent-registrations", payload)
    except AgentRequestError as exc:
        if exc.code == "token_revoked":
            raise ValueError(f"The credential stored for {name} was revoked; run `worklog agent --as {name} retire` to forget it before registering {name} again") from None
        if exc.code == "chat_mismatch":
            raise ValueError(f"{name} is registered for another chat; register this chat under another --name") from None
        if exc.status is None or exc.status >= 500:
            raise AgentRequestError(f"{exc}; result uncertain; run the same register command again", exc.status) from None
        raise
    return {"name": name, **registration}


def retire_command(client: AgentClient, args: argparse.Namespace) -> dict:
    name = _agent_name(args.as_name)
    try:
        result = client.request("POST", "/agent-registrations/retire", {})
    except AgentRequestError as exc:
        # Forget the token only when Worklog itself refuses it: revoked, already retired, or never registered.
        if exc.status != 401 or exc.code != "authentication":
            raise
        result = {"retired": True, "alreadyRevoked": True}
    (_token_store(args, client.base) / (name + ".json")).unlink(missing_ok=True)
    return {"name": name, **result}


def agent_command(args: argparse.Namespace, port: int, redact: list[str]) -> Any:
    base = _agent_url(args.url or local_url(port))
    if args.agent_action == "retire" and args.as_name is None:
        # WORKLOG_AGENT_TOKEN may be shared by other agents; only the person revokes it.
        raise ValueError("Retire an agent you registered with `worklog agent --as NAME retire`; revoke other credentials in Settings")
    token = None
    # An empty --as (say, from an unset variable) must fail, never fall back to WORKLOG_AGENT_TOKEN.
    if args.as_name is not None:
        name = _agent_name(args.as_name)
        token = _stored_token(_token_store(args, base) / (name + ".json"))
        if token is None:
            raise ValueError(f"No agent named {name} was registered for {base}; run `worklog agent register --chat CHAT_ID --name {name} --label LABEL` first")
        redact.append(token)
    client = AgentClient(base, token)
    if args.agent_action == "register":
        return register_command(client, args, redact)
    if args.agent_action == "retire":
        return retire_command(client, args)
    if args.agent_action == "discussions":
        return discussion_command(client, args)
    if args.agent_action in {"repositories", "runs", "participation", "updates"}:
        return coordination_command(client, args)
    if args.agent_action == "tickets":
        return ticket_command(client, args)
    if args.agent_action == "resources":
        if args.resource_action == "list":
            return client.request("GET", "/resources" + _coordination_query(args, "q", "archived", "limit", "offset"))
        return client.request("GET", "/resources/" + args.id)
    if args.agent_action == "inbox":
        return client.request("POST", "/inbox/receive", {"chatId": args.chat} if args.chat else {},
                              request_id=args.request_id)
    if args.agent_action == "context":
        return client.request("GET", "/context" + _coordination_query(args, "date", "repositoryId", "itemId"))
    if args.agent_action == "search":
        return client.request("GET", "/search?" + urlencode({"q": args.query, "limit": args.limit,
                                                            "archived": str(args.archived).lower()}))
    if args.agent_action == "roadmap":
        roadmap = client.request("GET", "/roadmaps/" + args.id)
        # Each history event keeps whole before-and-after copies of the plan; the steps already hold its text.
        roadmap.pop("history", None)
        return roadmap
    if args.agent_action == "step":
        return client.request("GET", "/steps/" + args.id)
    if args.agent_action == "proposals":
        return client.request("GET", "/proposals" + ("?" + urlencode({"status": args.status}) if args.status else ""))
    action = args.proposal_action
    if action == "draft":
        return client.request("POST", "/proposals", _payload(args.file))
    route = "/proposals/" + args.id
    if action == "show":
        return client.request("GET", route)
    if action == "update":
        payload = _payload(args.file)
        payload["revision"] = args.revision
        return client.request("PATCH", route, payload)
    if action == "evidence":
        path = Path(args.file)
        if path.stat().st_size > 25_000_000:
            raise ValueError("Evidence exceeds 25,000,000 bytes")
        return client.request("POST", "/attachments?" + urlencode(
            {"ownerType": "proposal", "ownerId": args.id, "name": args.name or path.name}),
            raw=path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0])
    return client.request("POST", route + "/" + action, {"revision": args.revision})


def _coordination_query(args: argparse.Namespace, *fields: str) -> str:
    values = {key: getattr(args, key) for key in fields if getattr(args, key, None) is not None}
    values = {key: str(value).lower() if isinstance(value, bool) else value for key, value in values.items()}
    return "?" + urlencode(values) if values else ""


def _checkout_context(path: str) -> dict[str, str]:
    """A local checkout as Git sees it: its origin remote, branch, and checkout or worktree path."""
    def git(*arguments: str) -> list[str]:
        try:
            done = subprocess.run(["git", "-C", path, *arguments], capture_output=True, encoding="utf-8",
                                  errors="replace", timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError(f"Could not run git to read {path}") from None
        lines = done.stdout.splitlines()
        if done.returncode or not lines:
            raise ValueError(f"{path} is not a Git checkout with an origin remote")
        return lines

    url = git("remote", "get-url", "origin")[0].strip()
    top, git_dir, common, branch = git("rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir",
                                       "--git-common-dir", "--abbrev-ref", "HEAD")[:4]
    context = {"repositoryUrl": url, "branch": "" if branch == "HEAD" else branch}
    if Path(git_dir) == Path(common):
        context["checkoutPath"] = top
    else:
        # A linked worktree; its main checkout holds the shared Git directory, unless that is bare.
        context["worktreePath"] = top
        if Path(common).name == ".git":
            context["checkoutPath"] = str(Path(common).parent)
    return context


def coordination_command(client: AgentClient, args: argparse.Namespace) -> Any:
    action = args.coordination_action
    group = args.agent_action
    base = {"repositories": "/repositories", "runs": "/agent-runs",
            "participation": "/participations", "updates": "/coordination-updates"}[group]
    if action == "list":
        return client.request("GET", base + _coordination_query(
            args, "repositoryId", "itemId", "runId", "participationId", "q", "archived", "includeEnded", "includeReleased", "limit", "offset"))
    if action == "show":
        return client.request("GET", base + "/" + args.id)
    payload = _payload(args.file) if getattr(args, "file", None) else {}
    route = base
    method = "POST"
    if group == "repositories" and action == "ensure":
        route = "/repositories/ensure"
        payload["url"] = args.remote if args.remote is not None else _checkout_context(args.checkout)["repositoryUrl"]
        for flag in ("name", "description"):
            if getattr(args, flag) is not None:
                payload[flag] = getattr(args, flag)
    elif group == "runs" and action == "start":
        payload.update(clientRunKey=args.client_run_key, displayName=args.display_name)
        for flag, key in (("provider", "provider"), ("chat", "externalChatId")):
            if getattr(args, flag, None) is not None:
                payload[key] = getattr(args, flag)
    elif group == "participation" and action == "start":
        if args.run:
            payload["runId"] = args.run
        contexts = [{"repositoryId": repo} for repo in args.repo or []] + [_checkout_context(path) for path in args.checkout or []]
        if contexts:
            payload["contexts"] = contexts
        if args.item:
            payload["itemId"] = args.item
        if args.description is not None:
            payload["description"] = args.description
    elif group == "updates":
        route = "/participations/" + args.participation + "/updates"
        if args.text is not None:
            payload["text"] = args.text
            payload.setdefault("kind", "update")
        if args.kind is not None:
            payload["kind"] = args.kind
    else:
        route += "/" + args.id
        payload["revision"] = args.revision
        if group == "participation" and action == "update":
            method = "PATCH"
            if args.description is not None:
                payload["description"] = args.description
        else:
            route += "/" + action
    return client.request(method, route, payload, request_id=args.request_id)


def _coordination_parser(actions: Any) -> None:
    def pages(command: Any) -> None:
        command.add_argument("--limit", type=int, default=50)
        command.add_argument("--offset", type=int, default=0)

    def write(command: Any) -> None:
        command.add_argument("--request-id", required=True,
                             help="Stable operation key; reuse with the same payload after an uncertain result")

    repositories = actions.add_parser("repositories", help="Read the repository catalog and register remotes").add_subparsers(
        dest="coordination_action", required=True)
    listing = repositories.add_parser("list")
    listing.add_argument("--archived", action="store_true", help="List archived catalog records")
    listing.add_argument("--q", help="Search catalog name, URL and description")
    pages(listing)
    repositories.add_parser("show").add_argument("id")
    ensure = repositories.add_parser("ensure", help="Register a remote, or find the record that already holds it")
    source = ensure.add_mutually_exclusive_group(required=True)
    # Not --url: the agent command's own --url names the Worklog instance.
    source.add_argument("--remote", help="Remote address: https://, ssh://git@host/path or git@host:path")
    source.add_argument("--checkout", help="Local checkout whose origin remote to register")
    ensure.add_argument("--name", help="Name for a new record; defaults to the address's last path segment")
    ensure.add_argument("--description")
    write(ensure)
    runs = actions.add_parser("runs", help="Record this agent's explicit work runs").add_subparsers(
        dest="coordination_action", required=True)
    listing = runs.add_parser("list")
    listing.add_argument("--include-ended", dest="includeEnded", action="store_true")
    pages(listing)
    runs.add_parser("show").add_argument("id")
    start = runs.add_parser("start")
    start.add_argument("--client-run-key", required=True)
    start.add_argument("--display-name", required=True)
    start.add_argument("--provider")
    start.add_argument("--chat", help="The chat this run works in (default: the chat you registered with)")
    write(start)
    end = runs.add_parser("end")
    end.add_argument("id")
    end.add_argument("--revision", type=int, required=True)
    write(end)
    participation = actions.add_parser("participation", help="Report repository activity and optional work context").add_subparsers(
        dest="coordination_action", required=True)
    listing = participation.add_parser("list")
    for name, destination in (("repo", "repositoryId"), ("item", "itemId"), ("run", "runId")):
        listing.add_argument("--" + name, dest=destination)
    listing.add_argument("--include-released", dest="includeReleased", action="store_true")
    pages(listing)
    participation.add_parser("show").add_argument("id")
    start = participation.add_parser("start")
    start.add_argument("--file", help="JSON {runId,itemId?,contexts:[{repositoryId or repositoryUrl,checkoutPath?,worktreePath?,branch?}]}")
    start.add_argument("--run")
    start.add_argument("--repo", action="append", help="Catalog repository ID; repeat for multiple contexts")
    start.add_argument("--checkout", action="append",
                       help="Local checkout to report by its origin remote, branch and path, registering the remote if needed; repeatable")
    start.add_argument("--item", help="Optional work item ID")
    start.add_argument("--description", help="What this participation is doing")
    write(start)
    for verb in ("update", "check-in", "release", "rejoin"):
        command = participation.add_parser(verb)
        command.add_argument("id")
        command.add_argument("--revision", type=int, required=True)
        if verb == "update":
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--file", help="JSON object with itemId, description and/or replacement contexts")
            source.add_argument("--description", help="Replace the participation's activity description")
        write(command)
    updates = actions.add_parser("updates", help="Append immutable status, blocker and handoff messages").add_subparsers(
        dest="coordination_action", required=True)
    listing = updates.add_parser("list")
    for name, destination in (("repo", "repositoryId"), ("item", "itemId"), ("participation", "participationId")):
        listing.add_argument("--" + name, dest=destination)
    pages(listing)
    post = updates.add_parser("post")
    post.add_argument("--participation", required=True)
    post.add_argument("--kind", choices=("update", "blocker", "handoff"))
    source = post.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file", help="JSON {kind,text}; - reads standard input")
    write(post)


def _self_id(client: AgentClient) -> str:
    """This agent's communication identity: the one tickets record as author and assignee."""
    return client.request("GET", "/discussions/participants")["self"]["id"]


def _assignee(client: AgentClient, value: str) -> str | None:
    return None if value == "none" else _self_id(client) if value == "me" else value


def ticket_command(client: AgentClient, args: argparse.Namespace) -> Any:
    action = args.ticket_action
    if action == "list":
        if args.assigneeId == "me":
            args.assigneeId = _self_id(client)
        return client.request("GET", "/tickets" + _coordination_query(
            args, "status", "q", "kind", "priority", "label", "repositoryId", "itemId", "assigneeId", "sort",
            "archived", "limit", "offset"))
    route = "/tickets" if action == "create" else "/tickets/" + args.ticket
    if action == "show":
        return client.request("GET", route + ("?" + urlencode({"comments": args.comments}) if args.comments else ""))
    payload = _payload(args.file) if getattr(args, "file", None) else {}
    if action == "comment":
        if args.text is not None:
            payload["text"] = args.text
        return client.request("POST", route + "/comments", payload, request_id=args.request_id)
    for flag, field in (("title", "title"), ("description", "description"), ("kind", "kind"),
                        ("priority", "priority"), ("status", "status"), ("resolution", "resolution"),
                        ("item", "itemId"), ("label", "labels"), ("repo", "repositoryIds")):
        if getattr(args, flag, None) is not None:
            payload[field] = getattr(args, flag)
    if getattr(args, "assign", None) is not None:
        payload["assigneeId"] = _assignee(client, args.assign)
    if action == "create":
        return client.request("POST", route, payload, request_id=args.request_id)
    if action == "close":
        payload["status"] = "done"
    for flag, field, empty in (("no_item", "itemId", None), ("no_labels", "labels", []), ("no_repos", "repositoryIds", [])):
        if getattr(args, flag, False):
            payload[field] = empty
    payload["revision"] = args.revision
    return client.request("PATCH", route, payload, request_id=args.request_id)


def _ticket_parser(actions: Any) -> None:
    def write(command: Any) -> None:
        command.add_argument("--request-id", help="Reuse with the same payload after an uncertain result")

    def ticket(command: Any) -> None:
        command.add_argument("ticket", help="T-12, 12 or the ticket's ID")

    tickets = actions.add_parser("tickets", help="File, work and close tickets, and search past ones").add_subparsers(
        dest="ticket_action", required=True)
    listing = tickets.add_parser("list", help="Open, doing and blocked tickets unless --status says otherwise")
    listing.add_argument("--status", choices=("active", "all", *TICKET_STATUSES))
    listing.add_argument("--q", help="Words from a ticket's title, text, resolution, labels or comments")
    listing.add_argument("--kind", choices=TICKET_KINDS)
    listing.add_argument("--priority", choices=TICKET_PRIORITIES)
    listing.add_argument("--label")
    listing.add_argument("--repo", dest="repositoryId", help="Catalog repository ID")
    listing.add_argument("--item", dest="itemId", help="Work item ID")
    listing.add_argument("--assignee", dest="assigneeId", help="me, none, local-user (the person) or an agent ID")
    listing.add_argument("--sort", choices=("priority", "recent"))
    listing.add_argument("--archived", action="store_true", help="List archived tickets")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)
    show = tickets.add_parser("show", help="A ticket with its latest comments and history")
    ticket(show)
    show.add_argument("--comments", type=int, help="How many of the latest comments to include (default 200)")
    for verb in ("create", "update", "close"):
        command = tickets.add_parser(verb)
        if verb != "create":
            ticket(command)
            command.add_argument("--revision", type=int, required=True)
        if verb == "close":
            command.add_argument("--resolution", required=True, help="What fixed it, or why it is closed (Markdown)")
        else:
            command.add_argument("--file", help="JSON object with any ticket fields; - reads standard input")
            command.add_argument("--title")
            command.add_argument("--description", help="Markdown")
            command.add_argument("--kind", choices=TICKET_KINDS)
            command.add_argument("--priority", choices=TICKET_PRIORITIES)
            command.add_argument("--status", choices=TICKET_STATUSES)
            command.add_argument("--resolution", help="Markdown; Done needs one")
            command.add_argument("--label", action="append", help="Repeat for several; replaces the ticket's labels")
            command.add_argument("--repo", action="append", help="Catalog repository ID; repeat for several; replaces the links")
            command.add_argument("--item", help="Work item ID")
            command.add_argument("--assign", help="me, none, local-user (the person) or an agent ID")
        if verb == "update":
            command.add_argument("--no-item", action="store_true", help="Unlink the work item")
            command.add_argument("--no-labels", action="store_true", help="Remove every label")
            command.add_argument("--no-repos", action="store_true", help="Remove every repository link")
        write(command)
    post = tickets.add_parser("comment", help="Add an immutable comment; correct one with another")
    ticket(post)
    source = post.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Markdown")
    source.add_argument("--file", help="JSON {text}; - reads standard input")
    write(post)


def _discussion_context(args: argparse.Namespace) -> dict:
    context = getattr(args, "context", None)
    if context:
        kind, separator, identity = context.partition(":")
        if not separator or kind not in {"item", "roadmap", "step"} or not identity:
            raise ValueError("Context must be item:ID, roadmap:ID or step:ID")
        return {"contextType": kind, "contextId": identity}
    return {}


def _discussion_query(args: argparse.Namespace, *names: str) -> str:
    values = {name: getattr(args, name) for name in names if getattr(args, name, None) is not None}
    values.update(_discussion_context(args))
    return "?" + urlencode(values) if values else ""


def _wait_for_discussion(client: AgentClient, identity: str, timeout: float) -> dict:
    """Observe one request, never consume the independent discovery feed."""
    deadline = time.monotonic() + timeout
    route = "/discussions/messages/" + identity
    delay_index = 0
    failures = 0
    last = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"outcome": "pending", "timedOut": True, "message": last}
        try:
            last = client.request("GET", route, timeout=min(30, remaining), deadline=deadline)
            failures = 0
        except AgentRequestError as exc:
            if exc.status in {401, 403}:
                return {"outcome": "authentication_failed" if exc.status == 401 else "forbidden", "error": str(exc)}
            if exc.status is not None and exc.status < 500:
                raise
            failures += 1
            if failures >= 3 or time.monotonic() >= deadline:
                return {"outcome": "unavailable", "error": str(exc)}
        else:
            request = last.get("request")
            if not request:
                raise ValueError("This message has no acknowledgement request")
            state = request["state"]
            if state != "pending":
                return {"outcome": state, "timedOut": False, "message": last}
            if time.monotonic() >= deadline:
                return {"outcome": "pending", "timedOut": True, "message": last}
        delay = (2, 5, 10)[min(delay_index, 2)] * random.uniform(0.9, 1.1)
        delay_index += 1
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(delay, remaining))


def discussion_command(client: AgentClient, args: argparse.Namespace) -> Any:
    action = args.discussion_action
    base = "/discussions"
    if action == "participants":
        return client.request("GET", base + "/participants")
    if action == "avatars":
        return client.request("GET", base + "/avatars")
    if action == "avatar":
        return client.request("POST", base + "/avatar", {"avatar": args.name}, request_id=args.request_id)
    if action == "list":
        return client.request("GET", base + _discussion_query(args, "view", "q", "repo", "limit", "offset"))
    if action in {"changes", "resume"}:
        fields = ("cursor", "limit") if action == "changes" else ("limit", "offset")
        return client.request("GET", base + "/" + action + _discussion_query(args, *fields))
    if action == "read":
        route = base + ("/messages/" if args.message else "/") + args.id
        return client.request("GET", route + ("" if args.message else _discussion_query(args, "limit", "offset")))
    if action == "wait":
        return _wait_for_discussion(client, args.id, args.timeout)
    if action == "roles":
        verb = args.role_action
        if verb == "list":
            return client.request("GET", base + "/roles" + _discussion_query(args, "limit", "offset"))
        if verb == "create":
            payload = {"label": args.label, **_discussion_context(args)}
            route = base + "/roles"
        else:
            payload = {"revision": args.revision}
            if verb == "offer":
                payload["successorId"] = args.to
            if getattr(args, "note", None) is not None:
                payload["note"] = args.note
            route = base + "/roles/" + args.id + "/" + verb
    elif action in {"ack", "cancel"}:
        payload = {"targetIds": args.target} if action == "ack" and args.target else {}
        route = base + "/messages/" + args.id + ("/acknowledge" if action == "ack" else "/cancel")
    else:
        payload = _payload(args.file) if args.file else {"text": args.text}
        if args.as_role:
            payload["onBehalfOfRoleId"] = args.as_role
        if args.chat:
            payload["chatId"] = args.chat
        if action == "post":
            if args.title:
                payload["title"] = args.title
            if args.context:
                context = _discussion_context(args)
                payload["contexts"] = [{"type": context["contextType"], "id": context["contextId"]}]
            if args.repo:
                payload["repos"] = args.repo
        route = base if action == "post" else base + "/" + args.id + "/messages"
    return client.request("POST", route, payload, request_id=args.request_id)


def _wait_timeout(value: str) -> float:
    result = float(value)
    if not 0 < result <= 60:
        raise argparse.ArgumentTypeError("Wait timeout must be greater than 0 and at most 60 seconds")
    return result


def _discussion_parser(actions: Any) -> None:
    discussions = actions.add_parser("discussions", help="Read and contribute to the shared discussion forum")
    commands = discussions.add_subparsers(dest="discussion_action", required=True)
    def context(command: Any, required: bool = False) -> None:
        command.add_argument("--context", required=required, metavar="TYPE:ID")
    def pages(command: Any) -> None:
        command.add_argument("--limit", type=int, default=50)
        command.add_argument("--offset", type=int, default=0)
    def write(command: Any) -> None:
        command.add_argument("--request-id", help="Reuse this key and the same payload when retrying a write")
    commands.add_parser("participants")
    commands.add_parser("avatars", help="List avatar names, which are free, and your current one")
    avatar = commands.add_parser("avatar", help="Show this avatar beside your name in discussions")
    avatar.add_argument("name", help="An available name from `avatars`, such as teal-visor-antenna")
    write(avatar)
    listing = commands.add_parser("list")
    listing.add_argument("--view", choices=("all", "awaiting", "archived"), default="all")
    listing.add_argument("--q")
    listing.add_argument("--repo")
    context(listing)
    pages(listing)
    read = commands.add_parser("read")
    read.add_argument("id")
    read.add_argument("--message", action="store_true", help="Read a message ID instead of a discussion ID")
    pages(read)
    changes = commands.add_parser("changes")
    changes.add_argument("--cursor")
    changes.add_argument("--limit", type=int, default=50)
    context(changes)
    resume = commands.add_parser("resume")
    context(resume, required=True)
    pages(resume)
    for verb in ("post", "reply"):
        command = commands.add_parser(verb)
        if verb == "reply":
            command.add_argument("id")
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--file", help="JSON object, or - for stdin; supports acknowledgement requests")
        source.add_argument("--text")
        command.add_argument("--as-role")
        command.add_argument("--chat")
        if verb == "post":
            command.add_argument("--title")
            command.add_argument("--repo", action="append")
            context(command)
        write(command)
    for verb in ("ack", "cancel"):
        command = commands.add_parser(verb)
        command.add_argument("id")
        if verb == "ack":
            command.add_argument("--target", action="append", help="Explicit acknowledgement target slot ID; repeat for multiple slots")
        write(command)
    waiting = commands.add_parser("wait")
    waiting.add_argument("id")
    waiting.add_argument("--timeout", type=_wait_timeout, default=30)
    roles = commands.add_parser("roles").add_subparsers(dest="role_action", required=True)
    role_list = roles.add_parser("list")
    context(role_list)
    pages(role_list)
    create = roles.add_parser("create")
    create.add_argument("--label", required=True)
    context(create, required=True)
    write(create)
    for verb in ("claim", "release", "offer", "accept", "decline", "withdraw"):
        command = roles.add_parser(verb)
        command.add_argument("id")
        command.add_argument("--revision", type=int, required=True)
        if verb == "offer":
            command.add_argument("--to", required=True, help="Successor's stable communication identity")
            command.add_argument("--note")
        write(command)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="worklog", description=__doc__)
    root.add_argument("--data-dir", help="Override persistent data directory")
    root.add_argument("--port", type=int, help=f"Loopback port (default: WORKLOG_PORT or {DEFAULT_PORT})")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("start", "stop", "check", "serve", "backup", "backups", "restore", "export"):
        sub = commands.add_parser(command, help="Internal server entry" if command == "serve" else None)
        sub.add_argument("--data-dir", default=argparse.SUPPRESS)
        if command in {"start", "stop", "check", "serve"}:
            sub.add_argument("--port", type=int, default=argparse.SUPPRESS)
        if command in {"restore", "export"}:
            sub.add_argument("file")
    agent = commands.add_parser(
        "agent", help="Agent commands; start with `worklog agent guide`",
        description="Commands for AI agents. `worklog agent guide` explains every one, with the rules agents follow.")
    agent.add_argument("--url", help="Agent base URL (default: the selected local port)")
    agent.add_argument("--port", type=int, default=argparse.SUPPRESS)
    agent.add_argument("--data-dir", default=argparse.SUPPRESS, help="Data directory, which keeps the tokens of registered agents")
    agent.add_argument("--as", dest="as_name", metavar="NAME",
                       help="Act as an agent registered here with `register` (default: WORKLOG_AGENT_TOKEN)")
    actions = agent.add_subparsers(dest="agent_action", required=True)
    actions.add_parser("guide", help="Print the agent guide; needs no credential or running Worklog")
    register = actions.add_parser("register", help="Register a new agent identity for your chat; its token is generated and kept here, never printed")
    register.add_argument("--chat", required=True, metavar="CHAT_ID",
                          help="Your chat's ID: a Claude Code session ID, a Claude desktop local_ ID or a Codex thread ID. "
                               "Your runs report it, and the work they report tracks your chat")
    register.add_argument("--name", help="Local name for --as NAME afterwards (default: the chat ID)")
    register.add_argument("--label", required=True, help="How the new agent appears in Worklog")
    register.add_argument("--avatar", help="An available name from `discussions avatars`")
    actions.add_parser("retire", help="Revoke the credential of the agent given with --as and forget its token")
    _discussion_parser(actions)
    _coordination_parser(actions)
    _ticket_parser(actions)
    inbox = actions.add_parser("inbox", help="Receive the messages the person wrote into your chats; each arrives once")
    inbox.add_argument("--chat", help="Only messages for your open runs that reported this chat ID")
    inbox.add_argument("--request-id", help="Reuse after an uncertain result to receive the same messages again")
    context = actions.add_parser("context")
    context.add_argument("--date")
    context.add_argument("--repo", dest="repositoryId", help="Canonical repository ID")
    context.add_argument("--item", dest="itemId", help="Work item ID")
    search = actions.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--archived", action="store_true")
    # Read-only: the person writes resources, and the client has no route that changes one.
    resources = actions.add_parser("resources", help="Read the reference pages the person keeps for you and themselves").add_subparsers(
        dest="resource_action", required=True)
    listing = resources.add_parser("list", help="Titles and summaries by title, without the pages' text")
    listing.add_argument("--q", help="Words from a page's title, summary or text")
    listing.add_argument("--archived", action="store_true", help="List archived pages")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)
    resources.add_parser("show", help="One page with its full Markdown text").add_argument("id")
    # context lists roadmaps without their steps and search shows snippets; these read the whole text.
    roadmap = actions.add_parser("roadmap", help="Read a roadmap with the full text of every step")
    roadmap.add_subparsers(dest="roadmap_action", required=True).add_parser("show").add_argument("id")
    step = actions.add_parser("step", help="Read one step's full text, progress and notes")
    step.add_subparsers(dest="step_action", required=True).add_parser("show").add_argument("id")
    proposals = actions.add_parser("proposals")
    proposals.add_argument("--status")
    proposal = actions.add_parser("proposal")
    operations = proposal.add_subparsers(dest="proposal_action", required=True)
    for verb in ("draft", "update", "submit", "reopen", "show", "withdraw", "evidence"):
        operation = operations.add_parser(verb)
        if verb != "draft":
            operation.add_argument("id")
        if verb in {"draft", "update", "evidence"}:
            operation.add_argument("--file", required=True)
        if verb in {"update", "submit", "reopen", "withdraw"}:
            operation.add_argument("--revision", required=True, type=int)
        if verb == "evidence":
            operation.add_argument("--name")
    return root


def _publish_new_file(output: Path, produce: Any) -> None:
    """Write produce() to output without ever overwriting it or leaving a partial file.

    The bytes go to a temporary file beside the target, then are published by a rename
    (Windows) or hard link (elsewhere); both fail if the target already exists.
    """
    exists = f"{output} already exists; choose another path"
    if output.exists():
        raise ValueError(exists)
    descriptor, name = tempfile.mkstemp(dir=output.parent, prefix=".worklog-export-", suffix=".zip")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(produce())
        try:
            if os.name == "nt":
                os.rename(temporary, output)
            else:
                os.link(temporary, output)
        except FileExistsError:
            raise ValueError(exists) from None
    finally:
        temporary.unlink(missing_ok=True)


def _redacted(value: str, tokens: list[str]) -> str:
    for token in tokens:
        if token:
            value = value.replace(token, "[redacted]")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # Every credential this command holds, including one generated or loaded on the way.
    redact = [os.environ.get("WORKLOG_AGENT_TOKEN", "")]
    try:
        if args.command == "agent" and args.agent_action == "guide":
            # Plain Markdown rather than JSON, readable before an agent has a credential. The guide is
            # ASCII, so it reaches the reader intact whatever code page a pipe or console uses.
            sys.stdout.write(AGENT_GUIDE.read_text(encoding="ascii"))
            return 0
        if args.command == "agent":
            result = agent_command(args, resolve_port(args.port), redact)
        else:
            directory = data_directory(args.data_dir)
            database = directory / "worklog.sqlite3"
            if args.command == "serve":
                serve(directory, resolve_port(args.port))
                return 0
            if args.command in {"start", "stop", "check"}:
                result = {"start": start, "stop": stop, "check": check}[args.command](
                    directory, resolve_port(args.port))
            elif args.command == "backup":
                result = backup_database(database, directory / "backups")
            elif args.command == "backups":
                result = list_backups(directory / "backups")
            elif args.command == "restore":
                result = restore_database(database, Path(args.file), directory / "backups")
            else:
                output = Path(args.file).resolve()
                _publish_new_file(output, lambda: export_database(database))
                result = {"exported": str(output), "size": output.stat().st_size}
        # ASCII JSON: \u escapes reach the reader intact whatever code page a pipe or console uses.
        print(_redacted(json.dumps(result, indent=2), redact))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print("Worklog: " + _redacted(str(exc), redact), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
