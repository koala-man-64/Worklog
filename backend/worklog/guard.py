"""Every request's first checks, as plain ASGI (consolidation step 3.1, REM-22).

The guard answers before any route does. A request must come to Worklog's own address and from no
foreign browser origin. An API request needs a caller: an agent's credential, or the person's
session, CSRF-checked for a write. policy.ROUTES decides what an agent may call. A JSON body is read
here, refused as soon as it passes its limit, and handed on to the route unchanged, so the route's
parser never allocates past it; uploads, HTML imports and the session bootstrap stream to their own
handlers, which keep their own limits. Every response, an error included, carries the security
headers. The guard checks the path the router matches, so a root_path cannot walk past it.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections.abc import Iterable
from typing import Any

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import policy
from .common import AppError, lone_surrogate, uid
from .db import Database

logger = logging.getLogger("worklog")

JSON_LIMIT = 8_000_000
# Their handlers read these bodies themselves, each to its own limit (common.FILE_LIMIT and HTML_LIMIT).
STREAMED = {"/api/v1/attachments", "/api/v1/imports/preview"}
# Starts the person's session, checking its own caller, so it needs none.
BOOTSTRAP = "/api/v1/session/bootstrap"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                               "font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; "
                               "form-action 'self'",
}


def error_response(exc: AppError) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "message": exc.message,
                                  **({"fields": exc.fields} if exc.fields is not None else {})}},
                        status_code=exc.status, headers={"Retry-After": "1"} if exc.code == "database_busy" else None)


def route_path(scope: Scope) -> str:
    """The path the router matches: the request's path without the app's root_path, taken as
    Starlette's router takes it."""
    path: str = scope["path"]
    root: str = scope.get("root_path", "")
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    return path[len(root):] if path[len(root)] == "/" else path


def cookies(headers: Headers) -> dict[str, str]:
    """The request's cookies as Starlette's Request.cookies reads them: from every Cookie header, a
    later one winning."""
    found: dict[str, str] = {}
    for header in headers.getlist("cookie"):
        found.update(cookie_parser(header))
    return found


class Disconnected(Exception):
    """The client left before its body arrived, so nothing is answered."""


async def read_body(receive: Receive, headers: Headers, maximum: int) -> bytes:
    """A request's whole body, refused with 413 as soon as it passes maximum, before the rest is read.
    As in Starlette's Request.stream, a message that is neither body nor disconnect is passed over."""
    length = headers.get("content-length")
    if length:
        try:
            if int(length) < 0:
                raise ValueError()
            if int(length) > maximum:
                raise AppError(413, "too_large", f"Request exceeds {maximum:,} bytes")
        except ValueError:
            raise AppError(400, "content_length", "Invalid Content-Length") from None
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise Disconnected
        if message["type"] != "http.request":
            continue
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > maximum:
            raise AppError(413, "too_large", f"Request exceeds {maximum:,} bytes")
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def replay(body: bytes, receive: Receive) -> Receive:
    """The body read once, for whoever reads it next; after that the client's own channel, which
    tells of its disconnect."""
    delivered = False

    async def replayed() -> Message:
        nonlocal delivered
        if delivered:
            return await receive()
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replayed


class RequestGuard:
    """The checks above, wrapped around the app with app.add_middleware. owner is the FastAPI app,
    whose routes the policy matches and whose sessions the person's cookie names."""

    def __init__(self, app: ASGIApp, *, owner: Any, db: Database, hosts: Iterable[str], origins: Iterable[str]) -> None:
        self.app, self.owner, self.db = app, owner, db
        self.hosts, self.origins = frozenset(hosts), frozenset(origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = route_path(scope)
        started = False

        async def secured(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                headers["Cache-Control"] = "no-store" if path.startswith("/api/") else "no-cache"
            await send(message)

        try:
            await self.app(scope, await self.check(scope, receive, path), secured)
        except Disconnected:
            return
        except AppError as exc:
            if started:
                raise
            await error_response(exc)(scope, receive, secured)
        except Exception:
            if started:
                raise
            # Unhandled failures still get the error envelope and the security headers.
            incident = uid()
            logger.exception("Unhandled request failure (incident %s)", incident)
            await error_response(AppError(500, "internal_error", f"Unexpected server error. Reference {incident}."))(
                scope, receive, secured)

    async def check(self, scope: Scope, receive: Receive, path: str) -> Receive:
        """Refuse the request with an AppError, or give the receive its route reads the body from."""
        headers = Headers(scope=scope)
        if headers.get("host", "") not in self.hosts:
            raise AppError(400, "host", "Use the local Worklog address")
        method = scope["method"]
        supplied_origin = headers.get("origin")
        if supplied_origin and supplied_origin not in self.origins:
            raise AppError(403, "origin", "Foreign browser origins are not allowed")
        if not path.startswith("/api/") or path == BOOTSTRAP:
            return receive
        # Request.state reads this. The guard sets both on every API call: the actor every handler acts
        # as, and the agent scope mutate() reads, None for the person.
        state = scope.setdefault("state", {})
        authorization = headers.get("authorization", "")
        if authorization:
            if not authorization.startswith("Bearer "):
                raise AppError(401, "authentication", "Use a valid credential")
            digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
            with self.db.read() as conn:
                credential = conn.execute("SELECT * FROM credentials WHERE token_hash=? AND revoked=0", (digest,)).fetchone()
            if credential is None:
                raise AppError(401, "authentication", "Credential is invalid or revoked")
            actor = {"id": credential["id"], "label": credential["label"], "kind": "agent"}
            # The scope policy.ROUTES gives the route decides. A call no route serves reaches no
            # handler: only a read goes on to the router, and not one it would redirect to the
            # person's page.
            routes = self.owner.router.routes
            granted = policy.decide(routes, scope)
            if not (policy.agent_may(granted) if granted is not None else policy.passes_unserved(routes, scope)):
                raise AppError(403, "agent_scope", "This call is the person's. Agents read, propose changes, discuss, "
                               "file tickets, report their own work and take their inbox")
            state["agent_scope"] = granted
        else:
            session = self.owner.state.sessions.get(cookies(headers).get("worklog_session", ""))
            if not session or session["expires"] < time.time():
                raise AppError(401, "session", "Open Worklog to establish a local session")
            actor = {"id": "local-user", "label": "You", "kind": "human"}
            state["agent_scope"] = None
            if method not in {"GET", "HEAD"}:
                # Compare bytes: compare_digest rejects non-ASCII str input with a TypeError.
                if supplied_origin not in self.origins or not secrets.compare_digest(
                        headers.get("x-csrf-token", "").encode(), session["csrf"].encode()):
                    raise AppError(403, "csrf", "Refresh Worklog before saving")
        state["actor"] = actor
        if method in {"GET", "HEAD"} or path in STREAMED:
            return receive
        # Bound JSON before the route's parser can allocate arbitrarily.
        body = await read_body(receive, headers, JSON_LIMIT)
        # Refused here, for every route and field at once: past this point a lone surrogate fails
        # deep in validation or in SQLite, as a 500.
        broken = lone_surrogate(body)
        if broken is not None:
            raise AppError(422, "validation", "The text holds a broken character, a lone UTF-16 surrogate; "
                           "remove it and retry", {broken: "invalid"})
        return replay(body, receive)
