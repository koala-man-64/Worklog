"""Supporting records: the notes, checklist tasks, file references, attached files, links, agent
chats, GitHub references, questions and decisions kept on work items, roadmaps, steps,
proposals and repositories.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import re
import sqlite3
from pathlib import PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from . import scratchpad, search
from .chat_messages import chat_agents, expire_pending
from .common import (
    FILE_LIMIT,
    AppError,
    boolean,
    event,
    now,
    repository_labels,
    require_revision,
    rows_in,
    setting,
    text,
    today,
    uid,
    valid_date,
)
from .coordination import repository_takes_files
from .discussions import communication_actor, identity_chat
from .proposals import evidence_file
from .records import FILE_OWNERS, check_owner, get_item, support_record
from .repository_links import linked_repositories, replace_repository_associations

# A work item's tasks in the person's order (v14), ties by when each was added.
TASK_ORDER = "position,created_at,id"


def valid_http_url(raw: Any, field: str = "url") -> str:
    link = text(raw, field, required=True, limit=2048).strip()
    url = urlsplit(link)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or re.search(r"[\x00-\x20]", link):
        raise AppError(422, "validation", f"{field} must be an absolute HTTP(S) URL")
    return link


def infer_github_reference(kind: str, link: str) -> str:
    path = urlsplit(link).path.strip("/")
    pr_match = re.search(r"(?:^|/)([^/]+/[^/]+)/pull/(\d+)(?:/|$)", path)
    if pr_match:
        return f"{pr_match.group(1)}#{pr_match.group(2)}"
    commit_match = re.search(r"(?:^|/)(?:([^/]+/[^/]+)/)?commit/([0-9a-fA-F]{7,40})(?:/|$)", path)
    if commit_match:
        repo, sha = commit_match.group(1), commit_match.group(2)[:7]
        return f"{repo}@{sha}" if repo else sha
    return ""


def chat_steps(c: sqlite3.Connection, chat_ids: builtins.list[str]) -> dict[str, builtins.list[str]]:
    if not chat_ids:
        return {}
    placeholders = ",".join("?" for _ in chat_ids)
    grouped: dict[str, builtins.list[str]] = {cid: [] for cid in chat_ids}
    for r in c.execute(f"SELECT chat_id,step_id FROM chat_steps WHERE chat_id IN ({placeholders}) ORDER BY created_at,step_id", chat_ids):
        grouped[r["chat_id"]].append(r["step_id"])
    return grouped


def chat_records(c: sqlite3.Connection, rows: builtins.list[Any]) -> builtins.list[dict]:
    steps = chat_steps(c, [r["id"] for r in rows])
    repos = linked_repositories(c, "chat", [r["id"] for r in rows])
    return [{**support_record(r), "stepIds": steps[r["id"]], "repositoryIds": repos.get(r["id"], [])} for r in rows]


def chat_overview(c: sqlite3.Connection, archived: bool = False) -> builtins.list[dict]:
    """Every agent chat with the record it belongs to, the roadmaps that record sits within, and the steps it works on."""
    chats = chat_records(c, c.execute("SELECT * FROM agent_chats WHERE archived=? ORDER BY created_date DESC,created_at DESC,id", (int(archived),)).fetchall())
    owners: dict[tuple[str, str], dict] = {}
    # The roadmap each owner sits directly in: a roadmap is its own, a work item takes its step's roadmap first.
    within: dict[tuple[str, str], str | None] = {}
    roadmaps = {r["id"]: r for r in c.execute("SELECT id,title,archived,parent_id FROM roadmaps")} if chats else {}
    for ch in chats:
        r = roadmaps.get(ch["ownerId"]) if ch["ownerType"] == "roadmap" else None
        if r is not None:
            owners[("roadmap", r["id"])] = {"type": "roadmap", "id": r["id"], "title": r["title"], "archived": bool(r["archived"])}
            within[("roadmap", r["id"])] = r["id"]
    ids = list({ch["ownerId"] for ch in chats if ch["ownerType"] == "item"})
    if ids:
        for r in c.execute(
            f"SELECT id,title,archived,COALESCE((SELECT roadmap_id FROM steps WHERE steps.id=work_items.step_id),roadmap_id) AS roadmap FROM work_items WHERE id IN ({','.join('?' for _ in ids)})",
            ids,
        ):
            owners[("item", r["id"])] = {"type": "item", "id": r["id"], "title": r["title"], "archived": bool(r["archived"])}
            within[("item", r["id"])] = r["roadmap"]

    def path(roadmap_id: str | None) -> builtins.list[dict]:
        """The roadmaps from the top level down to this one; a broken parent chain stops where it breaks."""
        chain: builtins.list[dict] = []
        while roadmap_id is not None and roadmap_id in roadmaps and all(r["id"] != roadmap_id for r in chain):
            row = roadmaps[roadmap_id]
            chain.append({"id": row["id"], "title": row["title"], "archived": bool(row["archived"])})
            roadmap_id = row["parent_id"]
        return chain[::-1]
    steps: dict[str, builtins.list[dict]] = {ch["id"]: [] for ch in chats}
    if chats:
        for r in c.execute(
            f"SELECT cs.chat_id,s.id,s.title,s.status,s.retired FROM chat_steps cs JOIN steps s ON s.id=cs.step_id WHERE cs.chat_id IN ({','.join('?' for _ in chats)}) ORDER BY cs.created_at,s.position,s.id",
            [ch["id"] for ch in chats],
        ):
            steps[r["chat_id"]].append({"id": r["id"], "title": r["title"], "status": r["status"], "retired": bool(r["retired"])})
    # A tracked chat names only the agents its own chat ID reaches, compared as delivery compares it.
    agents = chat_agents(c, [ch["chatId"] for ch in chats], exact=True)
    return [{**ch, "owner": owners.get((ch["ownerType"], ch["ownerId"]), {"type": ch["ownerType"], "id": ch["ownerId"], "title": "", "archived": False}),
             "roadmaps": path(within.get((ch["ownerType"], ch["ownerId"]))), "steps": steps[ch["id"]],
             "agents": agents.get(ch["chatId"], [])} for ch in chats]


def session_ties(c: sqlite3.Connection, sessions: builtins.list[dict]) -> builtins.list[dict]:
    """Running sessions with the chats that track them, the agents tied to them, and the chat ID
    to track them by.

    A session goes by its own ID and, when the Claude desktop app runs it, by the desktop
    session's local_ ID too; either ties it, compared without case. A session is tracked by the ID
    its agents registered with, were confirmed under or report, closest tie first, since delivery
    matches that ID exactly; failing those, by the ID a record already tracks it under, and
    otherwise by its own."""
    ids = {s["sessionId"]: [i for i in (s["sessionId"], s["hostSessionId"]) if i] for s in sessions}
    keys = {i for found in ids.values() for i in found}
    agents = chat_agents(c, keys, exact=False)
    chats = rows_in(c, "SELECT * FROM agent_chats WHERE lower(trim(chat_id)) IN ({})", keys)
    chats.sort(key=lambda ch: (ch["archived"], ch["created_at"], ch["id"]))
    owners = {("item", r["id"]): r for r in rows_in(c, "SELECT id,title FROM work_items WHERE id IN ({})",
                                                     [ch["owner_id"] for ch in chats if ch["owner_type"] == "item"])}
    owners.update((("roadmap", r["id"]), r) for r in rows_in(c, "SELECT id,title FROM roadmaps WHERE id IN ({})",
                                                             [ch["owner_id"] for ch in chats if ch["owner_type"] == "roadmap"]))
    order = {"registered": 0, "confirmed": 1, "run": 2}
    result = []
    for s in sessions:
        own = ids[s["sessionId"]]
        tied: dict[str, dict] = {}
        for agent in (a for key in own for a in agents.get(key, [])):
            if agent["id"] not in tied or order[agent["binding"]] < order[tied[agent["id"]]["binding"]]:
                tied[agent["id"]] = agent
        listed = sorted(tied.values(), key=lambda a: (order[a["binding"]], a["label"].casefold(), a["id"]))
        tracked = [ch for ch in chats if ch["chat_id"].strip().lower() in own]
        references = [a["chatId"] for a in listed] + [ch["chat_id"].strip() for ch in tracked]
        result.append({**s, "chatId": references[0] if references else s["sessionId"], "agents": listed,
                       "tracked": [{"id": ch["id"], "title": ch["title"], "archived": bool(ch["archived"]),
                                    "ownerType": ch["owner_type"], "ownerId": ch["owner_id"],
                                    "ownerTitle": owners[(ch["owner_type"], ch["owner_id"])]["title"]
                                    if (ch["owner_type"], ch["owner_id"]) in owners else ""} for ch in tracked]})
    return result


def for_owner(c: sqlite3.Connection, owner_type: str, owner_id: str) -> dict:
    result = {"notes": [support_record(r) for r in c.execute("SELECT * FROM notes WHERE owner_type=? AND owner_id=? ORDER BY created_at", (owner_type, owner_id))],
              "attachments": [support_record(r) for r in c.execute("SELECT id,owner_type,owner_id,name,mime,size,sha256,archived,revision,created_at FROM attachments WHERE owner_type=? AND owner_id=? ORDER BY created_at", (owner_type, owner_id))]}
    if owner_type in {"item", "roadmap"}:
        result["links"] = [support_record(r) for r in c.execute("SELECT * FROM web_links WHERE owner_type=? AND owner_id=? ORDER BY created_at,id", (owner_type, owner_id))]
        result["chats"] = chat_records(c, c.execute("SELECT * FROM agent_chats WHERE owner_type=? AND owner_id=? ORDER BY created_date DESC,created_at DESC,id", (owner_type, owner_id)).fetchall())
        result["github"] = [
            support_record(r)
            for r in c.execute(
                """SELECT g.*, ac.title AS chat_title
                   FROM github_refs g
                   LEFT JOIN agent_chats ac ON ac.id = g.chat_id
                   WHERE g.owner_type=? AND g.owner_id=?
                   ORDER BY g.created_at, g.id""",
                (owner_type, owner_id),
            )
        ]
    if owner_type == "roadmap":
        result["questions"] = [support_record(r) for r in c.execute("SELECT * FROM roadmap_questions WHERE roadmap_id=? ORDER BY created_at,id", (owner_id,))]
        result["decisions"] = [support_record(r) for r in c.execute("SELECT * FROM roadmap_decisions WHERE roadmap_id=? ORDER BY decided_date DESC,created_at DESC,id", (owner_id,))]
    if owner_type == "item":
        result["tasks"] = [support_record(r) for r in c.execute(f"SELECT * FROM tasks WHERE item_id=? ORDER BY {TASK_ORDER}", (owner_id,))]
        result["references"] = [support_record(r) for r in c.execute("SELECT * FROM file_references WHERE item_id=? ORDER BY created_at", (owner_id,))]
    return result


def apply(c: sqlite3.Connection, actor: dict, action: str, p: dict) -> dict:
    kind, verb = action.split(".", 1)
    table = {
        "note": "notes",
        "task": "tasks",
        "reference": "file_references",
        "attachment": "attachments",
        "link": "web_links",
        "chat": "agent_chats",
        "github": "github_refs",
        "question": "roadmap_questions",
        "decision": "roadmap_decisions",
    }[kind]
    creating = verb == "create"
    row = None if creating else c.execute(f"SELECT * FROM {table} WHERE id=?", (p.get("id", ""),)).fetchone()
    if not creating:
        require_revision(row, p.get("revision"))
    before = support_record(row) if row else None
    if kind == "chat" and before is not None:
        before["repositoryIds"] = linked_repositories(c, "chat", [before["id"]]).get(before["id"], [])
        before["stepIds"] = chat_steps(c, [before["id"]])[before["id"]]
    val: dict[str, Any]
    if verb in {"archive", "recover"}:
        assert row is not None
        if kind == "note" and verb == "recover" and row["owner_type"] == "item":
            pad = scratchpad.find(c, row["owner_id"])
            if pad is not None and pad[0] != row["id"]:
                raise AppError(409, "conflict", "This work item already has a scratchpad; copy what you need from this note into it")
        c.execute(f"UPDATE {table} SET archived=?,revision=revision+1 WHERE id=?", (int(verb == "archive"), row["id"]))
        if kind == "chat" and verb == "archive":
            # Recovering the chat later never revives these deliveries.
            expire_pending(c, "chat_archived", chat_id=row["id"])
    elif kind == "note" and verb in {"create", "update"}:
        val = before.copy() if before else {"id": uid(), "ownerType": p.get("ownerType"), "ownerId": p.get("ownerId"), "title": "", "text": "", "createdAt": now(), "revision": 0}
        check_owner(c, val["ownerType"], val["ownerId"])
        if val["ownerType"] == "proposal":
            raise AppError(422, "validation", "Use proposal rationale for proposed notes")
        for k in ("title", "text"):
            if k in p:
                val[k] = text(p[k], k, limit=2000 if k == "title" else 1_000_000)
        # A scratchpad may be emptied; other notes need something in them.
        if val["ownerType"] != "item" and not val["title"].strip() and not val["text"].strip():
            raise AppError(422, "validation", "A note needs a title or text")
        # A work item has one scratchpad, its only active note (v8).
        if creating and val["ownerType"] == "item" and scratchpad.find(c, val["ownerId"]):
            raise AppError(409, "conflict", "This work item already has a scratchpad; edit it instead")
        c.execute("INSERT INTO notes(id,owner_type,owner_id,title,text,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,text=excluded.text,updated_at=excluded.updated_at,revision=excluded.revision",
                  (val["id"], val["ownerType"], val["ownerId"], val["title"], val["text"], 0, val["revision"] + 1, val["createdAt"], now()))
        p = {**p, "id": val["id"]}
    elif kind == "link" and verb in {"create", "update"}:
        val = before.copy() if before else {"id": uid(), "ownerType": p.get("ownerType"), "ownerId": p.get("ownerId"), "url": "", "title": "", "description": "", "createdAt": now(), "revision": 0}
        if val["ownerType"] not in {"item", "roadmap"}:
            raise AppError(422, "validation", "Links must belong to a work item or roadmap")
        check_owner(c, val["ownerType"], val["ownerId"])
        for k in ("url", "title", "description"):
            if k in p and p[k] is not None:
                val[k] = p[k]
        val["url"] = valid_http_url(val["url"], "url")
        val["title"] = text(val["title"], "title", limit=1000).strip()
        val["description"] = text(val["description"], "description", limit=100_000).strip()
        c.execute(
            "INSERT INTO web_links(id,owner_type,owner_id,url,title,description,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET url=excluded.url,title=excluded.title,description=excluded.description,revision=excluded.revision,updated_at=excluded.updated_at",
            (val["id"], val["ownerType"], val["ownerId"], val["url"], val["title"], val["description"], 0, val["revision"] + 1, val["createdAt"], now()),
        )
        p = {**p, "id": val["id"]}
    elif kind == "chat" and verb in {"create", "update"}:
        default_date = today(setting(c, "timezone", "America/Chicago"))
        val = before.copy() if before else {"id": uid(), "ownerType": p.get("ownerType"), "ownerId": p.get("ownerId"), "chatId": "", "title": "", "createdDate": default_date,
                                            "provider": "", "model": "", "repos": [], "stepIds": [], "createdAt": now(), "revision": 0}
        if val["ownerType"] not in {"item", "roadmap"}:
            raise AppError(422, "validation", "Agent chats must belong to a work item or roadmap")
        check_owner(c, val["ownerType"], val["ownerId"])
        for k in ("chatId", "title", "createdDate", "provider", "model", "repos", "stepIds"):
            if k in p and p[k] is not None:
                val[k] = p[k]
        val["chatId"] = text(val["chatId"], "chatId", required=True, limit=500)
        val["title"] = text(val["title"], "title", required=True, limit=1000)
        raw_date = str(val.get("createdDate") or "").strip()
        val["createdDate"] = valid_date(raw_date) if raw_date else default_date
        val["provider"] = text(val["provider"], "provider", limit=200).strip()
        val["model"] = text(val["model"], "model", limit=200).strip()
        val["repos"] = repository_labels(val["repos"])
        previous = before["stepIds"] if before else []
        assigned = _chat_step_ids(c, val, previous)
        c.execute(
            "INSERT INTO agent_chats(id,owner_type,owner_id,chat_id,title,created_date,archived,revision,created_at,updated_at,provider,model,repos) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET chat_id=excluded.chat_id,title=excluded.title,created_date=excluded.created_date,provider=excluded.provider,model=excluded.model,repos=excluded.repos,revision=excluded.revision,updated_at=excluded.updated_at",
            (val["id"], val["ownerType"], val["ownerId"], val["chatId"], val["title"], val["createdDate"], 0, val["revision"] + 1, val["createdAt"], now(),
             val["provider"], val["model"], json.dumps(val["repos"])),
        )
        _assign_steps(c, actor, val, previous, assigned)
        if before is not None and before["chatId"] != val["chatId"]:
            # Runs that reported the old ID are no longer tied to this chat.
            expire_pending(c, "chat_changed", chat_id=val["id"])
        p = {**p, "id": val["id"]}
    elif kind == "github" and verb in {"create", "update"}:
        val = before.copy() if before else {"id": uid(), "ownerType": p.get("ownerType"), "ownerId": p.get("ownerId"), "kind": "pr", "url": "", "reference": "", "title": "", "description": "", "chatId": None, "createdAt": now(), "revision": 0}
        if val["ownerType"] not in {"item", "roadmap"}:
            raise AppError(422, "validation", "GitHub references must belong to a work item or roadmap")
        check_owner(c, val["ownerType"], val["ownerId"])
        for k in ("kind", "url", "reference", "title", "description", "chatId"):
            if k in p and (k == "chatId" or p[k] is not None):
                val[k] = p[k]
        if val["kind"] not in {"commit", "pr"}:
            raise AppError(422, "validation", "GitHub reference kind must be commit or pr")
        val["url"] = valid_http_url(val["url"], "url")
        val["reference"] = text(val["reference"], "reference", limit=300).strip() or infer_github_reference(val["kind"], val["url"])
        val["title"] = text(val["title"], "title", limit=1000).strip()
        val["description"] = text(val["description"], "description", limit=100_000).strip()
        if val["chatId"]:
            if not isinstance(val["chatId"], str):
                raise AppError(422, "validation", "Invalid chatId")
            val["chatId"] = _github_chat_id(c, val["ownerType"], val["ownerId"], val["chatId"])
        else:
            val["chatId"] = None
        c.execute(
            "INSERT INTO github_refs(id,owner_type,owner_id,kind,url,reference,title,description,chat_id,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,url=excluded.url,reference=excluded.reference,title=excluded.title,description=excluded.description,chat_id=excluded.chat_id,revision=excluded.revision,updated_at=excluded.updated_at",
            (val["id"], val["ownerType"], val["ownerId"], val["kind"], val["url"], val["reference"], val["title"], val["description"], val["chatId"], 0, val["revision"] + 1, val["createdAt"], now()),
        )
        p = {**p, "id": val["id"]}
    elif kind == "question" and verb in {"create", "update"}:
        val = before.copy() if before else {"id": uid(), "roadmapId": p.get("roadmapId"), "title": "", "details": "", "answer": "", "status": "open", "createdAt": now(), "revision": 0}
        check_owner(c, "roadmap", val["roadmapId"])
        for k in ("title", "details", "answer", "status"):
            if k in p and p[k] is not None:
                val[k] = p[k]
        val["title"] = text(val["title"], "title", required=True, limit=1000)
        val["details"] = text(val["details"], "details", limit=100_000)
        val["answer"] = text(val["answer"], "answer", limit=100_000)
        if val["status"] not in {"open", "answered"}:
            raise AppError(422, "validation", "Question status must be open or answered", {"status": "invalid"})
        # Reopening keeps the earlier answer; only an answered question needs one.
        if val["status"] == "answered" and not val["answer"].strip():
            raise AppError(422, "validation", "Write an answer before marking the question answered", {"answer": "required"})
        c.execute(
            "INSERT INTO roadmap_questions(id,roadmap_id,title,details,answer,status,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,details=excluded.details,answer=excluded.answer,status=excluded.status,revision=excluded.revision,updated_at=excluded.updated_at",
            (val["id"], val["roadmapId"], val["title"], val["details"], val["answer"], val["status"], 0, val["revision"] + 1, val["createdAt"], now()),
        )
        p = {**p, "id": val["id"]}
    elif kind == "decision" and verb in {"create", "update"}:
        default_date = today(setting(c, "timezone", "America/Chicago"))
        val = before.copy() if before else {"id": uid(), "roadmapId": p.get("roadmapId"), "title": "", "rationale": "", "decidedDate": default_date, "createdAt": now(), "revision": 0}
        check_owner(c, "roadmap", val["roadmapId"])
        for k in ("title", "rationale", "decidedDate"):
            if k in p and p[k] is not None:
                val[k] = p[k]
        val["title"] = text(val["title"], "title", required=True, limit=1000)
        val["rationale"] = text(val["rationale"], "rationale", limit=100_000)
        raw_date = str(val.get("decidedDate") or "").strip()
        if raw_date:
            val["decidedDate"] = valid_date(raw_date)
        elif before is None:
            val["decidedDate"] = default_date
        else:
            # A blank date must not quietly redate a decision recorded earlier.
            raise AppError(422, "validation", "Give the date the decision was made", {"decidedDate": "required"})
        c.execute(
            "INSERT INTO roadmap_decisions(id,roadmap_id,title,rationale,decided_date,archived,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,rationale=excluded.rationale,decided_date=excluded.decided_date,revision=excluded.revision,updated_at=excluded.updated_at",
            (val["id"], val["roadmapId"], val["title"], val["rationale"], val["decidedDate"], 0, val["revision"] + 1, val["createdAt"], now()),
        )
        p = {**p, "id": val["id"]}
    elif kind == "task" and verb in {"create", "update"}:
        # A new task was last updated when it was added, to the microsecond.
        stamp = now()
        val = before.copy() if before else {"id": uid(), "itemId": p.get("itemId"), "text": "", "done": False, "createdAt": stamp, "revision": 0}
        get_item(c, val["itemId"])
        if "text" in p:
            val["text"] = text(p["text"], "text", required=True)
        if "done" in p:
            val["done"] = boolean(p["done"], "done")
        if not val["text"]:
            raise AppError(422, "validation", "Checklist text is required")
        # A new task goes last. An edit keeps the task's place: the update below leaves position
        # as it is, and only move_task changes it.
        position = 0 if before else c.execute("SELECT coalesce(max(position),0)+1 FROM tasks WHERE item_id=?", (val["itemId"],)).fetchone()[0]
        c.execute("INSERT INTO tasks(id,item_id,text,done,archived,revision,created_at,updated_at,position) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text,done=excluded.done,revision=excluded.revision,updated_at=excluded.updated_at",
                  (val["id"], val["itemId"], val["text"], int(val["done"]), 0, val["revision"] + 1, val["createdAt"], stamp, position))
        p = {**p, "id": val["id"]}
    elif kind == "attachment" and creating:
        # Only an applied proposal attaches a file this way: a copy of evidence uploaded to it,
        # so the proposal keeps the file it was reviewed with. The person uploads directly.
        owner_type, owner_id = p.get("ownerType"), p.get("ownerId")
        if not (isinstance(owner_type, str) and owner_type in FILE_OWNERS.keys() - {"proposal"} and isinstance(owner_id, str)):
            raise AppError(422, "validation", "Files belong to a work item, roadmap, step or repository")
        source = evidence_file(c, p.get("proposalId"), p.get("attachmentId"))
        check_owner(c, owner_type, owner_id, files=True)
        if owner_type == "repository":
            repository_takes_files(c, owner_id)
        identity = uid()
        c.execute("INSERT INTO attachments(id,owner_type,owner_id,name,mime,size,sha256,content,archived,revision,created_at) "
                  "SELECT ?,?,?,name,mime,size,sha256,content,0,1,? FROM attachments WHERE id=?",
                  (identity, owner_type, owner_id, now(), source["id"]))
        p = {**p, "id": identity}
    elif kind == "reference" and creating:
        get_item(c, p.get("itemId", ""))
        identity = uid()
        value = text(p.get("path"), "path", required=True, limit=32768)
        if any(ord(ch) < 32 for ch in value):
            raise AppError(422, "validation", "Path contains control characters")
        c.execute("INSERT INTO file_references(id,item_id,path,archived,revision,created_at) VALUES(?,?,?,?,?,?)", (identity, p["itemId"], value, 0, 1, now()))
        p = {**p, "id": identity}
    else:
        raise AppError(422, "operation", "Unsupported supporting-record operation")
    if kind == "github":
        after = support_record(
            c.execute(
                "SELECT g.*, ac.title AS chat_title FROM github_refs g LEFT JOIN agent_chats ac ON ac.id=g.chat_id WHERE g.id=?",
                (p["id"],),
            ).fetchone()
        )
    elif kind == "chat":
        if "repositoryIds" in p:
            replace_repository_associations(c, "chat", p["id"], p["repositoryIds"])
        after = chat_records(c, [c.execute("SELECT * FROM agent_chats WHERE id=?", (p["id"],)).fetchone()])[0]
    else:
        after = support_record(c.execute(f"SELECT * FROM {table} WHERE id=?", (p["id"],)).fetchone())
    if kind in {"note", "attachment"} and after.get("ownerType") != "proposal":
        search.put(c, after["id"], kind, after.get("title", after.get("name", "")), after.get("text", ""), after["ownerId"], after["archived"])
    elif kind == "link":
        search.put(c, after["id"], "link", after.get("title") or after["url"], (after["url"] + "\n" + after.get("description", "")).strip(), after["ownerId"], after["archived"])
    elif kind == "chat":
        search.put(c, after["id"], "chat", after["title"], " ".join([after["chatId"], after["createdDate"], after["provider"], after["model"], *after["repos"]]).strip(), after["ownerId"], after["archived"])
    elif kind == "github":
        search.put(c, after["id"], "github", after.get("title") or after.get("reference") or after["url"], f"{after['kind']} {after.get('reference', '')} {after['url']}\n{after.get('description', '')}".strip(), after["ownerId"], after["archived"])
    elif kind == "question":
        search.put(c, after["id"], "question", after["title"], (after["details"] + "\n" + after["answer"]).strip(), after["roadmapId"], after["archived"])
    elif kind == "decision":
        search.put(c, after["id"], "decision", after["title"], after["rationale"], after["roadmapId"], after["archived"])
    elif kind == "attachment":
        # Proposal evidence lands here: unapproved files never appear in global search.
        search.remove(c, after["id"])
    event(c, actor, action, p["id"], before, after)
    return after


def move_task(c: sqlite3.Connection, actor: dict, identity: str, p: dict) -> dict:
    """Move an active task to a place in its work item's list, 1 first; a place past the last moves it last.

    As with a work item's stack rank, the move is checked against the task's own revision, so a
    stale view can only move a task it shows as it is now. Moving changes no task's revision or
    last-updated time, so an edit open on any of them still saves. Archived tasks keep their
    places among the rest and come back there when recovered.
    """
    wanted = p.get("position")
    if not isinstance(wanted, int) or isinstance(wanted, bool) or wanted < 1:
        raise AppError(422, "validation", "position must be a whole number from 1")
    row = c.execute("SELECT * FROM tasks WHERE id=?", (identity,)).fetchone()
    require_revision(row, p.get("revision"))
    if row["archived"]:
        raise AppError(409, "conflict", "An archived task has no place in the list; recover it to move it")
    order = [r[0] for r in c.execute(f"SELECT id FROM tasks WHERE item_id=? ORDER BY {TASK_ORDER}", (row["item_id"],))]
    listed = [r[0] for r in c.execute(f"SELECT id FROM tasks WHERE item_id=? AND archived=0 ORDER BY {TASK_ORDER}", (row["item_id"],))]
    before, after = listed.index(identity) + 1, min(wanted, len(listed))
    if after != before:
        others = [i for i in listed if i != identity]
        rest = [i for i in order if i != identity]
        # Taken out, the task goes back in just before the one that then holds its new place, or
        # just after the last. Every task then takes its own position, written only where it moved.
        rest.insert(rest.index(others[after - 1]) if after <= len(others) else rest.index(others[-1]) + 1, identity)
        held = dict(c.execute("SELECT id,position FROM tasks WHERE item_id=?", (row["item_id"],)).fetchall())
        c.executemany("UPDATE tasks SET position=? WHERE id=?",
                      [(place, task) for place, task in enumerate(rest, 1) if held[task] != place])
        event(c, actor, "task.move", identity, {"position": before}, {"position": after})
    return {"id": identity, "position": after}


def _github_chat_id(c: sqlite3.Connection, owner_type: str, owner_id: str, chat_ref: str) -> str:
    """Resolve a GitHub reference's chat by record ID or external chatId on this record or its linked roadmap/items."""
    ref = chat_ref.strip()
    if not ref:
        raise AppError(422, "validation", "Agent chat does not belong to this record")
    row = c.execute(
        """SELECT id FROM agent_chats
           WHERE owner_type=? AND owner_id=? AND (id=? OR chat_id=?)
           ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, archived ASC, created_at DESC, id DESC""",
        (owner_type, owner_id, ref, ref, ref),
    ).fetchone()
    if row is not None:
        return row["id"]
    if owner_type == "item":
        row = c.execute(
            """SELECT ac.id FROM agent_chats ac
               JOIN work_items w ON w.id=?
               LEFT JOIN steps s ON s.id=w.step_id
               WHERE ac.owner_type='roadmap'
                 AND ac.owner_id=COALESCE(s.roadmap_id, w.roadmap_id)
                 AND (ac.id=? OR ac.chat_id=?)
               ORDER BY CASE WHEN ac.id=? THEN 0 ELSE 1 END, ac.archived ASC, ac.created_at DESC, ac.id DESC""",
            (owner_id, ref, ref, ref),
        ).fetchone()
    elif owner_type == "roadmap":
        row = c.execute(
            """SELECT ac.id FROM agent_chats ac
               JOIN work_items w ON ac.owner_type='item' AND ac.owner_id=w.id
               LEFT JOIN steps s ON s.id=w.step_id
               WHERE COALESCE(s.roadmap_id, w.roadmap_id)=?
                 AND (ac.id=? OR ac.chat_id=?)
               ORDER BY CASE WHEN ac.id=? THEN 0 ELSE 1 END, ac.archived ASC, ac.created_at DESC, ac.id DESC""",
            (owner_id, ref, ref, ref),
        ).fetchone()
    if row is not None:
        return row["id"]
    raise AppError(422, "validation", "Agent chat does not belong to this record")


