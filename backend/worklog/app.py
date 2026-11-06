"""Single-origin local HTTP application. Importing this module starts no service."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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
from starlette.concurrency import run_in_threadpool

from . import __version__, operations, policy, support
from . import models as m
from .active_sessions import running_sessions
from .agent_link_api import install_agent_link_routes
from .agent_links import ties_of_agent, visible_history
from .chat_message_api import install_chat_message_routes
from .common import (
    FILE_LIMIT,
    HTML_LIMIT,
    AppError,
    event,
    lone_surrogate,
    now,
    read_events,
    setting,
    text,
    today,
    uid,
)
from .coordination import CoordinationService, agents_may_register, close_credential_runs
from .coordination_api import install_coordination_routes
from .db import Database
from .discussion_api import install_discussion_routes
from .discussions import bind_credential, communication_actor, registered_by, registered_chats
from .maintenance import prune_receipts
from .plans import PlanService
from .proposals import ProposalService, approval_required
from .records import check_owner, people_on, support_record
from .recovery import automatic_backup, backup_database, export_database, list_backups
from .registration import (
    active_registrations,
    install_registration_routes,
    revoke_registered,
)
from .registry_api import install_registry_routes
from .release_api import install_release_routes
from .releases import ReleaseService
from .reminder_api import install_reminder_routes
from .resource_api import install_resource_routes
from .resources import ResourceService
from .search import SearchType, global_search, index_plans
from .services import WorkService
from .ticket_api import install_ticket_routes
from .tickets import TicketService
from .transcripts import MESSAGE_LIMIT, TranscriptReader, TranscriptRoots

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


def create_app(data_dir: str | Path | None = None, *, testing: bool = False,
               transcript_roots: TranscriptRoots | None = None) -> FastAPI:
    directory = Path(data_dir) if data_dir else default_data_directory()
    db = Database(directory / "worklog.sqlite3")
    db.initialize()
    plans = PlanService(db)
    work = WorkService(db, plans)
    # Tests read only the transcripts they write; they never see this machine's own.
    transcripts = TranscriptReader(transcript_roots or (TranscriptRoots() if testing else TranscriptRoots.from_environment()))

    def dispatch(conn: sqlite3.Connection, actor: dict, action: str, payload: dict) -> dict:
        # A proposal's operation passes the same request model the person's own route takes,
        # then the same domain mutation; one that operations.py doesn't define never runs. Only
        # an approved proposal's change comes this way, which is what lets it write a resource.
        operations.validate(action, payload)
        return work.apply(conn, actor, action, payload, proposed=True)

    proposals = ProposalService(db, dispatch)
    work.proposals = proposals
    app = FastAPI(title="Worklog Local API", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url="/api/v1/openapi.json")
    app.state.db, app.state.work, app.state.plans, app.state.proposals = db, work, plans, proposals
    app.state.transcripts = transcripts
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
    # Receipts only serve retries: each start drops those past the retention window, and the
    # serve watcher repeats it hourly. A busy database leaves them to the watcher rather than
    # stopping the start.
    try:
        with db.transaction() as conn:
            prune_receipts(conn)
    except AppError as exc:
        if exc.code != "database_busy":
            raise
        logger.warning("Receipts were not pruned at startup: the database was busy")

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
                    # The scope policy.ROUTES gives the route decides. A call no route serves reaches no
                    # handler: only a read goes on to the router, and not one it would redirect to the
                    # person's page.
                    granted = policy.decide(app.router.routes, request.scope)
                    if not (policy.agent_may(granted) if granted is not None
                            else policy.passes_unserved(app.router.routes, request.scope)):
                        raise AppError(403, "agent_scope", "This call is the person's. Agents read, propose changes, discuss, "
                                       "file tickets, report their own work and take their inbox")
                    request.state.agent_scope = granted
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
                    # Refused here, for every route and field at once: past this point a lone
                    # surrogate fails deep in validation or in SQLite, as a 500.
                    broken = lone_surrogate(body)
                    if broken is not None:
                        raise AppError(422, "validation", "The text holds a broken character, a lone UTF-16 surrogate; "
                                       "remove it and retry", {broken: "invalid"})
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
               redact: tuple[str, ...] = (), communication: bool = False,
               receipt_if: Callable[[dict], bool] | None = None) -> dict:
        auto_backup()
        key = request.headers.get("idempotency-key")
        if required_key and not key:
            raise AppError(428, "idempotency", "An Idempotency-Key is required for this command")
        if key is not None and not 8 <= len(key) <= 200:
            raise AppError(422, "idempotency", "Use an idempotency key between 8 and 200 characters")
        digest = hashlib.sha256((request.method + str(request.url.path) + "?" + str(request.url.query) + json.dumps(payload, sort_keys=True)).encode()).hexdigest()
        actor = request.state.actor
        with db.transaction() as c:
            if actor["kind"] == "agent" and getattr(request.state, "agent_scope", None) in {policy.COORDINATE, policy.INBOX}:
                # Middleware authentication precedes the write lock; revoke may win that race.
                # Recheck before receipt replay as well as before a new mutation.
                communication_actor(c, actor)
            if communication:
                actor = communication_actor(c, actor)
            receipt_actor = "discussion:" + actor["id"] if communication else actor["id"]
            if key:
                receipt = c.execute("SELECT * FROM receipts WHERE actor_id=? AND key=?", (receipt_actor, key)).fetchone()
                if receipt:
                    if receipt["request_hash"] != digest:
                        raise AppError(409, "idempotency_conflict", "This key was already used for a different request")
                    return json.loads(receipt["response_json"])
            result = fn(c, actor)
            if key and (receipt_if is None or receipt_if(result)):
                stored = {**result, **dict.fromkeys(redact)}
                c.execute("INSERT INTO receipts(actor_id,key,request_hash,response_json,created_at) VALUES(?,?,?,?,?)",
                          (receipt_actor, key, digest, json.dumps(stored), now()))
            return result

    def discussion_mutate(request: Request, payload: Any, fn: Callable, *, required_key: bool = True) -> dict:
        return mutate(request, payload, fn, required_key=True, communication=True)

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
    def items(view: str = "all", archived: bool = False,
              status: list[str] | None = Query(None, description="Repeat to match any of several states"),
              roadmapId: str | None = None, personId: str | None = None, q: str = "", repositoryId: str | None = None,
              limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return work.items.list(view=view, archived=archived, states=status, roadmap_id=roadmapId, person_id=personId, limit=limit, offset=offset, q=q, repository_id=repositoryId)

    @app.post("/api/v1/work-items")
    def create_item(request: Request, body: m.ItemCreate) -> dict:
        return apply(request, "item.create", body.model_dump(), create=True)

    @app.post("/api/v1/work-items/reorder")
    def reorder_items(request: Request, body: m.Reorder) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.items.reorder(c, a, p))

    @app.get("/api/v1/work-items/{identity}")
    def item_detail(identity: str) -> dict:
        return work.items.detail(identity)

    @app.patch("/api/v1/work-items/{identity}")
    def update_item(identity: str, request: Request, body: m.ItemUpdate) -> dict:
        return apply(request, "item.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/work-items/{identity}/move")
    def move_item(identity: str, request: Request, body: m.Move) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.items.move_backlog(c, a, identity, p))

    @app.post("/api/v1/work-items/{identity}/rank")
    def rank_item(identity: str, request: Request, body: m.Rank) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.items.rank(c, a, identity, p))

    @app.post("/api/v1/work-items/{identity}/{verb}")
    def archive_item(identity: str, verb: str, request: Request, body: m.Expected) -> dict:
        if verb not in {"archive", "recover"}:
            raise AppError(404, "not_found", "Unknown command")
        return apply(request, "item." + verb, {**body.model_dump(), "id": identity})

    @app.get("/api/v1/days/{date}")
    def day(date: str) -> dict:
        return work.items.day(date)

    @app.put("/api/v1/days/{date}/entries/{item_id}")
    def update_entry(date: str, item_id: str, request: Request, body: m.EntryUpdate) -> dict:
        return apply(request, "entry.upsert", {**body.model_dump(exclude_unset=True), "date": date, "itemId": item_id})

    @app.post("/api/v1/days/{date}/entries/{item_id}/remove")
    def remove_entry(date: str, item_id: str, request: Request, body: m.Expected) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.items.remove_entry(c, a, date, item_id, p))

    @app.put("/api/v1/days/{date}/reflection")
    def reflection(date: str, request: Request, body: m.Reflection) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.items.reflection(c, a, date, p))

    @app.post("/api/v1/days/{date}/reorder")
    def reorder_day(date: str, request: Request, body: m.Reorder) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.items.reorder(c, a, p, date))

    @app.post("/api/v1/days/{date}/reschedule")
    def reschedule(date: str, request: Request, body: m.Reschedule) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: work.items.reschedule(c, a, date, p))

    def authorize_history(conn: sqlite3.Connection, identity: str, actor: dict) -> None:
        # A chat message's history holds its text and every recipient; agents receive only their own.
        if actor["kind"] == "agent" and conn.execute("SELECT 1 FROM chat_messages WHERE id=?", (identity,)).fetchone():
            raise AppError(403, "agent_scope", "A chat's messages stay with the person; agents receive theirs from the inbox")
        if conn.execute("SELECT 1 FROM proposals WHERE id=?", (identity,)).fetchone():
            proposals.detail(identity, actor)
        attachment = conn.execute("SELECT owner_type,owner_id FROM attachments WHERE id=?", (identity,)).fetchone()
        if attachment and attachment["owner_type"] == "proposal":
            proposals.detail(attachment["owner_id"], actor)

    @app.get("/api/v1/history")
    def history(request: Request, entityId: str, limit: int = Query(100, ge=1, le=200)) -> dict:
        with db.read() as c:
            authorize_history(c, entityId, request.state.actor)
            return {"items": visible_history(c, entityId, limit, request.state.actor)}

    @app.get("/api/v1/notes/{identity}")
    @app.get("/api/v1/tasks/{identity}")
    @app.get("/api/v1/references/{identity}")
    @app.get("/api/v1/links/{identity}")
    @app.get("/api/v1/chats/{identity}")
    @app.get("/api/v1/github/{identity}")
    @app.get("/api/v1/questions/{identity}")
    @app.get("/api/v1/decisions/{identity}")
    def supporting_detail(identity: str, request: Request) -> dict:
        resource = request.url.path.split("/")[-2]
        table = {
            "notes": "notes",
            "tasks": "tasks",
            "references": "file_references",
            "links": "web_links",
            "chats": "agent_chats",
            "github": "github_refs",
            "questions": "roadmap_questions",
            "decisions": "roadmap_decisions",
        }[resource]
        with db.read() as c:
            row = c.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Supporting record not found")
            return support.chat_records(c, [row])[0] if resource == "chats" else support_record(row)

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

    @app.post("/api/v1/tasks/{identity}/move")
    def move_task(identity: str, request: Request, body: m.TaskMove) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: support.move_task(c, a, identity, p))

    @app.post("/api/v1/references")
    def create_reference(request: Request, body: m.ReferenceCreate) -> dict:
        return apply(request, "reference.create", body.model_dump(), create=True)

    @app.post("/api/v1/links")
    def create_link(request: Request, body: m.LinkCreate) -> dict:
        return apply(request, "link.create", body.model_dump(), create=True)

    @app.patch("/api/v1/links/{identity}")
    def update_link(identity: str, request: Request, body: m.LinkUpdate) -> dict:
        return apply(request, "link.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.get("/api/v1/chats")
    def chat_list(archived: bool = False) -> dict:
        with db.read() as c:
            chats = support.chat_overview(c, archived)
        return {"items": [{**chat, "transcript": transcripts.summary(chat["chatId"])} for chat in chats]}

    @app.get("/api/v1/claude-sessions")
    def claude_sessions() -> dict:
        """The Claude Code sessions running on this machine, each with the chats tracking it and
        the agents tied to it, so Chats can suggest the ones nothing tracks yet."""
        found = running_sessions(transcripts.roots.claude_sessions)
        with db.read() as c:
            sessions = support.session_ties(c, found)
        items = []
        for session in sessions:
            title, model = transcripts.headline(session["sessionId"])
            # A session Claude Code has not named yet goes by its title, its desktop session's, or its folder.
            name = (session["name"] or title or transcripts.label(session["hostSessionId"] or "")
                    or re.split(r"[\\/]", session["cwd"].rstrip("\\/"))[-1])
            items.append({**session, "name": name, "model": model})
        return {"items": items}

    @app.get("/api/v1/chats/{identity}/transcript")
    def chat_transcript(identity: str, limit: int = Query(200, ge=1, le=MESSAGE_LIMIT), thread: str | None = None) -> dict:
        with db.read() as c:
            row = c.execute("SELECT * FROM agent_chats WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise AppError(404, "not_found", "Agent chat not found")
            chat = support.chat_records(c, [row])[0]
            labels = {r["chat_id"].strip().lower(): r["title"] for r in c.execute("SELECT chat_id,title FROM agent_chats WHERE archived=0")}
        return {"chat": chat, **transcripts.messages(chat["chatId"], limit=limit, thread=thread, labels=labels)}

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

    @app.post("/api/v1/questions")
    def create_question(request: Request, body: m.QuestionCreate) -> dict:
        return apply(request, "question.create", body.model_dump(), create=True)

    @app.patch("/api/v1/questions/{identity}")
    def update_question(identity: str, request: Request, body: m.QuestionUpdate) -> dict:
        return apply(request, "question.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/decisions")
    def create_decision(request: Request, body: m.DecisionCreate) -> dict:
        return apply(request, "decision.create", body.model_dump(), create=True)

    @app.patch("/api/v1/decisions/{identity}")
    def update_decision(identity: str, request: Request, body: m.DecisionUpdate) -> dict:
        return apply(request, "decision.update", {**body.model_dump(exclude_unset=True), "id": identity})

    def support_command(kind: str, verb: str) -> Callable:
        def command(identity: str, request: Request, body: m.Expected) -> dict:
            return apply(request, f"{kind}.{verb}", {"id": identity, **body.model_dump()})
        return command

    # Each supporting record archives and recovers by its own literal route, so no route has to be
    # moved behind the others to keep from shadowing them.
    for resource, kind in {"notes": "note", "tasks": "task", "references": "reference", "attachments": "attachment",
                           "links": "link", "chats": "chat", "github": "github", "questions": "question",
                           "decisions": "decision"}.items():
        for verb in ("archive", "recover"):
            app.add_api_route(f"/api/v1/{resource}/{{identity}}/{verb}", support_command(kind, verb),
                              methods=["POST"], name=f"{kind}_{verb}")

    @app.get("/api/v1/attachments")
    def attachments(request: Request, ownerType: str, ownerId: str, archived: bool = False) -> dict:
        with db.read() as c:
            if ownerType == "proposal":
                proposals.detail(ownerId, request.state.actor)
            else:
                check_owner(c, ownerType, ownerId, files=True)
            records = [support_record(r) for r in c.execute("SELECT id,owner_type,owner_id,name,mime,size,sha256,archived,revision,created_at FROM attachments WHERE owner_type=? AND owner_id=? AND archived=? ORDER BY created_at,id", (ownerType, ownerId, int(archived)))]
            return {"items": records, "total": len(records)}

    @app.post("/api/v1/attachments")
    async def upload(request: Request, ownerType: str, ownerId: str, name: str) -> dict:
        data = await bounded_body(request, FILE_LIMIT)
        payload = {"ownerType": ownerType, "ownerId": ownerId, "name": name, "sha256": hashlib.sha256(data).hexdigest()}
        return await run_in_threadpool(mutate, request, payload,
                                      lambda c, a: support.upload(c, a, ownerType, ownerId, name, request.headers.get("content-type", "application/octet-stream"), data, proposals), required_key=True)

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
    def roadmap_list(archived: bool = False, personId: str | None = None, q: str = Query("", max_length=500)) -> dict:
        result = plans.list(archived)
        items_list = result["items"] if isinstance(result, dict) else result
        # Roadmaps whose title holds every word, in any case: how a picker finds one to tie.
        words = q.casefold().split()
        if words:
            items_list = [r for r in items_list if all(word in r["title"].casefold() for word in words)]
        with db.read() as c:
            if personId:
                linked_ids = {r[0] for r in c.execute("SELECT target_id FROM person_links WHERE target_type='roadmap' AND person_id=?", (personId,))}
                items_list = [r for r in items_list if r["id"] in linked_ids]
            people_map = people_on(c, "roadmap", [r["id"] for r in items_list])
            enriched = [{**r, "people": people_map.get(r["id"], [])} for r in items_list]
        return {"items": enriched, "total": len(enriched)}

    @app.post("/api/v1/roadmaps")
    def roadmap_create(request: Request, body: m.RoadmapCreate) -> dict:
        return apply(request, "roadmap.create", body.model_dump(), create=True)

    @app.get("/api/v1/roadmaps/{identity}")
    def roadmap_detail(identity: str) -> dict:
        result = plans.detail(identity)
        with db.read() as c:
            result.update(support.for_owner(c, "roadmap", identity))
            for step in result["steps"]:
                step.update(support.for_owner(c, "step", step["id"]))
            result["workItems"] = work.items.roadmap_items(c, identity)
            result["people"] = people_on(c, "roadmap", [identity]).get(identity, [])
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
            return {**current, "roadmapId": row["roadmap_id"], **support.for_owner(c, "step", identity)}

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
            index_plans(c, result["id"])
            if payload.get("roadmapId"):
                proposals.notify_stale_proposals(c, result["id"])
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
            result.update(support.for_owner(c, "proposal", identity))
        return result

    @app.get("/api/v1/proposals")
    def proposal_list(request: Request, status: str | None = None) -> dict:
        return proposals.list(request.state.actor, status)

    @app.get("/api/v1/proposals/{identity}")
    def proposal_show(identity: str, request: Request) -> dict:
        return proposal_detail(identity, request.state.actor)

    # Each record's proposal history, the latest first: what was applied to a work item or
    # roadmap, and what an agent proposed. Agents read the first two; /agents is the person's.
    @app.get("/api/v1/work-items/{identity}/proposals")
    def item_proposals(identity: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return proposals.applied_to("item", identity, limit, offset)

    @app.get("/api/v1/roadmaps/{identity}/proposals")
    def roadmap_proposals(identity: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return proposals.applied_to("roadmap", identity, limit, offset)

    @app.get("/api/v1/agents/{identity}/proposals")
    def agent_proposals(identity: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return proposals.from_agent(identity, limit, offset)

    @app.post("/api/v1/proposals")
    def proposal_create(request: Request, body: m.ProposalCreate) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: proposals.create(p, a, conn=c), required_key=True)

    @app.patch("/api/v1/proposals/{identity}")
    def proposal_update(identity: str, request: Request, body: m.ProposalUpdate) -> dict:
        return proposals.update(identity, body.model_dump(exclude_unset=True), request.state.actor)

    @app.get("/api/v1/search")
    def search(q: str, archived: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               types: list[SearchType] = Query([], alias="type")) -> dict:
        with db.read() as c:
            return global_search(c, q, archived, limit, offset, types)

    @app.get("/api/v1/context")
    def context(request: Request, date: str | None = None, itemId: str | None = None,
                repositoryId: str | None = None, limit: int = Query(100, ge=1, le=200),
                offset: int = Query(0, ge=0)) -> dict:
        coordination = CoordinationService(db)
        actor = request.state.actor
        with db.read() as c:
            c.execute("BEGIN")
            current = today(setting(c, "timezone", "America/Chicago"))
            activity = {**coordination.context_summaries(c, item_id=itemId, repository_id=repositoryId, limit=limit, offset=offset),
                        # What the calling agent is tied to, and its role on each; the person has no ties.
                        "ties": (ties_of_agent(c, communication_actor(c, actor)["id"], actor) if actor["kind"] == "agent"
                                 else {"items": [], "total": 0}),
                        "participations": coordination.participations(c, item_id=itemId, repository_id=repositoryId, limit=limit, offset=offset),
                        "updates": coordination.updates(c, item_id=itemId, repository_id=repositoryId, limit=limit, offset=offset),
                        # Open, doing and blocked tickets, so an agent sees known issues before it starts.
                        "tickets": TicketService().list(c, item_id=itemId, repository_id=repositoryId, limit=limit, offset=offset),
                        # The person's reference pages by title and summary; `resources show` reads one.
                        "resources": ResourceService().list(c, limit=limit, offset=offset),
                        # Upcoming releases with their counts, or those holding the item; `releases show` reads one.
                        "releases": ReleaseService().list(c, target_type="item" if itemId else None, target_id=itemId,
                                                          limit=limit, offset=offset)}
        items_result = work.items.list(item_id=itemId, repository_id=repositoryId, limit=limit, offset=offset)
        return {"day": work.items.day(date or current), "workItems": items_result,
                "roadmaps": plans.list(), "people": work.people.list(), "proposals": proposals.list(request.state.actor),
                **activity}

    @app.get("/api/v1/settings")
    def settings() -> dict:
        with db.read() as c:
            tz = setting(c, "timezone", "America/Chicago")
            require_approval = approval_required(c)
            register_repositories = agents_may_register(c)
            rows = c.execute("SELECT c.id,c.label,c.created_at,c.revoked,b.agent_id FROM credentials c LEFT JOIN communication_credentials b ON b.credential_id=c.id").fetchall()
            sponsors = registered_by(c, [r["id"] for r in rows])
            chats = registered_chats(c, [r["id"] for r in rows])
            registered = active_registrations(c, [r["agent_id"] for r in rows if r["agent_id"]])
            creds = [{"id": r["id"], "label": r["label"], "createdAt": r["created_at"], "revoked": bool(r["revoked"]), "agentId": r["agent_id"],
                      "registeredBy": sponsors.get(r["id"]), "chatId": chats.get(r["id"]),
                      "registeredActive": registered.get(r["agent_id"], 0)} for r in rows]
            agents = [{"id": r["id"], "label": r["label"], "avatar": r["avatar"]} for r in c.execute("SELECT id,label,avatar FROM communication_agents ORDER BY label,id")]
        # Settings shows no hashes, and hashing would read every backup on each load.
        backups = list_backups(directory / "backups", hashed=False)
        size = db.path.stat().st_size
        return {"timezone": tz, "today": today(tz), "version": __version__, "requireProposalApproval": require_approval,
                "agentRepositoryRegistration": register_repositories,
                "credentials": creds, "agents": agents,
                "backups": backups, "storage": {"databaseBytes": size, "backupBytes": sum(b.get("size", 0) for b in backups),
                                                 "warning": app.state.backup_error or ("Primary data exceeds 2 GB; review retained files and backup capacity" if size > 2_000_000_000 else None)}}

    @app.patch("/api/v1/settings")
    def settings_update(request: Request, body: m.SettingsUpdate) -> dict:
        if body.timezone is not None:
            try:
                ZoneInfo(body.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                raise AppError(422, "timezone", "Choose a valid IANA timezone") from None
        with db.transaction() as c:
            if body.timezone is not None:
                before = setting(c, "timezone")
                c.execute("UPDATE settings SET value=? WHERE key='timezone'", (json.dumps(body.timezone),))
                event(c, request.state.actor, "settings.timezone", "settings", before, body.timezone)
            if body.requireProposalApproval is not None:
                # Agents never reach this route, so only the person can let their proposals skip review.
                was = approval_required(c)
                c.execute("INSERT INTO settings(key,value) VALUES('requireProposalApproval',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (json.dumps(body.requireProposalApproval),))
                event(c, request.state.actor, "settings.requireProposalApproval", "settings", was, body.requireProposalApproval)
            if body.agentRepositoryRegistration is not None:
                was = agents_may_register(c)
                c.execute("INSERT INTO settings(key,value) VALUES('agentRepositoryRegistration',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (json.dumps(body.agentRepositoryRegistration),))
                event(c, request.state.actor, "settings.agentRepositoryRegistration", "settings", was, body.agentRepositoryRegistration)
        return settings()

    @app.post("/api/v1/credentials")
    def credential_create(request: Request, body: m.CredentialCreate) -> dict:
        def issue(c: sqlite3.Connection, actor: dict) -> dict:
            label, token, identity = text(body.label, "label", required=True, limit=200), secrets.token_urlsafe(32), uid()
            c.execute("INSERT INTO credentials(id,label,token_hash,created_at,revoked) VALUES(?,?,?,?,0)",
                      (identity, label, hashlib.sha256(token.encode()).hexdigest(), now()))
            agent = bind_credential(c, identity, label, avatar=body.avatar)
            event(c, actor, "credential.issue", identity, None, {"label": label, "avatar": agent["avatar"]})
            return {"id": identity, "agentId": agent["id"], "label": label, "avatar": agent["avatar"], "token": token}
        return mutate(request, body.model_dump(), issue, required_key=True, redact=("token",))

    @app.post("/api/v1/credentials/{identity}/rotate")
    def credential_rotate(identity: str, request: Request, body: m.CredentialRotate) -> dict:
        def rotate(c: sqlite3.Connection, actor: dict) -> dict:
            previous = c.execute("SELECT * FROM credentials WHERE id=?", (identity,)).fetchone()
            if previous is None:
                raise AppError(404, "not_found", "Credential does not exist")
            binding = c.execute("SELECT agent_id FROM communication_credentials WHERE credential_id=?", (identity,)).fetchone()
            if binding is None:
                raise AppError(409, "identity_missing", "Credential has no communication identity")
            label = text(body.label if body.label is not None else previous["label"], "label", required=True, limit=200)
            token, replacement = secrets.token_urlsafe(32), uid()
            # Also handles reissue after an offline restore. A stale older token cannot
            # leave another live credential for the same identity behind.
            for linked in c.execute("SELECT credential_id FROM communication_credentials WHERE agent_id=?", (binding["agent_id"],)).fetchall():
                close_credential_runs(c, actor, linked["credential_id"], "credential_rotated")
            c.execute("UPDATE credentials SET revoked=1 WHERE id IN (SELECT credential_id FROM communication_credentials WHERE agent_id=?)", (binding["agent_id"],))
            c.execute("INSERT INTO credentials(id,label,token_hash,created_at,revoked) VALUES(?,?,?,?,0)",
                      (replacement, label, hashlib.sha256(token.encode()).hexdigest(), now()))
            agent = bind_credential(c, replacement, label, agent_id=binding["agent_id"])
            event(c, actor, "credential.rotate", replacement, {"credentialId": identity}, {"agentId": agent["id"], "label": label, "avatar": agent["avatar"]})
            return {"id": replacement, "agentId": agent["id"], "label": label, "token": token}
        return mutate(request, body.model_dump(), rotate, required_key=True, redact=("token",))

    @app.post("/api/v1/credentials/{identity}/revoke")
    def credential_revoke(identity: str, request: Request) -> dict:
        with db.transaction() as c:
            if not c.execute("UPDATE credentials SET revoked=1 WHERE id=?", (identity,)).rowcount:
                raise AppError(404, "not_found", "Credential does not exist")
            close_credential_runs(c, request.state.actor, identity)
            event(c, request.state.actor, "credential.revoke", identity, None, {"revoked": True})
            # Containment: what this identity registered loses access with it. Rotation keeps it.
            registered = revoke_registered(c, request.state.actor, identity)
        return {"ok": True, "registeredRevoked": registered}

    @app.post("/api/v1/backups")
    def manual_backup() -> dict:
        result = backup_database(db.path, directory / "backups")
        app.state.backup_error = None
        return result

    @app.get("/api/v1/export")
    def export() -> Response:
        return download_response(export_database(db.path), "worklog-export.zip", "application/zip")

    @app.get("/api/v1/people")
    def people_list(archived: bool = False, q: str = "") -> dict:
        return work.people.list(archived=archived, q=q)

    @app.post("/api/v1/people")
    def person_create(request: Request, body: m.PersonCreate) -> dict:
        return apply(request, "person.create", body.model_dump(), create=True)

    @app.get("/api/v1/people/{identity}")
    def person_detail(identity: str) -> dict:
        return work.people.detail(identity)

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
        if verb not in {"submit", "withdraw", "reopen", "approve", "reject"}:
            raise AppError(404, "not_found", "Unknown proposal command")
        p = body.model_dump()

        def transition(c: sqlite3.Connection, actor: dict) -> dict:
            if actor["kind"] == "agent":
                # Middleware authentication precedes the write lock, and with approval off a submit
                # applies the proposal, so a revoke that wins that race must stop it here.
                communication_actor(c, actor)
            return proposals.transition(identity, verb, p, actor, conn=c)
        return mutate(request, p, transition, required_key=verb == "approve")

    install_coordination_routes(app, db, mutate)
    install_chat_message_routes(app, db, mutate)
    install_ticket_routes(app, db, mutate)
    install_agent_link_routes(app, db, mutate)
    install_resource_routes(app, db, mutate)
    install_release_routes(app, db, apply)
    install_reminder_routes(app, db, mutate)
    install_discussion_routes(app, db, discussion_mutate)
    install_registration_routes(app, db, auto_backup)
    install_registry_routes(app, db, transcripts)
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets", follow_symlink=False), name="assets")

    @app.get("/")
    def frontend() -> Response:
        if not (dist / "index.html").is_file():
            return Response("Worklog UI is not built. Run scripts/Setup.ps1 first.", status_code=503, media_type="text/plain")
        return FileResponse(dist / "index.html")

    return app
