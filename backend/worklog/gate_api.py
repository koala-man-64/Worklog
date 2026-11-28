"""HTTP endpoints for pre-completion gates on roadmap steps."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Query, Request

from . import gate_models as gm
from . import gates
from . import models as m
from . import responses as out
from .db import Database


def install_gate_routes(
    app: FastAPI,
    db: Database,
    apply: Callable[..., dict[str, Any]],
) -> None:
    @app.get("/api/v1/roadmaps/{identity}/gates", response_model=out.RoadmapGates)
    def roadmap_gates(identity: str, archived: bool = False, onlyOpen: bool = False) -> dict:
        return db.snapshot(
            lambda conn: gates.list_for_roadmap(conn, identity, archived=archived, only_open=onlyOpen)
        )

    @app.get("/api/v1/steps/{identity}/gates", response_model=out.StepGates)
    def step_gates(identity: str, archived: bool = False) -> dict:
        return db.snapshot(lambda conn: gates.list_for_step(conn, identity, archived=archived))

    @app.post("/api/v1/steps/{identity}/gates", response_model=out.StepGate)
    def gate_create(identity: str, request: Request, body: gm.GateCreate) -> dict:
        return apply(
            request,
            "gate.create",
            {"stepId": identity, **body.model_dump(exclude_unset=True)},
            create=True,
        )

    @app.get("/api/v1/gates/{identity}", response_model=out.StepGate)
    def gate_detail(identity: str) -> dict:
        return db.snapshot(lambda conn: gates.get_gate(conn, identity))

    @app.get("/api/v1/gates/{identity}/history", response_model=out.History)
    def gate_history(
        identity: str,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict:
        return db.snapshot(lambda conn: gates.history(conn, identity, limit=limit, offset=offset))

    @app.patch("/api/v1/gates/{identity}", response_model=out.StepGate)
    def gate_update(identity: str, request: Request, body: gm.GateUpdate) -> dict:
        return apply(request, "gate.update", {"id": identity, **body.model_dump(exclude_unset=True)})

    @app.post("/api/v1/gates/{identity}/pass", response_model=out.StepGate)
    def gate_pass(identity: str, request: Request, body: gm.GatePass) -> dict:
        return apply(request, "gate.pass", {"id": identity, **body.model_dump()})

    @app.post("/api/v1/gates/{identity}/waive", response_model=out.StepGate)
    def gate_waive(identity: str, request: Request, body: gm.GateWaive) -> dict:
        return apply(request, "gate.waive", {"id": identity, **body.model_dump()})

    @app.post("/api/v1/gates/{identity}/reopen", response_model=out.StepGate)
    def gate_reopen(identity: str, request: Request, body: gm.GateReopen) -> dict:
        return apply(request, "gate.reopen", {"id": identity, **body.model_dump()})

    @app.post("/api/v1/gates/{identity}/archive", response_model=out.StepGate)
    def gate_archive(identity: str, request: Request, body: m.Expected) -> dict:
        return apply(request, "gate.archive", {"id": identity, **body.model_dump()})

    @app.post("/api/v1/gates/{identity}/recover", response_model=out.StepGate)
    def gate_recover(identity: str, request: Request, body: m.Expected) -> dict:
        return apply(request, "gate.recover", {"id": identity, **body.model_dump()})
