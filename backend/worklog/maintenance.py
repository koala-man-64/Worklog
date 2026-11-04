"""Retention for records that only serve retries."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

# A retry replays its first result while the request's receipt is kept.
RECEIPT_DAYS = 14


def prune_receipts(conn: sqlite3.Connection, *, now: datetime | None = None, days: int = RECEIPT_DAYS) -> int:
    """Delete idempotency receipts older than the retention window; return how many went.

    receipts.created_at (v11) holds common.now() text, which sorts in time order.
    """
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=days)).isoformat()
    return conn.execute("DELETE FROM receipts WHERE created_at<?", (cutoff,)).rowcount
