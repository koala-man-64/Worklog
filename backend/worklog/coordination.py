"""Reported activity with overlapping participants and no execution authority."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import search
from .chat_messages import expire_pending
from .common import AppError, event, now, require_revision, setting, text, uid
from .discussions import communication_actor


def _get(c: sqlite3.Connection, table: str, identity: str) -> sqlite3.Row:
    # Table names are internal constants.
    row = c.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Coordination record not found")
    return row


def repository_record(row: Any) -> dict:
    registrant = ({"kind": row["registered_by_kind"], "label": row["registered_by_label"], "agentId": row["registered_by_agent"]}
                  if row["registered_by_kind"] else None)
    return {"id": row["id"], "name": row["name"], "url": row["url"],
            "remoteIdentity": row["remote_identity"], "duplicateOf": row["duplicate_of"], "mergedInto": row["merged_into"],
            "description": row["description"], "archived": bool(row["archived"]),
            "registeredBy": registrant, "reviewed": bool(row["reviewed"]),
            "revision": row["revision"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def agents_may_register(c: sqlite3.Connection) -> bool:
    # Only an explicit false restores the approval gate; a missing or unexpected value keeps registration open.
    return setting(c, "agentRepositoryRegistration", True) is not False


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


def _release(c: sqlite3.Connection, actor: dict, row: Any, reason: str) -> None:
    if row["released_at"] is not None:
        return
    stamp = now()
    c.execute("UPDATE participations SET released_at=?,release_reason=?,updated_at=?,revision=revision+1 WHERE id=?",
              (stamp, reason, stamp, row["id"]))
    event(c, actor, "participation.release", row["id"], dict(row), {"reason": reason})


def release_item_participations(c: sqlite3.Connection, actor: dict, item_id: str) -> None:
    for row in c.execute("SELECT * FROM participations WHERE item_id=? AND released_at IS NULL", (item_id,)).fetchall():
        _release(c, actor, row, "item_archived")


def close_credential_runs(c: sqlite3.Connection, actor: dict, credential_id: str, reason: str = "credential_revoked") -> None:
    for row in c.execute("SELECT * FROM agent_runs WHERE credential_id=? AND ended_at IS NULL", (credential_id,)).fetchall():
        _close_run(c, actor, row, reason)


def _close_run(c: sqlite3.Connection, actor: dict, row: Any, reason: str) -> None:
    stamp = now()
    c.execute("UPDATE agent_runs SET ended_at=?,end_reason=?,updated_at=?,revision=revision+1 WHERE id=?",
              (stamp, reason, stamp, row["id"]))
    for participation in c.execute("SELECT * FROM participations WHERE run_id=? AND released_at IS NULL", (row["id"],)).fetchall():
        _release(c, actor, participation, reason)
    expire_pending(c, reason, run_id=row["id"], stamp=stamp)
    event(c, actor, "run.end", row["id"], dict(row), {"reason": reason})


def repository_holder(c: sqlite3.Connection, identity: str, exclude: str = "") -> Any:
    """The record that answers for a remote: an active one before an archived one, and the
    canonical record before a duplicate flagged for merging. A merged record's remote
    answers with the record it was merged into."""
    if not identity:
        return None
    row = c.execute("SELECT * FROM repositories WHERE remote_identity=? AND id<>? ORDER BY merged_into IS NOT NULL,"
                    "archived,duplicate_of IS NOT NULL,created_at,id LIMIT 1", (identity, exclude)).fetchone()
    if row is not None and row["merged_into"]:
        row = _get(c, "repositories", row["merged_into"])
    return None if row is None or row["id"] == exclude else row


def _point(c: sqlite3.Connection, actor: dict, row: Any, column: str, target: str | None) -> None:
    # Column names are internal constants: duplicate_of or merged_into.
    c.execute(f"UPDATE repositories SET {column}=?,revision=revision+1,updated_at=? WHERE id=?", (target, now(), row["id"]))
    event(c, actor, "repository.update", row["id"], repository_record(row), repository_record(_get(c, "repositories", row["id"])))


def _hand_over(c: sqlite3.Connection, actor: dict, identity: str, old_key: str) -> None:
    """The canonical record left its remote: the duplicates it leaves behind name a new one."""
    heirs = c.execute("SELECT * FROM repositories WHERE duplicate_of=? AND remote_identity=? ORDER BY archived,created_at,id",
                      (identity, old_key)).fetchall()
    for position, heir in enumerate(heirs):
        _point(c, actor, heir, "duplicate_of", None if position == 0 else heirs[0]["id"])


def _refuse_merged(c: sqlite3.Connection, row: Any) -> None:
    if row["merged_into"]:
        name = _get(c, "repositories", row["merged_into"])["name"]
        raise AppError(409, "repository_merged", f"{row['name']} was merged into {name}; use that repository instead",
                       {"repositoryId": row["merged_into"]})


def repository_takes_files(c: sqlite3.Connection, identity: str) -> None:
    """Only an active record takes new files, as with links; the ones it has stay readable."""
    row = _get(c, "repositories", identity)
    _refuse_merged(c, row)
    if row["archived"]:
        raise AppError(409, "archived", f"Recover {row['name']} before attaching files to it", {"repositoryId": identity})


def repository_takes_people(c: sqlite3.Connection, identity: str, *, new: bool) -> None:
    """A person is tied to a repository that exists; a new tie, like a new file, needs an active record."""
    row = c.execute("SELECT * FROM repositories WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Repository does not exist")
    if not new:
        return
    _refuse_merged(c, row)
    if row["archived"]:
        raise AppError(409, "archived", f"Recover {row['name']} before tying people to it", {"repositoryId": identity})


def repository_resolution(c: sqlite3.Connection, url: str) -> dict:
    """What registering this address would do: link to the record holding its remote, or add one."""
    address = _repository_url(url)
    key = repository_identity(address)
    holder = repository_holder(c, key)
    return {"url": address, "remoteIdentity": key, "repository": repository_record(holder) if holder else None}


# Tables that link other records to a repository; each keys on its owner and the repository.
_REPOSITORY_LINKS = {"items": "item_repositories", "chats": "chat_repositories", "tickets": "ticket_repositories",
                     "updates": "coordination_update_repositories"}


def repository_merge(c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
    """Fold a record into another that stands for the same repository: a person's decision.

    Links, reported contexts, updates and attached files move to the survivor without changing
    the revisions of the records that hold them; update snapshots keep the names they were posted with.
    The merged record stays, archived and read-only, and its remote answers with the survivor.
    """
    if actor["kind"] != "human":
        raise AppError(403, "forbidden", "Only a person may merge repositories")
    source = _get(c, "repositories", identity)
    survivor = _get(c, "repositories", text(p.get("intoId"), "intoId", required=True, limit=200))
    require_revision(source, p.get("revision"))
    require_revision(survivor, p.get("intoRevision"))
    if source["id"] == survivor["id"]:
        raise AppError(422, "validation", "Choose another repository to merge into")
    _refuse_merged(c, source)
    _refuse_merged(c, survivor)
    if survivor["archived"]:
        raise AppError(409, "archived", f"Recover {survivor['name']} before merging into it", {"repositoryId": survivor["id"]})
    moved = {"contexts": c.execute("UPDATE participation_contexts SET repository_id=? WHERE repository_id=?",
                                   (survivor["id"], source["id"])).rowcount}
    for kind, table in _REPOSITORY_LINKS.items():
        # Table names are internal constants. An owner already linked to the survivor keeps that link.
        moved[kind] = c.execute(f"SELECT count(*) FROM {table} WHERE repository_id=?", (source["id"],)).fetchone()[0]
        c.execute(f"UPDATE OR IGNORE {table} SET repository_id=? WHERE repository_id=?", (survivor["id"], source["id"]))
        c.execute(f"DELETE FROM {table} WHERE repository_id=?", (source["id"],))
    # So do the people tied to it. One tied to both keeps the survivor's tie, with its role.
    moved["people"] = c.execute("SELECT count(*) FROM person_links WHERE target_type='repository' AND target_id=?",
                                (source["id"],)).fetchone()[0]
    c.execute("UPDATE OR IGNORE person_links SET target_id=? WHERE target_type='repository' AND target_id=?",
              (survivor["id"], source["id"]))
    c.execute("DELETE FROM person_links WHERE target_type='repository' AND target_id=?", (source["id"],))
    # Attached files follow too, archived ones included, so none stay on a record that takes no changes.
    search.move_repository_files(c, source["id"], survivor["id"])
    moved["attachments"] = c.execute("UPDATE attachments SET owner_id=? WHERE owner_type='repository' AND owner_id=?",
                                     (survivor["id"], source["id"])).rowcount
    before = repository_record(source)
    c.execute("UPDATE repositories SET archived=1,duplicate_of=NULL,merged_into=?,revision=revision+1,updated_at=? WHERE id=?",
              (survivor["id"], now(), source["id"]))
    # The survivor now answers for whatever pointed at the source: its duplicates and earlier merges.
    if survivor["duplicate_of"] == source["id"]:
        _point(c, actor, survivor, "duplicate_of", None)
    for column in ("duplicate_of", "merged_into"):
        for row in c.execute(f"SELECT * FROM repositories WHERE {column}=?", (source["id"],)).fetchall():
            _point(c, actor, row, column, survivor["id"])
    result = repository_record(_get(c, "repositories", source["id"]))
    event(c, actor, "repository.merge", source["id"], before, {**result, "moved": moved})
    kept = repository_record(_get(c, "repositories", survivor["id"]))
    event(c, actor, "repository.merge", survivor["id"], kept, {**kept, "mergedFrom": source["id"], "moved": moved})
    return result


def _registered(c: sqlite3.Connection, key: str) -> Any:
    """The active record holding this remote; one held only by an archived record is the person's to recover."""
    holder = repository_holder(c, key)
    if holder and holder["archived"]:
        raise AppError(409, "repository_archived", f"{holder['name']} is registered at that address but archived; "
                       "recover it instead", {"repositoryId": holder["id"]})
    return holder


