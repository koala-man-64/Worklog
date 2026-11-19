"""What reached each registered repository's default branch, read from its local checkout.

The Pull requests page charts commits per day, lines added and removed, and pull requests merged, by
repository (GET /pull-requests/activity). None of that is in the database. A background scanner, which
the serve process starts and no request waits on, reads each repository's checkout with git and keeps
what it counts in a side file, git-activity.sqlite3 next to worklog.sqlite3, as usage.sqlite3 is. The
file is derived: backups and restores leave it out, and deleting it costs only a recount.

Git only reads, and never fetches: the counts are as fresh as the checkout's last fetch, which each
repository reports. Checkout paths come from agents' participation reports, so every call turns off
what a repository's own configuration could make git run (fsmonitor, signature checks, external diffs,
textconv), no call goes through a shell, and a checkout counts only while its origin remote is the
repository's own.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
import threading
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .coordination import repository_identity

logger = logging.getLogger("worklog")

FILE = "git-activity.sqlite3"
# How far back a repository's first count, or a recount after its branch was rewritten, reaches.
FIRST_SCAN_DAYS = 365
# How often a pass goes over every repository again, and how long one git call may take. A full count
# reads up to a year at once: on real checkouts all 15 took 131 s together, but one had needed 88 s for
# 90 days cold (2026-11-19), so a full count's log gets far longer than other calls, never ending in a
# timeout that would repeat each pass.
SCAN_EVERY_S = 600
GIT_TIMEOUT_S = 180
FULL_COUNT_TIMEOUT_S = 1800
WINDOWS = (7, 30, 90, 365)
BRANCHES = ("main", "master")

# How the commit a pull request lands on the default branch words its subject: Azure DevOps' "Merged
# PR 123: title", "Merge pull request 123 from branch into main" and "title (PR 123)"; GitHub's merge
# commit "Merge pull request #123 from owner/branch" and squash "title (#123)". All seen in real
# checkouts (2026-11-19).
MERGE_SUBJECT = re.compile(r"^Merged PR (\d+):|^Merge pull request #?(\d+) from |\((?:#|PR )(\d+)\)$")
# The cache file's layout and what it counts as a merge: a file of another version is counted afresh.
CACHE_VERSION = 2
# A pull request's address: Azure DevOps' .../_git/repo/pullrequest/N, GitHub's .../pull/N.
PR_URL = re.compile(r"^(?P<repository>.+?)/(?:pullrequest|pull)/(?P<number>\d+)(?:[/?#].*)?$", re.IGNORECASE)

# Every call: no pager or optional locks, and none of the repository settings that make git run a
# program for a log or a diff. log.showSignature would run gpg.program; diff drivers run with
# --no-ext-diff and --no-textconv on each command that diffs. A partial clone would fetch the blobs a
# diff needs from its promisor remote, writing into the checkout and running what its config names
# for the transport (uploadpack, sshCommand, credential helpers): --no-lazy-fetch (git 2.44) refuses.
SAFE = ("--no-pager", "--no-optional-locks", "--no-lazy-fetch", "-c", "core.fsmonitor=false",
        "-c", "log.showSignature=false")
NO_PROGRAMS = ("--no-ext-diff", "--no-textconv")

SCHEMA = """
CREATE TABLE IF NOT EXISTS commits(repository_id TEXT NOT NULL, sha TEXT NOT NULL, committed_at TEXT NOT NULL,
    additions INTEGER NOT NULL, deletions INTEGER NOT NULL, files INTEGER NOT NULL, PRIMARY KEY(repository_id, sha));
CREATE INDEX IF NOT EXISTS commits_committed ON commits(committed_at);
CREATE TABLE IF NOT EXISTS merges(repository_id TEXT NOT NULL, sha TEXT NOT NULL, committed_at TEXT NOT NULL,
    pr_number INTEGER NOT NULL, merge INTEGER NOT NULL, additions INTEGER, deletions INTEGER, files INTEGER,
    PRIMARY KEY(repository_id, sha));
CREATE INDEX IF NOT EXISTS merges_number ON merges(repository_id, pr_number);
CREATE TABLE IF NOT EXISTS scans(repository_id TEXT PRIMARY KEY, checkout_path TEXT NOT NULL, branch TEXT NOT NULL,
    tip TEXT NOT NULL, fetched_at TEXT, scanned_at TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL);
