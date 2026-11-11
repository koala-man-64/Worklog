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
reads it offline (report); the Usage page reads it through the running server, with the counts held
for its next write (live_report).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args
from zoneinfo import ZoneInfo

from . import policy
from .guard import Disconnected
from .models import Actor, Page

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
        # Held by a flush from taking the counts to putting back those its write could not add, and by
        # snapshot while it reads: each count is then in the file or held, never in both or neither.
        self._flushing = threading.Lock()
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

    def snapshot[T](self, read: Callable[[], T]) -> tuple[T, dict[Key, tuple[int, int]]]:
        """read()'s answer, which reads the file, and the counts held then (held), with no flush
        between them: a flush adds counts to the file and takes them from those held, so one between
        the two would show its counts in neither, or in both. A flush waits until this returns."""
        with self._flushing:
            return read(), self.held()

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
        with self._flushing:
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


def _start(today: date, days: int) -> str:
    """The first of the `days` days to today."""
    return (today - timedelta(days=days - 1)).isoformat()


@dataclass
class Counts:
    """What a report sums: from its window's first day (`start`) on, each feature's uses and failures
    by actor, and each day's by surface, actor and whether the call was a read (a GET); and, over
    every day, the first day anything was used and the last day each feature was. With an `actor`, it
    sums that actor's uses alone, though `since` still goes by everyone's: counting began then."""

    start: str
    since: str | None = None
    actor: Actor | None = None
    # (surface, feature) -> the last day it was used
    last: dict[tuple[str, str], str] = field(default_factory=dict)
    # (surface, feature, actor) -> [uses, failures]
    features: dict[tuple[str, str, str], list[int]] = field(default_factory=dict)
    # (day, surface, actor, read) -> [uses, failures]
    days: dict[tuple[str, str, str, bool], list[int]] = field(default_factory=dict)

    def add(self, day: str, surface: str, feature: str, actor: str, uses: int, failures: int) -> None:
        """Takes in one count the file doesn't hold, such as one held for the next write."""
        self.since = min(self.since or day, day)
        if self.actor is not None and actor != self.actor:
            return
        self.last[surface, feature] = max(self.last.get((surface, feature), day), day)
        if day >= self.start:
            for sums in (self.features.setdefault((surface, feature, actor), [0, 0]),
                         self.days.setdefault((day, surface, actor, feature.startswith("GET ")), [0, 0])):
                sums[0] += uses
                sums[1] += failures


@dataclass
class Recorded:
    """What the file says for a report: its meta (version, timezone), the report's last day, the counts,
    and the features the last version to run serves, with their scopes."""

    meta: dict[str, str]
    today: date
    counts: Counts
    served: dict[tuple[str, str], str]


def _read(path: Path, days: int, today: date | None = None, *, actor: Actor | None = None) -> Recorded | None:
    """The file's counts for the `days` days to today, or None while it records nothing yet: no file,
    or one nothing has written. Today is Worklog's, in the timezone it last wrote counts in, unless
    given. With an `actor`, that actor's counts alone (Counts). Every query reads the same state of
    the file."""
    if not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
            found = conn.execute("PRAGMA user_version").fetchone()[0]
            if found == 0:
                return None
            if found != FORMAT:
                raise ValueError(f"{path} holds usage format {found}, which this version of Worklog does not read")
            # One read transaction, so a write between two of the queries cannot make them disagree.
            conn.execute("BEGIN")
            meta = dict(conn.execute("SELECT key,value FROM meta").fetchall())
            today = today or datetime.now(_zone(meta.get("timezone"))).date()
            counts = Counts(_start(today, days), conn.execute("SELECT MIN(day) FROM counts").fetchone()[0],
                            actor=actor)
            # Each query below keeps a row when no actor is asked for, or when the row is that actor's.
            asked = {"start": counts.start, "actor": actor}
            counts.last = {(s, f): d for s, f, d in conn.execute(
                "SELECT surface,feature,MAX(day) FROM counts WHERE :actor IS NULL OR actor=:actor "
                "GROUP BY surface,feature", asked)}
            counts.features = {(s, f, a): [uses, failures] for s, f, a, uses, failures in conn.execute(
                "SELECT surface,feature,actor,SUM(uses),SUM(failures) FROM counts "
                "WHERE day>=:start AND (:actor IS NULL OR actor=:actor) GROUP BY surface,feature,actor", asked)}
            counts.days = {(d, s, a, bool(read)): [uses, failures] for d, s, a, read, uses, failures in conn.execute(
                "SELECT day,surface,actor,substr(feature,1,4)='GET ' AS read,SUM(uses),SUM(failures) FROM counts "
                "WHERE day>=:start AND (:actor IS NULL OR actor=:actor) GROUP BY day,surface,actor,read", asked)}
            served = {(s, f): scope for s, f, scope in conn.execute("SELECT surface,feature,scope FROM features")}
    except sqlite3.DatabaseError as exc:
        if isinstance(exc, sqlite3.OperationalError) and any(word in str(exc) for word in ("locked", "busy")):
            raise ValueError(f"{path} is busy ({exc}); try again") from exc
        raise ValueError(f"{path} cannot be read ({exc}). Starting Worklog repairs a file a crash left half "
                         "written; if it still cannot be read, delete it with Worklog stopped to start the "
                         "counts over.") from exc
    return Recorded(meta, today, counts, served)


