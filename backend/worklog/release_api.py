"""HTTP surface for releases: the person changes them here, and agents read them.

Every write runs through WorkService.apply, the dispatch approved proposals use too. No agent
write route is allowed for releases, so the middleware refuses an agent's write with 403
agent_scope before it reaches these handlers.
"""

from collections.abc import Callable

from fastapi import FastAPI, Query, Request

from . import models as m
from . import responses as out
from .db import Database
from .releases import ReleaseService


def install_release_routes(app: FastAPI, db: Database, apply: Callable) -> None:
    service = ReleaseService()
    read = db.snapshot

    @app.get("/api/v1/releases", response_model=out.Releases)
    def releases(status: str = Query("planned", description="planned, released, cancelled or all"),
                 archived: bool = False, q: str = "",
                 targetType: str | None = Query(None, description="item, roadmap, step or github, with targetId: "
                                                                  "the releases that hold that record"),
                 targetId: str | None = None,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(lambda c: service.list(c, status=status, archived=archived, q=q, target_type=targetType,
                                           target_id=targetId, limit=limit, offset=offset))

    @app.post("/api/v1/releases", response_model=out.ReleaseFields)
    def release_create(request: Request, body: m.ReleaseCreate) -> dict:
        return apply(request, "release.create", body.model_dump(), create=True)

    @app.get("/api/v1/releases/{identity}", response_model=out.Release)
    def release(identity: str) -> dict:
        return read(lambda c: service.detail(c, identity))

    @app.patch("/api/v1/releases/{identity}", response_model=out.ReleaseFields)
    def release_update(identity: str, request: Request, body: m.ReleaseUpdate) -> dict:
        return apply(request, "release.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/releases/{identity}/archive", response_model=out.ReleaseFields)
    def release_archive(identity: str, request: Request, body: m.Expected) -> dict:
        return apply(request, "release.archive", {**body.model_dump(), "id": identity})

    @app.post("/api/v1/releases/{identity}/recover", response_model=out.ReleaseFields)
    def release_recover(identity: str, request: Request, body: m.Expected) -> dict:
        return apply(request, "release.recover", {**body.model_dump(), "id": identity})

    @app.post("/api/v1/release-contents", response_model=out.ReleaseHolding)
    def release_add(request: Request, body: m.ReleaseContentChange) -> dict:
        return apply(request, "release.add", body.model_dump())

    @app.post("/api/v1/release-contents/remove", response_model=out.ReleaseUnheld)
    def release_remove(request: Request, body: m.ReleaseContentChange) -> dict:
        return apply(request, "release.remove", body.model_dump())

    @app.post("/api/v1/release-checks", response_model=out.ReleaseCheckRecord)
    def check_create(request: Request, body: m.ReleaseCheckCreate) -> dict:
        return apply(request, "check.create", body.model_dump(), create=True)

    @app.get("/api/v1/release-checks/{identity}", response_model=out.ReleaseCheck)
    def check(identity: str) -> dict:
        return read(lambda c: service.check(c, identity))

    @app.patch("/api/v1/release-checks/{identity}", response_model=out.ReleaseCheckRecord)
    def check_update(identity: str, request: Request, body: m.ReleaseCheckUpdate) -> dict:
        return apply(request, "check.update", {**body.model_dump(exclude_unset=True), "id": identity})

    @app.post("/api/v1/release-checks/{identity}/archive", response_model=out.ReleaseCheckRecord)
    def check_archive(identity: str, request: Request, body: m.Expected) -> dict:
        return apply(request, "check.archive", {**body.model_dump(), "id": identity})

    @app.post("/api/v1/release-checks/{identity}/recover", response_model=out.ReleaseCheckRecord)
    def check_recover(identity: str, request: Request, body: m.Expected) -> dict:
        return apply(request, "check.recover", {**body.model_dump(), "id": identity})