def _register(c: sqlite3.Connection, actor: dict, name: str, url: str, description: str, *, reviewed: bool) -> str:
    # An agent is recorded by its identity, which outlives the credential that registered.
    agent = c.execute("SELECT a.id,a.label FROM communication_agents a JOIN communication_credentials cc ON cc.agent_id=a.id"
                      " WHERE cc.credential_id=?", (actor["id"],)).fetchone() if actor["kind"] == "agent" else None
    identity, stamp = uid(), now()
    c.execute("INSERT INTO repositories(id,name,url,remote_identity,description,archived,revision,created_at,updated_at,"
              "registered_by_kind,registered_by_label,registered_by_agent,reviewed) VALUES(?,?,?,?,?,0,1,?,?,?,?,?,?)",
              (identity, name, url, repository_identity(url), description, stamp, stamp, actor["kind"],
               agent["label"] if agent else actor["label"], agent["id"] if agent else None, int(reviewed)))
    return identity


def repository_ensure(c: sqlite3.Connection, actor: dict, p: dict) -> dict:
    """Register a remote, or return the record that already holds it: how agents register
    the repositories they work in. Their registrations stay unreviewed until the person
    confirms or edits them, and the person can turn agent registration off in Settings."""
    url = _repository_url(p.get("url", ""))
    if not url:
        raise AppError(422, "validation", "url is required", {"url": "required"})
    holder = _registered(c, repository_identity(url))
    if holder:
        return {**repository_record(holder), "created": False}
    if actor["kind"] == "agent" and not agents_may_register(c):
        raise AppError(403, "repository_registration_off", "That remote is not registered, and agents may not register "
                       "repositories; propose repository.create instead", {"url": url})
    # Unnamed, a repository takes its own name from the address: the last path segment.
    name = text(p.get("name") or re.split(r"[/:]", url.rstrip("/"))[-1].removesuffix(".git") or url, "name", required=True, limit=200)
    identity = _register(c, actor, name, url, text(p.get("description", ""), "description", limit=10000),
                         reviewed=actor["kind"] != "agent")
    result = repository_record(_get(c, "repositories", identity))
    event(c, actor, "repository.create", identity, None, result)
    return {**result, "created": True}


