"""Authenticated delivery to a connector enrolled for its own chat."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import Field, StrictBool

from .common import AppError
from .db import Database
from .models import Command
from .push import DeliveryService
from .responses import Answer

HEARTBEAT_SECONDS = 20


class PushEnroll(Command):
    provider: Literal["claude", "codex"]
    chatId: str = Field(min_length=1, max_length=500)


class PushBinding(Command):
    enrollmentId: str = Field(min_length=1, max_length=200)


class PushSubscription(PushBinding):
    kind: Literal["ticket", "item", "run"]
    ref: str = Field(min_length=1, max_length=500)
    enabled: StrictBool


class PushAttempt(PushBinding):
    attemptId: str = Field(min_length=1, max_length=200)


class PushReceipt(PushAttempt):
    outcome: Literal["submitted", "queued", "unavailable", "uncertain"]
    resumeWarning: StrictBool = False


class PushEnrollment(Answer):
    id: str
    provider: Literal["claude", "codex"]
    chatId: str
    enabled: bool
    createdAt: str
    updatedAt: str


class PushWatch(Answer):
    id: str
    enrollmentId: str
    subjectType: Literal["ticket", "item", "run"]
    subjectId: str
    enabled: bool
    createdAt: str
    updatedAt: str


class PushDelivery(Answer):
    id: str
    seq: int
    kind: Literal["discussion.ask", "discussion.reply", "ticket.status", "item.status", "run.end", "run.update"]
    subjectType: Literal["discussion", "ticket", "item", "run"]
    subjectId: str
    discussionId: str | None
    messageId: str | None
    sourceEventId: str
    recipientAgentId: str
    state: Literal["pending", "claimed", "submitted", "queued", "unavailable", "uncertain", "cancelled"]
    attemptCount: int
    attemptId: str | None
    claimExpiresAt: str | None
    createdAt: str
    updatedAt: str
    outcomeAt: str | None
    resumeWarning: bool


class PushPage(Answer):
    items: list[PushDelivery]
    hasMore: bool
    nextSeq: int


class PushClaim(Answer):
    acquired: bool
    delivery: PushDelivery


def _event(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def install_push_routes(app: FastAPI, db: Database, mutate: Callable) -> None:
    service = DeliveryService()

    @app.post("/api/v1/push/enroll", response_model=PushEnrollment)
    def enroll(request: Request, body: PushEnroll) -> dict:
        return mutate(request, body.model_dump(), lambda c, a: service.enroll(c, a, body.provider, body.chatId),
                      required_key=True)

    @app.get("/api/v1/push/enrollments/{identity}", response_model=PushEnrollment)
    def enrollment(identity: str, request: Request) -> dict:
        return db.snapshot(lambda c: service.enrollment(c, request.state.actor, identity))

    @app.post("/api/v1/push/disable", response_model=PushEnrollment)
    def disable(request: Request, body: PushBinding) -> dict:
        return mutate(request, body.model_dump(), lambda c, a: service.disable(c, a, body.enrollmentId),
                      required_key=True)

    @app.post("/api/v1/push/subscriptions", response_model=PushWatch)
    def subscribe(request: Request, body: PushSubscription) -> dict:
        return mutate(request, body.model_dump(),
                      lambda c, a: service.subscribe(c, a, body.enrollmentId, body.kind, body.ref, body.enabled),
                      required_key=True)

    @app.get("/api/v1/push/status", response_model=PushPage)
    def status(request: Request, box: Literal["sent", "received"] = "received",
               limit: int = Query(50, ge=1, le=200), beforeSeq: int | None = Query(None, ge=1)) -> dict:
        return db.snapshot(lambda c: service.status(c, request.state.actor, box, limit, beforeSeq))

    @app.post("/api/v1/push/deliveries/{identity}/claim", response_model=PushClaim)
    def claim(identity: str, request: Request, body: PushAttempt) -> dict:
        return mutate(request, body.model_dump(),
                      lambda c, a: service.claim(c, a, body.enrollmentId, identity, body.attemptId),
                      required_key=True, check_current_on_replay=True)

    @app.post("/api/v1/push/deliveries/{identity}/receipt", response_model=PushDelivery)
    def receipt(identity: str, request: Request, body: PushReceipt) -> dict:
        return mutate(request, body.model_dump(),
                      lambda c, a: service.receipt(c, a, body.enrollmentId, identity, body.attemptId, body.outcome,
                                                   resume_warning=body.resumeWarning),
                      required_key=True, check_current_on_replay=True)

    @app.get("/api/v1/push/stream", response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}})
    async def stream(request: Request, enrollmentId: str = Query(..., min_length=1, max_length=200)) -> StreamingResponse:
        def pending(after_seq: int, limit: int) -> dict:
            return db.snapshot(lambda c: service.pending(c, request.state.actor, enrollmentId, after_seq, limit))

        # Return an ordinary authorization error before sending any streaming response headers.
        await run_in_threadpool(pending, 0, 1)

        async def events() -> AsyncIterator[str]:
            after_seq = 0
            loop = asyncio.get_running_loop()
            next_heartbeat = loop.time() + HEARTBEAT_SECONDS
            with db.commits.listen() as changed:
                yield ": connected\n\n"
                while True:
                    # Register and clear before reading: a racing commit is visible in this snapshot
                    # or wakes this listener. No database lock is held while waiting on the receiver.
                    changed.clear()
                    try:
                        page = await run_in_threadpool(pending, after_seq, 50)
                    except AppError as error:
                        yield _event("closed", {"code": error.code})
                        return
                    for row in page["items"]:
                        after_seq = row["seq"]
                        yield _event("delivery", row)
                    if len(page["items"]) == 50:
                        continue
                    # Unrelated commits can keep this listener awake indefinitely. Keep the
                    # heartbeat on a deadline so a receiver can retry a pre-submission refusal.
                    remaining = next_heartbeat - loop.time()
                    if remaining > 0:
                        try:
                            await asyncio.wait_for(changed.wait(), timeout=remaining)
                            continue
                        except TimeoutError:
                            pass
                    yield ": keepalive\n\n"
                    next_heartbeat = loop.time() + HEARTBEAT_SECONDS

        return StreamingResponse(events(), media_type="text/event-stream")
