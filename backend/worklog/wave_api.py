"""HTTP routes for synchronized waves, Repo + Wave execution dispatch, repository queues, and proposal guardrails."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request

from . import coordination_models as cm
from . import models as m
from . import responses as out
from .common import AppError, event, now
from .coordination import _get as _get_repo
from .db import Database
from .dispatch import DispatchService, dispatch_advisories
from .proposals import ProposalService
from .repository_links import reorder_repository_queue
from .waves import WaveService


def _proposal_guardrail_targets(operations: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Collect concrete repository IDs and step IDs touched by a proposal's operations (skipping @names)."""
    repo_ids: list[str] = []
    step_ids: list[str] = []

    def add_repo(value: Any) -> None:
        if isinstance(value, str) and value and not value.startswith("@"):
            repo_ids.append(value)

    def add_step(value: Any) -> None:
        if isinstance(value, str) and value and not value.startswith("@"):
            step_ids.append(value)

    for op in operations:
        if not isinstance(op, dict):
            continue
        action, payload = op.get("action", ""), op.get("payload")
        if not isinstance(payload, dict):
            continue
        if action in {"progress.set", "step.update", "step.split", "step.move"}:
            add_step(payload.get("id") or payload.get("stepId"))
        if action in {"item.create", "item.update"}:
            add_step(payload.get("stepId"))
        if action == "wave.create" or action == "wave.steps":
            for sid in payload.get("stepIds") or []:
                add_step(sid)
        for rid in payload.get("repositoryIds") or []:
            add_repo(rid)
        if action == "step.split" and isinstance(payload.get("children"), list):
            for child in payload["children"]:
                if isinstance(child, dict):
                    for rid in child.get("repositoryIds") or []:
                        add_repo(rid)
        if action in {"wave.create", "wave.update"} and isinstance(payload.get("repositories"), list):
            for entry in payload["repositories"]:
                if isinstance(entry, dict):
                    add_repo(entry.get("repositoryId"))
        if action in {"repository.update", "repository.archive"}:
            add_repo(payload.get("id"))
    return list(dict.fromkeys(repo_ids)), list(dict.fromkeys(step_ids))