def _chat_step_ids(c: sqlite3.Connection, chat: dict, kept: builtins.list[str]) -> builtins.list[str]:
    """Validate a chat's complete step assignment; a retired step may stay but not be added."""
    step_ids = chat["stepIds"]
    if not isinstance(step_ids, list) or len(step_ids) > 500 or not all(isinstance(s, str) for s in step_ids):
        raise AppError(422, "validation", "stepIds must be a list of at most 500 step ids")
    step_ids = list(dict.fromkeys(step_ids))
    if step_ids and chat["ownerType"] != "roadmap":
        raise AppError(422, "validation", "Only a roadmap's agent chats can be assigned to steps")
    for step_id in step_ids:
        row = c.execute("SELECT roadmap_id,retired FROM steps WHERE id=?", (step_id,)).fetchone()
        if row is None or row["roadmap_id"] != chat["ownerId"]:
            raise AppError(422, "validation", "Assigned steps must belong to the chat's roadmap")
        if row["retired"] and step_id not in kept:
            raise AppError(409, "conflict", "Retired steps cannot take on agent chats")
    return step_ids


def _assign_steps(c: sqlite3.Connection, actor: dict, chat: dict,
                  previous: builtins.list[str], assigned: builtins.list[str]) -> None:
    # Each step's own history records which chats took it on or let it go.
    summary = {k: chat[k] for k in ("id", "chatId", "title", "provider", "model")}
    for step_id in previous:
        if step_id not in assigned:
            c.execute("DELETE FROM chat_steps WHERE chat_id=? AND step_id=?", (chat["id"], step_id))
            event(c, actor, "chat.unassign", step_id, summary, None)
    stamp = now()
    for step_id in assigned:
        if step_id not in previous:
            c.execute("INSERT INTO chat_steps(chat_id,step_id,created_at) VALUES(?,?,?)", (chat["id"], step_id, stamp))
            event(c, actor, "chat.assign", step_id, None, summary)


