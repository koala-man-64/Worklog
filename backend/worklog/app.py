"""Single-origin local HTTP application. Importing this module starts no service."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from . import __version__
from . import models as m
from .common import (
    FILE_LIMIT,
    HTML_LIMIT,
    AppError,
    event,
    now,
    read_events,
    setting,
    text,
    today,
    uid,
)
from .db import Database
from .plans import PlanService
from .proposals import ProposalService
from .recovery import automatic_backup, backup_database, export_database, list_backups
from .services import WorkService, support_record

logger = logging.getLogger("worklog")


def default_data_directory() -> Path:
    return Path(os.environ.get("WORKLOG_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "Worklog")


def error_response(exc: AppError) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "message": exc.message,
                                  **({"fields": exc.fields} if exc.fields is not None else {})}},
                        status_code=exc.status, headers={"Retry-After": "1"} if exc.code == "database_busy" else None)


async def bounded_body(request: Request, maximum: int) -> bytes:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) < 0:
                raise ValueError()
            if int(length) > maximum:
                raise AppError(413, "too_large", f"Request exceeds {maximum:,} bytes")
        except ValueError:
            raise AppError(400, "content_length", "Invalid Content-Length") from None
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > maximum:
            raise AppError(413, "too_large", f"Request exceeds {maximum:,} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(data_dir: str | Path | None = None, *, testing: bool = False) -> FastAPI:
    directory = Path(data_dir) if data_dir else default_data_directory()
    db = Database(directory / "worklog.sqlite3")
    db.initialize()
    plans = PlanService(db)
    work = WorkService(db, plans)

    def dispatch(conn: sqlite3.Connection, actor: dict, action: str, payload: dict) -> dict:
        # The proposal boundary shares these exact validators and domain mutations.
        for field in ("id", "itemId", "date"):
            if field in payload and not isinstance(payload[field], str):
                raise AppError(422, "validation", f"{field} must be a string")
        contracts: dict[str, type[m.Command]] = {
            "item.create": m.ItemCreate, "item.update": m.ItemUpdate,
            "item.archive": m.Expected, "item.recover": m.Expected,
            "entry.upsert": m.EntryUpdate, "note.create": m.NoteCreate, "note.update": m.NoteUpdate,
            "task.create": m.TaskCreate, "task.update": m.TaskUpdate,
            "roadmap.create": m.RoadmapCreate, "roadmap.revise": m.RoadmapRevision,
            "roadmap.move": m.RoadmapMove, "progress.set": m.Progress,
            "person.create": m.PersonCreate, "person.update": m.PersonUpdate,
            "person.archive": m.Expected, "person.recover": m.Expected,
            "person.link": m.PersonLinkCreate, "person.unlink": m.PersonLinkRemove,
            "link.create": m.LinkCreate, "link.update": m.LinkUpdate,
            "link.archive": m.Expected, "link.recover": m.Expected,
            "chat.create": m.ChatCreate, "chat.update": m.ChatUpdate,
            "chat.archive": m.Expected, "chat.recover": m.Expected,
            "github.create": m.GithubRefCreate, "github.update": m.GithubRefUpdate,
            "github.archive": m.Expected, "github.recover": m.Expected,
        }
        model = contracts.get(action)
        if model:
            excluded = {"id", "date", "itemId"} if action == "entry.upsert" else {"id"}
            try:
                model.model_validate({k: v for k, v in payload.items() if k not in excluded})
            except ValidationError as exc:
                raise AppError(422, "validation", "Invalid proposed operation", exc.errors(include_input=False, include_context=False)) from None
        return work.apply(conn, actor, action, payload)

    proposals = ProposalService(db, dispatch)
    work.proposals = proposals
    app = FastAPI(title="Worklog Local API", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url="/api/v1/openapi.json")
    app.state.db, app.state.work, app.state.plans, app.state.proposals = db, work, plans, proposals
    app.state.sessions = {}
    app.state.instance_id = os.environ.get("WORKLOG_INSTANCE_ID", uid())
    app.state.backup_error = None
    port = os.environ.get("WORKLOG_PORT") or "8877"
    origin = f"http://127.0.0.1:{port}"
    allowed_hosts = {f"127.0.0.1:{port}"}
    allowed_origins = {origin}
    if os.environ.get("WORKLOG_DEV") == "1":
        allowed_hosts.add("127.0.0.1:5173")
        allowed_origins.add("http://127.0.0.1:5173")

    def auto_backup() -> None:
        if testing or os.environ.get("WORKLOG_AUTO_BACKUP") == "0":
            return
        try:
            automatic_backup(db.path, directory / "backups")
            app.state.backup_error = None
        except (OSError, sqlite3.Error, ValueError, RuntimeError):
            app.state.backup_error = "Automatic backup failed. Check free disk space and retry a manual backup."
            logger.warning("Automatic backup failed; see storage settings")

    auto_backup()

    @app.exception_handler(AppError)
    async def on_domain_error(request: Request, exc: AppError) -> Response:
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> Response:
        return error_response(AppError(422, "validation", "Check the highlighted fields",
                                      [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]))

    @app.exception_handler(sqlite3.Error)
    async def on_database_error(request: Request, exc: sqlite3.Error) -> Response:
        if isinstance(exc, sqlite3.OperationalError) and any(s in str(exc) for s in ("locked", "busy")):
            return error_response(AppError(503, "database_busy", "Database is busy; retry this change"))
        logger.error("Database request failed (%s)", type(exc).__name__)
        return error_response(AppError(503, "database_error", "The database operation failed; no partial change was committed"))

    @app.exception_handler(OSError)
    async def on_storage_error(request: Request, exc: OSError) -> Response:
        logger.error("Storage request failed (%s)", type(exc).__name__)
        return error_response(AppError(503, "storage_error", "Storage operation failed. Check free disk space and folder permissions, then retry."))

    @app.middleware("http")
    async def protect(request: Request, call_next: Callable) -> Response:
        try:
            if request.headers.get("host", "") not in allowed_hosts:
                raise AppError(400, "host", "Use the local Worklog address")
            path, method = request.url.path, request.method
            supplied_origin = request.headers.get("origin")
            if supplied_origin and supplied_origin not in allowed_origins:
                raise AppError(403, "origin", "Foreign browser origins are not allowed")
            if path.startswith("/api/") and path != "/api/v1/session/bootstrap":
                authorization = request.headers.get("authorization", "")
                if authorization:
                    if not authorization.startswith("Bearer "):
                        raise AppError(401, "authentication", "Use a valid credential")
                    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
                    with db.read() as conn:
                        credential = conn.execute("SELECT * FROM credentials WHERE token_hash=? AND revoked=0", (digest,)).fetchone()
                    if credential is None:
                        raise AppError(401, "authentication", "Credential is invalid or revoked")
                    actor = {"id": credential["id"], "label": credential["label"], "kind": "agent"}
                    suffix = path.removeprefix("/api/v1")
                    forbidden = suffix.startswith(("/settings", "/credentials", "/backups", "/export", "/openapi"))
                    agent_write = ((suffix == "/proposals" and method == "POST") or
                                   (suffix.startswith("/proposals/") and (method == "PATCH" or (method == "POST" and suffix.rsplit("/", 1)[-1] in {"submit", "withdraw"}))) or
                                   (suffix == "/attachments" and method == "POST" and request.query_params.get("ownerType") == "proposal"))
                    if forbidden or (method != "GET" and not agent_write):
                        raise AppError(403, "agent_scope", "Agents may read context and submit proposals only")
                else:
                    session_id = request.cookies.get("worklog_session", "")
                    session = app.state.sessions.get(session_id)
                    if not session or session["expires"] < time.time():
                        raise AppError(401, "session", "Open Worklog to establish a local session")
                    actor = {"id": "local-user", "label": "You", "kind": "human"}
                    if method not in {"GET", "HEAD"}:
                        # Compare bytes: compare_digest rejects non-ASCII str input with a TypeError.
                        if supplied_origin not in allowed_origins or not secrets.compare_digest(request.headers.get("x-csrf-token", "").encode(), session["csrf"].encode()):
                            raise AppError(403, "csrf", "Refresh Worklog before saving")
                request.state.actor = actor
                # Bound JSON requests before FastAPI's body parser can allocate arbitrarily.
                if method not in {"GET", "HEAD"} and not (path == "/api/v1/attachments" or path == "/api/v1/imports/preview"):
                    body = await bounded_body(request, 8_000_000)
                    request._body = body
            response = await call_next(request)
        except AppError as exc:
            response = error_response(exc)
        except Exception:
            # Unhandled failures still get the error envelope and the security headers below.
            incident = uid()
            logger.exception("Unhandled request failure (incident %s)", incident)
            response = error_response(AppError(500, "internal_error", f"Unexpected server error. Reference {incident}."))
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        return response

    def mutate(request: Request, payload: Any, fn: Callable, *, required_key: bool = False,
               redact: tuple[str, ...] = ()) -> dict:
        auto_backup()
        key = request.headers.get("idempotency-key")
        if required_key and not key:
            raise AppError(428, "idempotency", "An Idempotency-Key is required for this command")
        if key is not None and not 8 <= len(key) <= 200:
            raise AppError(422, "idempotency", "Use an idempotency key between 8 and 200 characters")
        digest = hashlib.sha256((request.method + str(request.url.path) + "?" + str(request.url.query) + json.dumps(payload, sort_keys=True)).encode()).hexdigest()
        actor = request.state.actor
        with db.transaction() as c:
            if key:
                receipt = c.execute("SELECT * FROM receipts WHERE actor_id=? AND key=?", (actor["id"], key)).fetchone()
                if receipt:
                    if receipt["request_hash"] != digest:
                        raise AppError(409, "idempotency_conflict", "This key was already used for a different request")
                    return json.loads(receipt["response_json"])
            result = fn(c, actor)
            if key:
                stored = {**result, **dict.fromkeys(redact)}
                c.execute("INSERT INTO receipts VALUES(?,?,?,?)", (actor["id"], key, digest, json.dumps(stored)))
            return result

    def apply(request: Request, action: str, payload: dict, *, create: bool = False) -> dict:
        return mutate(request, payload, lambda c, a: work.apply(c, a, action, payload), required_key=create)

    @app.get("/health")
    def health() -> dict:
        with db.read() as c:
            return {"app": "worklog", "version": __version__, "instanceId": app.state.instance_id,
                    "dataId": setting(c, "dataId"), "ready": True}

    @app.post("/api/v1/session/bootstrap")
    async def bootstrap(request: Request) -> Response:
        if request.headers.get("authorization") or request.headers.get("origin") not in allowed_origins or request.headers.get("x-worklog-bootstrap") != "1":
            raise AppError(403, "bootstrap", "Open the local Worklog page to start a session")
        await bounded_body(request, 1024)
        old = request.cookies.get("worklog_session", "")
        previous = app.state.sessions.get(old)
        if previous and previous["expires"] > time.time():
            token, session_id = previous["csrf"], old
        else:
            token, session_id = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            app.state.sessions = {k: v for k, v in app.state.sessions.items() if v["expires"] > time.time()}
            if len(app.state.sessions) >= 1024:
                raise AppError(429, "sessions", "Too many local sessions")
            app.state.sessions[session_id] = {"csrf": token, "expires": time.time() + 86400}
        with db.read() as c:
            tz = setting(c, "timezone", "America/Chicago")
            data_id = setting(c, "dataId")
        response = JSONResponse({"csrfToken": token, "today": today(tz), "timezone": tz, "dataId": data_id})
        response.set_cookie("worklog_session", session_id, httponly=True, samesite="strict", max_age=86400)
        return response

    @app.get("/api/v1/work-items")
    def items(view: str = "all", archived: bool = False, status: str | None = None,
              roadmapId: str | None = None, personId: str | None = None, q: str = "",
              limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return work.list(view=view, archived=archived, state=status, roadmap_id=roadmapId, person_id=personId, limit=limit, offset=offset, q=q)

    @app.post("/api/v1/work-items")
    def create_item(request: Request, body: m.ItemCreate) -> dict:
        return apply(request, "item.create", body.model_dump(), create=True)

    @app.post("/api/v1/work-items/reorder")
    def reorder_items(request: Request, body: m.Reorder) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.reorder(c, a, p))

    @app.get("/api/v1/work-items/{identity}")
    def item_detail(identity: str) -> dict:
        return work.detail(identity)

    @app.patch("/api/v1/work-items/{identity}")
    def update_item(identity: str, request: Request, body: m.ItemUpdate) -> dict:
        return apply(request, "item.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/work-items/{identity}/move")
    def move_item(identity: str, request: Request, body: m.Move) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.move_backlog(c, a, identity, p))

    @app.post("/api/v1/work-items/{identity}/{verb}")
    def archive_item(identity: str, verb: str, request: Request, body: m.Expected) -> dict:
        if verb not in {"archive", "recover"}:
            raise AppError(404, "not_found", "Unknown command")
        return apply(request, "item." + verb, {**body.model_dump(), "id": identity})

    @app.get("/api/v1/days/{date}")
    def day(date: str) -> dict:
        return work.day(date)

    @app.put("/api/v1/days/{date}/entries/{item_id}")
    def update_entry(date: str, item_id: str, request: Request, body: m.EntryUpdate) -> dict:
        return apply(request, "entry.upsert", {**body.model_dump(exclude_unset=True), "date": date, "itemId": item_id})

    @app.put("/api/v1/days/{date}/reflection")
    def reflection(date: str, request: Request, body: m.Reflection) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.reflection(c, a, date, p))

    @app.post("/api/v1/days/{date}/reorder")
    def reorder_day(date: str, request: Request, body: m.Reorder) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.reorder(c, a, p, date))

    @app.post("/api/v1/days/{date}/reschedule")
    def reschedule(date: str, request: Request, body: m.Reschedule) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.reschedule(c, a, date, p))

    def authorize_history(conn: sqlite3.Connection, identity: str, actor: dict) -> None:
        if conn.execute("SELECT 1 FROM proposals WHERE id=?", (identity,)).fetchone():
            proposals.detail(identity, actor)
        attachment = conn.execute("SELECT owner_type,owner_id FROM attachments WHERE id=?", (identity,)).fetchone()
        if attachment and attachment["owner_type"] == "proposal":
            proposals.detail(attachment["owner_id"], actor)

    @app.get("/api/v1/history")
    def history(request: Request, entityId: str, limit: int = Query(100, ge=1, le=200)) -> dict:
        with db.read() as c:
            authorize_history(c, entityId, request.state.actor)
            return {"items": read_events(c, entityId, limit)}

    @app.get("/api/v1/notes/{identity}")
    @app.get("/api/v1/tasks/{identity}")
    @app.get("/api/v1/references/{identity}")
    @app.get("/api/v1/links/{identity}")
    @app.get("/api/v1/chats/{identity}")
    @app.get("/api/v1/github/{identity}")
    def supporting_detail(identity: str, request: Request) -> dict:
        resource = request.url.path.split("/")[-2]
        table = {
            "notes": "notes",
            "tasks": "tasks",
            "references": "file_references",
            "links": "web_links",
            "chats": "agent_chats",
            "github": "github_refs",
        }[resource]
        with db.read() as c:
            row = c.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Supporting record not found")
            return support_record(row)

    @app.post("/api/v1/notes")
    def create_note(request: Request, body: m.NoteCreate) -> dict:
        return apply(request, "note.create", body.model_dump(), create=True)

    @app.patch("/api/v1/notes/{identity}")
    def update_note(identity: str, request: Request, body: m.NoteUpdate) -> dict:
        return apply(request, "note.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/tasks")
    def create_task(request: Request, body: m.TaskCreate) -> dict:
        return apply(request, "task.create", body.model_dump(), create=True)

    @app.patch("/api/v1/tasks/{identity}")
    def update_task(identity: str, request: Request, body: m.TaskUpdate) -> dict:
        return apply(request, "task.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/references")
    def create_reference(request: Request, body: m.ReferenceCreate) -> dict:
        return apply(request, "reference.create", body.model_dump(), create=True)

    @app.post("/api/v1/links")
    def create_link(request: Request, body: m.LinkCreate) -> dict:
        return apply(request, "link.create", body.model_dump(), create=True)

    @app.patch("/api/v1/links/{identity}")
    def update_link(identity: str, request: Request, body: m.LinkUpdate) -> dict:
        return apply(request, "link.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/chats")
    def create_chat(request: Request, body: m.ChatCreate) -> dict:
        return apply(request, "chat.create", body.model_dump(), create=True)

    @app.patch("/api/v1/chats/{identity}")
    def update_chat(identity: str, request: Request, body: m.ChatUpdate) -> dict:
        return apply(request, "chat.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/github")
    def create_github_ref(request: Request, body: m.GithubRefCreate) -> dict:
        return apply(request, "github.create", body.model_dump(), create=True)

    @app.patch("/api/v1/github/{identity}")
    def update_github_ref(identity: str, request: Request, body: m.GithubRefUpdate) -> dict:
        return apply(request, "github.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/{resource}/{identity}/{verb}")
    def support_archive(resource: str, identity: str, verb: str, request: Request, body: m.Expected) -> dict:
        kind = {
            "notes": "note",
            "tasks": "task",
            "references": "reference",
            "attachments": "attachment",
            "links": "link",
            "chats": "chat",
            "github": "github",
        }.get(resource)
        if not kind or verb not in {"archive", "recover"}:
            raise AppError(404, "not_found", "Unknown supporting-record command")
        return apply(request, kind + "." + verb, {"id": identity, **body.model_dump()})

    @app.get("/api/v1/attachments")
    def attachments(request: Request, ownerType: str, ownerId: str, archived: bool = False) -> dict:
        with db.read() as c:
            if ownerType == "proposal":
                proposals.detail(ownerId, request.state.actor)
            else:
                work.owner(c, ownerType, ownerId)
            records = [support_record(r) for r in c.execute("SELECT id,owner_type,owner_id,name,mime,size,sha256,archived,revision,created_at FROM attachments WHERE owner_type=? AND owner_id=? AND archived=?", (ownerType, ownerId, int(archived)))]
            return {"items": records, "total": len(records)}

    @app.post("/api/v1/attachments")
    async def upload(request: Request, ownerType: str, ownerId: str, name: str) -> dict:
        data = await bounded_body(request, FILE_LIMIT)
        payload = {"ownerType": ownerType, "ownerId": ownerId, "name": name, "sha256": hashlib.sha256(data).hexdigest()}
        return await run_in_threadpool(mutate, request, payload,
                                      lambda c, a: work.upload(c, a, ownerType, ownerId, name, request.headers.get("content-type", "application/octet-stream"), data), required_key=True)

    def download_response(content: bytes, name: str, mime: str = "application/octet-stream", inline: bool = False) -> Response:
        safe = "".join(ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\", "/"} else "_" for ch in name)
        response = Response(content, media_type=mime)
        response.headers["Content-Disposition"] = f"{'inline' if inline else 'attachment'}; filename=\"{safe}\"; filename*=UTF-8''{quote(name, safe='')}"
        return response

    @app.get("/api/v1/attachments/{identity}")
    def download(identity: str, request: Request, download: int = 0) -> Response:
        with db.read() as c:
            row = c.execute("SELECT * FROM attachments WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Attachment not found")
            if row["owner_type"] == "proposal":
                proposals.detail(row["owner_id"], request.state.actor)
            inline = not download and row["mime"] in {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp", "text/plain"}
            return download_response(row["content"], row["name"], row["mime"], inline)

    @app.get("/api/v1/roadmaps")
    def roadmap_list(archived: bool = False, personId: str | None = None) -> dict:
        result = plans.list(archived)
        items_list = result["items"] if isinstance(result, dict) else result
        with db.read() as c:
            if personId:
                linked_ids = {r[0] for r in c.execute("SELECT target_id FROM person_links WHERE target_type='roadmap' AND person_id=?", (personId,))}
                items_list = [r for r in items_list if r["id"] in linked_ids]
            people_map = work.target_people(c, "roadmap", [r["id"] for r in items_list])
            enriched = [{**r, "people": people_map.get(r["id"], [])} for r in items_list]
        return {"items": enriched, "total": len(enriched)}

    @app.post("/api/v1/roadmaps")
    def roadmap_create(request: Request, body: m.RoadmapCreate) -> dict:
        return apply(request, "roadmap.create", body.model_dump(), create=True)

    @app.get("/api/v1/roadmaps/{identity}")
    def roadmap_detail(identity: str) -> dict:
        result = plans.detail(identity)
        with db.read() as c:
            result.update(work.support(c, "roadmap", identity))
            for step in result["steps"]:
                step.update(work.support(c, "step", step["id"]))
            result["workItems"] = work.roadmap_items(c, identity)
            result["people"] = work.target_people(c, "roadmap", [identity]).get(identity, [])
            result["history"] = read_events(c, identity)
            result["imports"] = [{"id": r["id"], "name": r["name"], "sha256": r["sha256"], "createdAt": r["created_at"]} for r in c.execute("SELECT * FROM plan_imports WHERE roadmap_id=?", (identity,))]
        return result

    @app.post("/api/v1/roadmaps/{identity}/revisions")
    def roadmap_revise(identity: str, request: Request, body: m.RoadmapRevision) -> dict:
        return apply(request, "roadmap.revise", {"id": identity, **body.model_dump()}, create=True)

    @app.get("/api/v1/roadmaps/{identity}/revisions/{number}")
    def roadmap_version(identity: str, number: int) -> dict:
        return plans.version(identity, number)

    @app.post("/api/v1/roadmaps/{identity}/restore")
    def roadmap_restore(identity: str, request: Request, body: m.Restore) -> dict:
        return apply(request, "roadmap.restore", {"id": identity, **body.model_dump()})

    @app.post("/api/v1/roadmaps/{identity}/move")
    def roadmap_move(identity: str, request: Request, body: m.RoadmapMove) -> dict:
        return apply(request, "roadmap.move", {"id": identity, **body.model_dump()})

    # Explicit routes precede the generic support command in routing order below.
    @app.patch("/api/v1/steps/{identity}/progress")
    def progress(identity: str, request: Request, body: m.Progress) -> dict:
        return apply(request, "progress.set", {"id": identity, **body.model_dump()})

    @app.get("/api/v1/steps/{identity}")
    def step_detail(identity: str) -> dict:
        with db.read() as c:
            row = plans.get_step(c, identity)
            roadmap = plans._detail(c, row["roadmap_id"])
            current = next((s for s in roadmap["steps"] if s["id"] == identity), None)
            if current is None:
                current = {"id": row["id"], "title": row["title"], "description": row["description"], "parentId": row["parent_id"], "position": row["position"], "status": row["status"], "revision": row["revision"], "retired": True}
            return {**current, "roadmapId": row["roadmap_id"], **work.support(c, "step", identity)}

    @app.get("/api/v1/steps/{identity}/history")
    def step_history(identity: str) -> dict:
        return plans.history(identity)

    @app.post("/api/v1/imports/preview")
    async def import_preview(request: Request, name: str = "roadmap.html") -> dict:
        data = await bounded_body(request, HTML_LIMIT)
        return await run_in_threadpool(plans.preview, data, name, request.state.actor)

    @app.post("/api/v1/imports/{identity}/commit")
    def import_commit(identity: str, request: Request, body: m.ImportCommit) -> dict:
        payload = body.model_dump(exclude_none=True)
        def commit(c: sqlite3.Connection, actor: dict) -> dict:
            result = plans.import_commit(identity, payload, actor, conn=c)
            work.refresh_plans(c, result["id"])
            return result
        return mutate(request, payload, commit, required_key=True)

    @app.get("/api/v1/imports/{identity}/download")
    def original_import(identity: str, request: Request) -> Response:
        with db.read() as c:
            row = c.execute("SELECT * FROM plan_imports WHERE id=?", (identity,)).fetchone()
            if row and not row["roadmap_id"] and request.state.actor["kind"] == "agent" and row["actor_id"] != request.state.actor["id"]:
                raise AppError(404, "not_found", "Import not found")
        data, name = plans.import_download(identity)
        return download_response(data, name)

    def proposal_detail(identity: str, actor: dict) -> dict:
        result = proposals.detail(identity, actor)
        with db.read() as c:
            result.update(work.support(c, "proposal", identity))
        return result

    @app.get("/api/v1/proposals")
    def proposal_list(request: Request, status: str | None = None) -> dict:
        return proposals.list(request.state.actor, status)

    @app.get("/api/v1/proposals/{identity}")
    def proposal_show(identity: str, request: Request) -> dict:
        return proposal_detail(identity, request.state.actor)

    @app.post("/api/v1/proposals")
    def proposal_create(request: Request, body: m.ProposalCreate) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: proposals.create(p, a, conn=c), required_key=True)

    @app.patch("/api/v1/proposals/{identity}")
    def proposal_update(identity: str, request: Request, body: m.ProposalUpdate) -> dict:
        return proposals.update(identity, body.model_dump(exclude_unset=True), request.state.actor)

    @app.get("/api/v1/search")
    def search(q: str, archived: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return work.search(q, archived, limit, offset)

    @app.get("/api/v1/context")
    def context(request: Request, date: str | None = None) -> dict:
        with db.read() as c:
            current = today(setting(c, "timezone", "America/Chicago"))
        return {"day": work.day(date or current), "workItems": work.list(),
                "roadmaps": plans.list(), "people": work.people_list(), "proposals": proposals.list(request.state.actor)}

    @app.get("/api/v1/settings")
    def settings() -> dict:
        with db.read() as c:
            tz = setting(c, "timezone", "America/Chicago")
            creds = [{"id": r["id"], "label": r["label"], "createdAt": r["created_at"], "revoked": bool(r["revoked"])} for r in c.execute("SELECT id,label,created_at,revoked FROM credentials")]
        backups = list_backups(directory / "backups")
        size = db.path.stat().st_size
        return {"timezone": tz, "today": today(tz), "version": __version__, "credentials": creds,
                "backups": backups, "storage": {"databaseBytes": size, "backupBytes": sum(b.get("size", 0) for b in backups),
                                                 "warning": app.state.backup_error or ("Primary data exceeds 2 GB; review retained files and backup capacity" if size > 2_000_000_000 else None)}}

    @app.patch("/api/v1/settings")
    def settings_update(request: Request, body: m.SettingsUpdate) -> dict:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise AppError(422, "timezone", "Choose a valid IANA timezone") from None
        with db.transaction() as c:
            before = setting(c, "timezone")
            c.execute("UPDATE settings SET value=? WHERE key='timezone'", (json.dumps(body.timezone),))
            event(c, request.state.actor, "settings.timezone", "settings", before, body.timezone)
        return settings()

    @app.post("/api/v1/credentials")
    def credential_create(request: Request, body: m.CredentialCreate) -> dict:
        def issue(c: sqlite3.Connection, actor: dict) -> dict:
            label, token, identity = text(body.label, "label", required=True, limit=200), secrets.token_urlsafe(32), uid()
            c.execute("INSERT INTO credentials VALUES(?,?,?,?,0)", (identity, label, hashlib.sha256(token.encode()).hexdigest(), now()))
            event(c, actor, "credential.issue", identity, None, {"label": label})
            return {"id": identity, "label": label, "token": token}
        return mutate(request, body.model_dump(), issue, required_key=True, redact=("token",))

    @app.post("/api/v1/credentials/{identity}/revoke")
    def credential_revoke(identity: str, request: Request) -> dict:
        with db.transaction() as c:
            if not c.execute("UPDATE credentials SET revoked=1 WHERE id=?", (identity,)).rowcount:
                raise AppError(404, "not_found", "Credential does not exist")
            event(c, request.state.actor, "credential.revoke", identity, None, {"revoked": True})
        return {"ok": True}

    @app.post("/api/v1/backups")
    def manual_backup() -> dict:
        result = backup_database(db.path, directory / "backups")
        app.state.backup_error = None
        return result

    @app.get("/api/v1/export")
    def export() -> Response:
        return download_response(export_database(db.path), "worklog-export.zip", "application/zip")

    # Move broad command matcher behind all named routes to avoid shadowing them.
    generic = next(r for r in app.router.routes if getattr(r, "name", "") == "support_archive")
    app.router.routes.remove(generic)

    @app.get("/api/v1/people")
    def people_list(archived: bool = False, q: str = "") -> dict:
        return work.people_list(archived=archived, q=q)

    @app.post("/api/v1/people")
    def person_create(request: Request, body: m.PersonCreate) -> dict:
        return apply(request, "person.create", body.model_dump(), create=True)

    @app.get("/api/v1/people/{identity}")
    def person_detail(identity: str) -> dict:
        return work.person_detail(identity)

    @app.patch("/api/v1/people/{identity}")
    def person_update(identity: str, request: Request, body: m.PersonUpdate) -> dict:
        return apply(request, "person.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/people/{identity}/{verb}")
    def person_archive(identity: str, verb: str, request: Request, body: m.Expected) -> dict:
        if verb not in {"archive", "recover"}:
            raise AppError(404, "not_found", "Unknown person command")
        return apply(request, "person." + verb, {"id": identity, **body.model_dump()})

    @app.post("/api/v1/person-links")
    def person_link_create(request: Request, body: m.PersonLinkCreate) -> dict:
        return apply(request, "person.link", body.model_dump())

    @app.post("/api/v1/person-links/unlink")
    def person_link_remove(request: Request, body: m.PersonLinkRemove) -> dict:
        return apply(request, "person.unlink", body.model_dump())

    @app.post("/api/v1/roadmaps/{identity}/{verb}")
    def roadmap_archive(identity: str, verb: str, request: Request, body: m.Expected) -> dict:
        if verb not in {"archive", "recover"}:
            raise AppError(404, "not_found", "Unknown roadmap command")
        return apply(request, "roadmap." + verb, {"id": identity, **body.model_dump()})

    @app.post("/api/v1/proposals/{identity}/{verb}")
    def proposal_transition(identity: str, verb: str, request: Request, body: m.Expected) -> dict:
        if verb not in {"submit", "withdraw", "approve", "reject"}:
            raise AppError(404, "not_found", "Unknown proposal command")
        p = body.model_dump()
        return mutate(request, p, lambda c, a: proposals.transition(identity, verb, p, a, conn=c), required_key=verb == "approve")

    app.router.routes.append(generic)
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets", follow_symlink=False), name="assets")

    @app.get("/")
    def frontend() -> Response:
        if not (dist / "index.html").is_file():
            return Response("Worklog UI is not built. Run scripts/Setup.ps1 first.", status_code=503, media_type="text/plain")
        return FileResponse(dist / "index.html")

    return app