def install_wave_routes(app: FastAPI, db: Database, mutate: Callable, proposals: ProposalService) -> None:
    waves = WaveService(db)
    dispatch = DispatchService(db)
    read = db.snapshot

    def write(request: Request, body: Any, fn: Callable, *, required_key: bool = True, exclude_unset: bool = False) -> dict:
        return mutate(request, body.model_dump(exclude_unset=exclude_unset), fn, required_key=required_key)

    @app.get("/api/v1/roadmaps/{identity}/dispatch", response_model=out.RoadmapDispatch)
    def roadmap_dispatch(identity: str, waveId: str | None = None, repositoryId: str | None = None,
                         includeDone: bool = False) -> dict:
        return read(lambda c: dispatch.roadmap_dispatch(
            c, identity, wave_id=waveId, repository_id=repositoryId, include_done=includeDone
        ))

    @app.get("/api/v1/roadmaps/{identity}/waves", response_model=out.Waves)
    def roadmap_waves(identity: str, status: str | None = None, archived: bool = False) -> dict:
        return read(lambda c: waves.list_waves(c, identity, status=status, archived=archived))

    @app.post("/api/v1/roadmaps/{identity}/waves", response_model=out.WaveDetail)
    def wave_create(identity: str, request: Request, body: m.WaveCreate) -> dict:
        return write(request, body, lambda c, a: waves.create(c, a, {"roadmapId": identity, **body.model_dump()}))

    @app.get("/api/v1/waves/{identity}", response_model=out.WaveDetail)
    def wave_detail(identity: str) -> dict:
        return read(lambda c: waves.detail(c, identity))

    @app.patch("/api/v1/waves/{identity}", response_model=out.WaveDetail)
    def wave_update(identity: str, request: Request, body: m.WaveUpdate) -> dict:
        return write(
            request,
            body,
            lambda c, a: waves.update(c, a, identity, body.model_dump(exclude_unset=True)),
            required_key=False,
            exclude_unset=True,
        )

    @app.post("/api/v1/waves/{identity}/steps", response_model=out.WaveDetail)
    def wave_steps(identity: str, request: Request, body: m.WaveStepsUpdate) -> dict:
        return write(request, body, lambda c, a: waves.set_steps(c, a, identity, body.model_dump()), required_key=False)

    @app.post("/api/v1/waves/{identity}/status", response_model=out.WaveDetail)
    def wave_status(identity: str, request: Request, body: m.WaveStatusChange) -> dict:
        return write(request, body, lambda c, a: waves.set_status(c, a, identity, body.model_dump()), required_key=False)

    @app.post("/api/v1/waves/{identity}/freeze", response_model=out.WaveDetail)
    def wave_freeze(identity: str, request: Request, body: m.WaveFreezeUpdate) -> dict:
        return write(request, body, lambda c, a: waves.set_freeze(c, a, identity, body.model_dump()), required_key=False)

    @app.post("/api/v1/waves/{identity}/repins/report", response_model=out.WaveRepin)
    def wave_repin_report(identity: str, request: Request, body: m.WaveRepinReport) -> dict:
        return write(request, body, lambda c, a: waves.report_repin(c, a, identity, body.model_dump()))

    @app.post("/api/v1/waves/{identity}/repins/verify", response_model=out.WaveRepin)
    def wave_repin_verify(identity: str, request: Request, body: m.WaveRepinVerify) -> dict:
        return write(request, body, lambda c, a: waves.verify_repin(c, a, identity, body.model_dump()), required_key=False)

    @app.post("/api/v1/waves/{identity}/{verb}", response_model=out.WaveDetail)
    def wave_archive(identity: str, verb: str, request: Request, body: m.Expected) -> dict:
        if verb not in {"archive", "recover"}:
            raise AppError(404, "not_found", "Unknown wave command")
        return write(
            request,
            body,
            lambda c, a: waves.set_archived(c, a, identity, body.model_dump(), archived=verb == "archive"),
            required_key=False,
        )

    @app.get("/api/v1/repositories/{identity}/queue", response_model=out.RepositoryQueue)
    def repository_queue(identity: str, includeDone: bool = False) -> dict:
        return read(lambda c: dispatch.repository_queue(c, identity, include_done=includeDone))

    @app.post("/api/v1/repositories/{identity}/queue/reorder", response_model=out.RepositoryQueue)
    def repository_queue_reorder(identity: str, request: Request, body: cm.RepositoryQueueReorder) -> dict:
        def apply_order(c: Any, actor: dict) -> dict:
            if actor["kind"] != "human":
                raise AppError(403, "forbidden", "Only a person may reorder a repository execution queue")
            repo = _get_repo(c, "repositories", identity)
            if repo["archived"] or repo["merged_into"]:
                raise AppError(409, "conflict", "Archived or merged repositories cannot reorder their queue")
            ordered = reorder_repository_queue(c, identity, body.stepIds)
            event(c, actor, "repository.queue.reorder", identity, None, {"stepIds": ordered, "at": now()})
            return dispatch.repository_queue(c, identity)

        return write(request, body, apply_order, required_key=False)

    @app.get("/api/v1/proposals/{identity}/guardrails", response_model=out.DispatchAdvisories)
    def proposal_guardrails(identity: str, request: Request) -> dict:
        def check(c: Any) -> dict:
            record = proposals.record(c, identity, request.state.actor)
            ops = record["operations"] if isinstance(record["operations"], list) else json.loads(record["operations"])
            repo_ids, step_ids = _proposal_guardrail_targets(ops)
            return dispatch_advisories(c, repo_ids, step_ids)

        return read(check)
