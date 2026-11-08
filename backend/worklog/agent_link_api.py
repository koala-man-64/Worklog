"""HTTP surface for agent ties: the person ties any agent to any record, and an agent ties only itself.
Agents may set and remove ties (policy.ROUTES, agent:tie); set_tie and remove_tie keep an agent to its
own identity."""

from collections.abc import Callable

from fastapi import FastAPI, Query, Request

from . import models as m
from . import responses as out
from .agent_links import remove_tie, set_tie, ties_of_agent, ties_on
from .common import AppError
from .db import Database
from .discussions import communication_actor


def install_agent_link_routes(app: FastAPI, db: Database, mutate: Callable) -> None:
    read = db.snapshot

    def write(request: Request, payload: dict, fn: Callable) -> dict:
        # Like a ticket: the agent's identity, not its credential, makes the change, its retry receipts
        # survive a token rotation, and a credential revoked while the call waited is refused.
        return mutate(request, payload, fn, required_key=True, communication=True)

    @app.get("/api/v1/agent-links", response_model=out.AgentTies)
    def agent_links(request: Request, agentId: str | None = None, targetType: m.AgentTieTarget | None = None,
                    targetId: list[str] = Query([])) -> dict:
        reader = request.state.actor

        def find(c):
            if targetType is not None or targetId:
                if agentId is not None or targetType is None or not targetId:
                    raise AppError(422, "validation", "Give agentId, or targetType with targetId, not both")
                return ties_on(c, targetType, targetId, reader)
            if agentId is not None:
                return ties_of_agent(c, agentId, reader)
            if reader["kind"] == "agent":
                return ties_of_agent(c, communication_actor(c, reader)["id"], reader)
            raise AppError(422, "validation", "Give agentId, or targetType with targetId")
        return read(find)

    @app.post("/api/v1/agent-links", response_model=out.AgentTie)
    def agent_link_set(request: Request, body: m.AgentLinkSet) -> dict:
        p, reader = body.model_dump(), request.state.actor
        return write(request, p, lambda c, actor: set_tie(c, actor, p, reader=reader))

    @app.post("/api/v1/agent-links/unlink", response_model=out.AgentUntied)
    def agent_link_remove(request: Request, body: m.AgentLinkRemove) -> dict:
        p, reader = body.model_dump(), request.state.actor
        return write(request, p, lambda c, actor: remove_tie(c, actor, p, reader=reader))
