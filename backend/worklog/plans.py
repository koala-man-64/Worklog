"""Versioned roadmap trees and deliberately non-merging HTML imports."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import nullcontext
from pathlib import PurePath

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from .common import AppError, event, now, read_events, require_revision, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS roadmaps (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, overview TEXT NOT NULL,
 revision INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
 id TEXT PRIMARY KEY, roadmap_id TEXT NOT NULL REFERENCES roadmaps(id),
 title TEXT NOT NULL, description TEXT NOT NULL, parent_id TEXT,
 position INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 revision INTEGER NOT NULL, retired INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS steps_roadmap ON steps(roadmap_id);
CREATE TABLE IF NOT EXISTS roadmap_versions (
 roadmap_id TEXT NOT NULL REFERENCES roadmaps(id), number INTEGER NOT NULL,
 created_at TEXT NOT NULL, note TEXT NOT NULL, snapshot TEXT NOT NULL,
 PRIMARY KEY(roadmap_id,number)
);
CREATE TABLE IF NOT EXISTS plan_imports (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, original BLOB NOT NULL, sha256 TEXT NOT NULL,
 preview TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL,
 roadmap_id TEXT REFERENCES roadmaps(id)
);
"""

STATUSES = {"open", "doing", "blocked", "done"}


def _text(value, field, default=None, maximum=1_000_000):
    if value is None and default is not None:
        value = default
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise AppError(422, "validation", f"Invalid {field}")
    return value


def _title(value):
    value = _text(value, "title", maximum=1000).strip()
    if not value:
        raise AppError(422, "validation", "Title is required")
    return value


def _row(conn, identifier):
    row = conn.execute("SELECT * FROM roadmaps WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Roadmap not found")
    return row


