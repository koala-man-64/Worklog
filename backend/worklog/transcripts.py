"""Read-only views of the agent chat transcripts Claude Code and Codex keep on this machine.

Worklog never writes these files. A chat ID selects files only once it has the UUID shape
those tools use, so no request can name a path. Transcripts are append-only logs: each is
parsed once and afterwards only for the lines added since, so a live chat stays cheap to watch.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
CHAT_ID = re.compile(rf"(local_)?({UUID})")
CODEX_FILE = re.compile(rf"rollout-.+-({UUID})\.jsonl")
TEXT_LIMIT = 20_000  # characters kept of one message
TOOL_LIMIT = 50  # tool calls listed in one run; the count keeps going
WORKING_SECONDS = 15 * 60  # an open turn this recent counts as working
TAIL_BYTES = 512 * 1024  # how far back a summary looks for the latest turn
META_BYTES = 5_000_000  # largest metadata file read
CACHE_FILES = 64
INDEX_SECONDS = 10  # how long a listing of the transcript folders is reused

# Wrappers the tools put around their own context; they are not messages anyone sent.
SKIPPED_TEXT = re.compile(r"\s*(<(system-reminder|local-command-[a-z]+|task-notification|environment_context|"
                          r"user_instructions|turn_aborted|external_[a-z_]+)[\s>]|# AGENTS\.md instructions)")
COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


@dataclass(frozen=True)
class TranscriptRoots:
    """Where each tool keeps its transcripts. Empty roots find nothing."""

    claude_projects: tuple[Path, ...] = ()
    # Folders holding the desktop app's claude-code-sessions records, directly or one level down.
    claude_desktop: tuple[Path, ...] = ()
    # Codex homes, each with sessions/, archived_sessions/ and session_index.jsonl.
    codex: tuple[Path, ...] = ()

    @classmethod
    def from_environment(cls) -> TranscriptRoots:
        home = Path.home()
        claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude")
        codex = Path(os.environ.get("CODEX_HOME") or home / ".codex")
        desktop = [home / "Library" / "Application Support" / "Claude"]
        if os.environ.get("APPDATA"):
            desktop.append(Path(os.environ["APPDATA"]) / "Claude")
        if os.environ.get("LOCALAPPDATA"):
            desktop.append(Path(os.environ["LOCALAPPDATA"]) / "Claude-Data")
        return cls((claude / "projects",), tuple(desktop), (codex,))


@dataclass
class Thread:
    """One agent's transcript within a chat: the chat itself, or a subagent it started."""

    key: str
    path: Path
    format: str  # "claude" or "codex"
    name: str
    title: str = ""
    task: str = ""
    parent: str | None = None
    # Codex agents address each other by path, such as /root/api_review.
    address: str = ""


@dataclass
class Parsed:
    head: bytes = b""
    offset: int = 0
    lines: int = 0
    messages: list[dict] = field(default_factory=list)
    title: str = ""
    model: str = ""
    open: bool = False
    last_at: str | None = None
    message_id: str | None = None


def timestamp(value: Any) -> str | None:
    """One sortable form for both tools' ISO times."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def clip(value: str) -> tuple[str, bool]:
    value = value.strip()
    return (value[:TEXT_LIMIT], True) if len(value) > TEXT_LIMIT else (value, False)


def one_line(value: Any, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    line = " ".join(value.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def read_json(path: Path) -> Any:
    try:
        if path.stat().st_size > META_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def records(handle: Any) -> Iterator[tuple[int, Any]]:
    """Complete JSON lines from the handle's position, with the bytes each one used."""
    for raw in handle:
        if not raw.endswith(b"\n"):
            return  # still being written; read it on the next pass
        try:
            yield len(raw), json.loads(raw)
        except ValueError:
            yield len(raw), None


def codex_id(path: Path) -> str | None:
    match = CODEX_FILE.fullmatch(path.name)
    return match.group(1) if match else None


