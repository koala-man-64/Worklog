"""Which parts of Worklog get used: counts by feature and day, kept apart from the records.

Every API call a route serves counts once for its method and route template, as the person's or an
agent's, and as failed when it answers 400 or more. A route that takes a verb counts each verb it
accepts on its own, since archiving and recovering, or approving and rejecting, are different uses.
The browser counts each sidebar page the person opens (POST /page-views). A count names a feature,
never a record: no ID, text, search or query string is kept.

The serve process holds the counts in memory and adds them to usage.sqlite3 in the data folder every
minute and when it stops, so a crash loses at most a minute of them. When it starts it writes there
the features it serves, which is how a report tells what nothing used. The file is not the database:
backups, exports and restores leave it alone, and deleting it starts the counts over. `worklog usage`
reads it offline (report).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterable
from contextlib import closing
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args
from zoneinfo import ZoneInfo

from . import policy
from .guard import Disconnected
from .models import Page

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("worklog")

FILE = "usage.sqlite3"
# How often the serve watcher adds the counts held in memory to the file, in seconds.
FLUSH_S = 60
# How long a write waits for the file, in seconds. A write that gives up leaves its counts held for
# the next, so a stop never waits long on a file another program holds.
WRITE_WAIT_S = 1
# The file's own format, kept in its user_version. A file in another format is left as it is.
FORMAT = 1
PAGES: tuple[str, ...] = get_args(Page)
# Where the browser counts a page; those calls count as the page, not as calls.
PAGE_VIEWS = policy.PREFIX + "/page-views"
# The verbs each route that takes one accepts; test_usage.py holds each list to its handler.
VERBS: dict[str, tuple[str, ...]] = {
    policy.PREFIX + "/people/{identity}/{verb}": ("archive", "recover"),
    policy.PREFIX + "/proposals/{identity}/{verb}": ("submit", "withdraw", "reopen", "approve", "reject"),
    policy.PREFIX + "/reminders/{identity}/{verb}": ("archive", "recover"),
    policy.PREFIX + "/repositories/{identity}/{verb}": ("archive", "recover"),
    policy.PREFIX + "/resources/{identity}/{verb}": ("archive", "recover"),
    policy.PREFIX + "/roadmaps/{identity}/{verb}": ("archive", "recover"),
    policy.PREFIX + "/tickets/{ref}/{verb}": ("archive", "recover"),
    policy.PREFIX + "/work-items/{identity}/{verb}": ("archive", "recover"),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS counts(day TEXT NOT NULL, surface TEXT NOT NULL CHECK(surface IN('api','page')),
  feature TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN('person','agent')),
  uses INTEGER NOT NULL CHECK(uses>0), failures INTEGER NOT NULL CHECK(failures>=0 AND failures<=uses),
  PRIMARY KEY(day,surface,feature,actor)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS features(surface TEXT NOT NULL, feature TEXT NOT NULL, scope TEXT NOT NULL,
  PRIMARY KEY(surface,feature)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
"""
ADD = ("INSERT INTO counts(day,surface,feature,actor,uses,failures) VALUES(?,?,?,?,?,?) "
       "ON CONFLICT(day,surface,feature,actor) DO UPDATE SET uses=uses+excluded.uses, failures=failures+excluded.failures")
SET = "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"

Key = tuple[str, str, str, str]  # day, surface, feature, actor


def connect(path: Path) -> sqlite3.Connection:
    """The usage file, opened to write and made in this format if it is new."""
    conn = sqlite3.connect(path, timeout=WRITE_WAIT_S, isolation_level=None)
    try:
        found = conn.execute("PRAGMA user_version").fetchone()[0]
        if found == 0:
            conn.executescript(SCHEMA + f"PRAGMA user_version={FORMAT};")
        elif found != FORMAT:
            raise ValueError(f"{path} holds usage format {found}, which this version of Worklog does not write")
    except BaseException:
        conn.close()
        raise
    return conn


def api_feature(scope: Scope) -> str | None:
    """The feature a served call uses: its method and route template under /api/v1, with the verb in
    place for a route that takes one, such as `POST /work-items/{identity}/archive`. None for a call no
    route served with its method, for a verb its route does not accept, and for a page view."""
    route = scope.get("route")
    template = getattr(route, "path", "")
    method = scope.get("method", "")
    if (not template.startswith(policy.PREFIX + "/") or template == PAGE_VIEWS or method == "HEAD"
            or method not in (getattr(route, "methods", None) or ())):
        return None
    if "{verb}" in template:
        verb = (scope.get("path_params") or {}).get("verb")
        if verb not in VERBS.get(template, ()):
            return None
        template = template.replace("{verb}", verb)
    return f"{method} {template.removeprefix(policy.PREFIX)}"


