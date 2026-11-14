"""Prepare and reopen provider chats linked to human-owned Worklog items.

The provider CLI creates each session once. Worklog persists an idempotent launch receipt before
starting that process, then atomically links the returned chat and completes the receipt.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .common import AppError, now
from .db import Database
from .records import get_item
from .repository_links import linked_repositories
from .services import WorkService

RECEIPT_PREFIX = "session-launch:"
PREPARE_TIMEOUT_SECONDS = 90
OPEN_TIMEOUT_SECONDS = 30
CLAUDE_AUTH_OVERRIDES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
logger = logging.getLogger("worklog")
_SESSION_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class _ProviderFailure(RuntimeError):
    """A provider failure with no provider output attached."""


@dataclass(frozen=True)
class _LaunchJob:
    launcher: SessionLauncher
    identity: str
    actor: dict[str, Any]
    provider: str
    item_id: str
    item_title: str
    cwd: str
    prompt: str
    prepare: Callable[[str, str, str], tuple[str, str]]


def _safe_session_id(value: Any) -> str:
    if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
        raise _ProviderFailure("provider returned an invalid session identifier")
    return str(uuid.UUID(value))


def _clean_model(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value) > 200 or any(ord(ch) < 32 for ch in value):
        return ""
    return value


def _chat_title(provider: str, item_title: str) -> str:
    prefix = f"{provider.title()} — "
    return (prefix + item_title).encode("utf-8")[:1000].decode("utf-8", errors="ignore")

def _cli_executable(name: str) -> str:
    for candidate in (shutil.which(f"{name}.exe"), shutil.which(name)):
        if candidate and Path(candidate).suffix.lower() not in {".cmd", ".bat", ".ps1"}:
            return candidate
    raise FileNotFoundError(f"{name} CLI is unavailable")


def _codex_executable() -> list[str]:
    direct = None
    for candidate in (shutil.which("codex.exe"), shutil.which("codex")):
        if candidate and Path(candidate).suffix.lower() not in {".cmd", ".bat", ".ps1"}:
            direct = candidate
            break
    if direct:
        return [direct]

    npm_roots: list[Path] = []
    for wrapper in (shutil.which("codex.ps1"), shutil.which("codex.cmd"), shutil.which("codex.bat")):
        if wrapper:
            npm_roots.append(Path(wrapper).parent)
    appdata = os.environ.get("APPDATA")
    if appdata:
        npm_roots.append(Path(appdata) / "npm")

    for root in npm_roots:
        package = root / "node_modules" / "@openai" / "codex"
        native = (package / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" /
                  "x86_64-pc-windows-msvc" / "bin" / "codex.exe")
        if native.is_file():
            return [str(native)]
        script = package / "bin" / "codex.js"
        node = shutil.which("node.exe") or shutil.which("node")
        if script.is_file() and node:
            return [node, str(script)]
    raise FileNotFoundError("Codex CLI is unavailable")


def _child_environment(provider: str) -> dict[str, str]:
    env = os.environ.copy()
    if provider == "claude":
        for name in CLAUDE_AUTH_OVERRIDES:
            env.pop(name, None)
    return env


def _initialization_prompt(item_id: str, title: str, task_prompt: str) -> str:
    return (
        f"This desktop chat is being prepared for Worklog item {item_id}: {title}\n\n"
        f"Task request to retain as context:\n{task_prompt}\n\n"
        "This is preparation only. Do not inspect files, use tools, run commands, or begin task work. "
        "Respond exactly WORKLOG_READY, then wait. Begin only after the user sends a separate "
        "message containing Continue in this desktop chat."
    )


def _process_options(*, new_console: bool = False) -> dict[str, Any]:
    if os.name != "nt":
        return {"start_new_session": True}
    if new_console:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        return {
            "creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            "startupinfo": startupinfo,
        }
    return {
        "creationflags": (getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                          getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
    }


def _kill_owned_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate only the provider process started by this service and its descendants."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        taskkill = shutil.which("taskkill.exe") or "taskkill.exe"
        try:
            subprocess.run(
                [taskkill, "/T", "/F", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            try:
                process.kill()
            except OSError:
                pass
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


_ACTIVE_PROCESSES: set[subprocess.Popen[bytes]] = set()
_ACTIVE_LOCK = threading.Lock()


def _stop_active_processes() -> None:
    with _ACTIVE_LOCK:
        processes = list(_ACTIVE_PROCESSES)
    for process in processes:
        _kill_owned_tree(process)
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


atexit.register(_stop_active_processes)


def _run_cli(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    input_bytes: bytes | None,
    timeout: int,
    capture_output: bool,
    new_console: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    options: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdin": subprocess.PIPE if input_bytes is not None else None,
        "stdout": subprocess.PIPE if capture_output else None,
        "stderr": subprocess.PIPE if capture_output else None,
        "close_fds": True,
        "shell": False,
        **_process_options(new_console=new_console),
    }
    process = subprocess.Popen(command, **options)
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES.add(process)
    try:
        try:
            stdout, stderr = process.communicate(input=input_bytes, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_owned_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    stdout, stderr = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    stdout, stderr = None, None
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from exc
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE_PROCESSES.discard(process)


def _prepare_session(provider: str, cwd: str, prompt: str) -> tuple[str, str]:
    """Create one completed, ready-only provider turn and return its durable session ID and model."""
    if provider == "codex":
        command = [*_codex_executable(), "exec", "--json", "--sandbox", "read-only", "-"]
        completed = _run_cli(
            command,
            cwd=cwd,
            env=_child_environment(provider),
            input_bytes=prompt.encode("utf-8"),
            timeout=PREPARE_TIMEOUT_SECONDS,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise _ProviderFailure("Codex did not complete session preparation")
        thread_id = ""
        model: Any = ""
        turn_completed = False
        output = (completed.stdout or b"").decode("utf-8-sig", errors="replace")
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "thread.started":
                thread = event.get("thread")
                candidate = event.get("thread_id")
                if not isinstance(candidate, str) and isinstance(thread, dict):
                    candidate = thread.get("id")
                if isinstance(candidate, str):
                    thread_id = candidate
            elif event.get("type") == "turn.started":
                turn = event.get("turn")
                if isinstance(turn, dict) and isinstance(turn.get("model"), str):
                    model = turn["model"]
                elif isinstance(event.get("model"), str):
                    model = event["model"]
            elif event.get("type") == "turn.completed":
                turn_completed = True
        if not turn_completed:
            raise _ProviderFailure("Codex did not confirm session preparation")
        return _safe_session_id(thread_id), _clean_model(model)

    if provider == "claude":
        session_id = str(uuid.uuid4())
        command = [
            _cli_executable("claude"), "-p", "--session-id", session_id,
            "--output-format", "json", "--max-turns", "1", "--tools", "",
        ]
        completed = _run_cli(
            command,
            cwd=cwd,
            env=_child_environment(provider),
            input_bytes=prompt.encode("utf-8"),
            timeout=PREPARE_TIMEOUT_SECONDS,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise _ProviderFailure("Claude did not complete session preparation")
        try:
            result = json.loads((completed.stdout or b"").decode("utf-8-sig", errors="replace"))
        except (TypeError, ValueError) as exc:
            raise _ProviderFailure("Claude did not return a session receipt") from exc
        if not isinstance(result, dict) or result.get("is_error"):
            raise _ProviderFailure("Claude did not confirm session preparation")
        reported_id = result.get("session_id")
        if not isinstance(reported_id, str) or _safe_session_id(reported_id) != session_id:
            raise _ProviderFailure("Claude returned an invalid session identifier")
        return session_id, _clean_model(result.get("model"))

    raise _ProviderFailure("Unsupported provider")


def _open_desktop(provider: str, cwd: str, chat_id: str) -> None:
    """Open an already prepared chat, waiting for the provider handoff to finish."""
    chat_id = _safe_session_id(chat_id)
    if provider == "codex":
        uri = f"codex://threads/{quote(chat_id, safe='-')}"
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise OSError("Codex desktop URL handling is unavailable")
        startfile(uri)
        return

    if provider == "claude":
        command = [_cli_executable("claude"), "--desktop", "--resume", chat_id]
        completed = _run_cli(
            command,
            cwd=cwd,
            env=_child_environment(provider),
            input_bytes=None,
            timeout=OPEN_TIMEOUT_SECONDS,
            capture_output=False,
            new_console=True,
        )
        if completed.returncode != 0:
            raise _ProviderFailure("Claude Desktop did not accept the prepared chat")
        return

    raise _ProviderFailure("Unsupported provider")

class SessionLauncher:
    """Durable launch receipts and at most two preparations, with no automatic provider retries."""

    def __init__(self, db: Database, work: WorkService):
        self.db, self.work = db, work
        self.token = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.workers: dict[str, threading.Thread] = {}
        self.opening: set[str] = set()

    @staticmethod
    def _namespace(actor: dict) -> str:
        if actor.get("kind") != "human" or not actor.get("id"):
            raise AppError(403, "agent_scope", "Only the person starts desktop sessions")
        return RECEIPT_PREFIX + actor["id"]

    def _load(self, c: Any, identity: str, actor: dict) -> dict:
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise AppError(404, "not_found", "Session launch not found")
        row = c.execute("SELECT response_json FROM receipts WHERE actor_id=? AND key=?",
                        (self._namespace(actor), identity)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Session launch not found")
        return json.loads(row["response_json"])

    def _save(self, c: Any, identity: str, actor: dict, record: dict) -> None:
        changed = c.execute("UPDATE receipts SET response_json=? WHERE actor_id=? AND key=?",
                            (json.dumps(record), self._namespace(actor), identity))
        if changed.rowcount != 1:
            raise AppError(404, "not_found", "Session launch not found")

    @staticmethod
    def _validate(payload: dict) -> tuple[str, str, str]:
        provider, cwd, prompt = payload.get("provider"), payload.get("cwd"), payload.get("prompt")
        if provider not in {"codex", "claude"}:
            raise AppError(422, "validation", "Choose Codex or Claude")
        if (not isinstance(cwd, str) or len(cwd) > 2000 or "\0" in cwd or
                not re.match(r"^[A-Za-z]:[\\/]", cwd)):
            raise AppError(422, "validation", "Choose an absolute local drive working folder")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
            raise AppError(422, "validation", "Enter a task prompt of at most 12,000 characters")
        return provider, cwd, prompt

    @staticmethod
    def _folder(cwd: str) -> str:
        try:
            path = Path(cwd).resolve(strict=True)
            if not re.match(r"^[A-Za-z]:[\\/]", str(path)) or not path.is_dir():
                raise ValueError("not a local folder")
        except (OSError, RuntimeError, ValueError) as exc:
            raise AppError(422, "validation", "Choose an existing local drive working folder") from exc
        return str(path)

    def start(self, item_id: str, payload: dict, actor: dict, key: str | None) -> dict:
        namespace = self._namespace(actor)
        if not key:
            raise AppError(428, "idempotency", "An Idempotency-Key is required for this command")
        if not 8 <= len(key) <= 200:
            raise AppError(422, "idempotency", "Use an idempotency key between 8 and 200 characters")
        provider, cwd, prompt = self._validate(payload)
        identity = hashlib.sha256(key.encode()).hexdigest()
        digest = hashlib.sha256(json.dumps({"item": item_id, "payload": payload}, sort_keys=True).encode()).hexdigest()
        with self.lock:
            with self.db.read() as c:
                row = c.execute("SELECT request_hash FROM receipts WHERE actor_id=? AND key=?",
                                (namespace, identity)).fetchone()
            if row is not None:
                if row["request_hash"] != digest:
                    raise AppError(409, "idempotency_conflict", "This key was already used for a different session request")
                return self.get(identity, actor)
            if len(self.workers) >= 2:
                raise AppError(429, "session_limit", "Two chats are being prepared. Wait for one to finish.")
            folder = self._folder(cwd)
            with self.db.transaction() as c:
                item = get_item(c, item_id, active=True)
                result = {"id": identity, "provider": provider, "status": "starting", "chat": None,
                          "sessionId": None, "message": "Preparing a linked desktop chat…"}
                record = {"kind": "session-launch", "processToken": self.token, "cwd": folder,
                          "itemId": item_id, "itemTitle": item["title"], "result": result}
                c.execute("INSERT INTO receipts(actor_id,key,request_hash,response_json,created_at) VALUES(?,?,?,?,?)",
                          (namespace, identity, digest, json.dumps(record), now()))
            job = _LaunchJob(self, identity, dict(actor), provider, item_id, item["title"], folder, prompt, _prepare_session)
            worker = threading.Thread(target=self._run_launch, args=(job,), name="worklog-session-prepare", daemon=True)
            self.workers[identity] = worker
            try:
                worker.start()
            except Exception:
                self.workers.pop(identity, None)
                self._state(identity, actor, "failed", "Preparation could not be started. No provider session was requested.")
            return self.get(identity, actor)

    def get(self, identity: str, actor: dict) -> dict:
        with self.lock, self.db.transaction() as c:
            record = self._load(c, identity, actor)
            result = record["result"]
            worker = self.workers.get(identity)
            alive = identity in self.opening or (worker is not None and worker.is_alive())
            if result["status"] == "starting" and (record.get("processToken") != self.token or not alive):
                result.update(status="failed", message="Preparation was interrupted. It was not retried. "
                              "Reopen the saved session if available; otherwise check provider history before starting another.")
                self._save(c, identity, actor, record)
            return dict(result)

    def _state(self, identity: str, actor: dict, status: str, message: str) -> None:
        with self.lock, self.db.transaction() as c:
            record = self._load(c, identity, actor)
            record["processToken"] = self.token
            record["result"].update(status=status, message=message)
            self._save(c, identity, actor, record)

    def _record_session(self, job: _LaunchJob, chat_id: str, model: str) -> None:
        # Commit the provider ID first. A later local link failure must never discard it.
        with self.lock, self.db.transaction() as c:
            record = self._load(c, job.identity, job.actor)
            record["result"]["sessionId"] = _safe_session_id(chat_id)
            record["model"] = _clean_model(model)
            self._save(c, job.identity, job.actor, record)

    def _link_prepared(self, identity: str, actor: dict) -> dict:
        with self.lock, self.db.transaction() as c:
            record = self._load(c, identity, actor)
            result = record["result"]
            if result["chat"] is None:
                chat_id = _safe_session_id(result.get("sessionId"))
                item = get_item(c, record["itemId"], active=True)
                result["chat"] = self.work.apply(c, actor, "chat.create", {
                    "ownerType": "item", "ownerId": record["itemId"], "chatId": chat_id,
                    "title": _chat_title(result["provider"], item["title"]),
                    "provider": result["provider"], "model": record.get("model", ""),
                    "repositoryIds": linked_repositories(c, "item", [record["itemId"]]).get(record["itemId"], []),
                })
                self._save(c, identity, actor, record)
            return record

    def _handoff(self, identity: str, actor: dict) -> None:
        record = self._link_prepared(identity, actor)
        result = record["result"]
        _open_desktop(result["provider"], record["cwd"], result["chat"]["chatId"])
        self._state(identity, actor, "ready", "Chat prepared and linked. Open request sent. "
                    "Send Continue in the desktop app to begin work.")

    def _failure(self, identity: str, actor: dict, exc: Exception) -> None:
        # Never include provider output, prompt text, environment values or exception messages.
        logger.warning("Desktop session preparation/handoff failed (%s)", type(exc).__name__)
        with self.lock, self.db.transaction() as c:
            record = self._load(c, identity, actor)
            result = record["result"]
            if result.get("sessionId") and result["chat"] is None:
                message = "The provider chat is saved, but its Worklog link could not be saved. Retry linking and opening this session."
            elif result["chat"] is not None:
                message = "The linked chat could not be opened. Use Open in app to retry this same chat."
            elif isinstance(exc, FileNotFoundError):
                message = "The provider CLI could not be found. Check its installation before starting another session."
            else:
                message = "Preparation could not be confirmed. It was not retried; check provider history before starting another session."
            result.update(status="failed", message=message)
            self._save(c, identity, actor, record)

    def _run_launch(self, job: _LaunchJob) -> None:
        try:
            with self.db.read() as c:
                get_item(c, job.item_id, active=True)
            chat_id, model = job.prepare(job.provider, job.cwd,
                                         _initialization_prompt(job.item_id, job.item_title, job.prompt))
            self._record_session(job, chat_id, model)
            self._handoff(job.identity, job.actor)
        except Exception as exc:
            try:
                self._failure(job.identity, job.actor, exc)
            except Exception:
                # Once this worker leaves, GET recovers its stranded pending receipt. A database
                # outage must not permanently consume a worker slot or trigger a new provider run.
                logger.error("Could not save session launch failure; its receipt remains recoverable")
        finally:
            with self.lock:
                self.workers.pop(job.identity, None)

    def open(self, identity: str, actor: dict) -> dict:
        with self.lock:
            result = self.get(identity, actor)
            if result["status"] == "starting" or identity in self.opening:
                return result
            if result["chat"] is None and not result.get("sessionId"):
                raise AppError(409, "session_launch", "No saved provider session is available to reopen")
            self.opening.add(identity)
        try:
            self._state(identity, actor, "starting", "Opening the saved session…")
            self._handoff(identity, actor)
        except Exception as exc:
            self._failure(identity, actor, exc)
        finally:
            with self.lock:
                self.opening.discard(identity)
        return self.get(identity, actor)