# The work an agent tracks its own chat on, by the table each lives in.
CHAT_OWNERS = {"item": ("work_items", "work item"), "roadmap": ("roadmaps", "roadmap")}


def track_own_chat(c: sqlite3.Connection, actor: dict, p: dict) -> dict:
    """An agent tracks the chat it registered with on a work item or roadmap, and on steps of that
    roadmap, without a proposal: the person asked for agents to do this themselves.

    Only the identity's registered chat counts, as when its reported work tracks it
    (coordination._track_chat), so an agent cannot fill work with chats by naming them. Where that
    chat is tracked already, it takes on the steps named and nothing else changes. What the person
    decided stays: a chat they archived there stays archived, and a step it was taken off, by them
    or by a proposal they let apply, is not assigned again. Returns the chat with `created`."""
    if actor["kind"] != "agent":
        raise AppError(403, "forbidden", "Agents track their own chats here; add a chat under Agent chats")
    agent = communication_actor(c, actor)
    chat_id = identity_chat(c, agent["id"])
    if chat_id is None:
        raise AppError(403, "agent_scope", "Only an identity registered with its chat tracks it this way; register one "
                       "for your chat, or propose chat.create")
    table, kind = CHAT_OWNERS[p["ownerType"]]
    owner_id = text(p["ownerId"], "ownerId", required=True, limit=200)
    owner = c.execute(f"SELECT archived FROM {table} WHERE id=?", (owner_id,)).fetchone()
    if owner is None:
        raise AppError(404, "not_found", "Owner does not exist")
    if owner["archived"]:
        raise AppError(409, "archived", f"This {kind} is archived and takes no new chats")
    step_ids = list(dict.fromkeys(p.get("stepIds") or []))
    # An active record before an archived one: the person may have tracked the chat there twice.
    row = c.execute("SELECT * FROM agent_chats WHERE owner_type=? AND owner_id=? AND chat_id=? ORDER BY archived,created_at,id",
                    (p["ownerType"], owner_id, chat_id)).fetchone()
    if row is None:
        title = agent["label"] if p.get("title") is None else p["title"]
        tracked = apply(c, actor, "chat.create", {"ownerType": p["ownerType"], "ownerId": owner_id, "chatId": chat_id,
                                                  "title": title, "provider": p.get("provider", ""),
                                                  "model": p.get("model", ""), "stepIds": step_ids})
        return {**tracked, "created": True}
    if row["archived"]:
        raise AppError(409, "archived", f"The person archived your chat on this {kind}; it stays archived unless they recover it",
                       {"id": row["id"]})
    current = chat_steps(c, [row["id"]])[row["id"]]
    added = [step_id for step_id in step_ids if step_id not in current]
    # Only an update to the chat takes it off a step, the person's own or a proposal they let apply
    # (_assign_steps), and each records chat.unassign in the step's history.
    taken_off = [step_id for step_id in added if c.execute(
        "SELECT 1 FROM events WHERE entity_id=? AND kind='chat.unassign' AND json_extract(before_json,'$.id')=?",
        (step_id, row["id"])).fetchone()]
    if taken_off:
        raise AppError(403, "forbidden", "Your chat was taken off that step; ask the person to assign it again",
                       {"stepIds": taken_off})
    if not added:
        return {**chat_records(c, [row])[0], "created": False}
    tracked = apply(c, actor, "chat.update", {"id": row["id"], "revision": row["revision"], "stepIds": [*current, *added]})
    return {**tracked, "created": False}