def scope_of(method: str, template: str, verb: str | None = None) -> str:
    """Who may make a call, as policy.ROUTES says: `human` for the person alone, or an agent scope."""
    rule = policy.ROUTES.get(template.removeprefix(policy.PREFIX), {}).get(method)
    if isinstance(rule, policy.Only) and rule.query:
        # A query value decides each call, so agents reach the route in this scope at most.
        return rule.scope
    return policy.scope_for(method, template, {"verb": verb} if verb else {})


def catalog(routes: Iterable[Any]) -> list[tuple[str, str, str]]:
    """Every feature this version serves, as (surface, feature, scope): each method of each API route,
    once for each verb a route that takes one accepts, and each sidebar page."""
    found = {("page", page, policy.HUMAN) for page in PAGES}
    for route in routes:
        template = getattr(route, "path", "")
        if not template.startswith(policy.PREFIX + "/") or template == PAGE_VIEWS:
            continue
        verbs: tuple[str | None, ...] = VERBS.get(template, ()) if "{verb}" in template else (None,)
        for method in set(getattr(route, "methods", None) or ()) - {"HEAD"}:
            for verb in verbs:
                path = template if verb is None else template.replace("{verb}", verb)
                found.add(("api", f"{method} {path.removeprefix(policy.PREFIX)}", scope_of(method, template, verb)))
    return sorted(found)


