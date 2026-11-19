"""The Pull requests page's activity: what reached each repository's default branch, counted from its
local checkout with git (git_activity.py), and GET /pull-requests/activity, which reads the count."""

import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from test_api import client, write  # noqa: F401
from test_step_update import ok, read, refused
from worklog import git_activity as g
from worklog.coordination import repository_identity

REMOTE = "https://github.com/example/parser.git"
IDENTITY = repository_identity(REMOTE)
NOW = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)


def ago(days: int, hour: int = 12) -> str:
    """A time `days` before today, at `hour` UTC: the same date in Chicago from 6 to 23 UTC."""
    return (NOW - timedelta(days=days)).replace(hour=hour).isoformat()


@pytest.fixture(scope="module")
def git_env(tmp_path_factory):
    """Setup's own git: no system or global configuration (no hooks, no signing), fixed names."""
    empty = tmp_path_factory.mktemp("git") / "gitconfig"
    empty.write_text("")
    return {**os.environ, "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Tester",
            "GIT_AUTHOR_EMAIL": "tester@example.com", "GIT_COMMITTER_NAME": "Tester",
            "GIT_COMMITTER_EMAIL": "tester@example.com"}


class Repo:
    """An origin, a working clone that pushes to it, and the checkout Worklog reads, whose origin
    remote names REMOTE (the scanner never fetches, so the address need not be reachable)."""

    def __init__(self, root: Path, env: dict) -> None:
        self.root, self.env = root, env
        self.origin, self.work, self.checkout = root / "origin.git", root / "work", root / "checkout"

    def create(self) -> "Repo":
        self.run(self.root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.run(self.root, "init", "-q", "-b", "main", str(self.work))
        return self

    def run(self, cwd: Path, *args: str, when: str | None = None) -> str:
        env = {**self.env, **({"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else {})}
        done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
        return done.stdout.strip()

    def commit(self, name: str, text: str, subject: str, when: str) -> str:
        (self.work / name).write_text(text, newline="\n")
        self.run(self.work, "add", name)
        self.run(self.work, "commit", "-q", "-m", subject, when=when)
        return self.run(self.work, "rev-parse", "HEAD")

    def clone(self) -> None:
        self.run(self.root, "clone", "-q", str(self.origin), str(self.checkout))
        self.run(self.checkout, "remote", "set-url", "origin", REMOTE)

    def publish(self, *refs: str, force: bool = False) -> None:
        """Push from the working clone, and fetch into the checkout as a fetch from REMOTE would."""
        self.run(self.work, "push", "-q", *(["--force"] if force else []), str(self.origin), *refs)
        if self.checkout.exists():
            self.run(self.checkout, "fetch", "-q", str(self.origin), "+refs/heads/*:refs/remotes/origin/*")

    def tip(self) -> str:
        return self.run(self.checkout, "rev-parse", "refs/remotes/origin/main")


@pytest.fixture(scope="module")
def built(tmp_path_factory, git_env):
    """main: a direct commit (+2), a squash merge of PR 5 (+2 -1), and PR 7 merged as a merge
    commit bringing two lexer commits (+1, +1) on a branch kept on origin. Built once: each git
    process can take a second on Windows, and each test changes its own copy."""
    r = Repo(tmp_path_factory.mktemp("repo"), git_env).create()
    r.commit("a.txt", "one\ntwo\n", "Start the parser", ago(9))
    r.commit("a.txt", "one\nTWO\nthree\n", "Fix the parser (#5)", ago(8))
    r.run(r.work, "checkout", "-q", "-b", "lexer")
    r.commit("lex.txt", "x\n", "Start the lexer", ago(7, 9))
    r.commit("lex.txt", "x\ny\n", "Finish the lexer", ago(7, 10))
    r.run(r.work, "checkout", "-q", "main")
    r.run(r.work, "merge", "-q", "--no-ff", "lexer", "-m", "Merged PR 7: Add the lexer", when=ago(6))
    r.publish("main", "lexer")
    r.clone()
    return r


@pytest.fixture
def repo(tmp_path, built, git_env):
    shutil.copytree(built.root, tmp_path / "repo")
    return Repo(tmp_path / "repo", git_env)


def target(repo: Repo, *, identity: str = IDENTITY, paths: tuple[str, ...] | None = None,
           wanted: frozenset[int] = frozenset({5, 7})) -> g.Target:
    return g.Target("repo-1", identity, paths if paths is not None else (str(repo.checkout),), wanted)


class Recording:
    """The real git, remembering each call's arguments."""

    def __init__(self) -> None:
        self.git, self.calls = g.Git(), []

    def __call__(self, path: str, *arguments: str, **options) -> str:
        self.calls.append(arguments)
        return self.git(path, *arguments, **options)

    def logs(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] == "log"]


def scanner(tmp_path: Path, found: list[g.Target], git=None) -> g.Scanner:
    return g.Scanner(g.Cache(tmp_path / g.FILE), lambda: found, git=git)


def counted(scan: g.Scanner, *, days: int = 30, prs=(("pr-5", "repo-1", 5), ("pr-7", "repo-1", 7))) -> dict:
    return g.report(scan.cache, scan.progress(), days=days, today=NOW.date(), timezone="America/Chicago",
                    repos=[("repo-1", "Parser", IDENTITY)], prs=list(prs))


# Parsing


def test_numstat_counts_lines_and_files_with_binaries_and_renames():
    tokens = ["3\t1\tsrc/a.py", "-\t-\tlogo.png", "2\t2\t", "src/old.py", "src/new.py", "0\t4\tgone.txt", ""]
    assert g._numstat(tokens) == (5, 7, 4)


def test_commits_parse_from_log_output_with_dates_in_utc():
    output = ("\x1eaaa\x1f2026-11-15T12:13:41-06:00\0\n1\t0\tx.py\0-\t-\tlogo.png\0"
              "\x1ebbb\x1f2026-11-15T16:44:49Z\0\x1eccc\x1f2026-11-14T23:30:00+01:00\0\n2\t3\t\0a\0b\0")
    assert g.parse_commits(output) == [("aaa", "2026-11-15T18:13:41Z", 1, 0, 2),
                                       ("bbb", "2026-11-15T16:44:49Z", 0, 0, 0),
                                       ("ccc", "2026-11-14T22:30:00Z", 2, 3, 1)]


@pytest.mark.parametrize("subject, number", [
    ("Merged PR 1234: Add the lexer", 1234),
    ("Add a pull request registry to Worklog (#204)", 204),
    ("Give a roadmap a Chats view (R10) (#199)", 199),
    ("Merge pull request 3424 from feature/3424-totals into main", 3424),
    ("Merge pull request #12 from example/lexer", 12),
    ("Total the sleeves (PR 4281)", 4281),
    ("Merge pull request from nowhere", None),
    ("(#12) fix the reader", None),
    ("Merge branch 'main' into feature", None),
    ("Mention Merged PR 5: inside", None),
    ("Fix (#12) in the middle", None),
])
def test_only_a_merge_subject_names_a_pull_request(subject, number):
    assert g.merge_number(subject) == number


def test_merges_parse_from_first_parent_output():
    output = ("m1\x1f2026-11-15T12:00:00-06:00\x1fp1 p2\x1fMerged PR 7: Lexer\0"
              "s1\x1f2026-11-14T12:00:00Z\x1fp0\x1fFix \x1f odd (#5)\0"
              "d1\x1f2026-11-13T12:00:00Z\x1fpp\x1fDirect commit\0")
    assert g.parse_merges(output) == [("m1", "2026-11-15T18:00:00Z", 7, True), ("s1", "2026-11-14T12:00:00Z", 5, False)]


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/example/parser/pull/12", ("repo-1", 12)),
    ("https://github.com/Example/Parser/pull/12/files", ("repo-1", 12)),
    ("https://dev.azure.com/org/project/_git/parser/pullrequest/34?_a=files", ("repo-2", 34)),
    ("https://org.visualstudio.com/project/_git/parser/pullrequest/34", ("repo-2", 34)),
    ("https://github.com/someone/else/pull/1", (None, None)),
    ("https://github.com/example/parser/commit/abc", (None, None)),
])
def test_a_registered_pull_request_names_its_repository_and_number(url, expected):
    identities = {IDENTITY: "repo-1", "dev.azure.com/org/project/_git/parser": "repo-2"}
    assert g.pull_request_number(url, identities) == expected


# Counting a real checkout


def test_a_checkout_counts_commits_lines_and_pull_request_merges(tmp_path, repo):
    scan = scanner(tmp_path, [target(repo, paths=(str(tmp_path / "gone"), str(repo.checkout)))])
    scan.run_once()
    answer = counted(scan)
    [row] = answer["repositories"]
    assert {key: row[key] for key in ("branch", "state", "commits", "additions", "deletions", "merged", "registered")} == {
        "branch": "main", "state": "ready", "commits": 4, "additions": 6, "deletions": 1, "merged": 2, "registered": 2}
    assert row["scannedAt"] and row["detail"] == ""
    busy = {d["day"]: (d["commits"], d["additions"], d["deletions"], d["merged"]) for d in answer["daily"] if any(
        (d["commits"], d["merged"]))}
    day = {n: (NOW.date() - timedelta(days=n)).isoformat() for n in (9, 8, 7, 6)}
    assert busy == {day[9]: (1, 2, 0, 0), day[8]: (1, 2, 1, 1), day[7]: (2, 2, 0, 0), day[6]: (0, 0, 0, 1)}
    sizes = {p["number"]: (p["additions"], p["deletions"], p["files"], p["mergedAt"]) for p in answer["pullRequests"]}
    # PR 5 is a squash, sized by its own commit; PR 7 a merge, sized by what it brought to main.
    assert sizes == {5: (2, 1, 1, ago(8).replace("+00:00", "Z")), 7: (2, 0, 1, ago(6).replace("+00:00", "Z"))}
    assert len(answer["daily"]) == 30 and answer["daily"][-1]["day"] == NOW.date().isoformat()
    assert (answer["scanning"], answer["scanned"], answer["total"]) == (False, 1, 1)


def test_a_second_count_reads_only_what_is_new(tmp_path, repo):
    git = Recording()
    scan = scanner(tmp_path, [target(repo)], git)
    scan.run_once()
    old = repo.tip()
    git.calls.clear()
    scan.run_once()
    assert git.logs() == []  # nothing new: no log at all
    repo.commit("b.txt", "1\n2\n3\n", "Add b", ago(1))
    repo.publish("main")
    scan.run_once()
    assert {call[1] for call in git.logs()} == {f"{old}..{repo.tip()}"}
    row = counted(scan)["repositories"][0]
    assert (row["commits"], row["additions"]) == (5, 9)


def test_a_rewritten_branch_is_counted_again(tmp_path, repo):
    git = Recording()
    scan = scanner(tmp_path, [target(repo)], git)
    scan.run_once()
    repo.run(repo.work, "reset", "-q", "--hard", "HEAD~1")  # drops PR 7's merge
    repo.commit("c.txt", "c\n", "Rewrite (#9)", ago(2))
    repo.publish("main", force=True)
    git.calls.clear()
    scan.run_once()
    assert all(call[1] == repo.tip() and call[2].startswith("--since=") for call in git.logs())
    answer = counted(scan, prs=[("pr-7", "repo-1", 7), ("pr-9", "repo-1", 9)])
    row = answer["repositories"][0]
    assert (row["commits"], row["additions"], row["merged"]) == (3, 5, 2)
    assert {p["number"]: p["mergedAt"] is not None for p in answer["pullRequests"]} == {7: False, 9: True}


def test_origin_head_on_a_feature_branch_falls_back_to_main(tmp_path, repo):
    repo.run(repo.checkout, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/lexer")
    scan = scanner(tmp_path, [target(repo)])
    scan.run_once()
    row = counted(scan)["repositories"][0]
    assert (row["branch"], row["commits"]) == ("main", 4)


def test_a_checkout_without_main_or_master_says_so(tmp_path, repo):
    repo.run(repo.checkout, "update-ref", "-d", "refs/remotes/origin/main")
    repo.run(repo.checkout, "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
    scan = scanner(tmp_path, [target(repo)])
    scan.run_once()
    row = counted(scan)["repositories"][0]
    assert (row["state"], row["commits"]) == ("no_default_branch", 0)


def test_a_checkout_of_another_remote_or_none_at_all_is_not_counted(tmp_path, repo):
    scan = scanner(tmp_path, [target(repo, identity="github.com/example/other")])
    scan.run_once()
    assert counted(scan)["repositories"][0]["state"] == "checkout_mismatch"
    scan = scanner(tmp_path / "x", [target(repo, paths=(str(tmp_path / "gone"),))])
    (tmp_path / "x").mkdir()
    scan.run_once()
    row = counted(scan)["repositories"][0]
    assert (row["state"], row["commits"]) == ("no_checkout", 0) and "reported" in row["detail"]


def test_a_failed_count_keeps_what_the_last_one_found(tmp_path, repo):
    scan = scanner(tmp_path, [target(repo)])
    scan.run_once()
    scan.git = lambda path, *arguments, **options: (_ for _ in ()).throw(g.GitTimedOut("git took longer than 180 s"))
    scan.run_once()
    row = counted(scan)["repositories"][0]
    assert (row["state"], row["detail"], row["commits"]) == ("timed_out", "git took longer than 180 s", 4)


def test_a_slow_repository_does_not_stop_the_others(tmp_path, repo):
    real, slow_path = g.Git(), tmp_path / "slow"
    slow_path.mkdir()

    def git(path: str, *arguments: str, **options) -> str:
        if path == str(slow_path):
            raise g.GitTimedOut("git took longer than 180 s")
        return real(path, *arguments, **options)

    slow = g.Target("repo-0", IDENTITY, (str(slow_path),), frozenset())
    scan = scanner(tmp_path, [slow, target(repo)], git)
    scan.run_once()
    answer = g.report(scan.cache, scan.progress(), days=30, today=NOW.date(), timezone="America/Chicago",
                      repos=[("repo-0", "Slow", IDENTITY), ("repo-1", "Parser", IDENTITY)], prs=[])
    assert {row["name"]: row["state"] for row in answer["repositories"]} == {"Slow": "timed_out", "Parser": "ready"}


def test_a_checkouts_own_configuration_runs_nothing(tmp_path, repo):
    """Agents report checkout paths, so a checkout's config must not make git run a program: a plain
    `git log` runs gpg.program for a signed commit when log.showSignature is set; fsmonitor, textconv
    and external diffs are turned off too."""
    marker = (tmp_path / "marker").as_posix()
    script = tmp_path / "trap.sh"
    script.write_text(f'#!/bin/sh\necho ran >> "{marker}"\nexit 1\n', newline="\n")
    script.chmod(0o755)
    c = repo.checkout
    # A commit object with a signature header, so log.showSignature calls gpg.program on it.
    raw = repo.run(c, "cat-file", "commit", "refs/remotes/origin/main")
    head, _, message = raw.partition("\n\n")
    signed = head + "\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEz\n -----END PGP SIGNATURE-----\n\n" + message + "\n"
    # Bytes, so Windows writes no CRLF into the object.
    sha = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=c, env=repo.env,
                         input=signed.encode(), capture_output=True, check=True).stdout.decode().strip()
    repo.run(c, "update-ref", "refs/remotes/origin/main", sha)
    for key, value in (("log.showSignature", "true"), ("gpg.program", script.as_posix()),
                       ("core.fsmonitor", f"echo ran >> '{marker}'"),
                       ("diff.trap.textconv", f"echo ran >> '{marker}'; cat"),
                       ("diff.trap.command", f"echo ran >> '{marker}'")):
        repo.run(c, "config", key, value)
    (c / ".git" / "info" / "attributes").write_text("* diff=trap\n")
    # The control: the same checkout with git's defaults runs the trap.
    subprocess.run(["git", "-C", str(c), "log", "--numstat", "refs/remotes/origin/main"], env=g._environment(),
                   capture_output=True, check=False)
    assert Path(marker).exists()
    Path(marker).unlink()

    scan = scanner(tmp_path, [target(repo)])
    scan.run_once()
    assert counted(scan)["repositories"][0]["state"] == "ready"
    assert not Path(marker).exists()
    assert not (c / ".git" / "index.lock").exists()


def test_git_never_fetches(tmp_path, repo):
    git = Recording()
    scanner(tmp_path, [target(repo)], git).run_once()
    assert {call[0] for call in git.calls} <= {"config", "symbolic-ref", "rev-parse", "merge-base", "log", "diff"}


def test_a_cache_of_another_version_is_counted_afresh(tmp_path, repo):
    scan = scanner(tmp_path, [target(repo)])
    scan.run_once()
    path = tmp_path / g.FILE
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version=1")
    fresh = scanner(tmp_path, [target(repo)])
    assert counted(fresh)["repositories"][0]["state"] == "pending"  # the old counts are gone
    fresh.run_once()
    assert counted(fresh)["repositories"][0]["commits"] == 4
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == g.CACHE_VERSION


# Days in Worklog's timezone


def test_days_are_counted_in_worklogs_timezone_across_a_clock_change(tmp_path):
    cache = g.Cache(tmp_path / g.FILE)
    with closing(cache.connect()) as conn, conn:
        conn.executemany("INSERT INTO commits VALUES('repo-1',?,?,1,0,1)", [
            ("a", "2026-11-01T04:30:00Z"),  # 23:30 CDT, Oct 31
            ("b", "2026-11-01T05:30:00Z"),  # 00:30 CDT, Nov 1 (DST ends at 02:00)
            ("c", "2026-11-02T05:30:00Z"),  # 23:30 CST, Nov 1
            ("d", "2026-11-02T06:30:00Z"),  # 00:30 CST, Nov 2
            ("e", "2026-10-26T04:59:59Z"),  # 23:59 CDT, Oct 25: before the window
        ])
    answer = g.report(cache, {}, days=7, today=date(2026, 11, 2), timezone="America/Chicago",
                      repos=[("repo-1", "Parser", IDENTITY)], prs=[])
    assert [(d["day"], d["commits"]) for d in answer["daily"]] == [
        ("2026-10-27", 0), ("2026-10-28", 0), ("2026-10-29", 0), ("2026-10-30", 0),
        ("2026-10-31", 1), ("2026-11-01", 2), ("2026-11-02", 1)]
    assert answer["window"] == {"days": 7, "from": "2026-10-27", "to": "2026-11-02", "timezone": "America/Chicago"}
    assert answer["repositories"][0]["state"] == "pending"


def test_a_repository_being_counted_says_so(tmp_path):
    answer = g.report(g.Cache(tmp_path / g.FILE), {"running": True, "done": 1, "total": 3, "current": "repo-1"},
                      days=7, today=date(2026, 11, 2), timezone="UTC", repos=[("repo-1", "Parser", IDENTITY)], prs=[])
    assert (answer["repositories"][0]["state"], answer["scanning"], answer["scanned"], answer["total"]) == (
        "scanning", True, 1, 3)


# The route


def test_the_route_reads_the_count_of_registered_repositories_and_pull_requests(client, repo):  # noqa: F811
    repository = ok(write(client, "POST", "/repositories", {"name": "Parser", "url": REMOTE}))
    ok(write(client, "POST", "/repositories", {"name": "Notes"}))
    work = ok(write(client, "POST", "/work-items", {"title": "Parser"}))
    for number in (5, 7, 99):
        ok(write(client, "POST", "/github", {"ownerType": "item", "ownerId": work["id"], "kind": "pr",
                                             "url": f"https://github.com/example/parser/pull/{number}"}))
    ok(write(client, "POST", "/github", {"ownerType": "item", "ownerId": work["id"], "kind": "pr",
                                         "url": "https://dev.azure.com/org/p/_git/elsewhere/pullrequest/1"}))
    issued = ok(write(client, "POST", "/credentials", {"label": "Counter"}))
    stamp = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(client.app.state.db.path)) as conn, conn:
        conn.execute("INSERT INTO agent_runs VALUES('run',?,?,'run','Run','codex',NULL,1,?,?,NULL,NULL)",
                     (issued["agentId"], issued["id"], stamp, stamp))
        conn.execute("INSERT INTO participations VALUES('part','run',NULL,'',1,?,?,?,NULL,NULL)", (stamp, stamp, stamp))
        conn.execute("INSERT INTO participation_contexts VALUES('part',0,?,?,'','main')",
                     (repository["id"], str(repo.checkout)))

    pending = ok(read(client, "/pull-requests/activity?days=7"))
    assert [(r["name"], r["state"]) for r in pending["repositories"]] == [("Notes", "pending"), ("Parser", "pending")]
    assert len(pending["daily"]) == 7 and not pending["scanning"]

    client.app.state.git_activity.run_once()
    answer = ok(read(client, "/pull-requests/activity?days=30"))
    rows = {r["name"]: r for r in answer["repositories"]}
    assert [r["name"] for r in answer["repositories"]] == ["Parser", "Notes"]  # most commits first
    assert (rows["Parser"]["commits"], rows["Parser"]["registered"], rows["Parser"]["state"]) == (4, 3, "ready")
    assert (rows["Notes"]["state"], rows["Notes"]["registered"]) == ("no_checkout", 0)
    merged = {p["number"]: p["mergedAt"] is not None for p in answer["pullRequests"]}
    assert merged == {5: True, 7: True, 99: False, None: False}
    assert sum(d["commits"] for d in answer["daily"]) == 4
    assert (client.app.state.db.path.parent / g.FILE).exists()

    refused(read(client, "/pull-requests/activity?days=8"), 422, "validation")
    issued_token = {"token": issued["token"]}
    refused(read(client, "/pull-requests/activity", issued_token), 403, "agent_scope")


# What the review of the change found (2026-11-19)


def missing_objects(checkout: Path, env: dict) -> int:
    out = subprocess.run(["git", "rev-list", "--objects", "--missing=print", "--all"], cwd=checkout, env=env,
                         capture_output=True, text=True, check=True).stdout
    return sum(line.startswith("?") for line in out.splitlines())


def test_a_partial_clone_fetches_nothing_and_runs_nothing(tmp_path, repo):
    """A blobless clone would fetch the blobs a --numstat needs from its promisor remote, writing into
    the checkout and running the transport its config names (here remote.origin.uploadpack)."""
    repo.run(repo.origin, "config", "uploadpack.allowFilter", "true")
    url = repo.origin.as_uri()
    partial = tmp_path / "partial"
    repo.run(tmp_path, "clone", "-q", "--filter=blob:none", "--no-checkout", url, str(partial))
    marker = (tmp_path / "marker").as_posix()
    repo.run(partial, "config", "remote.origin.uploadpack", f"echo ran >> '{marker}'; git-upload-pack")
    missing = missing_objects(partial, repo.env)
    assert missing > 0

    scan = scanner(tmp_path, [g.Target("repo-1", repository_identity(url), (str(partial),), frozenset({5, 7}))])
    scan.run_once()
    row = counted(scan)["repositories"][0]
    assert row["state"] == "failed" and "lazy" in row["detail"].lower()
    assert not Path(marker).exists()
    assert missing_objects(partial, repo.env) == missing
    # The control: git's defaults fetch the blobs, through the configured upload-pack.
    subprocess.run(["git", "-C", str(partial), "log", "--numstat", "refs/remotes/origin/main"], env=g._environment()
                   | {"GIT_NO_LAZY_FETCH": "0"}, capture_output=True, check=False)
    assert Path(marker).exists()


@pytest.mark.parametrize("path, local", [
    ("C:/Users/me/repo" if os.name == "nt" else "/home/me/repo", True),
    (r"\\host\share\repo", False),
    ("//host/share/repo", False),
    ("repo", False),
    ("../repo", False),
])
def test_only_a_local_absolute_checkout_is_opened(path, local):
    assert g._local(path) is local


def test_a_network_share_is_never_opened(tmp_path, repo):
    git = Recording()
    scan = scanner(tmp_path, [target(repo, paths=(r"\\host\share\parser", "relative/parser"))], git)
    scan.run_once()
    assert counted(scan)["repositories"][0]["state"] == "no_checkout"
    assert git.calls == []


def test_the_checkout_counted_last_stays_first(tmp_path, repo, git_env):
    second = tmp_path / "second"
    shutil.copytree(repo.checkout, second)
    scan = scanner(tmp_path, [target(repo, paths=(str(second),))])
    scan.run_once()
    git = Recording()
    scan.git = git
    scan.read_targets = lambda: [target(repo, paths=(str(repo.checkout), str(second)))]
    scan.run_once()
    assert git.logs() == []  # still the same checkout, nothing new: no recount
    assert g.Cache(tmp_path / g.FILE).scan_of("repo-1")["path"] == str(second)


def test_a_merge_that_cannot_be_sized_leaves_the_rest_counted(tmp_path, repo):
    real = g.Git()

    def git(path: str, *arguments: str, **options) -> str:
        if arguments[0] == "diff":
            raise g.GitFailed("fatal: bad revision")
        return real(path, *arguments, **options)

    scan = scanner(tmp_path, [target(repo)], git)
    scan.run_once()
    answer = counted(scan)
    assert answer["repositories"][0]["state"] == "ready"
    sizes = {p["number"]: p["additions"] for p in answer["pullRequests"]}
    assert sizes == {5: 2, 7: None}  # the squash keeps its size; the merge waits for a later pass


def test_stopping_mid_count_marks_nothing(tmp_path, repo):
    scan = scanner(tmp_path, [target(repo)])

    def git(path: str, *arguments: str, **options) -> str:
        scan._stop.set()
        raise g.GitFailed("")

    scan.git = git
    scan.scan(target(repo))
    assert g.Cache(tmp_path / g.FILE).scan_of("repo-1") is None


def test_a_cache_deleted_while_worklog_runs_is_laid_out_again(tmp_path, repo):
    scan = scanner(tmp_path, [target(repo)])
    scan.run_once()
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / (g.FILE + suffix)).unlink(missing_ok=True)
    assert counted(scan)["repositories"][0]["state"] == "pending"
    scan.run_once()
    assert counted(scan)["repositories"][0]["commits"] == 4


def test_a_git_too_old_for_the_safe_flags_says_so(tmp_path, repo):
    def git(path: str, *arguments: str, **options) -> str:
        raise g.GitFailed("error: unknown option `no-lazy-fetch'")

    scan = scanner(tmp_path, [target(repo)], git)
    scan.run_once()
    row = counted(scan)["repositories"][0]
    assert (row["state"], row["detail"]) == ("failed", "error: unknown option `no-lazy-fetch'")


def test_a_checkout_without_an_origin_remote_is_another_remote(tmp_path, repo):
    repo.run(repo.checkout, "remote", "remove", "origin")
    scan = scanner(tmp_path, [target(repo)])
    scan.run_once()
    assert counted(scan)["repositories"][0]["state"] == "checkout_mismatch"