def upload(c: sqlite3.Connection, actor: dict, owner_type: str, owner_id: str,
           name: str, mime: str, content: bytes, proposals: Any) -> dict:
    check_owner(c, owner_type, owner_id, files=True)
    if owner_type == "proposal":
        proposals.check_upload(c, owner_id, actor, len(content))
    if owner_type == "repository":
        repository_takes_files(c, owner_id)
    if not content or len(content) > FILE_LIMIT:
        raise AppError(413 if content else 422, "file_size", "Attach a nonempty file up to 25,000,000 bytes")
    name = PureWindowsPath(name.replace("/", "\\")).name
    name = "".join(ch for ch in name if ord(ch) >= 32 and ord(ch) != 127)[:200]
    if name in {"", ".", ".."}:
        raise AppError(422, "validation", "A filename is required")
    mime = mime.split(";", 1)[0].lower().strip()
    if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", mime):
        mime = "application/octet-stream"
    identity = uid()
    c.execute("INSERT INTO attachments(id,owner_type,owner_id,name,mime,size,sha256,content,archived,revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (identity, owner_type, owner_id, name, mime, len(content), hashlib.sha256(content).hexdigest(), content, 0, 1, now()))
    after = support_record(c.execute("SELECT * FROM attachments WHERE id=?", (identity,)).fetchone())
    # Unapproved evidence must never appear in canonical global search.
    if owner_type != "proposal":
        search.put(c, identity, "attachment", name, "", owner_id, False)
    event(c, actor, "attachment.create", identity, None, after)
    return after
