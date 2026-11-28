"""The person's read-only view of work moving across agents, chats and repositories."""

from pathlib import Path

from fastapi import FastAPI, Query

from . import responses as out
from .active_sessions import running_sessions
from .db import Database
from .live import LiveService
from .transcripts import TranscriptReader


def install_live_routes(
    app: FastAPI,
    db: Database,
    transcripts: TranscriptReader,
    *,
    antigravity_brain_root: Path | None = None,
    check_antigravity: bool = True,
) -> None:
    service = LiveService(
        transcripts,
        antigravity_brain_root=antigravity_brain_root,
        check_antigravity=check_antigravity,
    )

    @app.get("/api/v1/live", response_model=out.LiveSnapshot)
    def live(
        roadmapId: str | None = Query(None, max_length=200),
        roadmapIds: list[str] | None = Query(None),
        windowMinutes: int = Query(60, ge=5, le=1440),
        includeIdle: bool = False,
        includeRevoked: bool | None = Query(None),
    ) -> dict:
        # Process/session records and transcript files are deliberately read outside the database
        # snapshot. A slow or damaged local transcript must never hold SQLite's shared read lock.
        # Each running session as its own record gives it; no other record is read for a process ID
        # nothing shows (audit finding P7).
        found = running_sessions(transcripts.roots.claude_sessions)
        draft = db.snapshot(
            lambda c: service.snapshot(
                c,
                found,
                roadmap_id=roadmapId,
                roadmap_ids=roadmapIds,
                window_minutes=windowMinutes,
                include_idle=includeIdle,
                include_revoked=includeRevoked,
            )
        )
        return service.enrich(draft)