def repository_confirm(c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
    """The person accepts a repository an agent registered for itself."""
    if actor["kind"] != "human":
        raise AppError(403, "forbidden", "Only a person may confirm a repository")
    row = _get(c, "repositories", identity)
    require_revision(row, p.get("revision"))
    _refuse_merged(c, row)
    if row["reviewed"]:
        return repository_record(row)
    c.execute("UPDATE repositories SET reviewed=1,revision=revision+1,updated_at=? WHERE id=?", (now(), identity))
    result = repository_record(_get(c, "repositories", identity))
    event(c, actor, "repository.confirm", identity, repository_record(row), result)
    return result


def repository_apply(c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
    """The person's catalog commands, and approved proposals': what they register or edit counts as reviewed."""
    if action not in {"repository.create", "repository.update", "repository.archive", "repository.recover"}:
        raise AppError(422, "validation", "Unknown repository command")
    current = None if action == "repository.create" else _get(c, "repositories", p["id"])
    before = repository_record(current) if current else None
    if before:
        require_revision(before, p.get("revision"))
        _refuse_merged(c, current)
    if action.endswith(("archive", "recover")):
        assert before is not None
        archived = action.endswith("archive")
        c.execute("UPDATE repositories SET archived=?,revision=revision+1,updated_at=? WHERE id=?", (int(archived), now(), before["id"]))
        if archived:
            # A repository disappearing never implies that the work was completed.
            rows = c.execute("SELECT DISTINCT p.* FROM participations p JOIN participation_contexts pc ON pc.participation_id=p.id WHERE pc.repository_id=? AND p.released_at IS NULL", (before["id"],)).fetchall()
            for row in rows:
                live_item = row["item_id"] and c.execute("SELECT 1 FROM work_items WHERE id=? AND archived=0", (row["item_id"],)).fetchone()
                live_repo = c.execute("SELECT 1 FROM participation_contexts pc JOIN repositories r ON r.id=pc.repository_id WHERE pc.participation_id=? AND r.archived=0", (row["id"],)).fetchone()
                if not live_item and not live_repo:
                    _release(c, actor, row, "repository_archived")
        identity = before["id"]
    else:
        values = {"name": "", "url": "", "description": ""} if before is None else dict(before)
        for field in ("name", "url", "description"):
            if field in p:
                values[field] = p[field]
        name = text(values["name"], "name", required=True, limit=200)
        url = _repository_url(values["url"])
        description = text(values["description"], "description", limit=10000)
        key = repository_identity(url)
        old_key = before["remoteIdentity"] if before else None
        if before is None:
            holder = _registered(c, key)
            if holder:
                # Registering a repository again links to the record it already has.
                return {**repository_record(holder), "created": False}
            identity = _register(c, actor, name, url, description, reviewed=True)
        else:
            holder = repository_holder(c, key, before["id"]) if key != old_key else None
            if holder:
                raise AppError(409, "repository_duplicate", f"That repository is already registered as {holder['name']}"
                               + (" (archived)" if holder["archived"] else ""), {"repositoryId": holder["id"]})
            identity, duplicate_of = before["id"], before["duplicateOf"] if key == old_key else None
            c.execute("UPDATE repositories SET name=?,url=?,remote_identity=?,duplicate_of=?,description=?,reviewed=1,"
                      "revision=revision+1,updated_at=? WHERE id=?", (name, url, key, duplicate_of, description, now(), identity))
            if old_key and key != old_key and before["duplicateOf"] is None:
                _hand_over(c, actor, identity, old_key)
    result = repository_record(_get(c, "repositories", identity))
    event(c, actor, action, identity, before, result)
    return {**result, "created": True} if action == "repository.create" else result


class CoordinationService:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _page(c: sqlite3.Connection, sql: str, args: list, limit: int, offset: int, render) -> dict:
        total = c.execute("SELECT COUNT(*) FROM (" + sql + ")", args).fetchone()[0]
        rows = c.execute(sql + " LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return {"items": [render(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def repositories(self, c, archived=False, limit=50, offset=0, q="") -> dict:
        q = text(q, "q", limit=500).strip().lower()
        return self._page(c, "SELECT * FROM repositories WHERE archived=? AND (?='' OR instr(lower(name || ' ' || url || ' ' || description),?)>0) ORDER BY name COLLATE NOCASE,id", [int(archived), q, q], limit, offset, repository_record)

    def context_summaries(self, c, item_id=None, repository_id=None, limit=50, offset=0) -> dict:
        """Filter before paging; reported context never creates a canonical work link."""
        if item_id is None and repository_id is None:
            return {"repositories": self.repositories(c, limit=limit, offset=offset),
                    "runs": self.runs(c, limit=limit, offset=offset)}
        repo_where, repo_args = ["1=1"], []
        run_where, run_args = ["p.run_id=ar.id", "p.released_at IS NULL"], []
        if item_id is not None:
            repo_where.append("(r.id IN (SELECT repository_id FROM item_repositories WHERE item_id=?) OR EXISTS(SELECT 1 FROM participation_contexts pc JOIN participations p ON p.id=pc.participation_id WHERE pc.repository_id=r.id AND p.item_id=? AND p.released_at IS NULL))")
            repo_args.extend([item_id, item_id])
            run_where.append("p.item_id=?")
            run_args.append(item_id)
        if repository_id is not None:
            repo_where.append("r.id=?")
            repo_args.append(repository_id)
            run_where.append("EXISTS(SELECT 1 FROM participation_contexts pc JOIN repositories r ON r.id=pc.repository_id WHERE pc.participation_id=p.id AND pc.repository_id=? AND r.archived=0)")
            run_args.append(repository_id)
        repositories = self._page(c, "SELECT r.* FROM repositories r WHERE " + " AND ".join(repo_where) + " ORDER BY r.name COLLATE NOCASE,r.id", repo_args, limit, offset, repository_record)
        runs = self._page(c, "SELECT ar.* FROM agent_runs ar WHERE ar.ended_at IS NULL AND EXISTS(SELECT 1 FROM participations p WHERE " + " AND ".join(run_where) + ") ORDER BY ar.created_at DESC,ar.id DESC", run_args, limit, offset, self.run_record)
        return {"repositories": repositories, "runs": runs}

    @staticmethod
    def run_record(row) -> dict:
        return {"id": row["id"], "agentId": row["agent_id"], "credentialId": row["credential_id"],
                "clientRunKey": row["client_run_key"], "displayName": row["display_name"],
                "provider": row["provider"], "externalChatId": row["external_chat_id"],
                "revision": row["revision"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
                "endedAt": row["ended_at"], "endReason": row["end_reason"]}

    def _own_run(self, c, actor, identity, *, allow_ended=False):
        principal = communication_actor(c, actor)
        row = _get(c, "agent_runs", identity)
        if actor["kind"] != "agent" or row["credential_id"] != actor["id"] or row["agent_id"] != principal["id"]:
            raise AppError(403, "coordination_scope", "Only the originating credential may update this run")
        if row["ended_at"] is not None and not allow_ended:
            raise AppError(409, "run_ended", "This run has ended; start a new run")
        return row

    def create_run(self, c, actor, p) -> dict:
        principal = communication_actor(c, actor)
        if actor["kind"] != "agent":
            raise AppError(403, "coordination_scope", "Agent credentials create their own runs")
        key = text(p["clientRunKey"], "clientRunKey", required=True, limit=200)
        existing = c.execute("SELECT * FROM agent_runs WHERE credential_id=? AND client_run_key=?", (actor["id"], key)).fetchone()
        if existing:
            return self.run_record(existing)
        identity, stamp = uid(), now()
        external = p.get("externalChatId")
        if external is not None:
            external = text(external, "externalChatId", required=True, limit=500)
        else:
            # A registered identity's runs report the chat it registered with unless they name another.
            from .registration import identity_chat

            external = identity_chat(c, principal["id"])
        c.execute("INSERT INTO agent_runs(id,agent_id,credential_id,client_run_key,display_name,provider,external_chat_id,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,?,?)",
                  (identity, principal["id"], actor["id"], key, text(p["displayName"], "displayName", required=True, limit=200), text(p.get("provider", ""), "provider", limit=100), external, stamp, stamp))
        result = self.run_record(_get(c, "agent_runs", identity))
        event(c, actor, "run.create", identity, None, result)
        return result

    def runs(self, c, include_ended=False, external_chat_id=None, limit=50, offset=0) -> dict:
        conditions, args = ["1=1"], []
        if not include_ended:
            conditions.append("ended_at IS NULL")
        if external_chat_id is not None:
            conditions.append("external_chat_id=?")
            args.append(external_chat_id)
        return self._page(c, "SELECT * FROM agent_runs WHERE " + " AND ".join(conditions) + " ORDER BY created_at DESC,id DESC", args, limit, offset, self.run_record)

    def end_run(self, c, actor, identity, p) -> dict:
        row = _get(c, "agent_runs", identity) if actor["kind"] == "human" else self._own_run(c, actor, identity, allow_ended=True)
        require_revision(row, p.get("revision"))
        if row["ended_at"] is None:
            _close_run(c, actor, row, "human_closed" if actor["kind"] == "human" else "run_ended")
        return self.run_record(_get(c, "agent_runs", identity))

    @staticmethod
    def _contexts(c, identity) -> list[dict]:
        return [{"repositoryId": r["repository_id"], "repositoryName": r["name"], "archived": bool(r["archived"]),
                 "checkoutPath": r["checkout_path"], "worktreePath": r["worktree_path"], "branch": r["branch"]}
                for r in c.execute("SELECT pc.*,r.name,r.archived FROM participation_contexts pc JOIN repositories r ON r.id=pc.repository_id WHERE participation_id=? ORDER BY position", (identity,))]

    def participation_record(self, c, row) -> dict:
        stale = datetime.fromisoformat(row["last_seen_at"]) < datetime.now(UTC) - timedelta(hours=2)
        return {"id": row["id"], "runId": row["run_id"], "itemId": row["item_id"], "description": row["description"],
                "contexts": self._contexts(c, row["id"]), "revision": row["revision"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"], "lastSeenAt": row["last_seen_at"],
                "releasedAt": row["released_at"], "releaseReason": row["release_reason"], "stale": stale,
                "run": self.run_record(_get(c, "agent_runs", row["run_id"]))}

    @staticmethod
    def _validate_targets(c, actor, item_id, contexts, prior_contexts=None) -> list[dict]:
        if not isinstance(contexts, list) or len(contexts) > 40:
            raise AppError(422, "validation", "contexts must contain at most 40 repository contexts")
        if item_id is not None:
            item_id = text(item_id, "itemId", required=True, limit=200)
            if _get(c, "work_items", item_id)["archived"]:
                raise AppError(409, "archived", "Archived work cannot receive new participation")
        result = []
        for context in contexts:
            if context.get("repositoryUrl") is not None:
                if context.get("repositoryId") is not None:
                    raise AppError(422, "validation", "Give each context a repositoryId or a repositoryUrl, not both")
                # A remote reported by address is registered, or found, in this same transaction.
                repo_id = repository_ensure(c, actor, {"url": context["repositoryUrl"]})["id"]
            else:
                repo_id = text(context.get("repositoryId"), "repositoryId", required=True, limit=200)
            repository = _get(c, "repositories", repo_id)
            value = {"repositoryId": repo_id, **{field: text(context.get(field, ""), field, limit=2000) for field in ("checkoutPath", "worktreePath", "branch")}}
            # Retain historical archived contexts only when the declaration is unchanged.
            if repository["archived"] and value not in (prior_contexts or []):
                _refuse_merged(c, repository)
                raise AppError(409, "archived", "Archived repositories cannot receive new contexts")
            result.append(value)
        if item_id is None and not result:
            raise AppError(422, "validation", "Participation requires work or a repository")
        if item_id is None and not any(not _get(c, "repositories", v["repositoryId"])["archived"] for v in result):
            raise AppError(409, "archived", "Participation requires an active target")
        return result

    @staticmethod
    def _replace_contexts(c, identity, contexts) -> None:
        c.execute("DELETE FROM participation_contexts WHERE participation_id=?", (identity,))
        for position, context in enumerate(contexts):
            c.execute("INSERT INTO participation_contexts(participation_id,position,repository_id,checkout_path,worktree_path,branch) VALUES(?,?,?,?,?,?)",
                      (identity, position, context["repositoryId"], context["checkoutPath"], context["worktreePath"], context["branch"]))

    def _track_chat(self, c, actor, run_id, item_id) -> None:
        """Track a registered identity's chat on the work item its run reports, once, so the
        agent's chat appears with its work without anyone tracking it by hand. The person asked
        for this to happen without a proposal. Only the chat the identity registered with
        counts: any other chat ID a run reports stays provenance, so an agent cannot fill work
        with chats by naming them. A chat already tracked on that item, archived there
        included, is left as the person keeps it."""
        if item_id is None:
            return
        run = _get(c, "agent_runs", run_id)
        chat_id = run["external_chat_id"]
        if not chat_id:
            return
        from .registration import identity_chat

        if identity_chat(c, run["agent_id"]) != chat_id:
            return
        if c.execute("SELECT 1 FROM agent_chats WHERE owner_type='item' AND owner_id=? AND chat_id=?", (item_id, chat_id)).fetchone():
            return
        from .services import WorkService

        WorkService(self.db, None)._support_apply(c, actor, "chat.create", {
            "ownerType": "item", "ownerId": item_id, "chatId": chat_id,
            "title": run["display_name"], "provider": run["provider"]})

    def create_participation(self, c, actor, p) -> dict:
        self._own_run(c, actor, p["runId"])
        item_id = text(p["itemId"], "itemId", required=True, limit=200) if p.get("itemId") is not None else None
        contexts = self._validate_targets(c, actor, item_id, p.get("contexts", []))
        identity, stamp = uid(), now()
        c.execute("INSERT INTO participations(id,run_id,item_id,description,revision,created_at,updated_at,last_seen_at) VALUES(?,?,?,?,1,?,?,?)", (identity, p["runId"], item_id, text(p.get("description", ""), "description", limit=2000), stamp, stamp, stamp))
        self._replace_contexts(c, identity, contexts)
        result = self.participation_record(c, _get(c, "participations", identity))
        event(c, actor, "participation.create", identity, None, result)
        self._track_chat(c, actor, p["runId"], item_id)
        return result

    def change_participation(self, c, actor, identity, p, verb="update") -> dict:
        row = _get(c, "participations", identity)
        self._own_run(c, actor, row["run_id"])
        require_revision(row, p.get("revision"))
        before = self.participation_record(c, row)
        if verb == "release":
            _release(c, actor, row, "released")
            return self.participation_record(c, _get(c, "participations", identity))
        if row["released_at"] is not None and verb != "rejoin":
            raise AppError(409, "released", "Explicitly rejoin before reporting further activity")
        prior = [{k: context[k] for k in ("repositoryId", "checkoutPath", "worktreePath", "branch")} for context in before["contexts"]]
        item_id = p.get("itemId", row["item_id"])
        if item_id is not None:
            item_id = text(item_id, "itemId", required=True, limit=200)
        contexts = self._validate_targets(c, actor, item_id, p.get("contexts", prior), prior if verb != "rejoin" else None)
        stamp = now()
        description = text(p.get("description", row["description"]), "description", limit=2000)
        c.execute("UPDATE participations SET item_id=?,description=?,last_seen_at=?,updated_at=?,revision=revision+1,released_at=NULL,release_reason=NULL WHERE id=?", (item_id, description, stamp, stamp, identity))
        self._replace_contexts(c, identity, contexts)
        result = self.participation_record(c, _get(c, "participations", identity))
        event(c, actor, "participation." + verb, identity, before, result)
        self._track_chat(c, actor, row["run_id"], item_id)
        return result

    def participations(self, c, item_id=None, repository_id=None, run_id=None, include_released=False, limit=50, offset=0) -> dict:
        conditions, args = ["1=1"], []
        if not include_released:
            conditions.append("p.released_at IS NULL")
        for column, value in (("p.item_id", item_id), ("p.run_id", run_id)):
            if value is not None:
                conditions.append(column + "=?")
                args.append(value)
        if repository_id is not None:
            active = " AND r.archived=0" if not include_released else ""
            conditions.append("EXISTS(SELECT 1 FROM participation_contexts pc JOIN repositories r ON r.id=pc.repository_id WHERE pc.participation_id=p.id AND pc.repository_id=?" + active + ")")
            args.append(repository_id)
        sql = "SELECT p.* FROM participations p WHERE " + " AND ".join(conditions) + " ORDER BY p.last_seen_at DESC,p.id DESC"
        return self._page(c, sql, args, limit, offset, lambda row: self.participation_record(c, row))

    @staticmethod
    def update_record(row) -> dict:
        return {"id": row["id"], "participationId": row["participation_id"], "runId": row["run_id"],
                "agentId": row["agent_id"], "credentialId": row["credential_id"], "authorLabel": row["author_label"],
                "kind": row["kind"], "text": row["text"], "itemId": row["item_id"],
                "contexts": json.loads(row["contexts_json"]), "createdAt": row["created_at"]}

    def post_update(self, c, actor, identity, p) -> dict:
        row = _get(c, "participations", identity)
        run = self._own_run(c, actor, row["run_id"])
        if row["released_at"] is not None:
            raise AppError(409, "released", "Rejoin before posting activity")
        update_id, stamp = uid(), now()
        c.execute("INSERT INTO coordination_updates(id,participation_id,run_id,agent_id,credential_id,author_label,kind,text,item_id,contexts_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (update_id, identity, run["id"], run["agent_id"], actor["id"], run["display_name"], p["kind"], text(p["text"], "text", required=True, limit=4000), row["item_id"], json.dumps(self._contexts(c, identity)), stamp))
        c.executemany("INSERT INTO coordination_update_repositories(update_id,repository_id) VALUES(?,?)", [(update_id, repo_id) for repo_id in {context["repositoryId"] for context in self._contexts(c, identity)}])
        c.execute("UPDATE participations SET last_seen_at=?,updated_at=?,revision=revision+1 WHERE id=?", (stamp, stamp, identity))
        result = self.update_record(_get(c, "coordination_updates", update_id))
        event(c, actor, "coordination.update", update_id, None, result)
        return result

    def updates(self, c, participation_id=None, item_id=None, repository_id=None, limit=50, offset=0) -> dict:
        conditions, args = ["1=1"], []
        for column, value in (("participation_id", participation_id), ("item_id", item_id)):
            if value is not None:
                conditions.append(column + "=?")
                args.append(value)
        if repository_id is not None:
            conditions.append("EXISTS(SELECT 1 FROM coordination_update_repositories ur WHERE ur.update_id=coordination_updates.id AND ur.repository_id=?)")
            args.append(repository_id)
        return self._page(c, "SELECT * FROM coordination_updates WHERE " + " AND ".join(conditions) + " ORDER BY created_at DESC,id DESC", args, limit, offset, self.update_record)