class TranscriptReader:
    def __init__(self, roots: TranscriptRoots):
        self.roots = roots
        self.lock = threading.Lock()
        self.cache: OrderedDict[Path, Parsed] = OrderedDict()
        self.tails: OrderedDict[Path, tuple[tuple[int, int], Parsed]] = OrderedDict()
        self.codex_meta: dict[Path, dict] = {}
        self.codex_names: dict[Path, tuple[tuple[int, int], dict[str, str]]] = {}
        self.labels: dict[str, str] = {}
        self.index: tuple[float, dict[str, list[Path]], dict[str, tuple[Path, Path]]] | None = None

    # Finding the files ----------------------------------------------------------------

    def _index(self) -> tuple[dict[str, list[Path]], dict[str, tuple[Path, Path]]]:
        """Every transcript by ID, listed again once it is INDEX_SECONDS old."""
        with self.lock:
            if self.index and time.monotonic() - self.index[0] < INDEX_SECONDS:
                return self.index[1], self.index[2]
        claude: dict[str, list[Path]] = {}
        for root in self.roots.claude_projects:
            for project in root.glob("*/"):
                for path in project.glob("*.jsonl"):
                    claude.setdefault(path.stem.lower(), []).append(path)
        codex: dict[str, tuple[Path, Path]] = {}
        for home in self.roots.codex:
            for path in self._codex_files(home):
                if ident := codex_id(path):
                    codex.setdefault(ident, (path, home))
        with self.lock:
            self.index = (time.monotonic(), claude, codex)
        return claude, codex

    def threads(self, chat_id: str) -> tuple[str | None, list[Thread]]:
        match = CHAT_ID.fullmatch(chat_id.strip().lower())
        if not match:
            return None, []
        ident, title = match.group(2), ""
        if match.group(1):
            # The desktop app's own session ID names the Claude Code session it runs.
            record = self._desktop_record(match.group(0))
            cli = string(record.get("cliSessionId")).lower() if record else ""
            if not re.fullmatch(UUID, cli):
                return None, []
            ident, title = cli, string(record.get("title")) if record else ""
        claude, codex = self._index()
        found = [p for p in claude.get(ident, []) if p.exists()]
        if found:
            # A session resumed from another folder leaves a copy; the latest one is current.
            return "claude", self._claude_threads(ident, max(found, key=lambda p: p.stat().st_mtime), title)
        if ident in codex and codex[ident][0].exists():
            return "codex", self._codex_threads(ident, *codex[ident])
        return None, []

    def label(self, ident: str) -> str:
        """A desktop session's title, for messages that name the session they came from."""
        if not re.fullmatch(rf"local_{UUID}", ident.lower()):
            return ""
        with self.lock:
            if ident in self.labels:
                return self.labels[ident]
        record = self._desktop_record(ident.lower())
        title = string(record.get("title")) if record else ""
        with self.lock:
            self.labels[ident] = title
        return title

    def _desktop_record(self, ident: str) -> dict | None:
        for base in self.roots.claude_desktop:
            for pattern in (f"claude-code-sessions/*/*/{ident}.json", f"*/claude-code-sessions/*/*/{ident}.json"):
                for path in base.glob(pattern):
                    record = read_json(path)
                    if isinstance(record, dict):
                        return record
        return None

    def _claude_threads(self, ident: str, path: Path, title: str) -> list[Thread]:
        threads = [Thread(ident, path, "claude", "Claude", title)]
        for sub in sorted((path.parent / ident / "subagents").glob("agent-*.jsonl")):
            meta = read_json(sub.with_name(sub.stem + ".meta.json"))
            meta = meta if isinstance(meta, dict) else {}
            threads.append(Thread(sub.stem.removeprefix("agent-"), sub, "claude", string(meta.get("agentType")) or "Subagent",
                                  task=string(meta.get("description")), parent=ident))
        return threads

    @staticmethod
    def _codex_files(home: Path) -> list[Path]:
        return sorted([*home.glob("sessions/*/*/*/rollout-*.jsonl"), *home.glob("archived_sessions/rollout-*.jsonl")])

    def _codex_session(self, path: Path) -> dict:
        """The first record's session metadata; it never changes once written."""
        with self.lock:
            if path in self.codex_meta:
                return self.codex_meta[path]
        meta: dict = {}
        try:
            with path.open("rb") as handle:
                for _, record in records(handle):
                    if isinstance(record, dict) and record.get("type") == "session_meta" and isinstance(record.get("payload"), dict):
                        meta = {k: record["payload"].get(k) for k in ("session_id", "agent_nickname", "agent_path", "parent_thread_id")}
                    break
        except OSError:
            return {}
        if meta:
            with self.lock:
                self.codex_meta[path] = meta
        return meta

    def _codex_title(self, home: Path, ident: str) -> str:
        """The thread's name from Codex's session index, which appends a line per rename."""
        index = home / "session_index.jsonl"
        try:
            stat = index.stat()
        except OSError:
            return ""
        key = (stat.st_size, stat.st_mtime_ns)
        with self.lock:
            cached = self.codex_names.get(home)
        if cached is None or cached[0] != key:
            names: dict[str, str] = {}
            try:
                with index.open("rb") as handle:
                    for _, record in records(handle):
                        if isinstance(record, dict) and isinstance(record.get("id"), str) and isinstance(record.get("thread_name"), str):
                            names[record["id"]] = record["thread_name"]
            except OSError:
                return ""
            cached = (key, names)
            with self.lock:
                self.codex_names[home] = cached
        return cached[1].get(ident, "")

    def _codex_threads(self, ident: str, path: Path, home: Path) -> list[Thread]:
        def thread(file: Path, meta: dict) -> Thread:
            key = codex_id(file) or ""
            address = string(meta.get("agent_path"))
            return Thread(key, file, "codex", string(meta.get("agent_nickname")) or "Codex", self._codex_title(home, key),
                          address.rsplit("/", 1)[-1], None if key == ident else string(meta.get("parent_thread_id")) or None,
                          address or ("/root" if key == ident else ""))

        meta = self._codex_session(path)
        threads = [thread(path, meta)]
        # A thread that started others is the session they all share.
        if meta.get("session_id") == ident:
            for other, other_home in self._index()[1].values():
                if other != path and other_home == home and self._codex_session(other).get("session_id") == ident:
                    threads.append(thread(other, self._codex_session(other)))
        return threads

    # Parsing ---------------------------------------------------------------------------

    def _apply(self, thread: Thread, state: Parsed, record: Any) -> None:
        if not isinstance(record, dict):
            return
        try:
            (self._claude if thread.format == "claude" else self._codex)(thread, state, record)
        except (KeyError, TypeError, AttributeError, ValueError):
            pass  # a record shaped in a way this reader does not know is left out, not fatal

    def parsed(self, thread: Thread) -> Parsed:
        with self.lock:
            state = self.cache.get(thread.path)
            try:
                size = thread.path.stat().st_size
                with thread.path.open("rb") as handle:
                    head = handle.read(64)
                    # A shorter or rewritten file starts over; an appended one picks up where it stopped.
                    if state is None or size < state.offset or not head.startswith(state.head):
                        state = Parsed(head=head)
                    if size > state.offset:
                        handle.seek(state.offset)
                        for used, record in records(handle):
                            state.offset += used
                            state.lines += 1
                            self._apply(thread, state, record)
            except OSError:
                return state or Parsed()
            self.cache[thread.path] = state
            self.cache.move_to_end(thread.path)
            while len(self.cache) > CACHE_FILES:
                self.cache.popitem(last=False)
            return state

    def latest(self, thread: Thread) -> Parsed:
        """The newest turn and title without reading the whole transcript."""
        with self.lock:
            known = thread.path in self.cache
        if known:
            return self.parsed(thread)
        try:
            stat = thread.path.stat()
        except OSError:
            return Parsed()
        key = (stat.st_size, stat.st_mtime_ns)
        with self.lock:
            cached = self.tails.get(thread.path)
        if cached and cached[0] == key:
            return cached[1]
        probe = Parsed()
        try:
            with thread.path.open("rb") as handle:
                if stat.st_size > TAIL_BYTES:
                    handle.seek(stat.st_size - TAIL_BYTES)
                    handle.readline()  # the rest of a line that began earlier
                for _, record in records(handle):
                    self._apply(thread, probe, record)
        except OSError:
            return probe
        probe.messages.clear()
        with self.lock:
            self.tails[thread.path] = (key, probe)
            while len(self.tails) > CACHE_FILES * 8:
                self.tails.popitem(last=False)
        return probe

    @staticmethod
    def _add(state: Parsed, thread: Thread, at: str | None, kind: str, text: str, **extra: Any) -> None:
        body, cut = clip(text)
        if body:
            state.messages.append({"id": f"{thread.key}:{state.lines}", "line": state.lines, "thread": thread.key, "at": at,
                                   "kind": kind, "text": body, "truncated": cut, **extra})

    @staticmethod
    def _tool(state: Parsed, thread: Thread, at: str | None, name: str, summary: str) -> None:
        # A run of tool calls between messages reads as one line of activity.
        last = state.messages[-1] if state.messages else None
        if last is None or last["kind"] != "tools":
            last = {"id": f"{thread.key}:{state.lines}", "line": state.lines, "thread": thread.key, "at": at, "kind": "tools", "count": 0, "tools": []}
            state.messages.append(last)
        last["count"] += 1
        last["until"] = at
        if len(last["tools"]) < TOOL_LIMIT:
            last["tools"].append({"name": name, "summary": summary})

    def _claude(self, thread: Thread, state: Parsed, r: dict) -> None:
        kind, at = r.get("type"), timestamp(r.get("timestamp"))
        if kind == "custom-title" and string(r.get("customTitle")):
            state.title = r["customTitle"]
        if kind not in {"user", "assistant"} or not isinstance(r.get("message"), dict):
            return
        # Older transcripts kept subagent turns inline; their own files hold them now.
        if r.get("isSidechain") and thread.parent is None:
            return
        state.last_at = at or state.last_at
        content = r["message"].get("content")
        raw_blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content if isinstance(content, list) else []
        blocks: list[dict[str, Any]] = [b for b in raw_blocks if isinstance(b, dict)]
        if kind == "assistant":
            if string(r["message"].get("model")) and not r["message"]["model"].startswith("<"):
                state.model = r["message"]["model"]
            state.open = r["message"].get("stop_reason") not in {"end_turn", "stop_sequence"}
            message_id = r["message"].get("id")
            for b in blocks:
                if b.get("type") == "text" and string(b.get("text")):
                    last = state.messages[-1] if state.messages else None
                    # One reply arrives as several records that share its message ID.
                    if last and last["kind"] == "assistant" and message_id and state.message_id == message_id:
                        last["text"], last["truncated"] = clip(last["text"] + "\n\n" + b["text"])
                    else:
                        self._add(state, thread, at, "assistant", b["text"])
                    state.message_id = message_id
                elif b.get("type") == "tool_use":
                    args: dict[str, Any] = b["input"] if isinstance(b.get("input"), dict) else {}
                    name = string(b.get("name")) or "tool"
                    if name == "SendMessage":
                        body = next((string(v) for v in (args.get("message"), args.get("content"), args.get("summary")) if string(v).strip()), "")
                        self._add(state, thread, at, "agent", body, sender=thread.key, to=string(args.get("to")))
                    else:
                        summary = next((one_line(args[k]) for k in ("description", "file_path", "pattern", "query", "url", "command", "prompt")
                                        if string(args.get(k))), "")
                        self._tool(state, thread, at, name, summary)
            return
        if any(b.get("type") == "tool_result" for b in blocks):
            state.open = True
            return
        origin: dict[str, Any] = r["origin"] if isinstance(r.get("origin"), dict) else {}
        text = "\n\n".join(b["text"] for b in blocks if b.get("type") == "text" and string(b.get("text")) and not SKIPPED_TEXT.match(b["text"]))
        if any(b.get("type") == "image" for b in blocks):
            text = (text + "\n\n[image]").strip()
        if origin.get("kind") == "peer":
            self._add(state, thread, at, "agent", text, sender=string(origin.get("from")), to=thread.key, inbound=True)
            return
        if r.get("isMeta") or origin.get("kind") not in {None, "human"}:
            return
        command = COMMAND.search(text)
        if command:
            args_match = COMMAND_ARGS.search(text)
            text = (command.group(1).strip() + " " + (args_match.group(1).strip() if args_match else "")).strip()
        state.open = True
        if thread.parent:
            # A subagent's prompt comes from the chat that started it.
            self._add(state, thread, at, "agent", text, sender=thread.parent, to=thread.key)
        else:
            self._add(state, thread, at, "user", text)

    def _codex(self, thread: Thread, state: Parsed, r: dict) -> None:
        kind, at = r.get("type"), timestamp(r.get("timestamp"))
        p: dict[str, Any] = r["payload"] if isinstance(r.get("payload"), dict) else {}
        state.last_at = at or state.last_at
        if kind == "turn_context" and string(p.get("model")):
            state.model = p["model"]
        elif kind == "event_msg" and p.get("type") == "task_started":
            state.open = True
        elif kind == "event_msg" and p.get("type") in {"task_complete", "turn_aborted"}:
            state.open = False
        if kind != "response_item":
            return
        item = string(p.get("type"))
        content: list[dict[str, Any]] = [b for b in p["content"] if isinstance(b, dict)] if isinstance(p.get("content"), list) else []
        text = "\n\n".join(b["text"] for b in content if b.get("type") in {"input_text", "output_text", "text"}
                           and string(b.get("text")) and not SKIPPED_TEXT.match(b["text"]))
        if item == "message" and p.get("role") in {"user", "assistant"}:
            self._add(state, thread, at, p["role"], text)
        elif item == "agent_message":
            self._add(state, thread, at, "agent", text, sender=string(p.get("author")), to=string(p.get("recipient")) or thread.address)
        elif item.endswith("_call"):
            name = string(p.get("name")) or item.removesuffix("_call")
            # Messages between agents show once, where they were received.
            if p.get("namespace") == "collaboration" and name in {"send_message", "followup_task"}:
                return
            raw = string(p.get("input")) or string(p.get("arguments"))
            summary = one_line(raw)
            if raw.startswith("{"):
                try:
                    args = json.loads(raw)
                except ValueError:
                    args = None
                if isinstance(args, dict):
                    summary = next((one_line(args[k]) for k in ("cmd", "command", "path", "query", "message", "task") if string(args.get(k))), summary)
            self._tool(state, thread, at, name, summary)

    # Views -----------------------------------------------------------------------------

    def _views(self, threads: list[Thread], states: dict[str, Parsed]) -> list[dict]:
        views = []
        for t in threads:
            state = states[t.key]
            try:
                modified = t.path.stat().st_mtime
            except OSError:
                modified = 0.0
            working = state.open and time.time() - modified < WORKING_SECONDS
            views.append({"key": t.key, "name": t.name, "title": state.title or t.title, "task": t.task, "parent": t.parent,
                          "model": state.model, "state": "working" if working else "idle",
                          "lastActivityAt": state.last_at or (timestamp(datetime.fromtimestamp(modified, UTC).isoformat()) if modified else None)})
        return views

    @staticmethod
    def _overall(source: str | None, views: list[dict]) -> dict:
        return {"found": bool(views), "source": source,
                "lastActivityAt": max((v["lastActivityAt"] for v in views if v["lastActivityAt"]), default=None),
                "state": "working" if any(v["state"] == "working" for v in views) else "idle", "threads": views}

    def summary(self, chat_id: str) -> dict:
        source, threads = self.threads(chat_id)
        return self._overall(source, self._views(threads, {t.key: self.latest(t) for t in threads}))

    def messages(self, chat_id: str, *, limit: int, thread: str | None = None, labels: dict[str, str] | None = None) -> dict:
        """The latest messages of a chat and every agent it started, oldest first.

        `labels` names other chats by their chat ID, for messages that came from them."""
        source, threads = self.threads(chat_id)
        states = {t.key: self.parsed(t) for t in threads}
        views = self._views(threads, states)
        # Messages name agents by key or, in Codex, by the path agents address each other with.
        names = {t.key: t.name for t in threads} | {t.address: t.name for t in threads if t.address}

        def name(ident: str) -> str:
            return names.get(ident) or (labels or {}).get(ident.lower()) or self.label(ident) or ident

        items = [m for t in threads for m in states[t.key].messages
                 # A message one of these agents sent with SendMessage already shows where it was sent.
                 if not (m.get("inbound") and m.get("sender") in states)]
        if thread is not None:
            # One agent's view: its own transcript, and what it sent that others recorded.
            chosen = next((t for t in threads if t.key == thread), None)
            sent = {chosen.key, chosen.address} - {""} if chosen else set()
            items = [m for m in items if m["thread"] == thread or m.get("sender") in sent]
        order = {t.key: i for i, t in enumerate(threads)}
        items.sort(key=lambda m: (m["at"] or "", order[m["thread"]], m["line"]))
        page = []
        for m in items[-limit:]:
            view = {k: v for k, v in m.items() if k not in {"line", "sender", "to", "inbound"}}
            if m["kind"] == "user":
                view["author"] = "You"
            elif m["kind"] == "agent":
                view["author"] = name(m["sender"]) or "Agent"
                view["authorThread"] = next((t.key for t in threads if m["sender"] in {t.key, t.address}), None)
                view["recipient"] = name(m["to"])
            else:
                view["author"] = names[m["thread"]]
                view["authorThread"] = m["thread"]
            page.append(view)
        return {**self._overall(source, views), "messages": page, "total": len(items), "more": len(items) > limit}
