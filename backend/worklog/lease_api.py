"""HTTP routes for repository checkout slot policies, base locks, and bounded FIFO leases."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Query, Request

from . import lease_models as m
from . import responses as out
from .common import AppError, now
from .db import Database
from .discussions import communication_actor
from .leases import LeaseService


def install_lease_routes(app: FastAPI, db: Database, leases: LeaseService) -> None:
    def idempotent_call(
        request: Request,
        payload: Any,
        fn: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        required_key: bool = True,
    ) -> dict[str, Any]:
        """Receipt-backed idempotency that does not hold SQLite's write lock across Git operations."""
        key = request.headers.get("idempotency-key")
        if required_key and not key:
            raise AppError(428, "idempotency", "An Idempotency-Key is required for this command")
        if key is not None and not 8 <= len(key) <= 200:
            raise AppError(422, "idempotency", "Use an idempotency key between 8 and 200 characters")
        digest = hashlib.sha256(
            (request.method + str(request.url.path) + "?" + str(request.url.query) + json.dumps(payload, sort_keys=True)).encode()
        ).hexdigest()
        actor = request.state.actor
        receipt_actor = actor["id"]
        with db.read() as c:
            if actor.get("kind") == "agent":
                communication_actor(c, actor)
            if key:
                receipt = c.execute(
                    "SELECT request_hash,response_json FROM receipts WHERE actor_id=? AND key=?",
                    (receipt_actor, key),
                ).fetchone()
                if receipt is not None:
                    if receipt["request_hash"] != digest:
                        raise AppError(409, "idempotency_conflict", "This key was already used for a different request")
                    return json.loads(receipt["response_json"])
        result = fn(actor)
        if key:
            with db.transaction() as c:
                existing = c.execute(
                    "SELECT request_hash,response_json FROM receipts WHERE actor_id=? AND key=?",
                    (receipt_actor, key),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != digest:
                        raise AppError(409, "idempotency_conflict", "This key was already used for a different request")
                    return json.loads(existing["response_json"])
                c.execute(
                    "INSERT INTO receipts(actor_id,key,request_hash,response_json,created_at) VALUES(?,?,?,?,?)",
                    (receipt_actor, key, digest, json.dumps(result), now()),
                )
        return result

    # Repository slot policy and base checkout read-only lock.
    # Installed before coordination_api so /repositories/{identity}/base-lock precedes /{verb}.
    @app.get("/api/v1/repositories/{identity}/slot-policy", response_model=out.SlotPolicy)
    def repository_slot_policy(identity: str) -> dict:
        return leases.get_policy(identity)

    @app.patch("/api/v1/repositories/{identity}/slot-policy", response_model=out.SlotPolicy)
    def repository_slot_policy_update(identity: str, request: Request, body: m.SlotPolicyUpdate) -> dict:
        payload = body.model_dump(exclude_unset=True)
        return idempotent_call(
            request,
            payload,
            lambda actor: leases.update_policy(actor, identity, payload),
            required_key=False,
        )

    @app.post("/api/v1/repositories/{identity}/base-lock", response_model=out.BaseLockResult)
    def repository_base_lock(identity: str, request: Request, body: m.BaseLockRequest) -> dict:
        payload = body.model_dump()
        return idempotent_call(
            request,
            payload,
            lambda actor: leases.set_base_lock(actor, identity, body.locked),
            required_key=False,
        )

    @app.get("/api/v1/repositories/{identity}/slots", response_model=out.RepositorySlots)
    def repository_slots(identity: str) -> dict:
        return leases.get_slots(identity)

    # Leases: list, acquire, read, poll, renew, release, rescue.
    # /leases/acquire is registered before /leases/{identity}.
    @app.get("/api/v1/leases", response_model=out.Leases)
    def list_leases(
        request: Request,
        repositoryId: str | None = None,
        agentId: str | None = None,
        mine: bool = False,
        state: str | None = None,
        includeClosed: bool = False,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict:
        effective_agent = agentId
        if mine:
            actor = request.state.actor
            if actor.get("kind") == "agent":
                with db.read() as c:
                    effective_agent = communication_actor(c, actor)["id"]
        return leases.list_leases(
            repository_id=repositoryId,
            agent_id=effective_agent,
            state=state,
            include_closed=includeClosed,
            limit=limit,
            offset=offset,
        )

    @app.post("/api/v1/leases/acquire", response_model=out.Lease)
    def acquire_lease(request: Request, body: m.LeaseAcquire) -> dict:
        payload = body.model_dump()
        return idempotent_call(request, payload, lambda actor: leases.acquire(actor, payload), required_key=True)

    @app.get("/api/v1/leases/{identity}", response_model=out.Lease)
    def get_lease(identity: str) -> dict:
        return leases.get_lease(identity)

    @app.post("/api/v1/leases/{identity}/poll", response_model=out.Lease)
    def poll_lease(identity: str, request: Request, body: m.LeasePoll) -> dict:
        return leases.poll(request.state.actor, identity, body.model_dump())

    @app.post("/api/v1/leases/{identity}/renew", response_model=out.Lease)
    def renew_lease(identity: str, request: Request, body: m.LeaseRenew) -> dict:
        payload = body.model_dump()
        return idempotent_call(
            request,
            payload,
            lambda actor: leases.renew(actor, identity, payload),
            required_key=True,
        )

    @app.post("/api/v1/leases/{identity}/release", response_model=out.Lease)
    def release_lease(identity: str, request: Request, body: m.LeaseRelease) -> dict:
        payload = body.model_dump()
        return idempotent_call(
            request,
            payload,
            lambda actor: leases.release(actor, identity, payload),
            required_key=True,
        )

    @app.post("/api/v1/leases/{identity}/rescue", response_model=out.Lease)
    def rescue_lease(identity: str, request: Request, body: m.LeaseRescue) -> dict:
        payload = body.model_dump()
        return idempotent_call(
            request,
            payload,
            lambda actor: leases.rescue(actor, identity, payload),
            required_key=False,
        )
