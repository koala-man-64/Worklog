"""HTTP surface for quota burndown, capacity telemetry, usage ledger, and credit plans."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import quota_models as qm
from .common import AppError
from .quota.service import CapacityService


def quota_agent_route_allowed(method: str, path: str) -> bool:
    """Agents may read capacity, status, and usage summaries; raw request text and writes stay with the person."""
    suffix = path.removeprefix("/api/v1").rstrip("/")
    return method == "GET" and suffix in {"/quota/capacity", "/quota/status", "/quota/usage"}


def install_quota_routes(app: FastAPI, quota_service: CapacityService) -> None:
    async def sse_stream(request: Request) -> AsyncIterator[str]:
        with quota_service._consumer_lock:
            if quota_service._consumers >= 32:
                raise AppError(503, "busy", "Too many quota event consumers")
            quota_service._consumers += 1
        try:
            snapshot = quota_service.state.read()
            sent_revision: int | None = None
            # A stream never ends by itself, so the server ends it before waiting on its connections.
            while not quota_service.streams_closed.is_set():
                if await request.is_disconnected():
                    break
                if sent_revision != snapshot["revision"]:
                    data = json.dumps(snapshot, separators=(",", ":"))
                    yield f"id: {snapshot['instance_id']}:{snapshot['revision']}\ndata: {data}\n\n"
                    sent_revision = snapshot["revision"]
                else:
                    yield ": heartbeat\n\n"
                if not quota_service.threads:
                    break
                snapshot = await asyncio.to_thread(quota_service.state.wait, snapshot["revision"], 15.0,
                                                   quota_service.streams_closed)
        finally:
            with quota_service._consumer_lock:
                quota_service._consumers -= 1

    @app.get("/api/v1/quota/dashboard")
    def quota_dashboard(targetDate: str | None = None) -> dict[str, Any]:
        return quota_service.dashboard_snapshot(target_date=targetDate)

    @app.get("/api/v1/quota/capacity")
    def quota_capacity() -> dict[str, Any]:
        quota_service.touch()
        return quota_service.state.read()

    @app.get("/api/v1/quota/capacity/events")
    async def quota_capacity_events(request: Request) -> StreamingResponse:
        return StreamingResponse(sse_stream(request), media_type="text/event-stream")

    @app.get("/api/v1/quota/status")
    def quota_status() -> dict[str, Any]:
        return {
            "items": quota_service.status_snapshot(),
            "latest": quota_service.latest_snapshot(),
        }

    @app.get("/api/v1/quota/usage")
    def quota_usage(view: Literal["summary", "efficiency"] = "summary") -> dict[str, Any]:
        return quota_service.usage_snapshot(view=view)

    @app.get("/api/v1/quota/plans")
    def quota_plans() -> dict[str, Any]:
        quota_service.touch()
        return quota_service.plans_snapshot()

    @app.post("/api/v1/quota/plans")
    def quota_plan_save(body: qm.QuotaPlanUpdate) -> Response:
        payload = body.model_dump()
        status, result = quota_service.apply_plan(payload)
        if status >= 400:
            code = "conflict" if status == 409 else "storage_error" if status >= 500 else "validation"
            raise AppError(status, code, str(result.get("error") or "Invalid plan request"))
        return JSONResponse(result, status_code=status)

    @app.post("/api/v1/quota/policy")
    def quota_policy_save(body: qm.QuotaPolicyUpdate) -> Response:
        status, result = quota_service.apply_policy(body.reserve_pct)
        if status >= 400:
            code = "storage_error" if status >= 500 else "validation"
            raise AppError(status, code, str(result.get("error") or "Invalid policy request"))
        return JSONResponse(result, status_code=status)

    @app.post("/api/v1/quota/collect")
    async def quota_collect(body: qm.QuotaCollectRequest) -> dict[str, Any]:
        return await run_in_threadpool(
            lambda: quota_service.trigger_collect(
                since_days=float(body.since_days),
                full=body.full,
                usage_on=body.usage,
            )
        )

    @app.get("/api/v1/quota/recent-text/{row_id:path}")
    async def quota_recent_text(row_id: str) -> Response:
        status, result = await run_in_threadpool(quota_service.read_recent_text, row_id)
        if status == 404:
            raise AppError(404, "not_found", str(result.get("note") or "Recent request text unavailable"))
        if status >= 500:
            raise AppError(503, "busy", str(result.get("note") or "Text viewer is busy"))
        return JSONResponse(result, status_code=status)

    # Legacy loopback endpoints for quota-burndown CLI / MCP compatibility
    @app.get("/v1/capacity", include_in_schema=False)
    def legacy_capacity() -> dict[str, Any]:
        quota_service.touch()
        return quota_service.state.read()

    @app.get("/v1/capacity/events", include_in_schema=False)
    async def legacy_capacity_events(request: Request) -> StreamingResponse:
        return StreamingResponse(sse_stream(request), media_type="text/event-stream")

    @app.get("/v1/plans", include_in_schema=False)
    def legacy_plans() -> dict[str, Any]:
        quota_service.touch()
        return quota_service.plans_snapshot()

    @app.post("/v1/plans", include_in_schema=False)
    async def legacy_plan_save(request: Request) -> Response:
        if request.headers.get("authorization"):
            raise AppError(403, "agent_scope", "This quota operation is outside agent scope")
        try:
            raw = await request.body()
            if not 0 < len(raw) <= 4096:
                raise ValueError("bounded JSON request required")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("plan request must be an object")
        except (ValueError, TypeError):
            return JSONResponse({"error": "bounded JSON plan request required"}, status_code=400)
        status, result = quota_service.apply_plan(payload)
        return JSONResponse(result, status_code=status)

    @app.post("/v1/policy", include_in_schema=False)
    async def legacy_policy_save(request: Request) -> Response:
        if request.headers.get("authorization"):
            raise AppError(403, "agent_scope", "This quota operation is outside agent scope")
        try:
            raw = await request.body()
            if not 0 < len(raw) <= 1024:
                raise ValueError()
            payload = json.loads(raw)
            reserve = payload["reserve_pct"]
        except (ValueError, TypeError, KeyError):
            return JSONResponse({"error": "reserve_pct must be 5, 10, or 20"}, status_code=400)
        status, result = quota_service.apply_policy(reserve)
        return JSONResponse(result, status_code=status)

    @app.get("/v1/usage", include_in_schema=False)
    def legacy_efficiency() -> dict[str, Any]:
        return quota_service.usage_snapshot(view="efficiency")

    @app.get("/v1/recent-text/{row_id:path}", include_in_schema=False)
    async def legacy_recent_text(row_id: str, request: Request) -> Response:
        if request.headers.get("authorization"):
            raise AppError(403, "agent_scope", "This quota operation is outside agent scope")
        status, result = await run_in_threadpool(quota_service.read_recent_text, row_id)
        return JSONResponse(result, status_code=status)

    @app.get("/latest.json", include_in_schema=False)
    def legacy_latest() -> dict[str, Any]:
        return quota_service.latest_snapshot()

    @app.get("/status.json", include_in_schema=False)
    def legacy_status() -> list[dict[str, Any]]:
        return quota_service.status_snapshot()

    @app.get("/usage.json", include_in_schema=False)
    def legacy_usage() -> dict[str, Any]:
        return quota_service.usage_snapshot(view="summary")
