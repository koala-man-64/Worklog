"""Small shared domain primitives; no HTTP or process side effects."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

STATUSES = {"open", "doing", "blocked", "done"}
# A work item can also wait on someone else (v15); tickets keep the four above.
ITEM_STATUSES = STATUSES | {"waiting"}
NOTE_LIMIT = 1_000_000
FILE_LIMIT = 25_000_000
HTML_LIMIT = 5_000_000
# Ids bound per statement by rows_in, well below SQLite's limit on variables.
IN_BATCH = 400
# Whether the agent identity aliased `a` has a credential that is not revoked. A query using it
# names communication_agents `a` and uses neither `cc` nor `cr` itself. This is not the check for
# one run's or one request's own credential, which a revoked credential must fail on its own.
LIVE_AGENT = ("EXISTS(SELECT 1 FROM communication_credentials cc JOIN credentials cr ON cr.id=cc.credential_id "
              "WHERE cc.agent_id=a.id AND cr.revoked=0)")
# Characters that don't show as themselves: controls, formatting (bidirectional overrides and
# zero-width characters among them), lone surrogates, and line and paragraph separators.
UNSEEN = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def shows_as_written(value: str) -> bool:
    """Whether every character of value shows as itself, so a reader sees what was recorded.
    A space of any kind shows as a space, as in a folder name."""
    return not any(unicodedata.category(ch) in UNSEEN for ch in value)


def rows_in(c: sqlite3.Connection, sql: str, ids: Iterable[str], size: int = IN_BATCH) -> list[sqlite3.Row]:
    """The rows of sql, whose `IN ({})` takes the ids, run in batches SQLite can bind.

    Each id is looked up once, and rows come batch by batch, each in its statement's order, so
    an ORDER BY holds among the rows of one id rather than across all of them.
    """
    unique = list(dict.fromkeys(ids))
    rows: list[sqlite3.Row] = []
    for start in range(0, len(unique), size):
        batch = unique[start:start + size]
        rows.extend(c.execute(sql.format(",".join("?" * len(batch))), batch).fetchall())
    return rows


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.fields = status, code, message, fields


def uid() -> str:
    return str(uuid.uuid4())


def now() -> str:
    return datetime.now(UTC).isoformat()


def today(tz: str = "America/Chicago") -> str:
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def valid_date(value: str) -> str:
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise AppError(422, "validation", "Use a valid YYYY-MM-DD calendar date", {"date": value}) from None
    return value


def require_revision(row: Any, expected: Any) -> None:
    if row is None:
        raise AppError(404, "not_found", "Record not found")
    if isinstance(expected, bool) or not isinstance(expected, int) or row["revision"] != expected:
        raise AppError(409, "conflict", "This record changed. Reload and review your draft.",
                       {"expected": expected, "currentRevision": row["revision"]})


def text(value: Any, field: str, *, required: bool = False, limit: int = NOTE_LIMIT) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise AppError(422, "validation", f"Invalid or oversized {field}", {field: "invalid"})
    if required and not value.strip():
        raise AppError(422, "validation", f"{field} is required", {field: "required"})
    return value.strip() if required else value


def status(value: Any) -> str:
    """A ticket's status."""
    if not isinstance(value, str) or value not in STATUSES:
        raise AppError(422, "validation", "Choose Open, Doing, Blocked, or Done", {"status": "invalid"})
    return str(value)


def item_status(value: Any) -> str:
    """A work item's status."""
    if not isinstance(value, str) or value not in ITEM_STATUSES:
        raise AppError(422, "validation", "Choose Open, Doing, Waiting, Blocked, or Done", {"status": "invalid"})
    return str(value)


def repository_labels(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 20:
        raise AppError(422, "validation", "repos must be a list of at most 20 repositories")
    names = [text(name, "repository", required=True, limit=500) for name in value]
    return list(dict.fromkeys(names))


def boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise AppError(422, "validation", f"{field} must be a boolean")
    return value


def setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def event(conn: sqlite3.Connection, actor: dict, kind: str, entity_id: str,
          before: Any, after: Any, *, at: str | None = None) -> dict:
    # `at` lets a record that carries this event's time state it before the event is written.
    result = {"id": uid(), "entityId": entity_id, "kind": kind, "actorId": actor["id"],
              "actorLabel": actor["label"], "actorKind": actor["kind"], "createdAt": at or now(),
              "localDate": today(setting(conn, "timezone", "America/Chicago")),
              "before": before, "after": after}
    conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,before_json,after_json) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (result["id"], entity_id, kind, actor["id"], actor["label"], actor["kind"],
                  result["createdAt"], result["localDate"], json.dumps(before), json.dumps(after)))
    return result


def read_events(conn: sqlite3.Connection, entity_id: str, limit: int = 100) -> list[dict]:
    rows = conn.execute("SELECT * FROM events WHERE entity_id=? ORDER BY created_at DESC,id DESC LIMIT ?",
                        (entity_id, limit)).fetchall()
    return [{"id": r["id"], "entityId": r["entity_id"], "kind": r["kind"],
             "actorId": r["actor_id"], "actorLabel": r["actor_label"],
             "actorKind": r["actor_kind"], "createdAt": r["created_at"],
             "localDate": r["local_date"], "before": json.loads(r["before_json"]),
             "after": json.loads(r["after_json"])} for r in rows]


def unpack(row: Any) -> dict | None:
    return dict(row) if row is not None else None


# A JSON escape of a high or low surrogate. A valid pair, such as an emoji the agent CLI sends
# as 🚀, matches too; only the parse below tells a pair from a lone half.
_ESCAPED_SURROGATE = re.compile(rb"\\u[dD][89a-fA-F]")


def lone_surrogate(body: bytes) -> str | None:
    """Where a JSON body holds a lone UTF-16 surrogate, as a dotted path such as
    `operations.0.payload.title`, or None when it holds none.

    A lone surrogate is a valid JSON escape that names no character: Python decodes it, but no
    validator can measure it and SQLite cannot bind it, so it must be refused where requests
    enter. Only a body that could hold one is parsed: one with a surrogate escape, or one that
    is not UTF-8 (raw surrogate bytes, or another encoding JSON allows).
    """
    try:
        body.decode("utf-8")
        if not _ESCAPED_SURROGATE.search(body):
            return None
    except UnicodeDecodeError:
        pass
    try:
        # As the request will be read: bytes decode with surrogatepass, so raw halves arrive too.
        document = json.loads(body)
    except ValueError:
        return None  # not JSON, which the route itself refuses

    def whole(value: str) -> bool:
        try:
            value.encode("utf-8")
            return True
        except UnicodeEncodeError:
            return False

    pending: list[tuple[str, Any]] = [("", document)]
    while pending:
        path, value = pending.pop()
        if isinstance(value, str) and not whole(value):
            return path[:200] or "body"
        if isinstance(value, dict):
            for key, item in value.items():
                if not whole(key):
                    return path[:200] or "body"
                pending.append((f"{path}.{key}" if path else key, item))
        elif isinstance(value, list):
            pending.extend((f"{path}.{index}" if path else str(index), item) for index, item in enumerate(value))
    return None
