"""Local lifecycle/recovery commands and the deliberately limited agent HTTP client."""

from __future__ import annotations

import argparse
import ctypes
import json
import mimetypes
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

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


def _open(request: Any, *, timeout: float) -> Any:
    # Local credentials must never reach an environment proxy or a redirect target.
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


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
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        with (directory / "server.log").open("ab") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "worklog.cli", "--data-dir", str(directory), "--port", str(port), "serve"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment,
                creationflags=flags, start_new_session=os.name != "nt", close_fds=True,
            )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Worklog startup failed; inspect {directory / 'server.log'}")
            state = _json_file(directory / "server-state.json")
            health = _health(port)
            if _owned(directory, health, state):
                return {"running": True, "url": local_url(port), **(health or {}), "pid": state["pid"],
                        "dataDir": str(directory), "reused": False}
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
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if _process_identity(int(state["pid"])) != state["process"]:
                return {"running": False, "stopped": True}
            time.sleep(0.1)
        raise RuntimeError("Graceful shutdown did not finish; no process was forcefully terminated")


def serve(directory: Path, port: int = DEFAULT_PORT) -> None:
    """Private foreground entry point; start launches this in one hidden process."""
    import uvicorn

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
        server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, access_log=False))
        finished = threading.Event()

        def watch() -> None:
            next_backup = time.monotonic() + 60
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


class AgentClient:
    def __init__(self, base_url: str | None = None) -> None:
        base_url = base_url or local_url(resolve_port())
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname != HOST or parsed.username
                or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("Agent URL must be plain HTTP on literal 127.0.0.1 without a path or credentials")
        self.base = base_url.rstrip("/")
        self.token = os.environ.get("WORKLOG_AGENT_TOKEN", "")
        if not self.token:
            raise ValueError("Set WORKLOG_AGENT_TOKEN to an agent credential issued in Worklog settings")

    def request(self, method: str, path: str, payload: Any = None,
                *, raw: bytes | None = None, content_type: str | None = None) -> Any:
        # Explicit route allowlist makes canonical writes and approval unreachable from this client.
        route = path.split("?", 1)[0]
        segments = route.strip("/").split("/")
        allowed = method == "GET" and route in {"/context", "/search", "/proposals"}
        def valid_id(value: str) -> bool:
            try:
                return str(uuid.UUID(value)) == value.lower()
            except ValueError:
                return False

        if len(segments) == 2 and segments[0] == "proposals":
            allowed = method in {"GET", "PATCH"} and valid_id(segments[1])
        if len(segments) == 3 and segments[0] == "proposals" and segments[2] in {"submit", "withdraw"}:
            allowed = method == "POST" and valid_id(segments[1])
        if route == "/proposals" and method == "POST":
            allowed = True
        if route == "/attachments" and method == "POST":
            from urllib.parse import parse_qs

            query = parse_qs(urlsplit(path).query)
            ids = query.get("ownerId", [])
            allowed = query.get("ownerType") == ["proposal"] and len(ids) == 1 and valid_id(ids[0])
        if not allowed:
            raise ValueError("Agent command is outside proposal/read scope")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        data = raw
        if raw is not None:
            headers["Content-Type"] = content_type or "application/octet-stream"
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if method == "POST":
            headers["Idempotency-Key"] = str(uuid.uuid4())
        request = Request(self.base + "/api/v1" + path, data=data, headers=headers, method=method)
        try:
            with _open(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                error = json.loads(exc.read()).get("error", {})
                message = str(error.get("message", "Request rejected"))
            except (ValueError, AttributeError):
                message = "Request rejected"
            # Do not reflect arbitrary server content containing a credential.
            raise RuntimeError(f"HTTP {exc.code}: " + message.replace(self.token, "[redacted]")) from None
        except URLError:
            raise RuntimeError("Cannot reach Worklog; run worklog check") from None


def _payload(path: str) -> dict[str, Any]:
    value = json.loads(sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Proposal file must contain a JSON object")
    return value


def agent_command(args: argparse.Namespace, port: int) -> Any:
    client = AgentClient(args.url or local_url(port))
    if args.agent_action == "context":
        return client.request("GET", "/context" + ("?" + urlencode({"date": args.date}) if args.date else ""))
    if args.agent_action == "search":
        return client.request("GET", "/search?" + urlencode({"q": args.query, "limit": args.limit,
                                                            "archived": str(args.archived).lower()}))
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
    agent = commands.add_parser("agent", help="Read context and propose reviewed changes")
    agent.add_argument("--url", help="Agent base URL (default: the selected local port)")
    agent.add_argument("--port", type=int, default=argparse.SUPPRESS)
    actions = agent.add_subparsers(dest="agent_action", required=True)
    context = actions.add_parser("context")
    context.add_argument("--date")
    search = actions.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--archived", action="store_true")
    proposals = actions.add_parser("proposals")
    proposals.add_argument("--status")
    proposal = actions.add_parser("proposal")
    operations = proposal.add_subparsers(dest="proposal_action", required=True)
    for verb in ("draft", "update", "submit", "show", "withdraw", "evidence"):
        operation = operations.add_parser(verb)
        if verb != "draft":
            operation.add_argument("id")
        if verb in {"draft", "update", "evidence"}:
            operation.add_argument("--file", required=True)
        if verb in {"update", "submit", "withdraw"}:
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


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "agent":
            result = agent_command(args, resolve_port(args.port))
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
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        token = os.environ.get("WORKLOG_AGENT_TOKEN")
        print(rendered.replace(token, "[redacted]") if token else rendered)
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        token = os.environ.get("WORKLOG_AGENT_TOKEN")
        message = str(exc).replace(token, "[redacted]") if token else str(exc)
        print("Worklog: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
