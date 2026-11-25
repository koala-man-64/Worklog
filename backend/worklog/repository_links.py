"""Catalog repository associations and normalized remote identities."""

import re
import sqlite3
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from .common import AppError, now, rows_in, text

Owner = Literal["item", "chat", "ticket"]
_TABLES = {"item": ("item_repositories", "item_id"), "chat": ("chat_repositories", "chat_id"),
           "ticket": ("ticket_repositories", "ticket_id")}


def _repository_url(value: Any) -> str:
    value = text(value, "url", limit=2000)
    error = "Use an HTTP(S), ssh://git@host/path, or git@host:path remote without passwords, query, fragment or control characters"
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise AppError(422, "validation", error)
    value = value.strip()
    if not value:
        return ""
    if any(char.isspace() for char in value) or "?" in value or "#" in value:
        raise AppError(422, "validation", error)
    # SCP-like Git remotes are declared addresses, never commands or filesystem paths.
    scp = re.fullmatch(r"git@([A-Za-z0-9][A-Za-z0-9._-]*):(.+)", value)
    if scp:
        return "git@" + scp[1].lower() + ":" + scp[2]
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https", "ssh"} or not parts.hostname or parts.password is not None:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", parts.hostname):
            raise ValueError
        if parts.scheme == "ssh" and (parts.username != "git" or parts.path in {"", "/"}):
            raise ValueError
        _ = parts.port
    except ValueError:
        raise AppError(422, "validation", error) from None
    if parts.scheme == "ssh":
        return urlunsplit(("ssh", parts.netloc.lower(), parts.path, "", ""))
    # An HTTP(S) user name, as in Azure DevOps clone URLs, may be a token: never keep it.
    return urlunsplit((parts.scheme.lower(), parts.netloc.rpartition("@")[2].lower(), parts.path.rstrip("/"), "", ""))


def repository_identity(url: str) -> str:
    """One key for every address of a remote: host plus path, lowercased, without
    scheme, user, port, `.git` or trailing `/`.

    HTTPS, ssh:// and scp-like forms share it, and so do Azure DevOps' dev.azure.com,
    {org}.visualstudio.com and ssh.dev.azure.com:v3 forms. Expects an address that
    _repository_url accepted; anything else keys as its lowercased text.
    """
    if not url:
        return ""
    scp = re.fullmatch(r"[^@/]+@([^:/]+):(.*)", url)
    if scp:
        host, path = scp[1].lower(), scp[2]
    else:
        try:
            parts = urlsplit(url)
        except ValueError:
            return url.lower()
        host, path = parts.hostname or "", parts.path
    segments = [segment for segment in path.lower().strip("/").removesuffix(".git").split("/") if segment]
    if host in {"ssh.dev.azure.com", "vs-ssh.visualstudio.com"}:
        if len(segments) == 4 and segments[0] == "v3":
            host, segments = "dev.azure.com", [segments[1], segments[2], "_git", segments[3]]
    elif host.endswith(".visualstudio.com"):
        if segments[:1] == ["defaultcollection"]:
            segments = segments[1:]
        host, segments = "dev.azure.com", [host.removesuffix(".visualstudio.com"), *segments]
    if host == "dev.azure.com" and len(segments) == 3 and segments[1] == "_git":
        # A project's default repository may leave out the project: {org}/_git/{project}.
        segments = [segments[0], segments[2], "_git", segments[2]]
    return "/".join([host, *segments])


def linked_repositories(conn: sqlite3.Connection, owner: Owner,
                        ids: list[str]) -> dict[str, list[str]]:
    table, column = _TABLES[owner]
    result: dict[str, list[str]] = {}
    for target, repository in rows_in(conn, f"SELECT {column},repository_id FROM {table} WHERE {column} IN ({{}}) "
                                            "ORDER BY repository_id", ids):
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
