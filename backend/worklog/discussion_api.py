"""HTTP boundary for communication; work/proposal authorization is unchanged."""

import re
from collections.abc import Callable
from typing import Literal

from fastapi import FastAPI, Query, Request

from . import discussion_models as m
from .db import Database
from .discussions import DiscussionService, communication_actor

_READ_PATHS = {
    "/discussions", "/discussions/participants", "/discussions/roles",
    "/discussions/changes", "/discussions/resume",
}


def agent_route_allowed(method: str, path: str) -> bool:
    """Use exact segment patterns: archive/release-request/reassign stay human-only."""
    suffix = path.removeprefix("/api/v1")
    if method == "GET":
        return (suffix in _READ_PATHS or
                re.fullmatch(r"/discussions/[^/]+", suffix) is not None or
                re.fullmatch(r"/discussions/messages/[^/]+", suffix) is not None)
    if method != "POST":
        return False
    return (suffix in {"/discussions", "/discussions/roles"} or
            re.fullmatch(r"/discussions/[^/]+/messages", suffix) is not None or
            re.fullmatch(r"/discussions/messages/[^/]+/(?:acknowledge|cancel)", suffix) is not None or
            re.fullmatch(r"/discussions/roles/[^/]+/(?:claim|release|offer|accept|decline|withdraw)", suffix) is not None)


def install_discussion_routes(app: FastAPI, db: Database, mutate: Callable) -> None:
    service = DiscussionService(db)

    def read(request: Request, fn: Callable) -> dict:
        with db.read() as c:
            # BEGIN keeps role owners, receipts, content and feed high-water mark
            # in one consistent snapshot. Closing ends the read transaction.
            c.execute("BEGIN")
            return fn(c, communication_actor(c, request.state.actor))

    def write(request: Request, body: m.Command, fn: Callable) -> dict:
        return mutate(request, body.model_dump(), fn, required_key=True)

    @app.get("/api/v1/discussions/participants")
    def participants(request: Request) -> dict:
        return read(request, service.participants)

    @app.get("/api/v1/discussions/roles")
    def roles(request: Request, contextType: m.ContextType | None = None,
              contextId: str | None = None, limit: int = Query(50, ge=1, le=200),
              offset: int = Query(0, ge=0)) -> dict:
        return read(request, lambda c, a: service.roles(c, contextType, contextId, limit, offset))

    @app.post("/api/v1/discussions/roles")
    def create_role(request: Request, body: m.RoleCreate) -> dict:
        return write(request, body, lambda c, a: service.create_role(c, a, body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/claim")
    def claim_role(identity: str, request: Request, body: m.RoleRevision) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "claim", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/release")
    def release_role(identity: str, request: Request, body: m.RoleRevision) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "release", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/offer")
    def offer_role(identity: str, request: Request, body: m.RoleOffer) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "offer", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/accept")
    def accept_role(identity: str, request: Request, body: m.RoleRevision) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "accept", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/decline")
    def decline_role(identity: str, request: Request, body: m.RoleRevision) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "decline", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/withdraw")
    def withdraw_role(identity: str, request: Request, body: m.RoleRevision) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "withdraw", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/reassign")
    def reassign_role(identity: str, request: Request, body: m.RoleReassign) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "reassign", body.model_dump()))

    @app.get("/api/v1/discussions/changes")
    def changes(request: Request, cursor: str | None = None,
                contextType: m.ContextType | None = None, contextId: str | None = None,
                limit: int = Query(50, ge=1, le=200)) -> dict:
        return read(request, lambda c, a: service.changes(c, cursor=cursor, context_type=contextType, context_id=contextId, limit=limit))

    @app.get("/api/v1/discussions/resume")
    def resume(request: Request, contextType: m.ContextType | None = None,
               contextId: str | None = None, limit: int = Query(50, ge=1, le=200),
               offset: int = Query(0, ge=0)) -> dict:
        return read(request, lambda c, a: service.resume(c, a, context_type=contextType, context_id=contextId, limit=limit, offset=offset))

    @app.get("/api/v1/discussions/messages/{identity}")
    def message(identity: str, request: Request) -> dict:
        return read(request, lambda c, a: service.message(c, identity, a))

    @app.post("/api/v1/discussions/messages/{identity}/acknowledge")
    def acknowledge(identity: str, request: Request, body: m.Acknowledge) -> dict:
        return write(request, body, lambda c, a: service.acknowledge(c, a, identity, body.model_dump()))

    @app.post("/api/v1/discussions/messages/{identity}/cancel")
    def cancel_request(identity: str, request: Request, body: m.Empty) -> dict:
        return write(request, body, lambda c, a: service.finish_request(c, a, identity, "cancel", body.model_dump()))

    @app.post("/api/v1/discussions/messages/{identity}/release")
    def release_request(identity: str, request: Request, body: m.ReleaseRequest) -> dict:
        return write(request, body, lambda c, a: service.finish_request(c, a, identity, "release", body.model_dump()))

    @app.get("/api/v1/discussions")
    def list_discussions(request: Request, view: Literal["all", "awaiting", "archived"] = "all",
                         q: str = "", contextType: m.ContextType | None = None,
                         contextId: str | None = None, repo: str | None = None,
                         limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(request, lambda c, a: service.list(c, a, view=view, q=q, context_type=contextType, context_id=contextId, repo=repo, limit=limit, offset=offset))

    @app.post("/api/v1/discussions")
    def create_discussion(request: Request, body: m.DiscussionCreate) -> dict:
        return write(request, body, lambda c, a: service.create(c, a, body.model_dump()))

    @app.get("/api/v1/discussions/{identity}")
    def read_discussion(identity: str, request: Request, limit: int = Query(50, ge=1, le=200),
                        offset: int = Query(0, ge=0)) -> dict:
        return read(request, lambda c, a: service.read(c, a, identity, limit, offset))

    @app.post("/api/v1/discussions/{identity}/messages")
    def post_message(identity: str, request: Request, body: m.MessageCreate) -> dict:
        return write(request, body, lambda c, a: service.post(c, a, identity, body.model_dump()))

    @app.post("/api/v1/discussions/{identity}/archive")
    def archive_discussion(identity: str, request: Request, body: m.Archive) -> dict:
        return write(request, body, lambda c, a: service.archive(c, a, identity, body.model_dump()))
