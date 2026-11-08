"""Narrow HTTP surface for reviewed repositories and self-reported activity."""

from collections.abc import Callable

from fastapi import FastAPI, Query, Request

from . import coordination_models as m
from . import responses as out
from . import support
from .common import AppError
from .coordination import (
    CoordinationService,
    _get,
    repository_apply,
    repository_confirm,
    repository_ensure,
    repository_merge,
    repository_record,
    repository_resolution,
    with_discussion,
)
from .db import Database
from .discussions import communication_actor
from .models import Expected
from .records import people_on
from .registry import OVERLAP_LIMIT, AgentRegistry


def install_coordination_routes(app: FastAPI, db: Database, mutate: Callable) -> None:
    service = CoordinationService(db)
    registry = AgentRegistry()

    read = db.snapshot

    def write(request: Request, body, fn: Callable, *, required_key=True, exclude_unset=False) -> dict:
        return mutate(request, body.model_dump(exclude_unset=exclude_unset), fn, required_key=required_key)

    @app.get("/api/v1/repositories")
    def repositories(archived: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), q: str = "") -> dict:
        return read(lambda c: service.repositories(c, archived, limit, offset, q))

    # Registered before /repositories/{identity}, which would otherwise take "resolve" as an ID.
    @app.get("/api/v1/repositories/resolve")
    def repository_resolve(url: str = Query(max_length=2000)) -> dict:
        return read(lambda c: repository_resolution(c, url))

    @app.get("/api/v1/repositories/{identity}", response_model=out.RepositoryDetail)
    def repository(identity: str) -> dict:
        # As on a work item or roadmap, the people tied to it come with the record.
        return read(lambda c: {**with_discussion(c, repository_record(_get(c, "repositories", identity))),
                               "people": people_on(c, "repository", [identity])[identity]})

    @app.post("/api/v1/repositories")
    def repository_create(request: Request, body: m.RepositoryCreate) -> dict:
        return write(request, body, lambda c, a: repository_apply(c, a, "repository.create", body.model_dump()))

    @app.post("/api/v1/repositories/ensure")
    def repository_ensure_route(request: Request, body: m.RepositoryEnsure) -> dict:
        return write(request, body, lambda c, a: repository_ensure(c, a, body.model_dump()))

    @app.patch("/api/v1/repositories/{identity}")
    def repository_update(identity: str, request: Request, body: m.RepositoryUpdate) -> dict:
        return write(request, body, lambda c, a: repository_apply(c, a, "repository.update", {"id": identity, **body.model_dump(exclude_unset=True)}), required_key=False, exclude_unset=True)

    # Registered before /repositories/{identity}/{verb}. Agents cannot reach it: merging is a person's call.
    @app.post("/api/v1/repositories/{identity}/merge")
    def repository_merge_into(identity: str, request: Request, body: m.RepositoryMerge) -> dict:
        return write(request, body, lambda c, a: repository_merge(c, a, identity, body.model_dump()))

    # The person confirms what an agent registered; registered before /repositories/{identity}/{verb}.
    @app.post("/api/v1/repositories/{identity}/confirm")
    def repository_confirm_route(identity: str, request: Request, body: Expected) -> dict:
        return write(request, body, lambda c, a: repository_confirm(c, a, identity, body.model_dump()))

    @app.post("/api/v1/repositories/{identity}/{verb}")
    def repository_archive(identity: str, verb: str, request: Request, body: Expected) -> dict:
        if verb not in {"archive", "recover"}:
            raise AppError(404, "not_found", "Unknown repository command")
        return write(request, body, lambda c, a: repository_apply(c, a, "repository." + verb, {"id": identity, **body.model_dump()}))

    @app.get("/api/v1/agent-runs")
    def runs(includeEnded: bool = False, externalChatId: str | None = None,
             limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(lambda c: service.runs(c, includeEnded, externalChatId, limit, offset))

    @app.get("/api/v1/agent-runs/{identity}")
    def run(identity: str) -> dict:
        return read(lambda c: service.run_record(_get(c, "agent_runs", identity)))

    @app.post("/api/v1/agent-runs")
    def run_create(request: Request, body: m.RunCreate) -> dict:
        return write(request, body, lambda c, a: service.create_run(c, a, body.model_dump()))

    @app.post("/api/v1/agent-runs/{identity}/end")
    def run_end(identity: str, request: Request, body: Expected) -> dict:
        return write(request, body, lambda c, a: service.end_run(c, a, identity, body.model_dump()))

    @app.get("/api/v1/participations")
    def participations(itemId: str | None = None, repositoryId: str | None = None,
                       runId: str | None = None, includeReleased: bool = False,
                       limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
        return read(lambda c: service.participations(c, itemId, repositoryId, runId, includeReleased, limit, offset))

    @app.get("/api/v1/participations/{identity}")
    def participation(identity: str) -> dict:
        return read(lambda c: service.participation_record(c, _get(c, "participations", identity)))

    @app.post("/api/v1/participations")
    def participation_create(request: Request, body: m.ParticipationCreate) -> dict:
        return write(request, body, lambda c, a: service.create_participation(c, a, body.model_dump()))

    @app.patch("/api/v1/participations/{identity}")
    def participation_update(identity: str, request: Request, body: m.ParticipationUpdate) -> dict:
        return write(request, body, lambda c, a: service.change_participation(c, a, identity, body.model_dump(exclude_unset=True)), required_key=False, exclude_unset=True)

    @app.post("/api/v1/participations/{identity}/release")
    def participation_release(identity: str, request: Request, body: Expected) -> dict:
        return write(request, body, lambda c, a: service.change_participation(c, a, identity, body.model_dump(), "release"))

    @app.post("/api/v1/participations/{identity}/rejoin")
    def participation_rejoin(identity: str, request: Request, body: Expected) -> dict:
        return write(request, body, lambda c, a: service.change_participation(c, a, identity, body.model_dump(), "rejoin"))

    @app.post("/api/v1/participations/{identity}/check-in")
    def participation_checkin(identity: str, request: Request, body: Expected) -> dict:
        return write(request, body, lambda c, a: service.change_participation(c, a, identity, body.model_dump(), "check-in"))

    @app.post("/api/v1/participations/{identity}/updates")
    def post_update(identity: str, request: Request, body: m.CoordinationUpdateCreate) -> dict:
        return write(request, body, lambda c, a: service.post_update(c, a, identity, body.model_dump()))

    # An agent tracks its own chat on the work it does, as its reported work does; the person adds
    # any chat with POST /chats.
    @app.post("/api/v1/chats/track")
    def chat_track(request: Request, body: m.ChatTrack) -> dict:
        return write(request, body, lambda c, a: support.track_own_chat(c, a, body.model_dump()))

    @app.get("/api/v1/coordination-updates")
    def updates(participationId: str | None = None, itemId: str | None = None,
                repositoryId: str | None = None, limit: int = Query(50, ge=1, le=200),
                offset: int = Query(0, ge=0)) -> dict:
        return read(lambda c: service.updates(c, participationId, itemId, repositoryId, limit, offset))

    # Who else works on these records, and what they announced: an agent checks it before it changes
    # them (agent guide, rule 11). The caller's own reports and messages are left out.
    @app.get("/api/v1/coordination-overlaps")
    def overlaps(request: Request, repositoryId: list[str] = Query([]), itemId: list[str] = Query([]),
                 stepId: list[str] = Query([]), ticketId: list[str] = Query([]),
                 limit: int = Query(20, ge=1, le=OVERLAP_LIMIT)) -> dict:
        return read(lambda c: registry.overlaps(c, communication_actor(c, request.state.actor)["id"],
                                                repositories=repositoryId, items=itemId, steps=stepId,
                                                tickets=ticketId, limit=limit))
