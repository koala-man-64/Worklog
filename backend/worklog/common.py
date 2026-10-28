"""Small shared domain primitives; no HTTP or process side effects."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

STATUSES = {"open", "doing", "blocked", "done"}
NOTE_LIMIT = 1_000_000
FILE_LIMIT = 25_000_000
HTML_LIMIT = 5_000_000


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
    if not isinstance(value, str) or value not in STATUSES:
        raise AppError(422, "validation", "Choose Open, Doing, Blocked, or Done", {"status": "invalid"})
    return str(value)


def setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def event(conn: sqlite3.Connection, actor: dict, kind: str, entity_id: str,
          before: Any, after: Any) -> dict:
    result = {"id": uid(), "entityId": entity_id, "kind": kind, "actorId": actor["id"],
              "actorLabel": actor["label"], "actorKind": actor["kind"], "createdAt": now(),
              "localDate": today(setting(conn, "timezone", "America/Chicago")),
              "before": before, "after": after}
    conn.execute("""INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)""",
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