"""


class GitFailed(Exception):
    """git could not start or exited non-zero. The message is its first line on stderr."""


class GitTimedOut(GitFailed):
    pass


class Unscannable(Exception):
    """A repository this pass cannot count, with the state and reason the page shows."""

    def __init__(self, state: str, detail: str) -> None:
        super().__init__(detail)
        self.state, self.detail = state, detail


def _environment() -> dict[str, str]:
    # The server's own GIT_* variables (GIT_DIR, GIT_WORK_TREE...) must not point git elsewhere.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1")
    return env


def _local(path: str) -> bool:
    """A path on this machine's own disks, absolute. An agent's report could name a network share,
    which opening would connect to (sending this user's credentials on Windows), or a relative path,
    which would resolve against wherever the server runs."""
    return os.path.isabs(path) and not path.startswith(("\\\\", "//")) and (os.name != "nt" or path[1:2] == ":")


class Git:
    """Runs one git command at a time against a checkout and returns its stdout. stop() kills the
    command running and refuses the rest, so a stopping server never waits on a slow repository."""

    def __init__(self, timeout: float = GIT_TIMEOUT_S) -> None:
        self.timeout = timeout
        self._lock = threading.Lock()
        self._running: subprocess.Popen[bytes] | None = None
        self._stopped = False

    def __call__(self, path: str, *arguments: str, timeout: float | None = None) -> str:
        timeout = timeout or self.timeout
        with self._lock:
            if self._stopped:
                raise GitFailed("Worklog is stopping")
            try:
                process = subprocess.Popen(["git", *SAFE, "-C", path, *arguments], stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_environment(),
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                raise GitFailed("git could not start") from None
            self._running = process
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise GitTimedOut(f"git took longer than {timeout:g} s") from None
        finally:
            with self._lock:
                self._running = None
        if process.returncode:
            lines = err.decode("utf-8", "replace").strip().splitlines()
            raise GitFailed(lines[0][:300] if lines else "")
        return out.decode("utf-8", "replace")

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._running is not None:
                self._running.kill()


def _utc(stamp: str) -> str:
    """A git ISO date as UTC text that sorts as it reads."""
    return datetime.fromisoformat(stamp).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _numstat(tokens: Iterable[str]) -> tuple[int, int, int]:
    """Lines added and removed, and files changed, from `--numstat -z` entries. A binary file ("-")
    counts as changed with no lines. A rename's entry has an empty path followed by both paths."""
    additions = deletions = files = 0
    pending = 0  # paths still to skip after a rename's entry
    for token in tokens:
        if pending:
            pending -= 1
            continue
        parts = token.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        files += 1
        additions += int(added) if added.isdigit() else 0
        deletions += int(removed) if removed.isdigit() else 0
        if not path:
            pending = 2
    return additions, deletions, files


def parse_commits(output: str) -> list[tuple[str, str, int, int, int]]:
    """(sha, committed_at in UTC, additions, deletions, files) from
    `git log -z --numstat --format=%x1e%H%x1f%cI`."""
    found = []
    for chunk in output.split("\x1e")[1:]:
        header, _, rest = chunk.partition("\0")
        sha, _, stamp = header.partition("\x1f")
        if not sha or not stamp:
            continue
        found.append((sha.strip(), _utc(stamp.strip()), *_numstat(rest.lstrip("\n").split("\0"))))
    return found


def merge_number(subject: str) -> int | None:
    """The pull request a default-branch commit's subject says it merges, if it says so."""
    match = MERGE_SUBJECT.search(subject.strip())
    return int(next(group for group in match.groups() if group)) if match else None


def parse_merges(output: str) -> list[tuple[str, str, int, bool]]:
    """(sha, committed_at in UTC, pull request number, is a merge commit) for each pull request merge
    in `git log -z --first-parent --format=%H%x1f%cI%x1f%P%x1f%s`."""
    found = []
    for record in output.split("\0"):
        fields = record.strip("\n").split("\x1f", 3)
        if len(fields) != 4:
            continue
        sha, stamp, parents, subject = fields
        number = merge_number(subject.splitlines()[0] if subject else "")
        if number is not None:
            found.append((sha, _utc(stamp), number, len(parents.split()) > 1))
    return found


def pull_request_number(url: str, identities: dict[str, str]) -> tuple[str | None, int | None]:
    """A registered pull request's repository (by its remote's identity) and number, from its address."""
    match = PR_URL.match(url.strip())
    if not match:
        return None, None
    repository = identities.get(repository_identity(match["repository"]))
    return (repository, int(match["number"])) if repository else (None, None)


@dataclass(frozen=True)
class Target:
    """A repository to count: its remote's identity, the checkouts agents reported for it (newest
    report first), and the numbers of its registered pull requests, whose merges get their own size."""

    repository_id: str
    identity: str
    paths: tuple[str, ...]
    wanted: frozenset[int]


def _identities(conn: sqlite3.Connection) -> dict[str, str]:
    return {row[1]: row[0] for row in conn.execute(
        "SELECT id, remote_identity FROM repositories WHERE remote_identity<>'' AND duplicate_of IS NULL "
        "AND merged_into IS NULL")}


def registered(conn: sqlite3.Connection) -> list[tuple[str, str | None, int | None]]:
    """(reference id, repository id, number) for each unarchived pull request reference."""
    identities = _identities(conn)
    return [(row[0], *pull_request_number(row[1], identities)) for row in conn.execute(
        "SELECT id, url FROM github_refs WHERE kind='pr' AND archived=0 ORDER BY created_at DESC, id DESC")]


def repositories(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """(id, name, remote identity) of each repository the page counts: active, neither a duplicate
    nor merged into another."""
    return [tuple(row) for row in conn.execute(
        "SELECT id, name, remote_identity FROM repositories WHERE archived=0 AND duplicate_of IS NULL "
        "AND merged_into IS NULL ORDER BY name COLLATE NOCASE, id")]


def targets(conn: sqlite3.Connection) -> list[Target]:
    paths: dict[str, list[str]] = {}
    for repository_id, checkout, worktree in conn.execute(
            "SELECT pc.repository_id, pc.checkout_path, pc.worktree_path FROM participation_contexts pc "
            "JOIN participations p ON p.id=pc.participation_id ORDER BY p.last_seen_at DESC, p.id"):
        known = paths.setdefault(repository_id, [])
        for path in (checkout, worktree):
            if path and path not in known:
                known.append(path)
    wanted: dict[str, set[int]] = {}
    for _, repository_id, number in registered(conn):
        if repository_id and number is not None:
            wanted.setdefault(repository_id, set()).add(number)
    return [Target(repository_id, identity, tuple(paths.get(repository_id, ())),
                   frozenset(wanted.get(repository_id, ())))
            for repository_id, _, identity in repositories(conn)]


class Cache:
    """The side file. The scanner writes it; requests read it. Each use opens its own connection."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        """A connection to a file of this version: one of another version, a new one, or one deleted
        while Worklog runs, is laid out afresh, so everything is counted again."""
        conn = sqlite3.connect(self.path, timeout=10)
        if conn.execute("PRAGMA user_version").fetchone()[0] != CACHE_VERSION:
            with self._lock:
                if conn.execute("PRAGMA user_version").fetchone()[0] != CACHE_VERSION:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript("DROP TABLE IF EXISTS commits; DROP TABLE IF EXISTS merges; "
                                       "DROP TABLE IF EXISTS scans;")
                    conn.executescript(SCHEMA)
                    conn.execute(f"PRAGMA user_version={CACHE_VERSION}")
        return conn

    def scan_of(self, repository_id: str) -> dict | None:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT checkout_path, branch, tip FROM scans WHERE repository_id=?",
                               (repository_id,)).fetchone()
        return {"path": row[0], "branch": row[1], "tip": row[2]} if row else None

    def mark(self, repository_id: str, state: str, detail: str, scanned_at: str) -> None:
        """A pass that couldn't count the repository: the counts and tip it had stay for the next."""
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO scans(repository_id,checkout_path,branch,tip,fetched_at,scanned_at,state,detail) "
                "VALUES(?,'','','',NULL,?,?,?) ON CONFLICT(repository_id) DO UPDATE SET scanned_at=excluded.scanned_at,"
                "state=excluded.state,detail=excluded.detail", (repository_id, scanned_at, state, detail))

    def record(self, repository_id: str, *, path: str, branch: str, tip: str, fetched_at: str | None,
               scanned_at: str, restart: bool, commits: list, merges: list) -> None:
        with closing(self.connect()) as conn, conn:
            if restart:
                conn.execute("DELETE FROM commits WHERE repository_id=?", (repository_id,))
                conn.execute("DELETE FROM merges WHERE repository_id=?", (repository_id,))
            conn.executemany("INSERT OR REPLACE INTO commits(repository_id,sha,committed_at,additions,deletions,files) "
                             "VALUES(?,?,?,?,?,?)",
                             [(repository_id, *commit) for commit in commits])
            conn.executemany("INSERT OR IGNORE INTO merges(repository_id,sha,committed_at,pr_number,merge) "
                             "VALUES(?,?,?,?,?)", [(repository_id, sha, at, number, int(merge))
                                                   for sha, at, number, merge in merges])
            conn.execute(
                "INSERT OR REPLACE INTO scans(repository_id,checkout_path,branch,tip,fetched_at,scanned_at,state,detail) "
                "VALUES(?,?,?,?,?,?,'ready','')",
                (repository_id, path, branch, tip, fetched_at, scanned_at))

    def unsized(self, repository_id: str, numbers: Iterable[int]) -> list[str]:
        """Merge commits of these pull requests whose size isn't counted yet."""
        numbers = list(numbers)
        if not numbers:
            return []
        with closing(self.connect()) as conn:
            return [row[0] for row in conn.execute(
                f"SELECT sha FROM merges WHERE repository_id=? AND merge=1 AND additions IS NULL "
                f"AND pr_number IN ({','.join('?' * len(numbers))})", (repository_id, *numbers))]

    def size(self, repository_id: str, sha: str, additions: int, deletions: int, files: int) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE merges SET additions=?,deletions=?,files=? WHERE repository_id=? AND sha=?",
                         (additions, deletions, files, repository_id, sha))


