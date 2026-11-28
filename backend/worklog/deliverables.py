"""Roadmap deliverables, their linked work, and the records reviewed at sign-off."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from urllib.parse import quote

from . import support
from .common import AppError, event, now, read_events, require_revision, rows_in, text, uid
from .db import Database
from .operations import deliverable_references

FAMILY = """WITH RECURSIVE family(id) AS (
    SELECT id FROM roadmaps WHERE id=?
    UNION
    SELECT r.id FROM roadmaps r JOIN family f ON r.parent_id=f.id WHERE r.archived=0
) """
SUMMARY = """d.id,d.roadmap_id,d.title,d.status,d.archived,d.revision,d.created_at,d.updated_at,
    json_array_length(d.definition,'$.requirements') AS requirement_count,
    (SELECT count(*) FROM json_each(d.definition,'$.requirements')
        WHERE json_extract(value,'$.checked')=1) AS checked_count"""
SOURCES = {
    "repository": "SELECT id,name AS label,revision,archived,merged_into IS NOT NULL AS unavailable,"
                  "NULL AS sha256 FROM repositories WHERE id IN ({})",
    "resource": "SELECT id,title AS label,revision,archived,0 AS unavailable,NULL AS sha256"
                " FROM resources WHERE id IN ({})",
    "attachment": "SELECT id,name AS label,revision,archived,(size>0 AND length(content)=0) AS unavailable,"
                  "sha256 FROM attachments WHERE owner_type<>'proposal' AND id IN ({})",
}
REFERENCE_PATHS = {
    "repository": "#repositories/{}",
    "resource": "#resources/{}",
    "attachment": "/api/v1/attachments/{}?download=1",
}
SNAPSHOT_FIELDS = ("title", "description", "steps", "requirements", "inputs", "outputs")


def _roadmap(conn: sqlite3.Connection, identity: str) -> sqlite3.Row:
    row = conn.execute("SELECT id,title,parent_id,archived FROM roadmaps WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise AppError(404, "not_found", "Roadmap not found")
    return row


def _paths(conn: sqlite3.Connection, ids: Iterable[str]) -> dict[str, list[dict[str, str]]]:
    """The current ancestry of all of these owners, without one read per deliverable."""
    owners = list(dict.fromkeys(ids))
    roads = {row["id"]: row for row in rows_in(conn, """
        WITH RECURSIVE lineage(id) AS (
            SELECT id FROM roadmaps WHERE id IN ({})
            UNION
            SELECT r.parent_id FROM roadmaps r JOIN lineage l ON r.id=l.id WHERE r.parent_id IS NOT NULL
        )
        SELECT r.id,r.title,r.parent_id FROM roadmaps r JOIN lineage l ON r.id=l.id
        """, owners)}
    paths: dict[str, list[dict[str, str]]] = {}
    for owner in owners:
        trail: list[dict[str, str]] = []
        seen: set[str] = set()
        cursor = owner
        while cursor in roads and cursor not in seen:
            seen.add(cursor)
            row = roads[cursor]
            trail.append({"id": row["id"], "title": row["title"]})
            cursor = row["parent_id"]
        paths[owner] = list(reversed(trail))
    return paths


def _summaries(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    paths = _paths(conn, (row["roadmap_id"] for row in rows))
    return [
        {"id": row["id"], "roadmapId": row["roadmap_id"], "roadmapPath": paths[row["roadmap_id"]],
         "roadmapTitle": paths[row["roadmap_id"]][-1]["title"], "title": row["title"], "status": row["status"],
         "archived": bool(row["archived"]), "revision": row["revision"], "createdAt": row["created_at"],
         "updatedAt": row["updated_at"], "requirementCount": row["requirement_count"],
         "checkedCount": row["checked_count"]}
        for row in rows
    ]


def _source_rows(conn: sqlite3.Connection, refs: Iterable[dict]) -> dict[tuple[str, str], sqlite3.Row]:
    ids: dict[str, list[str]] = {kind: [] for kind in SOURCES}
    for ref in refs:
        if ref["kind"] in SOURCES:
            ids[ref["kind"]].append(ref["id"])
    return {(kind, row["id"]): row for kind, query in SOURCES.items() for row in rows_in(conn, query, ids[kind])}


def _available(row: sqlite3.Row | None) -> bool:
    return row is not None and not row["archived"] and not row["unavailable"]


def _view_reference(ref: dict, sources: dict[tuple[str, str], sqlite3.Row]) -> dict:
    kind = ref["kind"]
    view = dict.fromkeys(("id", "url", "sourceRevision", "sha256")) | ref
    if kind == "url":
        return {**view, "availability": "available", "href": ref["url"]}
    row = sources.get((kind, ref["id"]))
    if row is None or not _available(row):
        return {**view, "availability": "unavailable", "href": None}
    changed = ref["sha256"] != row["sha256"] if kind == "attachment" else ref["sourceRevision"] != row["revision"]
    return {**view, "availability": "changed" if changed else "available",
            "href": REFERENCE_PATHS[kind].format(quote(ref["id"], safe=""))}


def _reference_key(ref: dict) -> tuple[str, str]:
    return ref["kind"], ref["url"] if ref["kind"] == "url" else ref["id"]


def _definition(conn: sqlite3.Connection, actor: dict, owner: str, payload: dict, old: dict,
                proposal_id: str | None) -> dict:
    """Normalize the lists together so one evidence upload is copied once per change."""
    copies: dict[str, str] = {}
    sources = _source_rows(conn, deliverable_references(payload))

    def normalized(values: list[dict], previous: list[dict]) -> list[dict]:
        result: list[dict] = []
        prior = {_reference_key(ref): ref for ref in previous}
        seen: set[tuple[str, str]] = set()
        for ref in values:
            kind = ref["kind"]
            label = text(ref.get("label", ""), "label", limit=1000).strip()
            if kind == "url":
                try:
                    url = support.valid_http_url(ref["url"])
                except ValueError as exc:
                    raise AppError(422, "validation", "url must be an absolute HTTP(S) URL") from exc
                saved = {"kind": kind, "url": url, "label": label or url}
            else:
                identity = text(ref["id"], "id", required=True, limit=200)
                if kind == "proposal_attachment":
                    if proposal_id is None:
                        raise AppError(422, "validation", "Upload proposal evidence to the proposal before using it")
                    if identity not in copies:
                        copied = support.apply(conn, actor, "attachment.create", {
                            "ownerType": "roadmap", "ownerId": owner, "attachmentId": identity, "proposalId": proposal_id,
                        })
                        copies[identity] = copied["id"]
                        sources.update(_source_rows(conn, [{"kind": "attachment", "id": copied["id"]}]))
                    kind, identity = "attachment", copies[identity]
                before = prior.get((kind, identity))
                if before is not None and not ref.get("refresh"):
                    saved = {**before, "label": label or before["label"]}
                else:
                    source = sources.get((kind, identity))
                    if source is None or not _available(source):
                        raise AppError(422, "validation", "Choose an available deliverable reference", {"id": "unavailable"})
                    saved = {"kind": kind, "id": identity, "label": label or source["label"],
                             "sourceRevision": source["revision"], "sha256": source["sha256"]}

            key = _reference_key(saved)
            if key in seen:
                raise AppError(422, "validation", "A reference can appear once in the same list")
            seen.add(key)
            result.append(saved)
        return result

    result = {key: normalized(payload[key], old.get(key, [])) if key in payload else old.get(key, [])
              for key in ("inputs", "outputs")}
    requirements: list[dict] = old.get("requirements", [])
    if "requirements" in payload:
        prior = {req["id"]: req for req in requirements}
        requirements = []
        seen: set[str] = set()
        for req in payload["requirements"]:
            identity = text(req["id"], "requirement id", required=True, limit=200) if req.get("id") else uid()
            if identity in seen:
                raise AppError(422, "validation", "Requirement IDs must be unique within a deliverable")
            seen.add(identity)
            requirements.append({
                "id": identity, "text": text(req["text"], "requirement", required=True, limit=4000).strip(),
                "checked": req.get("checked", False),
                "verificationNote": text(req.get("verificationNote", ""), "verificationNote", limit=8000).strip(),
                "evidence": normalized(req.get("evidence", []), prior.get(identity, {}).get("evidence", [])),
            })
    return {**result, "requirements": requirements}


def _set_steps(conn: sqlite3.Connection, identifier: str, owner: str, step_ids: list[str]) -> None:
    step_ids = [text(step, "stepId", required=True, limit=200) for step in step_ids]
    if len(step_ids) != len(set(step_ids)):
        raise AppError(422, "validation", "Link each step at most once", {"stepIds": "duplicate"})
    kept = {row[0] for row in conn.execute("SELECT step_id FROM deliverable_steps WHERE deliverable_id=?", (identifier,))}
    new = [step for step in step_ids if step not in kept]
    family = {row[0] for row in conn.execute(FAMILY + "SELECT id FROM family", (owner,))}
    found = rows_in(conn, "SELECT id,roadmap_id FROM steps WHERE retired=0 AND id IN ({})", new)
    if len(new) != len(found) or any(row["roadmap_id"] not in family for row in found):
        raise AppError(422, "validation", "Link active steps on this roadmap or its sub-roadmaps", {"stepIds": "unavailable"})
    conn.execute("DELETE FROM deliverable_steps WHERE deliverable_id=?", (identifier,))
    conn.executemany("INSERT INTO deliverable_steps(deliverable_id,step_id,position) VALUES(?,?,?)",
                     [(identifier, step_id, position) for position, step_id in enumerate(step_ids)])


def _ready(detail: dict) -> None:
    requirements = detail["requirements"]
    if not requirements:
        raise AppError(422, "validation", "Add a requirement before submitting a deliverable", {"requirements": "empty"})
    incomplete = [req["id"] for req in requirements if not req["checked"] or not (
        req["verificationNote"] or any(ref["availability"] == "available" for ref in req["evidence"])
    )]
    if incomplete:
        raise AppError(422, "validation", "Check every requirement and add available evidence or a verification note",
                       {"requirements": incomplete})


class DeliverableService:
    def __init__(self, db: Database):
        self.db = db

    def listing(self, owner: str, *, scope: str, archived: bool, limit: int, offset: int) -> dict:
        def read(conn: sqlite3.Connection) -> dict:
            _roadmap(conn, owner)
            prefix = FAMILY if scope == "tree" else ""
            scope_filter = "d.roadmap_id IN (SELECT id FROM family)" if scope == "tree" else "d.roadmap_id=?"
            args = (owner, int(archived))
            where = f" WHERE {scope_filter} AND d.archived=?"
            counts = dict(conn.execute(prefix + "SELECT d.status,count(*) FROM deliverables d" + where + " GROUP BY d.status", args))
            rows = conn.execute(
                prefix + f"SELECT {SUMMARY} FROM deliverables d JOIN roadmaps r ON r.id=d.roadmap_id" + where
                + " ORDER BY (d.roadmap_id=?) DESC,r.title COLLATE NOCASE,r.id,d.created_at,d.id LIMIT ? OFFSET ?",
                (*args, owner, limit, offset),
            ).fetchall()
            return {"items": _summaries(conn, rows), "total": sum(counts.values()),
                    "counts": {"planned": counts.get("planned", 0), "readyForReview": counts.get("ready_for_review", 0),
                               "accepted": counts.get("accepted", 0)}}

        return self.db.snapshot(read)

    def backlinks(self, identity: str, limit: int, offset: int) -> dict:
        def read(conn: sqlite3.Connection) -> dict:
            step = conn.execute(
                "SELECT s.roadmap_id,s.retired,r.archived FROM steps s JOIN roadmaps r ON r.id=s.roadmap_id WHERE s.id=?",
                (identity,),
            ).fetchone()
            if step is None:
                raise AppError(404, "not_found", "Step not found")
            if step["retired"] or step["archived"]:
                return {"items": [], "total": 0}
            owners = """WITH RECURSIVE owners(id) AS (
                SELECT ?
                UNION
                SELECT parent.id FROM owners o JOIN roadmaps r ON r.id=o.id
                JOIN roadmaps parent ON parent.id=r.parent_id WHERE parent.archived=0
            ) """
            scope = """ FROM deliverable_steps ds JOIN deliverables d ON d.id=ds.deliverable_id
                WHERE ds.step_id=? AND d.archived=0 AND d.roadmap_id IN (SELECT id FROM owners)"""
            args = (step["roadmap_id"], identity)
            total = conn.execute(owners + "SELECT count(*)" + scope, args).fetchone()[0]
            rows = conn.execute(owners + f"SELECT {SUMMARY}" + scope
                                + " ORDER BY d.title COLLATE NOCASE,d.id LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
            return {"items": _summaries(conn, rows), "total": total}

        return self.db.snapshot(read)

    def options(self, owner: str, kind: str, q: str, limit: int, offset: int) -> dict:
        def read(conn: sqlite3.Connection) -> dict:
            _roadmap(conn, owner)
            queries = {
                "step": FAMILY + """SELECT s.id,s.title,r.title AS detail FROM steps s
                    JOIN roadmaps r ON r.id=s.roadmap_id JOIN family f ON f.id=r.id WHERE s.retired=0 AND r.archived=0""",
                "resource": "SELECT id,title,summary AS detail FROM resources WHERE archived=0",
                "repository": "SELECT id,name AS title,url AS detail FROM repositories WHERE archived=0 AND merged_into IS NULL",
                "attachment": """SELECT a.id,a.name AS title,
                    coalesce(r.title,s.title,w.title,repo.name,a.owner_type) AS detail FROM attachments a
                    LEFT JOIN roadmaps r ON a.owner_type='roadmap' AND r.id=a.owner_id
                    LEFT JOIN steps s ON a.owner_type='step' AND s.id=a.owner_id
                    LEFT JOIN work_items w ON a.owner_type='item' AND w.id=a.owner_id
                    LEFT JOIN repositories repo ON a.owner_type='repository' AND repo.id=a.owner_id
                    WHERE a.owner_type<>'proposal' AND a.archived=0 AND (a.size=0 OR length(a.content)>0)""",
            }
            base = " FROM (" + queries[kind] + ") picked WHERE instr(lower(title||' '||detail),lower(?))>0 OR ?=''"
            args = (owner, q, q) if kind == "step" else (q, q)
            total = conn.execute("SELECT count(*)" + base, args).fetchone()[0]
            rows = conn.execute("SELECT id,title,detail" + base + " ORDER BY title COLLATE NOCASE,id LIMIT ? OFFSET ?",
                                (*args, limit, offset)).fetchall()
            return {"items": [dict(row) for row in rows], "total": total}

        return self.db.snapshot(read)

    @staticmethod
    def _row(conn: sqlite3.Connection, identity: str) -> sqlite3.Row:
        row = conn.execute(f"SELECT {SUMMARY},d.description,d.definition FROM deliverables d WHERE d.id=?", (identity,)).fetchone()
        if row is None:
            raise AppError(404, "not_found", "Deliverable not found")
        return row

    def record(self, conn: sqlite3.Connection, identity: str) -> dict:
        row = self._row(conn, identity)
        summary = _summaries(conn, [row])[0]
        definition = json.loads(row["definition"])
        sources = _source_rows(conn, deliverable_references(definition))
        steps = [
            {"id": step["id"], "title": step["title"], "roadmapId": step["roadmap_id"], "roadmapTitle": step["roadmap_title"],
             "available": bool(step["available"])}
            for step in conn.execute(FAMILY + """
                SELECT s.id,s.title,s.roadmap_id,r.title AS roadmap_title,
                       (s.retired=0 AND r.archived=0 AND f.id IS NOT NULL) AS available
                FROM deliverable_steps ds JOIN steps s ON s.id=ds.step_id JOIN roadmaps r ON r.id=s.roadmap_id
                LEFT JOIN family f ON f.id=r.id WHERE ds.deliverable_id=? ORDER BY ds.position,ds.step_id
                """, (row["roadmap_id"], identity))
        ]
        return {**summary, "description": row["description"], "steps": steps, "stepIds": [step["id"] for step in steps],
                "requirements": [{**req, "evidence": [_view_reference(ref, sources) for ref in req["evidence"]]}
                                 for req in definition["requirements"]],
                "inputs": [_view_reference(ref, sources) for ref in definition["inputs"]],
                "outputs": [_view_reference(ref, sources) for ref in definition["outputs"]]}

    def detail(self, identity: str) -> dict:
        return self.db.snapshot(lambda conn: self.record(conn, identity))

    def history(self, identity: str, limit: int, offset: int) -> dict:
        def read(conn: sqlite3.Connection) -> dict:
            self._row(conn, identity)
            total = conn.execute("SELECT count(*) FROM deliverable_acceptances WHERE deliverable_id=?", (identity,)).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM deliverable_acceptances WHERE deliverable_id=? ORDER BY revision DESC LIMIT ? OFFSET ?",
                (identity, limit, offset),
            )
            return {
                "total": total,
                "items": [{"revision": row["revision"], "acceptedAt": row["accepted_at"], "note": row["note"],
                           "actor": {"id": row["actor_id"], "label": row["actor_label"], "kind": row["actor_kind"]},
                           "proposalId": row["proposal_id"], "snapshot": json.loads(row["snapshot"])} for row in rows],
                "events": [{key: change[key] for key in ("kind", "actorId", "actorLabel", "actorKind", "createdAt")}
                           | {"reason": (change["after"] or {}).get("reason", "")}
                           for change in read_events(conn, identity, limit=50)],
            }

        return self.db.snapshot(read)

    def apply(self, conn: sqlite3.Connection, actor: dict, action: str, payload: dict, *,
              proposal_id: str | None = None) -> dict:
        if actor["kind"] != "human" and proposal_id is None:
            raise AppError(403, "agent_scope", "Agents change deliverables through proposals")
        verb = action.removeprefix("deliverable.")
        stamp = now()
        if verb == "create":
            roadmap = _roadmap(conn, payload["roadmapId"])
            if roadmap["archived"]:
                raise AppError(409, "conflict", "Recover this roadmap before adding deliverables")
            identity = uid()
            definition = _definition(conn, actor, roadmap["id"], payload, {}, proposal_id)
            conn.execute(
                "INSERT INTO deliverables(id,roadmap_id,title,description,definition,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (identity, roadmap["id"], text(payload["title"], "title", required=True, limit=1000).strip(),
                 text(payload.get("description", ""), "description", limit=100_000), json.dumps(definition), stamp, stamp),
            )
            _set_steps(conn, identity, roadmap["id"], payload.get("stepIds", []))
            before = None
            reason = ""
        else:
            identity = payload["id"]
            row = self._row(conn, identity)
            require_revision(row, payload.get("revision"))
            if _roadmap(conn, row["roadmap_id"])["archived"]:
                raise AppError(409, "conflict", "Recover this roadmap before changing its deliverables")
            before = {key: row[key] for key in ("title", "status", "archived", "revision")}
            reason = self._change(conn, actor, verb, row, payload, stamp, proposal_id)
            conn.execute("UPDATE deliverables SET revision=revision+1,updated_at=? WHERE id=?", (stamp, identity))

        after = self.record(conn, identity)
        event(conn, actor, action, identity, before,
              {key: after[key] for key in ("title", "status", "archived", "revision")} | {"reason": reason}, at=stamp)
        return after

    def _change(self, conn: sqlite3.Connection, actor: dict, verb: str, row: sqlite3.Row, payload: dict,
                stamp: str, proposal_id: str | None) -> str:
        identity = row["id"]
        status = row["status"]
        if verb in {"archive", "recover"}:
            if proposal_id is not None:
                raise AppError(403, "agent_scope", "Only the person archives or recovers deliverables")
            archived = verb == "archive"
            if archived == bool(row["archived"]):
                raise AppError(409, "conflict", "This deliverable is already in the requested archive state")
            conn.execute("UPDATE deliverables SET archived=? WHERE id=?", (int(archived), identity))
            return ""
        if row["archived"]:
            raise AppError(409, "conflict", "Recover this deliverable before changing it")
        if verb == "update" and status == "planned":
            definition = _definition(conn, actor, row["roadmap_id"], payload, json.loads(row["definition"]), proposal_id)
            title = text(payload.get("title") or row["title"], "title", required=True, limit=1000).strip()
            description = text(payload.get("description", row["description"]), "description", limit=100_000)
            conn.execute("UPDATE deliverables SET title=?,description=?,definition=? WHERE id=?",
                         (title, description, json.dumps(definition), identity))
            if "stepIds" in payload:
                _set_steps(conn, identity, row["roadmap_id"], payload["stepIds"])
            return ""
        if verb == "submit" and status == "planned":
            _ready(self.record(conn, identity))
            conn.execute("UPDATE deliverables SET status='ready_for_review' WHERE id=?", (identity,))
            return ""
        if verb == "accept" and status == "ready_for_review":
            detail = self.record(conn, identity)
            _ready(detail)
            conn.execute(
                "INSERT INTO deliverable_acceptances(deliverable_id,revision,accepted_at,actor_id,actor_label,actor_kind,"
                "proposal_id,note,snapshot) VALUES(?,?,?,?,?,?,?,?,?)",
                (identity, row["revision"], stamp, actor["id"], actor["label"], actor["kind"], proposal_id,
                 text(payload.get("note", ""), "note", limit=8000), json.dumps({key: detail[key] for key in SNAPSHOT_FIELDS})),
            )
            conn.execute("UPDATE deliverables SET status='accepted' WHERE id=?", (identity,))
            return ""
        if verb == "reopen" and status in {"ready_for_review", "accepted"}:
            reason = text(payload["reason"], "reason", required=True, limit=8000).strip()
            definition = json.loads(row["definition"])
            if status == "accepted":
                for req in definition["requirements"]:
                    req["checked"] = False
            conn.execute("UPDATE deliverables SET status='planned',definition=? WHERE id=?", (json.dumps(definition), identity))
            return reason
        raise AppError(409, "conflict", "This action is unavailable in the current deliverable state")
