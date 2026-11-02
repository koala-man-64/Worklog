"""Read-only HTTP surface for the agent registry; browser sessions only."""

from collections.abc import Callable

from fastapi import FastAPI, Query

from .db import Database
from .registry import AgentRegistry


def install_registry_routes(app: FastAPI, db: Database) -> None:
    registry = AgentRegistry()

    def read(fn: Callable) -> dict:
        with db.read() as c:
            c.execute("BEGIN")
            return fn(c)

    @app.get("/api/v1/agents")
    def agents(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(lambda c: registry.agents(c, limit, offset))

    @app.get("/api/v1/agents/{identity}")
    def agent(identity: str) -> dict:
        return read(lambda c: registry.agent(c, identity))

    @app.get("/api/v1/agents/{identity}/activity")
    def activity(identity: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(lambda c: registry.activity(c, identity, limit, offset))
