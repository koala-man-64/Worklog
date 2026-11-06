"""HTTP surface for reminders: the person and agents set, change, snooze and complete them, and only
the person archives and recovers them (policy.ROUTES)."""

from collections.abc import Callable
from typing import Literal

from fastapi import FastAPI, Query, Request

from . import models as m
from .common import AppError
from .db import Database
from .reminders import ReminderService


def install_reminder_routes(app: FastAPI, db: Database, mutate: Callable) -> None:
    service = ReminderService()

    read = db.snapshot

    def write(request: Request, payload: dict, fn: Callable, *, required_key: bool = False) -> dict:
        # The stable communication identity sets and changes each reminder, and its retry receipts
        # survive a token rotation; a revoked credential is refused inside the write lock.
        return mutate(request, payload, fn, required_key=required_key, communication=True)

    @app.get("/api/v1/reminders")
    def reminders(state: Literal["open", "due", "upcoming", "done", "all"] = "open",
                  date: str | None = Query(None, description="Only those due on this day, YYYY-MM-DD"),
                  until: str | None = Query(None, description="Only those due on or before this day, YYYY-MM-DD"),
                  targetType: m.ReminderTarget | None = None, targetId: str | None = None,
                  q: str = "", archived: bool = False,
                  limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(lambda c: service.list(
            c, state=state, date=date, until=until, target_type=targetType, target_id=targetId,
            q=q, archived=archived, limit=limit, offset=offset))

    @app.get("/api/v1/reminders/{identity}")
    def reminder(identity: str) -> dict:
        return read(lambda c: service.detail(c, identity))

    @app.post("/api/v1/reminders")
    def reminder_create(request: Request, body: m.ReminderCreate) -> dict:
        p = body.model_dump()
        return write(request, p, lambda c, a: service.create(c, a, p), required_key=True)

    @app.patch("/api/v1/reminders/{identity}")
    def reminder_update(identity: str, request: Request, body: m.ReminderUpdate) -> dict:
        p = body.model_dump(exclude_unset=True)
        return write(request, p, lambda c, a: service.update(c, a, identity, p))

    @app.post("/api/v1/reminders/{identity}/{verb}")
    def reminder_archive(identity: str, verb: str, request: Request, body: m.Expected) -> dict:
        if verb not in {"archive", "recover"}:
            raise AppError(404, "not_found", "Unknown reminder command")
        p = body.model_dump()
        return write(request, p, lambda c, a: service.set_archived(c, a, identity, p, verb == "archive"))
