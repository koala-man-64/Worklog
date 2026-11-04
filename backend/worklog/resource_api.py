"""HTTP surface for resources: the person writes them, and agents read them like the person.

No agent write route is allowed for resources, so the middleware refuses an agent's write
with 403 agent_scope before it reaches these handlers.
"""

from collections.abc import Callable

from fastapi import FastAPI, Query, Request

from . import models as m
from .common import AppError
from .db import Database
from .resources import ResourceService


def install_resource_routes(app: FastAPI, db: Database, mutate: Callable) -> None:
    service = ResourceService()

    def read(fn: Callable) -> dict:
        with db.read() as c:
            c.execute("BEGIN")
            return fn(c)

    @app.get("/api/v1/resources")
    def resources(q: str = "", archived: bool = False,
                  limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(lambda c: service.list(c, q=q, archived=archived, limit=limit, offset=offset))

    @app.get("/api/v1/resources/{identity}")
    def resource(identity: str) -> dict:
        return read(lambda c: service.detail(c, identity))

    @app.post("/api/v1/resources")
    def resource_create(request: Request, body: m.ResourceCreate) -> dict:
        p = body.model_dump()
        return mutate(request, p, lambda c, a: service.create(c, a, p), required_key=True)

    @app.patch("/api/v1/resources/{identity}")
    def resource_update(identity: str, request: Request, body: m.ResourceUpdate) -> dict:
        p = body.model_dump(exclude_unset=True)
        return mutate(request, p, lambda c, a: service.update(c, a, identity, p))

    @app.post("/api/v1/resources/{identity}/{verb}")
    def resource_archive(identity: str, verb: str, request: Request, body: m.Expected) -> dict:
        if verb not in {"archive", "recover"}:
            raise AppError(404, "not_found", "Unknown resource command")
        p = body.model_dump()
        return mutate(request, p, lambda c, a: service.set_archived(c, a, identity, p, verb == "archive"))
