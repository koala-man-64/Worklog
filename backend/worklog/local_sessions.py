"""Create a local provider chat once and register it as a Worklog recipient."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import get_args

from .common import AgentRequestError, shows_as_written
from .models import CLAUDE_SESSION_EFFORTS, SESSION_MODEL_PATTERN, SessionReasoningEffort
from .recovery import InstanceLock
from .session_launches import _prepare_session, _safe_session_id


def _digest(fields: list[str]) -> str:
    encoded = json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _save(path: Path, receipt: dict) -> None:
    # Replace only a fully flushed record. A killed process leaves either the previous phase
    # or the new one for the next invocation under the same request lock.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".session-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (UnicodeError, ValueError) as exc:
        raise ValueError("The local launch receipt is unreadable. Check the provider for the saved chat.") from exc
    if (not isinstance(record, dict) or not isinstance(record.get("phase"), str)
            or not isinstance(record.get("fingerprint"), str)):
        raise ValueError("The local launch receipt is incomplete. Check the provider for the saved chat.")
    if record["phase"] in {"prepared", "registering", "registered"}:
        try:
            _safe_session_id(record.get("chatId"))
            if record["phase"] == "registered":
                _safe_session_id(record.get("agentId"))
        except RuntimeError as exc:
            raise ValueError("The local launch receipt has an invalid ID. Check the provider for the saved chat.") from exc
        if not isinstance(record.get("model"), str):
            raise ValueError("The local launch receipt is incomplete. Check the provider for the saved chat.")
    return record


def start_local_session(*, receipt_dir: Path, parent_id: str, provider: str, cwd: Path, label: str,
                        request_id: str, worklog_command: list[str], prepare_credential: Callable[[str], None],
                        register_child: Callable[[str, str, str], dict], model: str | None = None,
                        reasoning_effort: str | None = None) -> dict:
    """Return the saved provider and Worklog identities. Never repeat a possibly completed launch."""
    if provider not in {"codex", "claude"}:
        raise ValueError("Choose codex or claude as the provider")
    requested_options = {}
    if model is not None:
        if len(model) > 200 or not re.fullmatch(SESSION_MODEL_PATTERN, model):
            raise ValueError("Give --model a model ID or alias of up to 200 characters")
        requested_options["requestedModel"] = model
    if reasoning_effort is not None:
        efforts = CLAUDE_SESSION_EFFORTS if provider == "claude" else get_args(SessionReasoningEffort)
        if reasoning_effort not in efforts:
            raise ValueError(f"Choose one of {', '.join(efforts)} for {provider} --reasoning-effort")
        requested_options["reasoningEffort"] = reasoning_effort
    if not 8 <= len(request_id) <= 200 or any(ord(ch) < 33 or ord(ch) > 126 for ch in request_id):
        raise ValueError("A request ID must be 8 to 200 printable ASCII characters without spaces")
    label = label.strip()
    if not label or not shows_as_written(label) or len(label.encode("utf-8")) > 200 or label.casefold() == "you":
        raise ValueError("Give the child a visible label of up to 200 bytes, other than 'You'")
    folder = cwd.resolve()
    directory = str(folder)
    if not shows_as_written(directory) or len(directory.encode("utf-8")) > 2000:
        raise ValueError("The working directory must be a visible absolute path of up to 2,000 bytes")

    key = _digest([parent_id, request_id])
    name = "session-" + key[:32]
    child_command = [*worklog_command, "--as", name]
    launch_fields = [provider, os.path.normcase(directory), label]
    if requested_options:
        launch_fields.extend((model or "", reasoning_effort or ""))
    fingerprint = _digest(launch_fields)
    receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = receipt_dir / (key + ".json")
    with ExitStack() as locks:
        try:
            locks.enter_context(InstanceLock(receipt_dir, key + ".lock"))
        except RuntimeError as exc:
            raise ValueError("This launch is in progress. Retry the same request ID when it finishes.") from exc
        record = _read(path)
        if record is not None:
            if record["fingerprint"] != fingerprint:
                raise ValueError("This request ID already identifies a different provider, directory, label, model or effort")
            if record["phase"] not in {"unstarted", "prepared", "registering", "registered"}:
                raise ValueError("The provider may already have created this chat. Check its sessions before starting another.")

        if record is None or record["phase"] == "unstarted":
            if not folder.is_dir():
                raise ValueError("Choose an existing working directory for the new chat")
            record = {"fingerprint": fingerprint, "phase": "starting"}
            _save(path, record)
            prompt = (
                f"This is a new Worklog agent session. Your local Worklog name is {name}. "
                "When assigned work, use this Worklog executable and argument list in this folder, "
                f"appending the desired command such as inbox: {json.dumps(child_command, ensure_ascii=True)}. "
                "For this preparation turn, reply WORKLOG_READY and wait for a task. Do not use tools."
            )
            try:
                chat_id, reported_model = _prepare_session(
                    provider, directory, prompt, requested_options=requested_options, allow_non_git=True,
                )
                chat_id = _safe_session_id(chat_id)
            except FileNotFoundError as exc:
                # Executable lookup or process creation failed before a chat could be started.
                record["phase"] = "unstarted"
                _save(path, record)
                raise ValueError(f"The {provider} CLI or working directory was unavailable. Retry the same request ID.") from exc
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                raise ValueError(f"{provider} did not confirm creation. Check its sessions before starting another.") from exc
            record.update(phase="prepared", chatId=chat_id, model=reported_model)
            _save(path, record)

        # Registration is resumable independently. A lost HTTP reply or limit must never make
        # us start the provider again: its confirmed ID is already durable.
        chat_id = record["chatId"]
        if record["phase"] == "prepared":
            prepare_credential(name)
            record["phase"] = "registering"
            _save(path, record)
        if record["phase"] == "registering":
            try:
                child = register_child(name, chat_id, directory)
            except AgentRequestError as exc:
                raise AgentRequestError(f"{provider} created chat {chat_id}. Worklog registration is incomplete. "
                                        f"The same request ID will reuse this chat. {exc}", exc.status, exc.code) from exc
            record.update(phase="registered", agentId=child["agentId"])
            _save(path, record)
        return {"state": "created", "provider": provider, "chatId": chat_id, "model": record["model"],
                "cwd": directory, "name": name, "agentId": record["agentId"], "worklogCommand": child_command,
                **requested_options}
