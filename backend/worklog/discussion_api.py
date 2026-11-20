"""HTTP boundary for communication. What agents may call here is in policy.ROUTES: archiving and
pinning a thread, releasing a request and reassigning a role stay the person's."""

import sqlite3
from collections.abc import Callable, Iterable
from typing import Literal

from fastapi import FastAPI, Query, Request

from . import discussion_models as m
from . import responses as out
from .common import AppError, rows_in
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

    # One participant: what it reports working on, the roles it owns, and since v33 where to reach it (the
    # chat it works in, its open runs' chats, the directory it registered from); never its credentials,
    # tracked chat records or transcripts, which stay on the person's Agents and Chats pages.
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

    def delivering(request: Request, fn: Callable, shown: Callable[[sqlite3.Connection, dict], Iterable[str]]) -> dict:
        """A read that delivers the requests it shows the caller (v33): the slots of pending requests in
        the messages `shown` names that wait on the caller and that it had not seen are marked seen,
        after the snapshot, in a write of their own that happens only when there is one to mark. A
        busy database leaves them for the next read; the read itself never fails for it."""
        def snapshot(c: sqlite3.Connection) -> tuple[dict, list[str]]:
            actor = communication_actor(c, request.state.actor)
            result = fn(c, actor)
            return result, service.seen_candidates(c, actor, shown(c, result))

        result, unseen = db.snapshot(snapshot)
        if unseen:
            try:
                with db.transaction() as c:
                    service.record_seen(c, communication_actor(c, request.state.actor), unseen)
            except AppError as exc:
                if exc.code != "database_busy":
                    raise
        return result

    # The requests the caller sent or that wait on it, with how far each slot got (v33).
    @app.get("/api/v1/discussions/requests", response_model=out.DiscussionRequests)
    def requests(request: Request, box: Literal["sent", "received"] = "received",
                 state: Literal["pending", "ended", "all"] = "pending",
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return delivering(request, lambda c, a: service.requests(c, a, box=box, state=state, limit=limit, offset=offset),
                          lambda c, r: [item["message"]["id"] for item in r["items"]] if box == "received" else [])

    # What waits on the caller and what came back to it since a cursor; `worklog agent inbox --wait`
    # polls it (v33).
    @app.get("/api/v1/discussions/inbox", response_model=out.DiscussionInbox)
    def inbox(request: Request, since: str | None = Query(None, max_length=2000)) -> dict:
        return delivering(request, lambda c, a: service.inbox(c, a, since=since),
                          lambda c, r: [item["message"]["id"] for item in r["received"]])

    @app.get("/api/v1/discussions/messages/{identity}", response_model=out.DiscussionMessage)
    def message(identity: str, request: Request) -> dict:
        return delivering(request, lambda c, a: service.message(c, identity, a),
                          lambda c, r: [r["id"]] if r["request"] else [])

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
        def pending(c: sqlite3.Connection, result: dict) -> list[str]:
            # Listing what awaits the caller shows it the pending requests in those threads.
            ids = [item["id"] for item in result["items"]] if view == "awaiting" else []
            return [row[0] for row in rows_in(c, "SELECT m.id FROM discussion_messages m JOIN discussion_requests r ON "
                                                 "r.message_id=m.id WHERE r.state='pending' AND m.discussion_id IN ({})", ids)]

        return delivering(request, lambda c, a: service.list(c, a, view=view, q=q, context_type=contextType, context_id=contextId,
                                                             repo=repo, pinned=pinned, limit=limit, offset=offset), pending)

    @app.post("/api/v1/discussions", response_model=out.DiscussionStarted)
    def create_discussion(request: Request, body: m.DiscussionCreate) -> dict:
        return write(request, body, lambda c, a: service.create(c, a, body.model_dump()))

    @app.get("/api/v1/discussions/{identity}", response_model=out.DiscussionThread)
    def read_discussion(identity: str, request: Request, limit: int = Query(50, ge=1, le=200),
                        offset: int = Query(0, ge=0)) -> dict:
        return delivering(request, lambda c, a: service.read(c, a, identity, limit, offset),
                          lambda c, r: [message["id"] for message in r["messages"] if message["request"]])

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
