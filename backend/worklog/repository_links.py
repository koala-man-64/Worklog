"""Catalog repository associations and normalized remote identities."""

import re
import sqlite3
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from .common import AppError, now, rows_in, text

Owner = Literal["item", "chat", "ticket", "step"]
_TABLES = {"item": ("item_repositories", "item_id"), "chat": ("chat_repositories", "chat_id"),
           "ticket": ("ticket_repositories", "ticket_id"), "step": ("step_repositories", "step_id")}


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


def roadmap_step_repositories(conn: sqlite3.Connection, roadmap_id: str) -> dict[str, list[str]]:
    """Every step's linked repository IDs on one roadmap, in one query (audit 3.3 bounded reads)."""
    result: dict[str, list[str]] = {}
    for step_id, repository_id in conn.execute(
        "SELECT sr.step_id,sr.repository_id FROM step_repositories sr "
        "JOIN steps s ON s.id=sr.step_id WHERE s.roadmap_id=? ORDER BY sr.step_id,sr.repository_id",
        (roadmap_id,),
    ):
        result.setdefault(step_id, []).append(repository_id)
    return result


def repository_dependencies(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict[str, list[str]]]:
    """Each repository's direct upstream and downstream catalog dependencies, in two bounded queries."""
    unique = list(dict.fromkeys(ids))
    result: dict[str, dict[str, list[str]]] = {rid: {"upstreamIds": [], "downstreamIds": []} for rid in unique}
    if not unique:
        return result
    for downstream, upstream in rows_in(
        conn, "SELECT downstream_id,upstream_id FROM repository_dependencies WHERE downstream_id IN ({}) ORDER BY upstream_id", unique
    ):
        result[downstream]["upstreamIds"].append(upstream)
    for upstream, downstream in rows_in(
        conn, "SELECT upstream_id,downstream_id FROM repository_dependencies WHERE upstream_id IN ({}) ORDER BY downstream_id", unique
    ):
        result[upstream]["downstreamIds"].append(downstream)
    return result


def replace_repository_dependencies(conn: sqlite3.Connection, repository_id: str, upstream_ids: list[str]) -> list[str]:
    """Replace a repository's direct upstream dependencies, refusing self-links, archived/merged targets, and cycles."""
    if not isinstance(upstream_ids, list) or len(upstream_ids) > 100 or not all(isinstance(i, str) for i in upstream_ids):
        raise AppError(422, "validation", "Choose up to 100 upstream repositories")
    desired = sorted(dict.fromkeys(upstream_ids))
    if repository_id in desired:
        raise AppError(422, "validation", "A repository cannot depend on itself", {"upstreamIds": "self"})
    if desired:
        placeholders = ",".join("?" for _ in desired)
        count = conn.execute(
            f"SELECT COUNT(*) FROM repositories WHERE archived=0 AND merged_into IS NULL AND id IN ({placeholders})",
            desired,
        ).fetchone()[0]
        if count != len(desired):
            raise AppError(422, "repository_unavailable", "Choose existing, active upstream repositories")
        # Cycle check: repository_id must not be reachable going upstream from any desired upstream.
        edges: dict[str, list[str]] = {}
        for up, down in conn.execute("SELECT upstream_id,downstream_id FROM repository_dependencies WHERE downstream_id<>?", (repository_id,)):
            edges.setdefault(down, []).append(up)
        edges[repository_id] = list(desired)
        stack = list(desired)
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current == repository_id:
                raise AppError(422, "validation", "Repository dependencies cannot form a cycle", {"upstreamIds": "cycle"})
            if current in seen:
                continue
            seen.add(current)
            stack.extend(edges.get(current, ()))
    previous = {row[0] for row in conn.execute("SELECT upstream_id FROM repository_dependencies WHERE downstream_id=?", (repository_id,))}
    wanted = set(desired)
    conn.executemany(
        "DELETE FROM repository_dependencies WHERE upstream_id=? AND downstream_id=?",
        [(up, repository_id) for up in previous - wanted],
    )
    stamp = now()
    conn.executemany(
        "INSERT INTO repository_dependencies(upstream_id,downstream_id,created_at) VALUES(?,?,?)",
        [(up, repository_id, stamp) for up in wanted - previous],
    )
    return desired


def reorder_repository_queue(conn: sqlite3.Connection, repository_id: str, step_ids: list[str]) -> list[str]:
    """Set explicit per-repository execution queue positions on step_repositories without bumping step revisions (K4)."""
    if not isinstance(step_ids, list) or len(step_ids) > 2000 or not all(isinstance(sid, str) and sid for sid in step_ids):
        raise AppError(422, "validation", "Provide an ordered list of step IDs")
    if len(set(step_ids)) != len(step_ids):
        raise AppError(422, "validation", "Duplicate step IDs in queue order")
    linked = {
        row[0]
        for row in conn.execute(
            "SELECT sr.step_id FROM step_repositories sr JOIN steps s ON s.id=sr.step_id "
            "WHERE sr.repository_id=? AND s.retired=0",
            (repository_id,),
        )
    }
    unknown = [sid for sid in step_ids if sid not in linked]
    if unknown:
        raise AppError(422, "validation", "Every queued step must be an active step linked to this repository", {"stepIds": unknown[:10]})
    for pos, sid in enumerate(step_ids, 1):
        conn.execute(
            "UPDATE step_repositories SET position=? WHERE step_id=? AND repository_id=?",
            (pos, sid, repository_id),
        )
    return list(step_ids)


def merge_repository_dependencies(conn: sqlite3.Connection, source_id: str, survivor_id: str) -> int:
    """Move a merged repository's dependency edges to the survivor, dropping self-edges and any edge
    that would introduce a cycle."""
    rows = conn.execute(
        "SELECT upstream_id,downstream_id,created_at FROM repository_dependencies WHERE upstream_id=? OR downstream_id=?",
        (source_id, source_id),
    ).fetchall()
    if not rows:
        return 0
    conn.execute("DELETE FROM repository_dependencies WHERE upstream_id=? OR downstream_id=?", (source_id, source_id))

    def reaches_upstream(start: str, target: str) -> bool:
        stack, seen = [start], set()
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(
                r[0] for r in conn.execute("SELECT upstream_id FROM repository_dependencies WHERE downstream_id=?", (current,))
            )
        return False

    moved = 0
    for up, down, created_at in rows:
        new_up = survivor_id if up == source_id else up
        new_down = survivor_id if down == source_id else down
        if new_up == new_down:
            continue
        if conn.execute(
            "SELECT 1 FROM repository_dependencies WHERE upstream_id=? AND downstream_id=?",
            (new_up, new_down),
        ).fetchone():
            continue
        # Adding new_up -> new_down creates a cycle iff new_up can already reach new_down going upstream.
        if reaches_upstream(new_up, new_down):
            continue
        conn.execute(
            "INSERT INTO repository_dependencies(upstream_id,downstream_id,created_at) VALUES(?,?,?)",
            (new_up, new_down, created_at),
        )
        moved += 1
    return moved
