"""The person's desktop-session launcher. Agent credentials cannot start local processes."""

from fastapi import FastAPI, Request

from . import models as m
from . import responses as out
from .session_launches import SessionLauncher


def install_session_routes(app: FastAPI, launcher: SessionLauncher) -> None:
    @app.post("/api/v1/work-items/{identity}/sessions", response_model=out.SessionLaunch, response_model_exclude_unset=True)
    def start_session(identity: str, body: m.SessionStart, request: Request) -> dict:
        return launcher.start(identity, body.model_dump(), request.state.actor,
                              request.headers.get("idempotency-key"))

    @app.get("/api/v1/session-launches/{identity}", response_model=out.SessionLaunch, response_model_exclude_unset=True)
    def session_launch(identity: str, request: Request) -> dict:
        return launcher.get(identity, request.state.actor)

    @app.post("/api/v1/session-launches/{identity}/open", response_model=out.SessionLaunch, response_model_exclude_unset=True)
    def open_session(identity: str, request: Request) -> dict:
        return launcher.open(identity, request.state.actor)
