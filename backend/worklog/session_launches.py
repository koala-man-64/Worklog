"""Prepare and reopen provider chats linked to human-owned Worklog items, and open the chat of an
agent a request waits on.

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
import sqlite3
import subprocess
import sys
import threading
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .claude_settings import require_claude_settings
from .common import AppError, now
from .db import Database
from .leases import LeaseService, policy_record, repository_slot_summaries
from .models import CLAUDE_SESSION_EFFORTS
from .records import get_item
from .registry import AgentRegistry
from .repository_links import linked_repositories
from .services import WorkService
from .transcripts import TranscriptReader

RECEIPT_PREFIX = "session-launch:"
PREPARE_TIMEOUT_SECONDS = 90
OPEN_TIMEOUT_SECONDS = 30
CHILD_ENVIRONMENT = frozenset({
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "SYSTEMDRIVE", "USERNAME",
    "OS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS",
})
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
    requested_options: dict[str, str]
    prepare: Callable[[str, str, str, dict[str, str]], tuple[str, str]]


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
    # Use the desktop subscription's saved profile, never unrelated service credentials or
    # alternate API/cloud authentication inherited by the Worklog server.
    profile = "CODEX_HOME" if provider == "codex" else "CLAUDE_CONFIG_DIR"
    allowed = CHILD_ENVIRONMENT | {profile}
    return {name: value for name, value in os.environ.items() if name.upper() in allowed}


def _slot_header(
    *,
    repo_name: str,
    repo_id: str,
    slot_index: int | None,
    max_slots: int,
    worktree_path: str,
    branch: str,
    lease_id: str,
    revision: int,
) -> str:
    slot_desc = f"{slot_index}/{max_slots}" if slot_index is not None else f"?/{max_slots}"
    lines = [
        "Worklog Checkout Slot Assignment:",
        f"- Repository: {repo_name} ({repo_id})",
        f"- Slot: {slot_desc}",
        f"- Worktree Path: {worktree_path or '(logical lease)'}",
        f"- Branch: {branch or '(unassigned)'}",
        f"- Lease ID: {lease_id}",
    ]
    if worktree_path:
        lines.append(f"- Edit files strictly inside {worktree_path}; never edit the read-only base checkout.")
    lines.append(
        f"- Renew if needed: worklog agent --as NAME leases renew {lease_id} --revision {revision} --request-id lease-renew-{lease_id[:8]}"
    )
    lines.append(
        f"- Release after pushing PR: worklog agent --as NAME leases release {lease_id} --revision {revision} --request-id lease-rel-{lease_id[:8]}"
    )
    return "\n".join(lines)


def _antigravity_brief(
    item_id: str,
    item_title: str,
    task_prompt: str,
    slot_header: str,
) -> str:
    return (
        f"Worklog item {item_id}: {item_title}\n\n"
        f"{slot_header}\n\n"
        f"Task request:\n{task_prompt}\n"
    )


def _initialization_prompt(item_id: str, title: str, task_prompt: str, slot_header: str = "") -> str:
    header = f"\n\n{slot_header}" if slot_header else ""
    return (
        f"This desktop chat is being prepared for Worklog item {item_id}: {title}{header}\n\n"
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


_CODEX_SANDBOX_MODES = frozenset({"read-only", "workspace-write", "danger-full-access"})
_MAX_CODEX_CONFIG_BYTES = 262_144


def _codex_configured_sandbox(env: dict[str, str]) -> tuple[str, bool]:
    """Read the user's configured Codex sandbox_mode so push-woken chats start children inherit it.

    Never elevates past what the user configured in CODEX_HOME/config.toml; falls back to read-only.
    """
    codex_home_raw = env.get("CODEX_HOME")
    if codex_home_raw:
        codex_home = Path(codex_home_raw)
    else:
        home_raw = env.get("USERPROFILE") or env.get("HOME")
        codex_home = (Path(home_raw) if home_raw else Path.home()) / ".codex"
    config_path = codex_home / "config.toml"
    try:
        if not config_path.is_file() or config_path.stat().st_size > _MAX_CODEX_CONFIG_BYTES:
            return "read-only", False
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError):
        return "read-only", False
    if not isinstance(parsed, dict):
        return "read-only", False
    mode = parsed.get("sandbox_mode")
    if not isinstance(mode, str) or mode not in _CODEX_SANDBOX_MODES:
        return "read-only", False
    ws = parsed.get("sandbox_workspace_write")
    network_access = mode == "workspace-write" and isinstance(ws, dict) and ws.get("network_access") is True
    return mode, network_access


def _prepare_session(
    provider: str, cwd: str, prompt: str, requested_options: dict[str, str] | None = None,
    *, allow_non_git: bool = False,
) -> tuple[str, str]:
    """Create one completed, ready-only provider turn and return its durable session ID and model."""
    options = requested_options or {}
    requested_model = options.get("requestedModel")
    reasoning_effort = options.get("reasoningEffort")
    if provider == "codex":
        child_env = _child_environment(provider)
        sandbox_mode = "read-only"
        ws_network = False
        if allow_non_git:
            # Codex CLI persists the preparation turn's sandbox policy onto the thread and replays
            # it when `codex queue --resume` wakes the session. Inherit the user's configured
            # sandbox_mode here while keeping all tools disabled for this preparation turn.
            sandbox_mode, ws_network = _codex_configured_sandbox(child_env)
        command = [
            *_codex_executable(), "exec", "--json", "--sandbox", sandbox_mode,
            "--ignore-user-config", "--disable", "shell_tool", "--disable", "multi_agent",
            "--disable", "apps", "--disable", "plugins",
            "--disable", "skill_mcp_dependency_install",
            "-c", 'web_search="disabled"',
        ]
        if ws_network:
            command.extend(["-c", "sandbox_workspace_write.network_access=true"])
        if allow_non_git:
            command.append("--skip-git-repo-check")
        if requested_model:
            command.extend(["--model", requested_model])
        if reasoning_effort:
            command.extend(["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
        command.append("-")
        completed = _run_cli(
            command,
            cwd=cwd,
            env=child_env,
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
        if requested_model:
            command.extend(["--model", requested_model])
        if reasoning_effort:
            command.extend(["--effort", reasoning_effort])
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
    """Durable launch receipts, repo-slot dispatch queueing, and at most two concurrent preparations."""

    def __init__(self, db: Database, work: WorkService, leases: LeaseService | None = None):
        self.db, self.work, self.leases = db, work, leases
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
    def _dispatch_receipt(c: sqlite3.Connection, identity: str) -> dict:
        row = c.execute(
            "SELECT response_json FROM receipts WHERE actor_id=? AND key=?",
            (RECEIPT_PREFIX + "local-user", identity),
        ).fetchone()
        return json.loads(row["response_json"]) if row is not None else {}

    @staticmethod
    def _requested_options(provider: str, payload: dict) -> dict[str, str]:
        model = payload.get("model")
        effort = payload.get("reasoningEffort")
        if provider == "antigravity" and (model is not None or effort is not None):
            raise AppError(422, "validation", "Model and reasoning settings are available for Codex and Claude")
        if provider == "claude" and effort is not None and effort not in CLAUDE_SESSION_EFFORTS:
            raise AppError(422, "validation", "Choose low, medium, high, xhigh, or max for Claude",
                           {"reasoningEffort": "invalid"})
        return {key: value for key, value in (("requestedModel", model), ("reasoningEffort", effort))
                if value is not None}

    @staticmethod
    def _validate(payload: dict) -> tuple[str, str | None, str | None, str, str, str, str]:
        provider = payload.get("provider")
        repository_id = payload.get("repositoryId")
        cwd = payload.get("cwd")
        branch = str(payload.get("branch") or "").strip()
        mode = str(payload.get("mode") or "shared")
        priority = str(payload.get("priority") or "normal")
        prompt = payload.get("prompt")
        if provider not in {"codex", "claude", "antigravity"}:
            raise AppError(422, "validation", "Choose Codex, Claude, or Antigravity")
        if repository_id is not None:
            if not isinstance(repository_id, str) or not repository_id.strip() or len(repository_id) > 200:
                raise AppError(422, "validation", "Invalid repositoryId", {"repositoryId": "invalid"})
            repository_id = repository_id.strip()
        if cwd is not None:
            if (not isinstance(cwd, str) or not cwd or len(cwd) > 2000 or "\0" in cwd or
                    not re.match(r"^[A-Za-z]:[\\/]", cwd)):
                raise AppError(422, "validation", "Choose an absolute local drive working folder")
        elif repository_id is None:
            # May auto-infer a single linked repository with a slot policy below; otherwise validated there.
            pass
        if mode not in {"shared", "exclusive"}:
            raise AppError(422, "validation", "Choose shared or exclusive mode", {"mode": "invalid"})
        if priority not in {"normal", "pr_fixup", "wave", "incident"}:
            raise AppError(422, "validation", "Invalid priority", {"priority": "invalid"})
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
            raise AppError(422, "validation", "Enter a task prompt of at most 12,000 characters")
        return provider, repository_id, cwd, branch, mode, priority, prompt

    @staticmethod
    def _folder(cwd: str) -> str:
        try:
            path = Path(cwd).resolve(strict=True)
            if not re.match(r"^[A-Za-z]:[\\/]", str(path)) or not path.is_dir():
                raise ValueError("not a local folder")
        except (OSError, RuntimeError, ValueError) as exc:
            raise AppError(422, "validation", "Choose an existing local drive working folder") from exc
        return str(path)

    def _infer_repository(self, c: sqlite3.Connection, item_id: str) -> str | None:
        linked = linked_repositories(c, "item", [item_id]).get(item_id, [])
        if len(linked) != 1:
            return None
        rid = linked[0]
        pol = c.execute(
            "SELECT base_checkout_path FROM repository_slot_policies WHERE repository_id=?",
            (rid,),
        ).fetchone()
        if pol is not None and pol["base_checkout_path"]:
            return rid
        return None

    def _dispatch_record(self, c: sqlite3.Connection, dq: sqlite3.Row) -> dict[str, Any]:
        lease = c.execute("SELECT * FROM repository_leases WHERE id=?", (dq["lease_id"],)).fetchone()
        repo = c.execute("SELECT id,name FROM repositories WHERE id=?", (dq["repository_id"],)).fetchone()
        item = c.execute("SELECT id,title FROM work_items WHERE id=?", (dq["item_id"],)).fetchone()
        pol = policy_record(c, dq["repository_id"])
        summary = repository_slot_summaries(c, [dq["repository_id"]]).get(dq["repository_id"], {})
        queue_position: int | None = None
        if lease is not None and lease["state"] == "queued":
            rows = c.execute(
                "SELECT id FROM repository_leases WHERE repository_id=? AND state='queued' "
                "ORDER BY CASE priority WHEN 'incident' THEN 0 WHEN 'wave' THEN 1 WHEN 'pr_fixup' THEN 2 ELSE 3 END, queue_seq ASC",
                (dq["repository_id"],),
            ).fetchall()
            queue_position = next((idx for idx, r in enumerate(rows, 1) if r["id"] == lease["id"]), None)
        chat: dict[str, Any] | None = None
        if dq["chat_record_id"]:
            chat_row = c.execute("SELECT * FROM agent_chats WHERE id=?", (dq["chat_record_id"],)).fetchone()
            if chat_row is not None:
                steps = [r[0] for r in c.execute("SELECT step_id FROM chat_steps WHERE chat_id=? ORDER BY created_at,step_id", (chat_row["id"],))]
                repos = linked_repositories(c, "chat", [chat_row["id"]]).get(chat_row["id"], [])
                chat = {
                    "id": chat_row["id"], "ownerType": chat_row["owner_type"], "ownerId": chat_row["owner_id"],
                    "chatId": chat_row["chat_id"], "title": chat_row["title"], "createdDate": chat_row["created_date"],
                    "archived": bool(chat_row["archived"]), "revision": int(chat_row["revision"]),
                    "createdAt": chat_row["created_at"], "updatedAt": chat_row["updated_at"],
                    "provider": chat_row["provider"], "model": chat_row["model"],
                    "repos": json.loads(chat_row["repos"]), "stepIds": steps, "repositoryIds": repos,
                }
        repo_name = repo["name"] if repo else dq["repository_id"]
        item_title = item["title"] if item else dq["item_id"]
        worktree_path = lease["worktree_path"] if lease else ""
        branch = (lease["branch"] if lease and lease["branch"] else dq["requested_branch"])
        slot_index = lease["slot_index"] if lease else None
        copyable_prompt: str | None = None
        if dq["status"] == "ready_manual" and lease is not None:
            header = _slot_header(
                repo_name=repo_name,
                repo_id=dq["repository_id"],
                slot_index=slot_index,
                max_slots=pol["maxActiveLeases"],
                worktree_path=worktree_path,
                branch=branch,
                lease_id=lease["id"],
                revision=int(lease["revision"]),
            )
            copyable_prompt = _antigravity_brief(dq["item_id"], item_title, dq["prompt"], header)
        return {
            "launch": {
                "id": dq["id"],
                "provider": dq["provider"],
                "status": dq["status"],
                "chat": chat,
                "message": dq["message"],
                "sessionId": dq["session_id"],
                "dispatchId": dq["id"],
                "repositoryId": dq["repository_id"],
                "repositoryName": repo_name,
                "leaseId": dq["lease_id"],
                "slotIndex": slot_index,
                "worktreePath": worktree_path,
                "branch": branch,
                "queuePosition": queue_position,
                "activeHolders": summary.get("activeLeases", []),
                "copyablePrompt": copyable_prompt,
                **self._dispatch_receipt(c, dq["id"]).get("requestedOptions", {}),
            },
            "item": {
                "id": dq["id"],
                "itemId": dq["item_id"],
                "itemTitle": item_title,
                "repositoryId": dq["repository_id"],
                "repositoryName": repo_name,
                "leaseId": dq["lease_id"],
                "provider": dq["provider"],
                "requestedBranch": dq["requested_branch"],
                "branch": branch,
                "slotIndex": slot_index,
                "worktreePath": worktree_path,
                "queuePosition": queue_position,
                "status": dq["status"],
                "sessionId": dq["session_id"],
                "chatRecordId": dq["chat_record_id"],
                "model": dq["model"],
                "message": dq["message"],
                "copyablePrompt": copyable_prompt,
                "revision": int(dq["revision"]),
                "createdAt": dq["created_at"],
                "updatedAt": dq["updated_at"],
            },
        }

    def start(self, item_id: str, payload: dict, actor: dict, key: str | None) -> dict:
        namespace = self._namespace(actor)
        if not key:
            raise AppError(428, "idempotency", "An Idempotency-Key is required for this command")
        if not 8 <= len(key) <= 200:
            raise AppError(422, "idempotency", "Use an idempotency key between 8 and 200 characters")
        provider, repository_id, cwd, branch, mode, priority, prompt = self._validate(payload)
        requested_options = self._requested_options(provider, payload)
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

            # Decide whether this launch uses repo-slot admission queueing or legacy raw cwd.
            effective_repo = repository_id
            if effective_repo is None and cwd is None and self.leases is not None:
                with self.db.read() as c:
                    effective_repo = self._infer_repository(c, item_id)

            if effective_repo is not None and self.leases is not None:
                return self._start_dispatched(
                    identity, namespace, digest, item_id, effective_repo,
                    provider, cwd, branch, mode, priority, prompt, requested_options, actor,
                )

            # Legacy raw-cwd launch path (or Antigravity with custom folder).
            if cwd is None:
                raise AppError(422, "validation", "Choose a repository or an absolute local drive working folder")
            if provider == "antigravity":
                folder = self._folder(cwd)
                with self.db.transaction() as c:
                    item = get_item(c, item_id, active=True)
                    brief = f"Worklog item {item_id}: {item['title']}\nFolder: {folder}\n\nTask request:\n{prompt}\n"
                    result = {
                        "id": identity, "provider": provider, "status": "ready_manual", "chat": None,
                        "sessionId": None, "worktreePath": folder, "copyablePrompt": brief,
                        "message": "Copy the brief below into your Antigravity session.",
                    }
                    record = {"kind": "session-launch", "processToken": self.token, "cwd": folder,
                              "itemId": item_id, "itemTitle": item["title"], "result": result}
                    c.execute("INSERT INTO receipts(actor_id,key,request_hash,response_json,created_at) VALUES(?,?,?,?,?)",
                              (namespace, identity, digest, json.dumps(record), now()))
                return result

            if len(self.workers) >= 2:
                raise AppError(429, "session_limit", "Two chats are being prepared. Wait for one to finish.")
            folder = self._folder(cwd)
            if provider == "claude":
                require_claude_settings(folder, requested_options)
            with self.db.transaction() as c:
                item = get_item(c, item_id, active=True)
                result = {"id": identity, "provider": provider, "status": "starting", "chat": None,
                          "sessionId": None, "message": "Preparing a linked desktop chat…", **requested_options}
                record = {"kind": "session-launch", "processToken": self.token, "cwd": folder,
                          "itemId": item_id, "itemTitle": item["title"], "result": result}
                c.execute("INSERT INTO receipts(actor_id,key,request_hash,response_json,created_at) VALUES(?,?,?,?,?)",
                          (namespace, identity, digest, json.dumps(record), now()))
            job = _LaunchJob(self, identity, dict(actor), provider, item_id, item["title"],
                             folder, prompt, requested_options, _prepare_session)
            worker = threading.Thread(target=self._run_launch, args=(job,), name="worklog-session-prepare", daemon=True)
            self.workers[identity] = worker
            try:
                worker.start()
            except Exception:
                self.workers.pop(identity, None)
                self._state(identity, actor, "failed", "Preparation could not be started. No provider session was requested.")
            return self.get(identity, actor)

    def _start_dispatched(
        self,
        identity: str,
        namespace: str,
        digest: str,
        item_id: str,
        repo_id: str,
        provider: str,
        cwd: str | None,
        branch: str,
        mode: str,
        priority: str,
        prompt: str,
        requested_options: dict[str, str],
        actor: dict,
    ) -> dict:
        assert self.leases is not None
        self.leases.sweep()
        fallback_cwd = self._folder(cwd) if cwd else ""
        with self.db.read() as c:
            pol = policy_record(c, repo_id)
        if not pol["baseCheckoutPath"] and not fallback_cwd and provider in {"codex", "claude"}:
            raise AppError(
                422,
                "validation",
                "Configure baseCheckoutPath on this repository or provide a local working folder",
                {"cwd": "required"},
            )
        if provider == "claude" and not pol["baseCheckoutPath"]:
            require_claude_settings(fallback_cwd, requested_options)
        stamp = now()
        with self.db.transaction() as c:
            item = get_item(c, item_id, active=True)
            lease = self.leases.acquire_for_dispatch(
                c,
                actor,
                repo_id=repo_id,
                provider=provider,
                holder_label=f"{provider.title()}: {item['title'][:80]}",
                branch=branch,
                mode=mode,
                priority=priority,
                reason=f"Worklog dispatch for {item['title'][:120]}",
            )
            if lease["state"] == "queued":
                dq_status = "queued"
                msg = f"Queued at #{lease['queuePosition']} for {lease['repositoryName']}. No provider process started."
            else:
                dq_status = "provisioning"
                msg = f"Slot {lease['slotIndex']} reserved for {lease['repositoryName']}; provisioning worktree…"
            c.execute(
                "INSERT INTO dispatch_queue(id,item_id,repository_id,lease_id,provider,requested_branch,prompt,"
                "status,session_id,chat_record_id,model,message,process_token,revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,NULL,NULL,'',?,?,1,?,?)",
                (identity, item_id, repo_id, lease["id"], provider, branch, prompt, dq_status, msg, self.token, stamp, stamp),
            )
            receipt_record = {
                "kind": "dispatch-launch", "dispatchId": identity, "cwd": fallback_cwd,
                "itemId": item_id, "itemTitle": item["title"], "requestedOptions": requested_options,
            }
            c.execute("INSERT INTO receipts(actor_id,key,request_hash,response_json,created_at) VALUES(?,?,?,?,?)",
                      (namespace, identity, digest, json.dumps(receipt_record), stamp))
        self.leases.write_snapshot()

        if lease["state"] == "queued":
            return self.get(identity, actor)

        if provider == "antigravity":
            return self._finish_antigravity_dispatch(identity, lease["id"], branch, actor)

        self._spawn_dispatch_worker(identity, dict(actor), auto_open=True)
        return self.get(identity, actor)

    def _finish_antigravity_dispatch(
        self,
        dispatch_id: str,
        lease_id: str,
        branch: str,
        actor: dict,
    ) -> dict:
        assert self.leases is not None
        try:
            activated = self.leases.activate_dispatch_lease(lease_id, actor, requested_branch=branch)
            with self.db.transaction() as c:
                stamp = now()
                c.execute(
                    "UPDATE dispatch_queue SET status='ready_manual',message=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (
                        f"Slot {activated['slotIndex']} ready for Antigravity. Copy the slot brief into your session.",
                        stamp,
                        dispatch_id,
                    ),
                )
        except Exception as exc:
            logger.warning("Antigravity slot provisioning failed (%s)", type(exc).__name__)
            self.leases.cancel_dispatch_lease(lease_id, actor, reason="provision_failed")
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE dispatch_queue SET status='failed',message=?,updated_at=?,revision=revision+1 WHERE id=?",
                    ("Slot provisioning failed; check the repository base checkout and retry.", now(), dispatch_id),
                )
        return self.get(dispatch_id, actor)

    def _spawn_dispatch_worker(
        self,
        dispatch_id: str,
        actor: dict,
        *,
        auto_open: bool,
    ) -> bool:
        with self.lock:
            if dispatch_id in self.workers or len(self.workers) >= 2:
                return False
            worker = threading.Thread(
                target=self._run_dispatch_worker,
                args=(dispatch_id, actor, auto_open),
                name="worklog-dispatch-prepare",
                daemon=True,
            )
            self.workers[dispatch_id] = worker
            try:
                worker.start()
            except Exception:
                self.workers.pop(dispatch_id, None)
                return False
            return True

    def _run_dispatch_worker(
        self,
        dispatch_id: str,
        actor: dict,
        auto_open: bool,
    ) -> None:
        assert self.leases is not None
        lease_id = ""
        session_saved = False
        try:
            with self.db.read() as c:
                dq = c.execute("SELECT * FROM dispatch_queue WHERE id=?", (dispatch_id,)).fetchone()
                if dq is None or dq["status"] in {"cancelled", "ready", "ready_manual"}:
                    return
                lease_id = dq["lease_id"]
                item = get_item(c, dq["item_id"], active=True)
                pol = policy_record(c, dq["repository_id"])
                provider = dq["provider"]
                branch = dq["requested_branch"]
                prompt = dq["prompt"]
                repo_id = dq["repository_id"]
                item_id = dq["item_id"]
                item_title = item["title"]
                receipt = self._dispatch_receipt(c, dispatch_id)

            activated = self.leases.activate_dispatch_lease(lease_id, actor, requested_branch=branch)
            folder = activated["worktreePath"] or receipt.get("cwd", "")
            if not folder:
                raise RuntimeError("No working folder available for dispatch")
            requested_options = receipt.get("requestedOptions", {})
            if provider == "claude" and requested_options:
                if activated["worktreePath"]:
                    self.leases.prepare_claude_settings(lease_id, folder, requested_options)
                else:
                    require_claude_settings(folder, requested_options)

            with self.db.transaction() as c:
                c.execute(
                    "UPDATE dispatch_queue SET status='preparing',process_token=?,message=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (self.token, "Preparing a linked desktop chat in read-only mode…", now(), dispatch_id),
                )
            header = _slot_header(
                repo_name=activated["repositoryName"],
                repo_id=repo_id,
                slot_index=activated["slotIndex"],
                max_slots=pol["maxActiveLeases"],
                worktree_path=activated["worktreePath"],
                branch=activated["branch"],
                lease_id=activated["id"],
                revision=activated["revision"],
            )
            chat_id, model = _prepare_session(
                provider,
                folder,
                _initialization_prompt(item_id, item_title, prompt, header),
                requested_options,
            )
            safe_id = _safe_session_id(chat_id)
            clean_model = _clean_model(model)
            session_saved = True
            self.leases.bind_dispatch_chat(lease_id, safe_id)
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE dispatch_queue SET session_id=?,model=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (safe_id, clean_model, now(), dispatch_id),
                )
            with self.db.transaction() as c:
                dq_now = c.execute("SELECT * FROM dispatch_queue WHERE id=?", (dispatch_id,)).fetchone()
                if dq_now is not None and not dq_now["chat_record_id"]:
                    repo_ids = sorted(set(linked_repositories(c, "item", [item_id]).get(item_id, [])) | {repo_id})
                    chat_rec = self.work.apply(c, actor, "chat.create", {
                        "ownerType": "item", "ownerId": item_id, "chatId": safe_id,
                        "title": _chat_title(provider, item_title),
                        "provider": provider, "model": clean_model,
                        "repositoryIds": repo_ids,
                    })
                    c.execute(
                        "UPDATE dispatch_queue SET chat_record_id=?,updated_at=?,revision=revision+1 WHERE id=?",
                        (chat_rec["id"], now(), dispatch_id),
                    )
            if auto_open:
                self._verify_desktop_settings(provider, folder, requested_options,
                                              lease_id if activated["worktreePath"] else None)
                _open_desktop(provider, folder, safe_id)
                ready_msg = "Chat prepared in slot and linked. Open request sent. Send Continue in the desktop app to begin work."
            else:
                ready_msg = "Promoted and prepared in read-only mode. Click Open in app and send Continue to begin work."
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE dispatch_queue SET status='ready',message=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (ready_msg, now(), dispatch_id),
                )
        except Exception as exc:
            logger.warning("Dispatched session preparation/handoff failed (%s)", type(exc).__name__)
            if lease_id and not session_saved:
                try:
                    self.leases.cancel_dispatch_lease(lease_id, actor, reason="dispatch_failed")
                except Exception:
                    logger.warning("Failed to release lease after dispatch error", exc_info=True)
            with self.db.transaction() as c:
                dq_now = c.execute("SELECT session_id,chat_record_id FROM dispatch_queue WHERE id=?", (dispatch_id,)).fetchone()
                if isinstance(exc, AppError) and exc.code == "claude_settings":
                    msg = exc.message
                elif dq_now is not None and dq_now["chat_record_id"]:
                    msg = "The linked chat could not be opened. Use Open in app to retry this same chat."
                elif dq_now is not None and dq_now["session_id"]:
                    msg = "The provider chat is saved, but its Worklog link could not be saved. Retry opening this session."
                elif isinstance(exc, FileNotFoundError):
                    msg = "The provider CLI could not be found. Check its installation before starting another session."
                else:
                    msg = "Preparation could not be confirmed. It was not retried; check repository slot status before retrying."
                c.execute(
                    "UPDATE dispatch_queue SET status='failed',message=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (msg, now(), dispatch_id),
                )
        finally:
            with self.lock:
                self.workers.pop(dispatch_id, None)
            # If another promoted dispatch was waiting on the 2-worker prep cap, pick it up now.
            self.drain_promoted()

    def drain_promoted(self) -> int:
        """Auto-prepare promoted queued dispatches whose lease is now offered/active."""
        if self.leases is None:
            return 0
        started = 0
        with self.db.read() as c:
            rows = c.execute(
                "SELECT dq.id,dq.lease_id,dq.provider,dq.requested_branch,dq.status "
                "FROM dispatch_queue dq JOIN repository_leases l ON l.id=dq.lease_id "
                "WHERE dq.status IN ('queued','provisioning') AND l.state IN ('offered','active') "
                "ORDER BY dq.created_at ASC, dq.id ASC"
            ).fetchall()
        actor = {"id": "local-user", "label": "You", "kind": "human"}
        for row in rows:
            with self.lock:
                if row["id"] in self.workers:
                    continue
                if row["provider"] != "antigravity" and len(self.workers) >= 2:
                    break
            if row["provider"] == "antigravity":
                with self.db.transaction() as c:
                    cur = c.execute("SELECT status FROM dispatch_queue WHERE id=?", (row["id"],)).fetchone()
                    if cur is None or cur["status"] not in {"queued", "provisioning"}:
                        continue
                    c.execute(
                        "UPDATE dispatch_queue SET status='provisioning',process_token=?,message=?,updated_at=?,revision=revision+1 WHERE id=?",
                        (self.token, "Slot granted; provisioning worktree…", now(), row["id"]),
                    )
                self._finish_antigravity_dispatch(row["id"], row["lease_id"], row["requested_branch"], actor)
                started += 1
            else:
                with self.lock:
                    if row["id"] in self.workers:
                        continue
                    if len(self.workers) >= 2:
                        break
                    with self.db.transaction() as c:
                        cur = c.execute("SELECT status FROM dispatch_queue WHERE id=?", (row["id"],)).fetchone()
                        if cur is None or cur["status"] not in {"queued", "provisioning"}:
                            continue
                        c.execute(
                            "UPDATE dispatch_queue SET status='provisioning',process_token=?,message=?,updated_at=?,revision=revision+1 WHERE id=?",
                            (self.token, "Slot granted; provisioning worktree…", now(), row["id"]),
                        )
                    if self._spawn_dispatch_worker(row["id"], actor, auto_open=False):
                        started += 1
        return started

    def list_dispatch_queue(
        self,
        *,
        repository_id: str | None = None,
        item_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        with self.db.read() as c:
            where = ["1=1"]
            args: list[Any] = []
            if repository_id:
                where.append("repository_id=?")
                args.append(repository_id)
            if item_id:
                where.append("item_id=?")
                args.append(item_id)
            if status:
                where.append("status=?")
                args.append(status)
            sql = "SELECT * FROM dispatch_queue WHERE " + " AND ".join(where) + " ORDER BY created_at DESC, id DESC"
            total = int(c.execute("SELECT COUNT(*) FROM (" + sql + ")", args).fetchone()[0])
            rows = c.execute(sql + " LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
            return {
                "items": [self._dispatch_record(c, r)["item"] for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def cancel_dispatch(self, dispatch_id: str, actor: dict, reason: str = "") -> dict[str, Any]:
        self._namespace(actor)
        with self.db.read() as c:
            dq = c.execute("SELECT * FROM dispatch_queue WHERE id=?", (dispatch_id,)).fetchone()
            if dq is None:
                raise AppError(404, "not_found", "Queued dispatch not found")
            if dq["status"] in {"cancelled", "ready", "ready_manual"}:
                raise AppError(409, "conflict", f"Dispatch is already {dq['status']}")
            lease_id = dq["lease_id"]
        if self.leases is not None:
            self.leases.cancel_dispatch_lease(lease_id, actor, reason=reason or "dispatch_cancelled")
        with self.db.transaction() as c:
            c.execute(
                "UPDATE dispatch_queue SET status='cancelled',message=?,updated_at=?,revision=revision+1 WHERE id=?",
                (reason or "Cancelled by user.", now(), dispatch_id),
            )
            updated = c.execute("SELECT * FROM dispatch_queue WHERE id=?", (dispatch_id,)).fetchone()
            return self._dispatch_record(c, updated)["item"]

    def open_dispatch(self, dispatch_id: str, actor: dict) -> dict[str, Any]:
        self._namespace(actor)
        with self.lock:
            with self.db.read() as c:
                dq = c.execute("SELECT * FROM dispatch_queue WHERE id=?", (dispatch_id,)).fetchone()
                if dq is None:
                    raise AppError(404, "not_found", "Dispatch entry not found")
                if dq["provider"] == "antigravity":
                    return self._dispatch_record(c, dq)["launch"]
                if dq["status"] in {"queued", "provisioning", "preparing"} or dispatch_id in self.opening:
                    return self._dispatch_record(c, dq)["launch"]
                if not dq["session_id"]:
                    raise AppError(409, "session_launch", "No saved provider session is available to reopen")
                lease_id = dq["lease_id"]
                lease = c.execute("SELECT worktree_path FROM repository_leases WHERE id=?", (lease_id,)).fetchone()
                slot_folder = lease["worktree_path"] if lease else ""
                receipt = self._dispatch_receipt(c, dispatch_id)
                folder = slot_folder or receipt.get("cwd", "")
                requested_options = receipt.get("requestedOptions", {})
                provider = dq["provider"]
                session_id = dq["session_id"]
                item_id = dq["item_id"]
                repo_id = dq["repository_id"]
                model = dq["model"]
                chat_record_id = dq["chat_record_id"]
            self.opening.add(dispatch_id)
        try:
            self._verify_desktop_settings(provider, folder, requested_options, lease_id if slot_folder else None)
            if not chat_record_id:
                with self.db.transaction() as c:
                    item = get_item(c, item_id, active=True)
                    repo_ids = sorted(set(linked_repositories(c, "item", [item_id]).get(item_id, [])) | {repo_id})
                    chat_rec = self.work.apply(c, actor, "chat.create", {
                        "ownerType": "item", "ownerId": item_id, "chatId": _safe_session_id(session_id),
                        "title": _chat_title(provider, item["title"]),
                        "provider": provider, "model": model,
                        "repositoryIds": repo_ids,
                    })
                    c.execute(
                        "UPDATE dispatch_queue SET chat_record_id=?,updated_at=?,revision=revision+1 WHERE id=?",
                        (chat_rec["id"], now(), dispatch_id),
                    )
            _open_desktop(provider, folder, session_id)
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE dispatch_queue SET status='ready',message=?,updated_at=?,revision=revision+1 WHERE id=?",
                    ("Chat opened in desktop app. Send Continue to begin work.", now(), dispatch_id),
                )
        except Exception as exc:
            logger.warning("Opening dispatched chat failed (%s)", type(exc).__name__)
            message = (exc.message if isinstance(exc, AppError) and exc.code == "claude_settings" else
                       "The linked chat could not be opened. Use Open in app to retry this same chat.")
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE dispatch_queue SET status='failed',message=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (message, now(), dispatch_id),
                )
        finally:
            with self.lock:
                self.opening.discard(dispatch_id)
        with self.db.read() as c:
            updated = c.execute("SELECT * FROM dispatch_queue WHERE id=?", (dispatch_id,)).fetchone()
            return self._dispatch_record(c, updated)["launch"]

    def get(self, identity: str, actor: dict) -> dict:
        self._namespace(actor)
        with self.lock, self.db.transaction() as c:
            dq = c.execute("SELECT * FROM dispatch_queue WHERE id=?", (identity,)).fetchone()
            if dq is not None:
                worker = self.workers.get(identity)
                alive = identity in self.opening or (worker is not None and worker.is_alive())
                if dq["status"] == "preparing" and (dq["process_token"] != self.token or not alive):
                    c.execute(
                        "UPDATE dispatch_queue SET status='failed',message=?,updated_at=?,revision=revision+1 WHERE id=?",
                        (
                            "Preparation was interrupted. It was not retried. Reopen the saved session if available; "
                            "otherwise check provider history before starting another.",
                            now(),
                            identity,
                        ),
                    )
                    dq = c.execute("SELECT * FROM dispatch_queue WHERE id=?", (identity,)).fetchone()
                return self._dispatch_record(c, dq)["launch"]
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

    def _verify_desktop_settings(
        self, provider: str, folder: str, options: dict[str, str], lease_id: str | None = None,
    ) -> None:
        if provider != "claude" or not options:
            return
        if lease_id and self.leases:
            self.leases.require_claude_slot_settings(lease_id, folder, options)
        else:
            require_claude_settings(folder, options)

    def _handoff(self, identity: str, actor: dict) -> None:
        record = self._link_prepared(identity, actor)
        result = record["result"]
        if result["provider"] == "claude":
            options = {key: result[key] for key in ("requestedModel", "reasoningEffort") if key in result}
            require_claude_settings(record["cwd"], options)
        _open_desktop(result["provider"], record["cwd"], result["chat"]["chatId"])
        self._state(identity, actor, "ready", "Chat prepared and linked. Open request sent. "
                    "Send Continue in the desktop app to begin work.")

    def _failure(self, identity: str, actor: dict, exc: Exception) -> None:
        # Never include provider output, prompt text, environment values or exception messages.
        logger.warning("Desktop session preparation/handoff failed (%s)", type(exc).__name__)
        with self.lock, self.db.transaction() as c:
            record = self._load(c, identity, actor)
            result = record["result"]
            if isinstance(exc, AppError) and exc.code == "claude_settings":
                message = exc.message
            elif result.get("sessionId") and result["chat"] is None:
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
                                         _initialization_prompt(job.item_id, job.item_title, job.prompt),
                                         job.requested_options)
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
        with self.db.read() as c:
            if c.execute("SELECT 1 FROM dispatch_queue WHERE id=?", (identity,)).fetchone() is not None:
                return self.open_dispatch(identity, actor)
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


APPS = {"claude": "Claude", "codex": "Codex"}
LOCAL_FOLDER = re.compile(r"^[A-Za-z]:[\\/]")


def _paste(discussion_id: str, message_id: str, target_id: str, acknowledged: bool) -> str:
    """What the person pastes into the agent's chat: the request by its IDs, and the commands that
    read, acknowledge and answer it. It names nothing an agent wrote, not even a thread's title or a
    sender's label, so no agent's words reach the chat as the person's; reading the request through
    Worklog shows who wrote it, and records that it was seen."""
    steps = [f"Read it: worklog agent --as NAME discussions read {message_id} --message"]
    if not acknowledged:
        steps.append(f"Acknowledge it: worklog agent --as NAME discussions ack {message_id} --target {target_id}")
    steps.append(f"Answer in its thread, or say when you will: worklog agent --as NAME discussions reply {discussion_id} "
                 f"--reply-to {message_id} --text 'YOUR ANSWER'")
    return "\n".join([
        f"A Worklog request waits on you: message {message_id} in thread {discussion_id}. "
        "Deal with it now, as the agent guide says (section 4.1).",
        "NAME is the name you registered with; give it as --as NAME, as in every Worklog command.",
        *(f"{number}. {step}" for number, step in enumerate(steps, 1)),
        "Then check your inbox: worklog agent --as NAME inbox",
    ])


class AgentChats:
    """The person's handoff of a pending request to the agent it waits on (decision R11).

    It opens that agent's own chat in its desktop app, as Open in app does for a launched chat, and
    gives the person a message to paste there. Worklog never types or sends into a chat and never
    resumes one headlessly: Codex refuses `exec resume` while the app has the thread open, a Claude
    desktop session does not show a turn another process appends, and a headless turn would either
    run the agent's tools unattended or run without them. It stores nothing; the person sends."""

    def __init__(self, db: Database, transcripts: TranscriptReader):
        self.db, self.transcripts = db, transcripts
        self.registry = AgentRegistry()
        # Sessions being opened now: a second click while the first handoff runs starts nothing.
        self.lock = threading.Lock()
        self.opening: set[str] = set()

    def _target(self, message_id: str, target_id: str) -> tuple[sqlite3.Row, dict]:
        with self.db.read() as c:
            row = c.execute(
                "SELECT m.discussion_id,r.state,t.kind,t.recipient_id,ro.owner_id AS role_owner,"
                "EXISTS(SELECT 1 FROM discussion_acknowledgements a WHERE a.target_id=t.id) AS acknowledged "
                "FROM discussion_targets t JOIN discussion_requests r ON r.message_id=t.message_id "
                "JOIN discussion_messages m ON m.id=t.message_id "
                "LEFT JOIN discussion_roles ro ON t.kind='role' AND ro.id=t.recipient_id "
                "WHERE t.id=? AND t.message_id=?", (target_id, message_id)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Request target not found")
            if row["state"] != "pending":
                raise AppError(409, "request_terminal", "This request already has a permanent outcome")
            # A role's slot waits on the agent that owns the role now.
            agent_id = {"agent": row["recipient_id"], "role": row["role_owner"]}.get(row["kind"])
            if not agent_id:
                raise AppError(409, "not_an_agent", "Only an agent's slot, or a role an agent owns, has a chat to open")
            return row, self.registry.profile(c, agent_id)

    @staticmethod
    def _folder(directory: str | None) -> str:
        """The folder a Claude session resumes from: the local drive folder its agent registered from,
        checked as written before it is resolved, so a share or a rootless path is never touched."""
        if not directory or "\0" in directory or not LOCAL_FOLDER.match(directory):
            return ""
        try:
            return SessionLauncher._folder(directory)
        except AppError:
            return ""

    def open(self, message_id: str, target_id: str, actor: dict) -> dict:
        if actor.get("kind") != "human":
            raise AppError(403, "agent_scope", "Only the person opens an agent's chat")
        row, profile = self._target(message_id, target_id)
        label = profile["label"]
        # The chat it registered for, else the one its newest open run reports.
        chat_id = profile["chatId"] or next((run["chatId"] for run in profile["runs"] if run["chatId"]), None)
        result: dict[str, Any] = {
            "messageId": message_id, "targetId": target_id, "agentId": profile["id"], "agentLabel": label,
            "chatId": chat_id, "directory": profile["directory"], "provider": None, "sessionId": None, "opened": False,
            "paste": _paste(row["discussion_id"], message_id, target_id, bool(row["acknowledged"]))}

        def unopened(reason: str) -> dict:
            return {**result, "message": reason + " Paste the message below where it works."}

        if not chat_id:
            return unopened(f"{label} reported no chat, so there is none to open.")
        found = self.transcripts.locate(chat_id)
        if found is None:
            return unopened(f"Worklog found no Claude or Codex chat with the ID {label} reported on this computer.")
        provider, session = found
        result.update(provider=provider, sessionId=session)
        if self.transcripts.archived(chat_id):
            return unopened(f"{label}'s chat is archived in the {APPS[provider]} app.")
        folder = ""
        if provider == "claude":
            # Claude resumes a session from the folder it works in, which is where the agent registered.
            folder = self._folder(profile["directory"])
            if not folder:
                return unopened(f"Claude resumes a session from its folder, and the folder {label} registered from "
                                "is missing or not a local drive folder.")
        with self.lock:
            if session in self.opening:
                return {**result, "message": f"Worklog is already opening {label}'s chat. Paste the message below into it once it opens."}
            self.opening.add(session)
        try:
            _open_desktop(provider, folder, session)
        except Exception as exc:
            # Never include provider output or exception messages.
            logger.warning("Opening an agent's chat failed (%s)", type(exc).__name__)
            if provider == "claude" and isinstance(exc, FileNotFoundError):
                return unopened("The Claude command line could not be found.")
            return unopened(f"The {APPS[provider]} app did not open the chat.")
        finally:
            with self.lock:
                self.opening.discard(session)
        return {**result, "opened": True,
                "message": f"Asked the {APPS[provider]} app to open {label}'s chat. Paste the message below into it and send it."}
