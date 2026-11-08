"""HTTP surface for tickets: agents file, change and comment directly; the person archives and
recovers them (policy.ROUTES)."""

from collections.abc import Callable
from typing import Literal

from fastapi import FastAPI, Query, Request

from . import models as m
from . import responses as out
from .common import AppError
from .db import Database
from .tickets import TicketService


def install_ticket_routes(app: FastAPI, db: Database, mutate: Callable) -> None:
    service = TicketService()

    read = db.snapshot

    def write(request: Request, payload: dict, fn: Callable, *, required_key: bool = True) -> dict:
        # The stable communication identity authors each change, and its retry receipts
        # survive a token rotation; a revoked credential is refused inside the write lock.
        return mutate(request, payload, fn, required_key=required_key, communication=True)

    @app.get("/api/v1/tickets", response_model=out.Tickets)
    def tickets(status: Literal["active", "open", "doing", "blocked", "done", "all"] = "active", q: str = "",
                kind: m.TicketKind | None = None, priority: m.TicketPriority | None = None,
                label: str | None = None, repositoryId: str | None = None, itemId: str | None = None,
                assigneeId: str | None = None, archived: bool = False,
                sort: Literal["priority", "recent"] = "priority",
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(lambda c: service.list(
            c, status=status, q=q, kind=kind, priority=priority, label=label, repository_id=repositoryId,
            item_id=itemId, assignee_id=assigneeId, archived=archived, sort=sort, limit=limit, offset=offset))

    @app.get("/api/v1/tickets/{ref}", response_model=out.Ticket)
    def ticket(ref: str, comments: int = Query(200, ge=1, le=1000)) -> dict:
        return read(lambda c: service.detail(c, ref, comments))

    @app.post("/api/v1/tickets", response_model=out.TicketListed)
    def ticket_create(request: Request, body: m.TicketCreate) -> dict:
        p = body.model_dump()
        return write(request, p, lambda c, a: service.create(c, a, p))

    @app.patch("/api/v1/tickets/{ref}", response_model=out.TicketListed)
    def ticket_update(ref: str, request: Request, body: m.TicketUpdate) -> dict:
        p = body.model_dump(exclude_unset=True)
        return write(request, p, lambda c, a: service.update(c, a, ref, p), required_key=False)

    @app.post("/api/v1/tickets/{ref}/comments", response_model=out.TicketComment)
    def ticket_comment(ref: str, request: Request, body: m.TicketCommentCreate) -> dict:
        p = body.model_dump()
        return write(request, p, lambda c, a: service.comment(c, a, ref, p))

    @app.post("/api/v1/tickets/{ref}/{verb}", response_model=out.TicketListed)
    def ticket_archive(ref: str, verb: str, request: Request, body: m.Expected) -> dict:
        if verb not in {"archive", "recover"}:
            raise AppError(404, "not_found", "Unknown ticket command")
        p = body.model_dump()
        return write(request, p, lambda c, a: service.set_archived(c, a, ref, p, verb == "archive"))
