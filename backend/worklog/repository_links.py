"""Catalog repository associations for work items, tracked chats and tickets."""

import sqlite3
from typing import Literal

from .common import AppError, now

Owner = Literal["item", "chat", "ticket"]
_TABLES = {"item": ("item_repositories", "item_id"), "chat": ("chat_repositories", "chat_id"),
           "ticket": ("ticket_repositories", "ticket_id")}


def linked_repositories(conn: sqlite3.Connection, owner: Owner,
                        ids: list[str]) -> dict[str, list[str]]:
    table, column = _TABLES[owner]
    result: dict[str, list[str]] = {}
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT {column},repository_id FROM {table} WHERE {column} IN ({placeholders}) "
            "ORDER BY repository_id", batch)
        for target, repository in rows:
            result.setdefault(target, []).append(repository)
    return result


def replace_repository_associations(conn: sqlite3.Connection, owner: Owner,
                                    identity: str, ids: list[str]) -> list[str]:
    if not isinstance(ids, list) or len(ids) > 100 or not all(isinstance(i, str) for i in ids):
        raise AppError(422, "validation", "Choose up to 100 repositories")
    desired = set(ids)
    previous = set(linked_repositories(conn, owner, [identity]).get(identity, []))
    added = desired - previous
    if added:
        placeholders = ",".join("?" for _ in added)
        count = conn.execute(
            f"SELECT COUNT(*) FROM repositories WHERE archived=0 AND id IN ({placeholders})",
            sorted(added)).fetchone()[0]
        if count != len(added):
            raise AppError(422, "repository_unavailable", "Choose an existing, active repository")
    table, column = _TABLES[owner]
    conn.executemany(f"DELETE FROM {table} WHERE {column}=? AND repository_id=?",
                     [(identity, repository) for repository in previous - desired])
    conn.executemany(f"INSERT INTO {table}({column},repository_id,created_at) VALUES(?,?,?)",
                     [(identity, repository, now()) for repository in added])
    return sorted(desired)
