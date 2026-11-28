"""HTTP routes for roadmap deliverables and the person reviewing them."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import FastAPI, Query, Request

from . import models as m
from . import responses as out
from .deliverables import DeliverableService


def install_deliverable_routes(app: FastAPI, service: DeliverableService, apply: Callable) -> None:
    @app.get("/api/v1/roadmaps/{identity}/deliverables", response_model=out.DeliverableList)
    def roadmap_deliverables(identity: str, scope: Literal["tree", "own"] = "tree", archived: bool = False,
                             limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return service.listing(identity, scope=scope, archived=archived, limit=limit, offset=offset)

    @app.post("/api/v1/roadmaps/{identity}/deliverables", response_model=out.DeliverableDetail)
    def deliverable_create(identity: str, request: Request, body: m.DeliverableCreate) -> dict:
        return apply(request, "deliverable.create", {"roadmapId": identity, **body.model_dump(exclude_unset=True)}, create=True)

    @app.get("/api/v1/roadmaps/{identity}/deliverable-options", response_model=out.DeliverableOptions)
    def deliverable_options(identity: str, kind: Literal["step", "attachment", "resource", "repository"],
                            q: str = Query("", max_length=500), limit: int = Query(50, ge=1, le=200),
                            offset: int = Query(0, ge=0)) -> dict:
        return service.options(identity, kind, q.strip(), limit, offset)

    @app.get("/api/v1/steps/{identity}/deliverables", response_model=out.DeliverableBacklinks)
    def step_deliverables(identity: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return service.backlinks(identity, limit, offset)

    @app.get("/api/v1/deliverables/{identity}", response_model=out.DeliverableDetail)
    def deliverable_detail(identity: str) -> dict:
        return service.detail(identity)

    @app.get("/api/v1/deliverables/{identity}/history", response_model=out.DeliverableHistory)
    def deliverable_history(identity: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return service.history(identity, limit, offset)

    @app.patch("/api/v1/deliverables/{identity}", response_model=out.DeliverableDetail)
    def deliverable_update(identity: str, request: Request, body: m.DeliverableUpdate) -> dict:
        return apply(request, "deliverable.update", {"id": identity, **body.model_dump(exclude_unset=True)})

    @app.post("/api/v1/deliverables/{identity}/submit", response_model=out.DeliverableDetail)
    def deliverable_submit(identity: str, request: Request, body: m.Expected) -> dict:
        return apply(request, "deliverable.submit", {"id": identity, **body.model_dump()})

    @app.post("/api/v1/deliverables/{identity}/accept", response_model=out.DeliverableDetail)
    def deliverable_accept(identity: str, request: Request, body: m.DeliverableAccept) -> dict:
        return apply(request, "deliverable.accept", {"id": identity, **body.model_dump()})

    @app.post("/api/v1/deliverables/{identity}/reopen", response_model=out.DeliverableDetail)
    def deliverable_reopen(identity: str, request: Request, body: m.DeliverableReopen) -> dict:
        return apply(request, "deliverable.reopen", {"id": identity, **body.model_dump()})

    @app.post("/api/v1/deliverables/{identity}/archive", response_model=out.DeliverableDetail)
    def deliverable_archive(identity: str, request: Request, body: m.Expected) -> dict:
        return apply(request, "deliverable.archive", {"id": identity, **body.model_dump()})

    @app.post("/api/v1/deliverables/{identity}/recover", response_model=out.DeliverableDetail)
    def deliverable_recover(identity: str, request: Request, body: m.Expected) -> dict:
        return apply(request, "deliverable.recover", {"id": identity, **body.model_dump()})
