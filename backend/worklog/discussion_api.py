"""HTTP boundary for communication. What agents may call here is in policy.ROUTES: archiving and
pinning a thread, releasing a request and reassigning a role stay the person's."""

from collections.abc import Callable
from typing import Literal

from fastapi import FastAPI, Query, Request

from . import discussion_models as m
from . import responses as out
from .db import Database
from .discussions import DiscussionService, communication_actor
from .registry import AgentRegistry


def install_discussion_routes(app: FastAPI, db: Database, mutate: Callable) -> None:
    service = DiscussionService(db)
    registry = AgentRegistry()

    def read(request: Request, fn: Callable) -> dict:
        # One snapshot keeps role owners, receipts, content and the feed's high-water mark consistent,
        # and a credential revoked since the middleware checked it is refused here, inside it.
        return db.snapshot(lambda c: fn(c, communication_actor(c, request.state.actor)))

    def write(request: Request, body: m.Command, fn: Callable) -> dict:
        return mutate(request, body.model_dump(), fn, required_key=True)

    @app.get("/api/v1/discussions/participants", response_model=out.Participants)
    def participants(request: Request) -> dict:
        return read(request, service.participants)

    # One participant: what it reports working on and the roles it owns, never its credentials or the
    # chats its runs report, which stay on the person's Agents page.
    @app.get("/api/v1/discussions/participants/{identity}", response_model=out.Participant | out.AgentProfile)
    def participant(identity: str, request: Request) -> dict:
        return read(request, lambda c, a: registry.profile(c, identity))

    @app.get("/api/v1/discussions/avatars", response_model=out.Avatars)
    def avatars(request: Request) -> dict:
        return read(request, service.avatars)

    @app.post("/api/v1/discussions/avatar", response_model=out.Participant)
    def choose_avatar(request: Request, body: m.AvatarChoice) -> dict:
        return write(request, body, lambda c, a: service.choose_avatar(c, a, body.model_dump()))

    @app.get("/api/v1/discussions/roles", response_model=out.DiscussionRoles)
    def roles(request: Request, contextType: m.RoleContextType | None = None,
              contextId: str | None = None, limit: int = Query(50, ge=1, le=200),
              offset: int = Query(0, ge=0)) -> dict:
        return read(request, lambda c, a: service.roles(c, contextType, contextId, limit, offset))

    @app.post("/api/v1/discussions/roles", response_model=out.DiscussionRole)
    def create_role(request: Request, body: m.RoleCreate) -> dict:
        return write(request, body, lambda c, a: service.create_role(c, a, body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/claim", response_model=out.DiscussionRole)
    def claim_role(identity: str, request: Request, body: m.RoleRevision) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "claim", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/release", response_model=out.DiscussionRole)
    def release_role(identity: str, request: Request, body: m.RoleRevision) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "release", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/offer", response_model=out.DiscussionRole)
    def offer_role(identity: str, request: Request, body: m.RoleOffer) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "offer", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/accept", response_model=out.DiscussionRole)
    def accept_role(identity: str, request: Request, body: m.RoleRevision) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "accept", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/decline", response_model=out.DiscussionRole)
    def decline_role(identity: str, request: Request, body: m.RoleRevision) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "decline", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/withdraw", response_model=out.DiscussionRole)
    def withdraw_role(identity: str, request: Request, body: m.RoleRevision) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "withdraw", body.model_dump()))

    @app.post("/api/v1/discussions/roles/{identity}/reassign", response_model=out.DiscussionRole)
    def reassign_role(identity: str, request: Request, body: m.RoleReassign) -> dict:
        return write(request, body, lambda c, a: service.change_role(c, a, identity, "reassign", body.model_dump()))

    @app.get("/api/v1/discussions/changes", response_model=out.DiscussionChanges)
    def changes(request: Request, cursor: str | None = None,
                contextType: m.ContextType | None = None, contextId: str | None = None,
                limit: int = Query(50, ge=1, le=200)) -> dict:
        return read(request, lambda c, a: service.changes(c, cursor=cursor, context_type=contextType, context_id=contextId, limit=limit))

    @app.get("/api/v1/discussions/resume", response_model=out.DiscussionResume)
    def resume(request: Request, contextType: m.ContextType | None = None,
               contextId: str | None = None, limit: int = Query(50, ge=1, le=200),
               offset: int = Query(0, ge=0)) -> dict:
        return read(request, lambda c, a: service.resume(c, a, context_type=contextType, context_id=contextId, limit=limit, offset=offset))

    @app.get("/api/v1/discussions/messages/{identity}", response_model=out.DiscussionMessage)
    def message(identity: str, request: Request) -> dict:
        return read(request, lambda c, a: service.message(c, identity, a))

    @app.post("/api/v1/discussions/messages/{identity}/acknowledge", response_model=out.DiscussionMessage)
    def acknowledge(identity: str, request: Request, body: m.Acknowledge) -> dict:
        return write(request, body, lambda c, a: service.acknowledge(c, a, identity, body.model_dump()))

    @app.post("/api/v1/discussions/messages/{identity}/cancel", response_model=out.DiscussionMessage)
    def cancel_request(identity: str, request: Request, body: m.Empty) -> dict:
        return write(request, body, lambda c, a: service.finish_request(c, a, identity, "cancel", body.model_dump()))

    @app.post("/api/v1/discussions/messages/{identity}/release", response_model=out.DiscussionMessage)
    def release_request(identity: str, request: Request, body: m.ReleaseRequest) -> dict:
        return write(request, body, lambda c, a: service.finish_request(c, a, identity, "release", body.model_dump()))

    @app.get("/api/v1/discussions", response_model=out.Discussions)
    def list_discussions(request: Request, view: Literal["all", "awaiting", "archived"] = "all",
                         q: str = "", contextType: m.ContextType | None = None,
                         contextId: str | None = None, repo: str | None = None, pinned: bool | None = None,
                         limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(request, lambda c, a: service.list(c, a, view=view, q=q, context_type=contextType, context_id=contextId,
                                                       repo=repo, pinned=pinned, limit=limit, offset=offset))

    @app.post("/api/v1/discussions", response_model=out.DiscussionStarted)
    def create_discussion(request: Request, body: m.DiscussionCreate) -> dict:
        return write(request, body, lambda c, a: service.create(c, a, body.model_dump()))

    @app.get("/api/v1/discussions/{identity}", response_model=out.DiscussionThread)
    def read_discussion(identity: str, request: Request, limit: int = Query(50, ge=1, le=200),
                        offset: int = Query(0, ge=0)) -> dict:
        return read(request, lambda c, a: service.read(c, a, identity, limit, offset))

    @app.post("/api/v1/discussions/{identity}/messages", response_model=out.DiscussionMessage)
    def post_message(identity: str, request: Request, body: m.MessageCreate) -> dict:
        return write(request, body, lambda c, a: service.post(c, a, identity, body.model_dump()))

    @app.post("/api/v1/discussions/{identity}/archive", response_model=out.DiscussionSummary)
    def archive_discussion(identity: str, request: Request, body: m.Archive) -> dict:
        return write(request, body, lambda c, a: service.archive(c, a, identity, body.model_dump()))

    @app.post("/api/v1/discussions/{identity}/pin", response_model=out.DiscussionSummary)
    def pin_discussion(identity: str, request: Request, body: m.Pin) -> dict:
        return write(request, body, lambda c, a: service.pin(c, a, identity, body.model_dump()))

    @app.post("/api/v1/discussions/{identity}/tags", response_model=out.DiscussionSummary)
    def tag_discussion(identity: str, request: Request, body: m.Context) -> dict:
        return write(request, body, lambda c, a: service.tag(c, a, identity, body.model_dump()))

    @app.post("/api/v1/discussions/{identity}/tags/remove", response_model=out.DiscussionSummary)
    def untag_discussion(identity: str, request: Request, body: m.Context) -> dict:
        return write(request, body, lambda c, a: service.untag(c, a, identity, body.model_dump()))