def _fetched_at(common_dir: str) -> str | None:
    try:
        stamp = (Path(common_dir) / "FETCH_HEAD").stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Scanner:
    """Counts every repository in turn, once at start and then every SCAN_EVERY_S, on its own thread.
    A repository is counted incrementally from the tip the last pass reached; a rewritten branch, or a
    change of checkout or branch, counts it again from FIRST_SCAN_DAYS back."""

    def __init__(self, cache: Cache, read_targets: Callable[[], list[Target]], *, git: Callable[..., str] | None = None,
                 every: float = SCAN_EVERY_S, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.cache, self.read_targets = cache, read_targets
        self.git = git or Git()
        self.every, self.clock = every, clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress = {"running": False, "done": 0, "total": 0, "current": None}

    def progress(self) -> dict:
        with self._lock:
            return dict(self._progress)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="git-activity", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        stop = getattr(self.git, "stop", None)
        if stop:
            stop()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                # The database may be busy or the cache unwritable: the next pass tries again.
                logger.warning("Counting git activity failed", exc_info=True)
            self._stop.wait(self.every)

    def run_once(self) -> None:
        found = self.read_targets()
        with self._lock:
            self._progress = {"running": True, "done": 0, "total": len(found), "current": None}
        try:
            for target in found:
                if self._stop.is_set():
                    return
                with self._lock:
                    self._progress["current"] = target.repository_id
                self.scan(target)
                with self._lock:
                    self._progress["done"] += 1
        finally:
            with self._lock:
                self._progress.update(running=False, current=None)

    def _stamp(self) -> str:
        return self.clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def scan(self, target: Target) -> None:
        try:
            self._count(target)
        except Exception as exc:
            if self._stop.is_set():
                return  # stopping killed git: the counts stand as the last pass left them
            if isinstance(exc, Unscannable):
                self.cache.mark(target.repository_id, exc.state, exc.detail, self._stamp())
            elif isinstance(exc, GitTimedOut):
                self.cache.mark(target.repository_id, "timed_out", str(exc), self._stamp())
            elif isinstance(exc, GitFailed):
                self.cache.mark(target.repository_id, "failed", str(exc) or "git failed", self._stamp())
            else:
                logger.warning("Counting git activity for a repository failed", exc_info=True)
                self.cache.mark(target.repository_id, "failed", "Counting failed unexpectedly", self._stamp())

    def _checkout(self, target: Target, last: str | None) -> str:
        """The first reported checkout that is a local folder whose origin is the repository's: the one
        the last count read while it still is, so two agents' checkouts don't take turns, each change
        of checkout counting everything again."""
        if not target.identity:
            raise Unscannable("no_checkout", "This repository has no remote address to match a checkout to")
        mismatched = False
        for path in sorted(target.paths, key=lambda path: path != last):
            if not _local(path) or not os.path.isdir(path):
                continue
            try:
                url = self.git(path, "config", "--get", "remote.origin.url").strip()
            except GitTimedOut:
                raise
            except GitFailed as exc:
                if str(exc):
                    raise  # git itself failed, as a git before 2.44 refusing --no-lazy-fetch: say so
                mismatched = True  # no origin remote: git says nothing
                continue
            if repository_identity(url) == target.identity:
                return path
            mismatched = True
        if mismatched:
            raise Unscannable("checkout_mismatch", "No reported checkout has this repository as its origin remote")
        raise Unscannable("no_checkout", "No agent has reported a checkout of this repository that exists")

    def _tip(self, path: str, branch: str) -> str | None:
        try:
            return self.git(path, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}^{{commit}}").strip() or None
        except GitTimedOut:
            raise
        except GitFailed:
            return None

    def _branch(self, path: str) -> tuple[str, str]:
        """The default branch to count and its tip. origin/HEAD decides only when it names main or
        master: some checkouts' origin/HEAD points at a feature branch."""
        try:
            head = self.git(path, "symbolic-ref", "-q", "refs/remotes/origin/HEAD").strip().removeprefix("refs/remotes/origin/")
        except GitTimedOut:
            raise
        except GitFailed:
            head = ""
        for branch in ([head] if head in BRANCHES else []) + [b for b in BRANCHES if b != head]:
            if tip := self._tip(path, branch):
                return branch, tip
        raise Unscannable("no_default_branch", "Neither origin/main nor origin/master is in this checkout")

    def _count(self, target: Target) -> None:
        previous = self.cache.scan_of(target.repository_id)
        path = self._checkout(target, previous["path"] if previous else None)
        branch, tip = self._branch(path)
        common = self.git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        base = None
        if previous and previous["tip"] and (previous["path"], previous["branch"]) == (path, branch):
            if previous["tip"] == tip:
                base = tip
            else:
                try:
                    self.git(path, "merge-base", "--is-ancestor", previous["tip"], tip)
                    base = previous["tip"]
                except GitTimedOut:
                    raise
                except GitFailed:
                    base = None  # rewritten, or the old tip is gone: count again
        commits: list = []
        merges: list = []
        if base != tip:
            reach = [f"{base}..{tip}"] if base else [tip, f"--since={FIRST_SCAN_DAYS}.days"]
            commits = parse_commits(self.git(path, "log", *reach, "--no-merges", "-z", "--numstat", "-M",
                                             "--no-show-signature", *NO_PROGRAMS, "--format=%x1e%H%x1f%cI",
                                             timeout=None if base else FULL_COUNT_TIMEOUT_S))
            merges = parse_merges(self.git(path, "log", *reach, "--first-parent", "-z", "--no-show-signature",
                                           "--format=%H%x1f%cI%x1f%P%x1f%s"))
        self.cache.record(target.repository_id, path=path, branch=branch, tip=tip, fetched_at=_fetched_at(common),
                          scanned_at=self._stamp(), restart=base is None, commits=commits, merges=merges)
        # A squash commit's size is its own; a merge commit's is what it brought to the first parent,
        # counted only for registered pull requests.
        for sha in self.cache.unsized(target.repository_id, target.wanted):
            if self._stop.is_set():
                return
            try:
                output = self.git(path, "diff", "--numstat", "-z", "-M", *NO_PROGRAMS, f"{sha}^1", sha)
            except GitTimedOut:
                raise
            except GitFailed:
                continue  # a parent missing, as past a shallow clone's edge: the others are still sized
            self.cache.size(target.repository_id, sha, *_numstat(output.split("\0")))


def _bounds(start: date, end: date, zone: ZoneInfo) -> tuple[str, str]:
    """The UTC text range of local days start through end."""
    def at(day: date) -> str:
        return datetime.combine(day, time.min, zone).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return at(start), at(end + timedelta(days=1))


def report(cache: Cache, progress: dict, *, days: int, today: date, timezone: str,
           repos: list[tuple[str, str, str]], prs: list[tuple[str, str | None, int | None]]) -> dict:
    """GET /pull-requests/activity: each day's commits, lines and merged pull requests in the window,
    each repository's totals and state, and each registered pull request's merge, if one is found."""
    zone = ZoneInfo(timezone)
    start = today - timedelta(days=days - 1)
    low, high = _bounds(start, today, zone)
    daily = {start + timedelta(days=n): [0, 0, 0, 0] for n in range(days)}
    totals = {repository_id: [0, 0, 0, 0] for repository_id, _, _ in repos}

    def tally(repository_id: str, stamp: str, values: tuple[int, ...], offset: int) -> None:
        if repository_id not in totals:
            return
        day = datetime.fromisoformat(stamp).astimezone(zone).date()
        for at, value in enumerate(values, offset):
            daily[day][at] += value
            totals[repository_id][at] += value

    sizes: dict[tuple[str, int], tuple] = {}
    with closing(cache.connect()) as conn:
        for repository_id, stamp, additions, deletions in conn.execute(
                "SELECT repository_id, committed_at, additions, deletions FROM commits "
                "WHERE committed_at>=? AND committed_at<?", (low, high)):
            tally(repository_id, stamp, (1, additions, deletions), 0)
        for repository_id, stamp in conn.execute(
                "SELECT repository_id, committed_at FROM merges WHERE committed_at>=? AND committed_at<?", (low, high)):
            tally(repository_id, stamp, (1,), 3)
        scans = {row[0]: row[1:] for row in conn.execute(
            "SELECT repository_id, branch, fetched_at, scanned_at, state, detail FROM scans")}
        # The latest merge of each registered pull request, sized by its own row (a merge commit) or by
        # its commit's (a squash, which counts among the commits).
        for repository_id, number, sha, stamp, additions, deletions, files in conn.execute(
                "SELECT m.repository_id, m.pr_number, m.sha, m.committed_at, "
                "CASE WHEN m.merge THEN m.additions ELSE c.additions END, "
                "CASE WHEN m.merge THEN m.deletions ELSE c.deletions END, "
                "CASE WHEN m.merge THEN m.files ELSE c.files END "
                "FROM merges m LEFT JOIN commits c ON c.repository_id=m.repository_id AND c.sha=m.sha "
                "ORDER BY m.committed_at"):
            sizes[(repository_id, number)] = (sha, stamp, additions, deletions, files)

    registered_in: dict[str, int] = {}
    for _, repository_id, _ in prs:
        if repository_id:
            registered_in[repository_id] = registered_in.get(repository_id, 0) + 1
    rows = []
    for repository_id, name, _ in repos:
        branch, fetched_at, scanned_at, state, detail = scans.get(repository_id, ("", None, None, "pending", ""))
        if progress.get("current") == repository_id:
            state = "scanning"
        commits, additions, deletions, merged = totals[repository_id]
        rows.append({"id": repository_id, "name": name, "branch": branch, "state": state, "detail": detail,
                     "fetchedAt": fetched_at, "scannedAt": scanned_at, "commits": commits, "additions": additions,
                     "deletions": deletions, "merged": merged, "registered": registered_in.get(repository_id, 0)})
    rows.sort(key=lambda row: (-row["commits"], -row["merged"], row["name"].casefold()))
    pulls = []
    for reference_id, repository_id, number in prs:
        sha, stamp, additions, deletions, files = sizes.get((repository_id, number), (None,) * 5) \
            if repository_id else (None,) * 5
        pulls.append({"id": reference_id, "repositoryId": repository_id, "number": number, "mergedAt": stamp,
                      "sha": sha, "additions": additions, "deletions": deletions, "files": files})
    running = bool(progress.get("running"))
    return {
        "window": {"days": days, "from": start.isoformat(), "to": today.isoformat(), "timezone": timezone},
        "daily": [{"day": day.isoformat(), "commits": c, "additions": a, "deletions": d, "merged": m}
                  for day, (c, a, d, m) in daily.items()],
        "repositories": rows,
        "pullRequests": pulls,
        "scanning": running,
        "scanned": progress.get("done", 0) if running else len(repos),
        "total": progress.get("total", 0) if running else len(repos),
    }
