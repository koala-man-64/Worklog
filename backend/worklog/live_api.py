"""The person's read-only view of work moving across agents, chats and repositories."""

import os
from pathlib import Path

from fastapi import FastAPI, Query

from . import responses as out
from .active_sessions import RECORD, RECORD_LIMIT, read_record, running_sessions
from .db import Database
from .live import LiveService
from .transcripts import TranscriptReader


def _session_pids(folders: tuple[Path, ...], sessions: list[dict]) -> list[dict]:
    """Add the process ID kept in the same session record without changing /claude-sessions."""
    missing = {session["sessionId"] for session in sessions if "pid" not in session}
    paths: list[tuple[float, Path]] = []
    for folder in folders:
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if RECORD.fullmatch(entry.name):
                        try:
                            paths.append((entry.stat().st_mtime, Path(entry.path)))
                        except OSError:
                            continue
        except OSError:
            continue
    pids: dict[str, int] = {}
    for _, path in sorted(paths, reverse=True)[:RECORD_LIMIT]:
        record = read_record(path)
        if record and record["sessionId"] in missing:
            pids.setdefault(record["sessionId"], int(record["pid"]))
    return [
        {**session, "pid": int(session.get("pid") or pids.get(session["sessionId"], 0))}
        for session in sessions
    ]


def install_live_routes(
    app: FastAPI,
    db: Database,
    transcripts: TranscriptReader,
    *,
    antigravity_brain_root: Path | None = None,
    check_antigravity: bool = True,
) -> None:
    service = LiveService(transcripts, antigravity_brain_root=antigravity_brain_root, check_antigravity=check_antigravity)

    @app.get("/api/v1/live", response_model=out.LiveSnapshot)
    def live(
        roadmapId: str | None = Query(None, max_length=200),
        windowMinutes: int = Query(60, ge=5, le=1440),
        includeIdle: bool = False,
    ) -> dict:
        # Process/session records and transcript files are deliberately read outside the database
        # snapshot. A slow or damaged local transcript must never hold SQLite's shared read lock.
        found = _session_pids(
            transcripts.roots.claude_sessions, running_sessions(transcripts.roots.claude_sessions)
        )
        draft = db.snapshot(
            lambda c: service.snapshot(
                c,
                found,
                roadmap_id=roadmapId,
                window_minutes=windowMinutes,
                include_idle=includeIdle,
            )
        )
        return service.enrich(draft)
