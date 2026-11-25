"""The person's desktop-session launcher and durable dispatch queue. Agent credentials cannot start local processes."""

from fastapi import FastAPI, Query, Request

from . import lease_models as lm
from . import models as m
from . import responses as out
from .session_launches import AgentChats, SessionLauncher


def install_session_routes(app: FastAPI, launcher: SessionLauncher, chats: AgentChats) -> None:
    # Open the chat of the agent a pending request's slot waits on, with a message to paste (R11).
    @app.post("/api/v1/discussions/messages/{identity}/open-chat", response_model=out.AgentChatOpened)
    def open_agent_chat(identity: str, body: m.AgentChatOpen, request: Request) -> dict:
        return chats.open(identity, body.targetId, request.state.actor)

    @app.post("/api/v1/work-items/{identity}/sessions", response_model=out.SessionLaunch, response_model_exclude_unset=True)
    def start_session(identity: str, body: m.SessionStart, request: Request) -> dict:
        return launcher.start(identity, body.model_dump(exclude_unset=True), request.state.actor,
                              request.headers.get("idempotency-key"))

    @app.get("/api/v1/session-launches/{identity}", response_model=out.SessionLaunch, response_model_exclude_unset=True)
    def session_launch(identity: str, request: Request) -> dict:
        return launcher.get(identity, request.state.actor)

    @app.post("/api/v1/session-launches/{identity}/open", response_model=out.SessionLaunch, response_model_exclude_unset=True)
    def open_session(identity: str, request: Request) -> dict:
        return launcher.open(identity, request.state.actor)

    @app.get("/api/v1/dispatch-queue", response_model=out.DispatchQueue)
    def dispatch_queue(
        repositoryId: str | None = None,
        itemId: str | None = None,
        status: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict:
        return launcher.list_dispatch_queue(
            repository_id=repositoryId,
            item_id=itemId,
            status=status,
            limit=limit,
            offset=offset,
        )

    @app.post("/api/v1/dispatch-queue/{identity}/cancel", response_model=out.DispatchItem)
    def cancel_dispatch(identity: str, body: lm.DispatchCancel, request: Request) -> dict:
        return launcher.cancel_dispatch(identity, request.state.actor, body.reason)

    @app.post("/api/v1/dispatch-queue/{identity}/open", response_model=out.SessionLaunch, response_model_exclude_unset=True)
    def open_dispatch(identity: str, request: Request) -> dict:
        return launcher.open_dispatch(identity, request.state.actor)
