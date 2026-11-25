"""Read-only HTTP surface for the agent registry; browser sessions only."""

from fastapi import FastAPI, Query

from . import responses as out
from .db import Database
from .registry import AgentRegistry
from .transcripts import TranscriptReader


def install_registry_routes(app: FastAPI, db: Database, transcripts: TranscriptReader) -> None:
    registry = AgentRegistry()
    read = db.snapshot

    @app.get("/api/v1/agents", response_model=out.Agents)
    def agents(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               q: str = Query("", max_length=200)) -> dict:
        return read(lambda c: registry.agents(c, limit, offset, q))

    @app.get("/api/v1/agents/{identity}", response_model=out.Agent)
    def agent(identity: str) -> dict:
        return read(lambda c: registry.agent(c, identity))

    @app.get("/api/v1/agents/{identity}/activity", response_model=out.AgentActivity)
    def activity(identity: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(lambda c: registry.activity(c, identity, limit, offset))

    @app.get("/api/v1/roadmaps/{identity}/agents", response_model=out.RoadmapAgents)
    def roadmap_agents(identity: str) -> dict:
        # Whether a tool archived a chat is read from folders on this machine, so it is asked before
        # the snapshot: a slow folder must never hold SQLite's shared read lock (audit finding P7). A
        # chat tracked in between counts as not archived.
        with db.read() as c:
            chat_ids = {r[0] for r in c.execute("SELECT chat_id FROM agent_chats WHERE archived=0")}
        flags = {chat_id: transcripts.archived(chat_id) for chat_id in chat_ids}
        # Who works on it now, and everyone whose work ever tied them to it, from one snapshot.
        return read(lambda c: {**registry.roadmap(c, identity, lambda chat_id: flags.get(chat_id, False)),
                               "roster": registry.roster(c, identity)})