class Usage:
    """The counts held in memory until flush adds them to the file. record and call may come from any
    thread, and flush from the serve watcher's. Neither call nor flush raises: counting must never
    fail a request or stop the server."""

    def __init__(self, path: Path, timezone: Callable[[], str] = lambda: "UTC",
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.path = path
        self._timezone, self._clock = timezone, clock
        self._lock = threading.Lock()
        self._held: dict[Key, list[int]] = {}  # each key's [uses, failures]
        self._zone: tzinfo = UTC
        self._zone_name = "UTC"
        self._warned = False  # counting a call failed; that is logged once
        self._failing = False  # the last write failed; logged once until a write succeeds
        self._refresh_zone()

    def _refresh_zone(self) -> None:
        """Takes up Worklog's timezone, which each day is counted in. While it can't be read, as when
        the database is busy, the one taken last stays."""
        try:
            name = self._timezone()
            self._zone, self._zone_name = ZoneInfo(name), name
        except Exception:
            pass

    def held(self) -> dict[Key, tuple[int, int]]:
        """A copy of the counts not yet written: (day, surface, feature, actor) -> (uses, failures)."""
        with self._lock:
            return {key: (uses, failures) for key, (uses, failures) in self._held.items()}

    def record(self, surface: str, feature: str, actor: str, *, failed: bool = False) -> None:
        """Counts one use of a feature, on today's date in Worklog's timezone."""
        day = self._clock().astimezone(self._zone).date().isoformat()
        with self._lock:
            counts = self._held.setdefault((day, surface, feature, actor), [0, 0])
            counts[0] += 1
            counts[1] += int(failed)

    def call(self, scope: Scope, status: int) -> None:
        """Counts an API call by its feature (api_feature), as the person's or an agent's, and as
        failed when it answered 400 or more. A call with no feature counts for nothing."""
        try:
            feature = api_feature(scope)
            if feature is None:
                return
            state = scope.get("state")
            actor = state.get("actor") if isinstance(state, dict) else None
            caller = "agent" if isinstance(actor, dict) and actor.get("kind") == "agent" else "person"
            self.record("api", feature, caller, failed=status >= 400)
        except Exception:
            if not self._warned:
                self._warned = True
                logger.warning("A call was not counted in usage; the call itself went on", exc_info=True)

    def flush(self, *, refresh_zone: bool = True) -> bool:
        """Adds the counts held to the file and, unless told not to, takes up a changed timezone for
        the next ones, which reads the database. Counts a failed write could not add stay held for
        the next flush."""
        with self._lock:
            batch, self._held = self._held, {}
        written = not batch or self._write(batch)
        if not written:
            with self._lock:
                for key, (uses, failures) in batch.items():
                    counts = self._held.setdefault(key, [0, 0])
                    counts[0] += uses
                    counts[1] += failures
        if refresh_zone:
            self._refresh_zone()
        return written

    def _write(self, batch: dict[Key, list[int]]) -> bool:
        try:
            with closing(connect(self.path)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.executemany(ADD, [(*key, uses, failures) for key, (uses, failures) in batch.items()])
                conn.execute(SET, ("timezone", self._zone_name))
                conn.commit()
        except Exception:
            if not self._failing:
                logger.warning("Usage counts could not be written to %s; they are kept for the next try",
                               self.path, exc_info=True)
            self._failing = True
            return False
        if self._failing:
            logger.warning("Usage counts are written to %s again", self.path)
        self._failing = False
        return True

    def describe(self, features: Iterable[tuple[str, str, str]], version: str) -> bool:
        """Puts the features this version serves, as catalog lists them, in place of those the last
        version to run wrote, so a report can list what nothing used. A failure is logged, not raised."""
        try:
            with closing(connect(self.path)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM features")
                conn.executemany("INSERT INTO features(surface,feature,scope) VALUES(?,?,?)", features)
                conn.executemany(SET, [("version", version), ("timezone", self._zone_name)])
                conn.commit()
        except Exception:
            logger.warning("The features this version serves could not be written to %s", self.path, exc_info=True)
            return False
        return True


class UsageMiddleware:
    """Counts each API call a route serves (Usage.call). It sits inside the request guard, so a call
    the guard refuses reaches no route and counts for nothing."""

    def __init__(self, app: ASGIApp, *, usage: Usage) -> None:
        self.app, self.usage = app, usage

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # A call that raises before it answers counts as failed: the guard answers an error with 500.
        status = 500

        async def watched(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, watched)
        except Disconnected:
            # The client left before the body an upload reads itself arrived: nothing was answered,
            # so nothing counts.
            raise
        except BaseException:
            self.usage.call(scope, status)
            raise
        self.usage.call(scope, status)


def _zone(name: str | None) -> tzinfo:
    try:
        return ZoneInfo(name) if name else UTC
    except (KeyError, ValueError):
        return UTC


def report(path: Path, days: int = 30, today: date | None = None) -> dict[str, Any]:
    """What was used in the `days` days to today, and what the last version to run serves that nothing
    used in them, from the file alone. Today is Worklog's, in the timezone it last wrote counts in.
    Each page and call carries the last day anything used it, if ever; a call's scope is null when
    the last version to run no longer serves it."""
    if not path.is_file():
        raise ValueError(f"No usage is recorded in {path.parent} yet. Worklog counts it while it runs "
                         "and writes it there every minute and when it stops.")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
            found = conn.execute("PRAGMA user_version").fetchone()[0]
            if found == 0:
                raise ValueError(f"No usage is recorded in {path} yet")
            if found != FORMAT:
                raise ValueError(f"{path} holds usage format {found}, which this version of Worklog does not read")
            meta = dict(conn.execute("SELECT key,value FROM meta").fetchall())
            today = today or datetime.now(_zone(meta.get("timezone"))).date()
            start = (today - timedelta(days=days - 1)).isoformat()
            since = conn.execute("SELECT MIN(day) FROM counts").fetchone()[0]
            last = {(s, f): d for s, f, d in conn.execute("SELECT surface,feature,MAX(day) FROM counts GROUP BY surface,feature")}
            window: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
            for s, f, actor, uses, failures in conn.execute(
                    "SELECT surface,feature,actor,SUM(uses),SUM(failures) FROM counts WHERE day>=? "
                    "GROUP BY surface,feature,actor", (start,)):
                window.setdefault((s, f), {})[actor] = (uses, failures)
            served = {(s, f): scope for s, f, scope in conn.execute("SELECT surface,feature,scope FROM features")}
    except sqlite3.DatabaseError as exc:
        if isinstance(exc, sqlite3.OperationalError) and any(word in str(exc) for word in ("locked", "busy")):
            raise ValueError(f"{path} is busy ({exc}); try again") from exc
        raise ValueError(f"{path} cannot be read ({exc}). Starting Worklog repairs a file a crash left half "
                         "written; if it still cannot be read, delete it with Worklog stopped to start the "
                         "counts over.") from exc
    pages: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for (surface, feature), by in sorted(window.items()):
        person, agent = by.get("person", (0, 0)), by.get("agent", (0, 0))
        if surface == "page":
            pages.append({"page": feature, "views": person[0] + agent[0], "lastUsed": last[surface, feature]})
        else:
            calls.append({"call": feature, "scope": served.get((surface, feature)), "person": person[0],
                          "agent": agent[0], "failed": person[1] + agent[1], "lastUsed": last[surface, feature]})
    pages.sort(key=lambda row: -row["views"])
    calls.sort(key=lambda row: -(row["person"] + row["agent"]))
    unused = [(s, f, scope) for (s, f), scope in sorted(served.items()) if (s, f) not in window]
    return {
        "file": str(path.resolve()),
        "version": meta.get("version"),
        "recordedSince": since,
        "window": {"days": days, "from": start, "to": today.isoformat()},
        "pages": {"used": pages,
                  "unused": [{"page": f, "lastUsed": last.get((s, f))} for s, f, _ in unused if s == "page"]},
        "calls": {"used": calls,
                  "unused": [{"call": f, "scope": scope, "lastUsed": last.get((s, f))} for s, f, scope in unused if s == "api"]},
    }
