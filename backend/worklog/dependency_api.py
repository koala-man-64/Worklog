"""Typed dependency reads and revision-checked writes."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request

from . import dependencies
from . import dependency_models as model
from . import responses as out
from .db import Database
from .models import Expected


def install_dependency_routes(
    app: FastAPI, db: Database, mutate: Callable[..., dict[str, Any]],
) -> None:
    @app.get("/api/v1/dependencies", response_model=out.Dependencies)
    def find_dependencies(kind: Annotated[model.DependencyType, Query(alias="type")], id: str) -> dict:
        return db.snapshot(lambda conn: dependencies.list_links(conn, (kind, id)))

    @app.get("/api/v1/dependencies/roadmaps/{identity}", response_model=out.RoadmapDependencies)
    def roadmap_dependencies(identity: str) -> dict:
        return db.snapshot(lambda conn: {"openByStep": dependencies.open_by_step(conn, identity)})

    @app.get("/api/v1/dependencies/waits", response_model=out.DependencyWaits)
    def reportable_waits(roadmapId: str | None = None) -> dict:
        return db.snapshot(lambda conn: {"items": dependencies.pending_waits(conn, roadmapId)})

    @app.get("/api/v1/dependencies/{identity}", response_model=out.DependencyLink)
    def dependency(identity: str) -> dict:
        return db.snapshot(lambda conn: dependencies.get_link(conn, identity))

    @app.post("/api/v1/dependencies", response_model=out.DependencyLink)
    def create_dependency(request: Request, body: model.DependencyCreate) -> dict:
        payload = body.model_dump()
        return mutate(request, payload, lambda conn, actor: dependencies.create(conn, actor, payload),
                      required_key=True, communication=True)

    @app.post("/api/v1/dependencies/waits", response_model=out.DependencyLink)
    def report_wait(request: Request, body: model.WaitReport) -> dict:
        payload = body.model_dump()
        return mutate(request, payload, lambda conn, actor: dependencies.report(conn, actor, payload),
                      required_key=True, communication=True)

    @app.post("/api/v1/dependencies/{identity}/confirm", response_model=out.DependencyLink)
    def confirm_dependency(request: Request, identity: str, body: model.DependencyConfirm) -> dict:
        payload = {"id": identity, **body.model_dump()}
        return mutate(request, payload, lambda conn, actor: dependencies.transition(conn, actor, "confirm", payload),
                      required_key=True, communication=True)

    @app.post("/api/v1/dependencies/{identity}/archive", response_model=out.DependencyLink)
    def archive_dependency(request: Request, identity: str, body: Expected) -> dict:
        payload = {"id": identity, **body.model_dump()}
        return mutate(request, payload, lambda conn, actor: dependencies.transition(conn, actor, "archive", payload),
                      required_key=True, communication=True)