def report(path: Path, days: int = 30, today: date | None = None) -> dict[str, Any]:
    """What was used in the `days` days to today, and what the last version to run serves that nothing
    used in them, from the file alone (summary). Today is Worklog's, in the timezone it last wrote
    counts in."""
    if not path.is_file():
        raise ValueError(f"No usage is recorded in {path.parent} yet. Worklog counts it while it runs "
                         "and writes it there every minute and when it stops.")
    recorded = _read(path, days, today)
    if recorded is None:
        raise ValueError(f"No usage is recorded in {path} yet")
    return summary(path, days, recorded.today, recorded.counts, recorded.served, recorded.meta.get("version"))


def live_report(usage: Usage, days: int, today: date, served: Iterable[tuple[str, str, str]],
                version: str, *, actor: Actor | None = None) -> dict[str, Any]:
    """What report() says, for the running server whose counts `usage` holds: those in the file and those
    held for its next write, each once (Usage.snapshot), to `today`. What nothing used goes by `served`,
    the features this version serves as catalog lists them. Before anything is written, the counts
    held are all there is. With an `actor`, it reports that actor's uses alone (summary)."""
    recorded, held = usage.snapshot(lambda: _read(usage.path, days, today, actor=actor))
    counts = recorded.counts if recorded else Counts(_start(today, days), actor=actor)
    for (day, surface, feature, who), (uses, failures) in held.items():
        counts.add(day, surface, feature, who, uses, failures)
    return summary(usage.path, days, today, counts, {(s, f): scope for s, f, scope in served}, version)


def summary(path: Path, days: int, today: date, counts: Counts, served: Mapping[tuple[str, str], str],
            version: str | None) -> dict[str, Any]:
    """The report: each page and call used in the window with its uses, the most used first, and what
    `served` lists that nothing used in it, by name; each with the last day anything used it, if ever.
    A call's scope is null when `served` no longer lists it. `daily` sums each day that has counts,
    oldest first: the page views, the reads (GET) and changes (any other method) the person and
    agents made, and the calls that failed. `actor` is null when the counts are everyone's. With an
    actor they are that actor's alone, and what nothing used is what it didn't of what it may use:
    for agents, no page and no call that is the person's alone (policy.agent_may)."""
    used: dict[tuple[str, str], dict[str, list[int]]] = {}
    for (surface, feature, actor), sums in counts.features.items():
        used.setdefault((surface, feature), {})[actor] = sums
    pages: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for (surface, feature), by in sorted(used.items()):
        person, agent = by.get("person", [0, 0]), by.get("agent", [0, 0])
        if surface == "page":
            pages.append({"page": feature, "views": person[0] + agent[0], "lastUsed": counts.last[surface, feature]})
        else:
            calls.append({"call": feature, "scope": served.get((surface, feature)), "person": person[0],
                          "agent": agent[0], "failed": person[1] + agent[1], "lastUsed": counts.last[surface, feature]})
    pages.sort(key=lambda row: -row["views"])
    calls.sort(key=lambda row: -(row["person"] + row["agent"]))
    unused = [(s, f, scope) for (s, f), scope in sorted(served.items())
              if (s, f) not in used and (counts.actor != "agent" or policy.agent_may(scope))]
    daily: dict[str, dict[str, Any]] = {}
    for (day, surface, actor, read), (uses, failures) in sorted(counts.days.items()):
        entry = daily.setdefault(day, {"day": day, "pageViews": 0, "reads": {"person": 0, "agent": 0},
                                       "changes": {"person": 0, "agent": 0}, "failed": 0})
        if surface == "page":
            entry["pageViews"] += uses
        else:
            entry["reads" if read else "changes"][actor] += uses
            entry["failed"] += failures
    return {
        "file": str(path.resolve()),
        "version": version,
        "recordedSince": counts.since,
        "window": {"days": days, "from": counts.start, "to": today.isoformat()},
        "actor": counts.actor,
        "daily": list(daily.values()),
        "pages": {"used": pages,
                  "unused": [{"page": f, "lastUsed": counts.last.get((s, f))} for s, f, _ in unused if s == "page"]},
        "calls": {"used": calls,
                  "unused": [{"call": f, "scope": scope, "lastUsed": counts.last.get((s, f))}
                             for s, f, scope in unused if s == "api"]},
    }