def _roadmap(row):
    return {"id": row["id"], "title": row["title"], "overview": row["overview"],
            "revision": row["revision"], "archived": bool(row["archived"]),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def _step(row):
    return {"id": row["id"], "title": row["title"], "description": row["description"],
            "parentId": row["parent_id"], "position": row["position"],
            "status": row["status"], "revision": row["revision"],
            "retired": bool(row["retired"])}


def _steps(conn, identifier, retired=False):
    rows = conn.execute("SELECT * FROM steps WHERE roadmap_id=? ORDER BY position,id",
                        (identifier,)).fetchall()
    steps = [_step(row) for row in rows if retired or not row["retired"]]
    active = {s["id"]: s for s in steps if not s["retired"]}
    children: dict[str | None, list[dict]] = {}
    for step in active.values():
        children.setdefault(step["parentId"], []).append(step)

    def derive(step):
        descendants = children.get(step["id"], [])
        if descendants:
            states = [derive(child) for child in descendants]
            step["status"] = ("done" if all(s == "done" for s in states) else
                              "blocked" if "blocked" in states else
                              "doing" if any(s in {"doing", "done"} for s in states) else "open")
        return step["status"]

    for root in children.get(None, []):
        derive(root)
    return steps


_URL_UNSAFE = re.compile(r"[()<>\s]")


def _normalize_url(value: str) -> str:
    """Make a kept URL safe to embed in Markdown link syntax.

    Protocol-relative URLs become https; parentheses, angle brackets and whitespace are
    percent-encoded so a crafted value cannot close the link and open another one.
    """
    value = value.strip()
    if value.startswith("//"):
        value = "https:" + value
    return _URL_UNSAFE.sub(lambda m: "".join(f"%{b:02X}" for b in m.group().encode()), value)


class PlanService:
    def __init__(self, db):
        self.db = db

    def list(self, archived=False):
        with self.db.read() as conn:
            items = [_roadmap(row) for row in conn.execute(
                "SELECT * FROM roadmaps WHERE archived=? ORDER BY created_at,id", (int(archived),))]
            return {"items": items, "total": len(items)}

    def _detail(self, conn, identifier):
        result = _roadmap(_row(conn, identifier))
        result["steps"] = _steps(conn, identifier, retired=True)
        result["versions"] = [{"number": r["number"], "createdAt": r["created_at"],
                               "note": r["note"]} for r in conn.execute(
            "SELECT * FROM roadmap_versions WHERE roadmap_id=? ORDER BY number", (identifier,))]
        result.update(notes=[], attachments=[], links=[], chats=[], github=[], history=[])
        return result

    def detail(self, identifier):
        with self.db.read() as conn:
            return self._detail(conn, identifier)

    def version(self, identifier, number):
        with self.db.read() as conn:
            _row(conn, identifier)
            row = conn.execute("SELECT * FROM roadmap_versions WHERE roadmap_id=? AND number=?",
                               (identifier, number)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Roadmap revision not found")
            return {"number": row["number"], "createdAt": row["created_at"],
                    "note": row["note"], **json.loads(row["snapshot"])}

    @staticmethod
    def get_step(conn, identifier):
        row = conn.execute("SELECT * FROM steps WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Step not found")
        return row

    def history(self, step_id):
        with self.db.read() as conn:
            self.get_step(conn, step_id)
            items = read_events(conn, step_id)
            return {"items": items, "total": len(items)}

    def _validate_steps(self, conn, identifier, steps):
        if not isinstance(steps, list) or len(steps) > 5000:
            raise AppError(422, "validation", "Steps must be a list of at most 5000 steps")
        normalized = []
        seen = set()
        for index, data in enumerate(steps):
            if not isinstance(data, dict):
                raise AppError(422, "validation", "Each step must be an object")
            sid = data.get("id") or uid()
            try:
                uuid.UUID(sid)
            except (ValueError, TypeError, AttributeError):
                raise AppError(422, "validation", "Invalid step id") from None
            if sid in seen:
                raise AppError(422, "validation", "Duplicate step id")
            seen.add(sid)
            previous = conn.execute("SELECT roadmap_id FROM steps WHERE id=?", (sid,)).fetchone()
            if previous and previous["roadmap_id"] != identifier:
                raise AppError(409, "conflict", "Step belongs to another roadmap")
            position = data.get("position", index)
            if type(position) is not int or position < 0:
                raise AppError(422, "validation", "Invalid step position")
            parent = data.get("parentId")
            if parent is not None and not isinstance(parent, str):
                raise AppError(422, "validation", "Step parentId must be a step id or null")
            normalized.append({"id": sid, "title": _title(data.get("title")),
                               "description": _text(data.get("description"), "description", ""),
                               "parentId": parent, "position": position})
        parents = {s["id"]: s["parentId"] for s in normalized}
        for sid, parent in parents.items():
            path = {sid}
            while parent is not None:
                if parent not in parents or parent in path:
                    raise AppError(422, "validation", "Step tree contains a missing parent or cycle")
                path.add(parent)
                if len(path) > 100:
                    raise AppError(422, "validation", "Step trees support at most 100 nested levels")
                parent = parents[parent]
        return normalized

    def _snapshot(self, conn, identifier, note):
        row = _row(conn, identifier)
        number = conn.execute("SELECT COALESCE(MAX(number),0)+1 FROM roadmap_versions WHERE roadmap_id=?",
                              (identifier,)).fetchone()[0]
        snapshot = {"title": row["title"], "overview": row["overview"],
                    "steps": _steps(conn, identifier)}
        conn.execute("INSERT INTO roadmap_versions VALUES(?,?,?,?,?)",
                     (identifier, number, now(), note, json.dumps(snapshot)))

    def apply(self, conn, actor, action, payload):
        if actor.get("kind") not in {"human", "agent"}:
            raise AppError(403, "forbidden", "Invalid actor")
        if action == "progress.set":
            identifier = payload.get("id") or payload.get("stepId")
            row = self.get_step(conn, identifier)
            require_revision(row, payload.get("revision"))
            roadmap = _row(conn, row["roadmap_id"])
            if row["retired"] or roadmap["archived"]:
                raise AppError(409, "conflict", "Retired or archived steps cannot change progress")
            if conn.execute("SELECT 1 FROM steps WHERE parent_id=? AND retired=0", (identifier,)).fetchone():
                raise AppError(422, "validation", "Parent progress is derived from its leaves")
            status = payload.get("status")
            if status not in STATUSES:
                raise AppError(422, "validation", "Invalid progress status")
            before = _step(row)
            conn.execute("UPDATE steps SET status=?,revision=revision+1 WHERE id=?", (status, identifier))
            # The roadmap revision binds every tree and leaf change for stale proposal detection.
            conn.execute("UPDATE roadmaps SET revision=revision+1,updated_at=? WHERE id=?", (now(), row["roadmap_id"]))
            result = _step(self.get_step(conn, identifier))
            event(conn, actor, action, identifier, before, result)
            return result
        identifier = payload.get("id") or payload.get("roadmapId")
        if action == "roadmap.create":
            identifier = uid()
            title = _title(payload.get("title"))
            overview = _text(payload.get("overview"), "overview", "")
            steps = self._validate_steps(conn, identifier, payload.get("steps", []))
            stamp = now()
            conn.execute("INSERT INTO roadmaps VALUES(?,?,?,?,?,?,?)", (identifier, title, overview, 1, 0, stamp, stamp))
            before = None
        else:
            row = _row(conn, identifier)
            require_revision(row, payload.get("revision"))
            before = self._detail(conn, identifier)
            if action in {"roadmap.archive", "roadmap.recover"}:
                conn.execute("UPDATE roadmaps SET archived=?,revision=revision+1,updated_at=? WHERE id=?",
                             (int(action == "roadmap.archive"), now(), identifier))
                result = self._detail(conn, identifier)
                event(conn, actor, action, identifier, before, result)
                return result
            if row["archived"]:
                raise AppError(409, "conflict", "Archived roadmaps must be recovered before editing")
            if action == "roadmap.restore":
                version = conn.execute("SELECT snapshot FROM roadmap_versions WHERE roadmap_id=? AND number=?",
                                       (identifier, payload.get("number"))).fetchone()
                if version is None:
                    raise AppError(404, "not_found", "Roadmap revision not found")
                content = json.loads(version["snapshot"])
                note = f"Restored revision {payload.get('number')}"
            elif action == "roadmap.revise":
                content = payload
                note = _text(payload.get("note"), "note", "")
            else:
                raise AppError(422, "validation", "Unsupported roadmap action")
            title = _title(content.get("title"))
            overview = _text(content.get("overview"), "overview", "")
            steps = self._validate_steps(conn, identifier, content.get("steps", []))
            conn.execute("UPDATE roadmaps SET title=?,overview=?,revision=revision+1,updated_at=? WHERE id=?",
                         (title, overview, now(), identifier))
        wanted = {s["id"] for s in steps}
        existing = conn.execute("SELECT * FROM steps WHERE roadmap_id=?", (identifier,)).fetchall()
        for old in existing:
            if old["id"] not in wanted and not old["retired"]:
                conn.execute("UPDATE steps SET retired=1,revision=revision+1 WHERE id=?", (old["id"],))
                event(conn, actor, "step.retire", old["id"], _step(old), _step(self.get_step(conn, old["id"])))
        for step in steps:
            old = conn.execute("SELECT * FROM steps WHERE id=?", (step["id"],)).fetchone()
            if old:
                conn.execute("UPDATE steps SET title=?,description=?,parent_id=?,position=?,retired=0,revision=revision+1 WHERE id=?",
                             (step["title"], step["description"], step["parentId"], step["position"], step["id"]))
            else:
                conn.execute("INSERT INTO steps VALUES(?,?,?,?,?,?,?,?,?)",
                             (step["id"], identifier, step["title"], step["description"], step["parentId"], step["position"], "open", 1, 0))
            event(conn, actor, "step.restore" if old and old["retired"] else "step.revise" if old else "step.create",
                  step["id"], _step(old) if old else None, _step(self.get_step(conn, step["id"])))
        self._snapshot(conn, identifier, "Created" if action == "roadmap.create" else note)
        result = self._detail(conn, identifier)
        event(conn, actor, action, identifier, before, result)
        return result

    def preview(self, data: bytes, name: str, actor):
        if not data or len(data) > 5_000_000:
            raise AppError(413, "too_large", "HTML import must contain 1 to 5,000,000 bytes")
        soup = BeautifulSoup(data, "html.parser")
        document_title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for tag in soup.find_all(["script", "style", "iframe", "frame", "frameset", "object", "embed", "applet", "audio", "video", "source", "track", "form", "input", "button", "svg", "math", "template", "noscript", "base", "link", "meta"]):
            tag.decompose()
        for tag in soup.find_all(True):
            depth = 0
            for _ in tag.parents:
                depth += 1
                if depth > 100:
                    raise AppError(422, "validation", "HTML nesting exceeds 100 levels")
            for key in list(tag.attrs):
                if key.lower().startswith("on") or key.lower() in {"style", "srcdoc"}:
                    del tag.attrs[key]
            for key in ("href", "src", "action", "poster", "xlink:href"):
                value = tag.get(key)
                compact = re.sub(r"[\x00-\x20\x7f]+", "", str(value or ""))
                if compact and (re.match(r"[a-z][a-z0-9+.-]*:", compact, re.I)
                                and not re.match(r"https?://", compact, re.I)):
                    del tag.attrs[key]
                elif compact:
                    tag.attrs[key] = _normalize_url(str(value))
        body = soup.body or soup
        if body is soup and soup.head:
            soup.head.decompose()
        headings = body.find_all(re.compile(r"^h[1-6]$"))
        title = (headings[0].get_text(" ", strip=True) if headings else
                 document_title or PurePath(name).stem) or "Imported roadmap"
        # Recurse through wrappers containing headings, retaining complete fragments
        # elsewhere. This includes loose text and inline markup in document order.
        pieces: list[str] = []
        sections: list[dict] = []
        current: dict | None = None
        stack: list[tuple[int, str]] = []
        overview = ""

        def visit(node):
            nonlocal current, pieces, overview
            if isinstance(node, Tag) and re.fullmatch(r"h[1-6]", node.name or ""):
                if current is not None:
                    current["description"] = markdownify("".join(pieces), heading_style="ATX").strip()
                else:
                    overview = markdownify("".join(pieces), heading_style="ATX").strip()
                pieces = []
                level = int(node.name[1])
                while stack and stack[-1][0] >= level:
                    stack.pop()
                current = {"id": uid(), "title": node.get_text(" ", strip=True) or "Untitled step",
                           "description": "", "parentId": stack[-1][1] if stack else None,
                           "position": len(sections)}
                sections.append(current)
                stack.append((level, current["id"]))
            elif isinstance(node, Tag) and node.find(re.compile(r"^h[1-6]$")):
                for child in node.children:
                    visit(child)
            else:
                pieces.append(str(node))

        for node in body.children:
            visit(node)
        if current is not None:
            current["description"] = markdownify("".join(pieces), heading_style="ATX").strip()
        else:
            overview = markdownify("".join(pieces), heading_style="ATX").strip()
        identifier = uid()
        result = {"id": identifier, "title": title, "overview": overview, "steps": sections,
                  "warnings": ["Review imported Markdown and select steps before committing."],
                  "sha256": hashlib.sha256(data).hexdigest()}
        safe_name = re.sub(r'[\x00-\x1f\x7f"]', "", name.replace("\\", "/").split("/")[-1])[:240] or "import.html"
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO plan_imports VALUES(?,?,?,?,?,?,?,NULL)",
                         (identifier, safe_name, data, result["sha256"], json.dumps(result), actor["id"], now()))
            event(conn, actor, "import.preview", identifier, None, {"sha256": result["sha256"], "name": safe_name})
        return result

    def import_commit(self, identifier, payload, actor, conn=None):
        with self.db.transaction() if conn is None else nullcontext(conn) as conn:
            row = conn.execute("SELECT * FROM plan_imports WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Import not found")
            if row["roadmap_id"]:
                raise AppError(409, "conflict", "Import has already been committed")
            target = payload.get("roadmapId")
            if target:
                roadmap = _row(conn, target)
                require_revision(roadmap, payload.get("revision"))
                if roadmap["archived"]:
                    raise AppError(409, "conflict", "Recover the roadmap before attaching an import")
                before = _roadmap(roadmap)
                conn.execute("UPDATE roadmaps SET revision=revision+1,updated_at=? WHERE id=?", (now(), target))
                result = self._detail(conn, target)
                event(conn, actor, "import.reference", target, before, {"importId": identifier, "sha256": row["sha256"]})
            else:
                result = self.apply(conn, actor, "roadmap.create", payload)
                target = result["id"]
            conn.execute("UPDATE plan_imports SET roadmap_id=? WHERE id=?", (target, identifier))
            result["importId"] = identifier
            return result

    def import_download(self, identifier):
        with self.db.read() as conn:
            row = conn.execute("SELECT original,name FROM plan_imports WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Import not found")
            return bytes(row["original"]), row["name"]
