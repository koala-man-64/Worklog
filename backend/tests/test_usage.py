"""Usage counts (usage.py): which features get used, counted by name and day apart from the records,
and `worklog usage`, which reports what was used and what nothing used."""

import ast
import json
import logging
import re
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_guard import call as guarded_call
from worklog import __version__, cli, policy, usage
from worklog.app import create_app
from worklog.usage import PAGES, VERBS, Usage, catalog, report

ORIGIN = "http://127.0.0.1:8877"
PLACEHOLDER = "00000000-0000-4000-8000-000000000000"
NAVIGATION = Path(__file__).resolve().parents[2] / "frontend" / "src" / "navigation.ts"
BOOTSTRAP_COUNTED = [("api", "POST /session/bootstrap", "person", 1, 0)]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def started(tmp_path):
    """The app, the person's client with a session, and an agent's client with a credential."""
    app = create_app(tmp_path / "data", testing=True)
    with TestClient(app, base_url=ORIGIN) as person, TestClient(app, base_url=ORIGIN) as agent:
        person.headers["Origin"] = ORIGIN
        boot = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        person.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        issued = person.post("/api/v1/credentials", json={"label": "Counted"}, headers={"Idempotency-Key": str(uuid.uuid4())})
        agent.headers["Authorization"] = "Bearer " + issued.json()["token"]
        yield app, person, agent


def counted(app) -> dict[tuple[str, str, str], tuple[int, int]]:
    """The counts held in memory, (surface, feature, actor) -> (uses, failures), whatever their day."""
    totals: dict[tuple[str, str, str], tuple[int, int]] = {}
    for (_, surface, feature, actor), (uses, failures) in app.state.usage.held().items():
        before = totals.get((surface, feature, actor), (0, 0))
        totals[surface, feature, actor] = (before[0] + uses, before[1] + failures)
    return totals


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def test_each_call_a_route_serves_counts_for_its_route_and_caller_and_names_no_record(started):
    app, person, agent = started
    item = person.post("/api/v1/work-items", json={"title": "Counted work"}, headers=key()).json()["id"]
    assert person.get(f"/api/v1/work-items/{item}").status_code == 200
    assert person.get(f"/api/v1/work-items/{PLACEHOLDER}").status_code == 404
    assert agent.get("/api/v1/work-items?q=Counted").status_code == 200
    # The guard refuses these before any route: an agent's write to the person's work, and a caller
    # without a session.
    assert agent.post("/api/v1/work-items", json={"title": "Refused"}, headers=key()).status_code == 403
    assert TestClient(app, base_url=ORIGIN).get("/api/v1/work-items").status_code == 401
    # No route serves these with their method, so no handler runs.
    assert person.get("/api/v1/nonexistent").status_code == 404
    assert person.delete(f"/api/v1/work-items/{item}").status_code == 405
    # HEAD is never counted, whether a route answers it or not.
    assert person.head("/api/v1/work-items").status_code in {200, 405}
    counts = counted(app)
    assert counts == {
        ("api", "POST /session/bootstrap", "person"): (1, 0),
        ("api", "POST /credentials", "person"): (1, 0),
        ("api", "POST /work-items", "person"): (1, 0),
        ("api", "GET /work-items/{identity}", "person"): (2, 1),
        ("api", "GET /work-items", "agent"): (1, 0),
    }
    assert all(len(day) == 10 for day, *_ in app.state.usage.held())


def test_a_verb_counts_on_its_own_and_one_its_route_does_not_take_counts_for_nothing(started):
    app, person, agent = started
    item = person.post("/api/v1/work-items", json={"title": "Archived"}, headers=key()).json()
    assert person.post(f"/api/v1/work-items/{item['id']}/archive", json={"revision": item["revision"]}).status_code == 200
    assert person.post(f"/api/v1/work-items/{item['id']}/shred", json={"revision": 1}).status_code == 404
    counts = counted(app)
    assert counts[("api", "POST /work-items/{identity}/archive", "person")] == (1, 0)
    assert not [feature for _, feature, _ in counts if "shred" in feature or "{verb}" in feature]


def test_the_person_counts_a_page_by_its_name_and_only_the_sidebars_pages(started):
    app, person, agent = started
    assert person.post("/api/v1/page-views", json={"page": "today"}).json() == {"ok": True}
    assert person.post("/api/v1/page-views", json={"page": "today"}).status_code == 200
    assert person.post("/api/v1/page-views", json={"page": "work"}).status_code == 200
    assert person.post("/api/v1/page-views", json={"page": "work/123"}).status_code == 422
    assert person.post("/api/v1/page-views", json={"page": "work", "id": PLACEHOLDER}).status_code == 422
    refused = agent.post("/api/v1/page-views", json={"page": "today"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "agent_scope"
    counts = counted(app)
    assert {k: v for k, v in counts.items() if k[0] == "page"} == {
        ("page", "today", "person"): (2, 0), ("page", "work", "person"): (1, 0)}
    # A page view counts as its page, never as a call.
    assert not [feature for _, feature, _ in counts if "page-views" in feature]


def test_a_call_that_fails_in_its_route_counts_as_failed_and_an_upload_whose_client_leaves_counts_for_nothing(
        started, monkeypatch):
    app, person, agent = started
    assert person.post("/api/v1/work-items", json={}, headers=key()).status_code == 422

    def broken(*args, **kwargs):
        raise RuntimeError("a bug")

    monkeypatch.setattr(app.state.work.items, "list", broken)
    assert person.get("/api/v1/work-items").status_code == 500

    async def leaves():
        return {"type": "http.disconnect"}

    # An upload reads its own body, past the guard, which answers a client that left nothing.
    assert guarded_call(app, "POST", "/api/v1/attachments", [(b"authorization", agent.headers["Authorization"].encode())],
                        leaves, query=b"ownerType=proposal&ownerId=missing&name=evidence.txt") is None
    counts = counted(app)
    assert counts["api", "POST /work-items", "person"] == (1, 1)
    assert counts["api", "GET /work-items", "person"] == (1, 1)
    assert not [k for k in counts if k[1] == "POST /attachments"]


def test_a_call_that_cannot_be_counted_still_answers_and_the_failure_is_logged_once(started, monkeypatch, caplog):
    app, person, _ = started

    def broken(*args, **kwargs):
        raise RuntimeError("counting broke")

    monkeypatch.setattr(app.state.usage, "record", broken)
    with caplog.at_level(logging.WARNING, logger="worklog"):
        assert person.get("/api/v1/work-items").status_code == 200
        assert person.get("/api/v1/people").status_code == 200
    assert len([r for r in caplog.records if "was not counted" in r.getMessage()]) == 1


def test_flush_adds_the_counts_to_the_file_and_later_counts_add_to_them(tmp_path):
    path = tmp_path / usage.FILE
    log = Usage(path, lambda: "America/Chicago", Clock(datetime(2026, 11, 8, 15, tzinfo=UTC)))
    log.record("api", "GET /work-items", "person")
    log.record("api", "GET /work-items", "person", failed=True)
    log.record("page", "today", "person")
    assert log.flush() and log.held() == {}
    log.record("api", "GET /work-items", "person")
    log.record("api", "GET /work-items", "agent")
    assert log.flush()
    # Nothing held writes nothing.
    assert log.flush()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT day,surface,feature,actor,uses,failures FROM counts ORDER BY 2,3,4").fetchall() == [
            ("2026-11-08", "api", "GET /work-items", "agent", 1, 0),
            ("2026-11-08", "api", "GET /work-items", "person", 3, 1),
            ("2026-11-08", "page", "today", "person", 1, 0)]
        assert dict(conn.execute("SELECT key,value FROM meta").fetchall()) == {"timezone": "America/Chicago"}
        assert conn.execute("PRAGMA user_version").fetchone()[0] == usage.FORMAT


def test_a_write_that_fails_keeps_the_counts_for_the_next_and_is_logged_once(tmp_path, caplog):
    folder = tmp_path / "not-yet"
    log = Usage(folder / usage.FILE, clock=Clock(datetime(2026, 11, 8, 15, tzinfo=UTC)))
    log.record("page", "work", "person")
    with caplog.at_level(logging.WARNING, logger="worklog"):
        assert not log.flush()
        log.record("page", "work", "person", failed=True)
        assert not log.flush()
    assert len([r for r in caplog.records if "could not be written" in r.getMessage()]) == 1
    assert list(log.held().values()) == [(2, 1)]
    folder.mkdir()
    assert log.flush() and log.held() == {}
    with closing(sqlite3.connect(folder / usage.FILE)) as conn:
        assert conn.execute("SELECT feature,uses,failures FROM counts").fetchall() == [("work", 2, 1)]


def test_a_file_in_another_format_is_left_as_it_is_and_its_counts_kept(tmp_path):
    path = tmp_path / usage.FILE
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version=99")
    log = Usage(path)
    log.record("page", "work", "person")
    assert not log.flush()
    assert not log.describe([("page", "work", "human")], "1.0.0")
    assert list(log.held().values()) == [(1, 0)]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_each_use_counts_on_worklogs_day_and_a_changed_timezone_from_the_next_flush(tmp_path):
    zone = ["America/Chicago"]
    # 21:30 on 7 November in Chicago; 12:30 on 8 November in Tokyo.
    log = Usage(tmp_path / usage.FILE, lambda: zone[0], Clock(datetime(2026, 11, 8, 3, 30, tzinfo=UTC)))
    log.record("page", "today", "person")
    zone[0] = "Asia/Tokyo"
    log.record("page", "today", "person")
    assert {day for day, *_ in log.held()} == {"2026-11-07"}
    assert log.flush()
    log.record("page", "today", "person")
    assert {day for day, *_ in log.held()} == {"2026-11-08"}
    # A timezone that can't be read keeps the last one.
    zone[0] = "Not/AZone"
    assert log.flush()
    log.record("page", "today", "person")
    assert {day for day, *_ in log.held()} == {"2026-11-08"}


def test_the_catalog_lists_each_feature_this_version_serves_with_who_may_use_it(started):
    app, _, _ = started
    features = catalog(app.router.routes)
    scopes = {(surface, feature): scope for surface, feature, scope in features}
    assert len(scopes) == len(features)
    assert scopes["api", "GET /work-items"] == policy.READ
    assert scopes["api", "POST /work-items"] == policy.HUMAN
    assert scopes["api", "POST /work-items/{identity}/archive"] == policy.HUMAN
    assert scopes["api", "POST /proposals/{identity}/approve"] == policy.HUMAN
    assert scopes["api", "POST /proposals/{identity}/submit"] == policy.PROPOSE
    # Agents attach evidence to a proposal; any other upload is the person's.
    assert scopes["api", "POST /attachments"] == policy.PROPOSE
    assert scopes["api", "POST /session/bootstrap"] == policy.PUBLIC
    assert scopes["api", "GET /openapi.json"] == policy.HUMAN
    assert [feature for surface, feature, _ in features if surface == "page"] == sorted(PAGES)
    assert not [feature for _, feature, _ in features
                if "page-views" in feature or feature.startswith("HEAD ") or "{verb}" in feature]
    names = {feature for _, feature, _ in features}
    for route in app.router.routes:
        template = getattr(route, "path", "")
        if not template.startswith(policy.PREFIX + "/") or template == usage.PAGE_VIEWS:
            continue
        paths = [template.replace("{verb}", verb) for verb in VERBS[template]] if "{verb}" in template else [template]
        for method in route.methods - {"HEAD"}:
            for path in paths:
                assert f"{method} {path.removeprefix(policy.PREFIX)}" in names, (method, path)


def verbs_in_handlers() -> dict[str, set[str]]:
    """Each {verb} route's verbs as its handler's `if verb not in {...}` check lists them."""
    found: dict[str, set[str]] = {}
    for source in sorted(Path(usage.__file__).parent.glob("*.py")):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            templates = [decorator.args[0].value for decorator in node.decorator_list
                         if isinstance(decorator, ast.Call) and decorator.args
                         and isinstance(decorator.args[0], ast.Constant) and "{verb}" in str(decorator.args[0].value)]
            if not templates:
                continue
            checks = [ast.literal_eval(test.comparators[0]) for test in ast.walk(node)
                      if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "verb"
                      and isinstance(test.ops[0], ast.NotIn)]
            assert len(checks) == 1, f"{source.name}: {node.name} needs one `if verb not in {{...}}` check"
            found.update(dict.fromkeys(templates, set(checks[0])))
    return found


def test_each_route_that_takes_a_verb_lists_exactly_the_verbs_its_handler_accepts(started):
    app, _, _ = started
    assert {route.path for route in app.router.routes if "{verb}" in getattr(route, "path", "")} == set(VERBS)
    assert verbs_in_handlers() == {template: set(verbs) for template, verbs in VERBS.items()}
    for template, verbs in VERBS.items():
        for verb in verbs:
            path = template.replace("{identity}", PLACEHOLDER).replace("{ref}", "T-1").replace("{verb}", verb)
            served = policy.served(app.router.routes, {"type": "http", "method": "POST", "path": path, "root_path": ""})
            # No route of its own, such as /repositories/{identity}/merge, takes the verb first.
            assert served is not None and served[0] == template, (template, verb)


def test_the_pages_counted_are_the_sidebars_then_the_guides_other_views():
    sidebar = re.findall(r'route: "([a-z-]+)"', NAVIGATION.read_text(encoding="utf-8"))
    views = re.findall(r'route: "([a-z-]+)"', (NAVIGATION.parent / "Guide.tsx").read_text(encoding="utf-8"))
    assert "guide" in sidebar and views[0] == "guide"
    assert [*sidebar, *views[1:]] == list(PAGES)


def test_the_report_shows_what_was_used_in_the_window_and_what_nothing_used(tmp_path):
    path = tmp_path / usage.FILE
    clock = Clock(datetime(2026, 10, 1, 15, tzinfo=UTC))
    log = Usage(path, lambda: "America/Chicago", clock)
    assert log.describe([("api", "GET /work-items", policy.READ), ("api", "POST /work-items", policy.HUMAN),
                         ("api", "POST /roadmaps/{identity}/restore", policy.HUMAN),
                         ("page", "today", policy.HUMAN), ("page", "releases", policy.HUMAN), ("page", "work", policy.HUMAN)],
                        "9.9.9")
    # Used before the window only.
    log.record("api", "POST /roadmaps/{identity}/restore", "person")
    log.record("page", "releases", "person")
    assert log.flush()
    clock.now = datetime(2026, 11, 6, 15, tzinfo=UTC)
    for _ in range(3):
        log.record("api", "GET /work-items", "agent")
    log.record("api", "GET /work-items", "person", failed=True)
    log.record("page", "today", "person")
    log.record("page", "today", "person")
    log.record("page", "work", "person")
    # A call an older version served, which the version that last ran no longer does.
    log.record("api", "GET /retired", "person")
    assert log.flush()
    result = report(path, 7, date(2026, 11, 8))
    assert result["file"] == str(path.resolve())
    assert (result["version"], result["recordedSince"]) == ("9.9.9", "2026-10-01")
    assert result["window"] == {"days": 7, "from": "2026-11-02", "to": "2026-11-08"}
    assert result["pages"] == {
        "used": [{"page": "today", "views": 2, "lastUsed": "2026-11-06"}, {"page": "work", "views": 1, "lastUsed": "2026-11-06"}],
        "unused": [{"page": "releases", "lastUsed": "2026-10-01"}]}
    assert result["calls"] == {
        "used": [{"call": "GET /work-items", "scope": policy.READ, "person": 1, "agent": 3, "failed": 1, "lastUsed": "2026-11-06"},
                 {"call": "GET /retired", "scope": None, "person": 1, "agent": 0, "failed": 0, "lastUsed": "2026-11-06"}],
        "unused": [{"call": "POST /roadmaps/{identity}/restore", "scope": policy.HUMAN, "lastUsed": "2026-10-01"},
                   {"call": "POST /work-items", "scope": policy.HUMAN, "lastUsed": None}]}
    # A wider window takes in the older uses; today defaults to Worklog's, from the file.
    wide = report(path, 3650)
    assert {row["page"] for row in wide["pages"]["used"]} == {"today", "work", "releases"}
    assert [row["call"] for row in wide["calls"]["unused"]] == ["POST /work-items"]


def test_a_window_starts_on_its_first_day_and_describe_replaces_the_last_versions_features(tmp_path):
    path = tmp_path / usage.FILE
    clock = Clock(datetime(2026, 11, 1, 18, tzinfo=UTC))
    log = Usage(path, clock=clock)
    assert log.describe([("page", "work", policy.HUMAN), ("page", "people", policy.HUMAN)], "1.0.0")
    assert log.describe([("page", "today", policy.HUMAN), ("page", "work", policy.HUMAN)], "1.1.0")
    # The day before a seven-day window to 8 November, then its first day.
    log.record("page", "work", "person")
    clock.now = datetime(2026, 11, 2, 18, tzinfo=UTC)
    log.record("page", "today", "person")
    assert log.flush()
    result = report(path, 7, date(2026, 11, 8))
    assert (result["version"], result["window"]["from"]) == ("1.1.0", "2026-11-02")
    assert result["pages"] == {"used": [{"page": "today", "views": 1, "lastUsed": "2026-11-02"}],
                               "unused": [{"page": "work", "lastUsed": "2026-11-01"}]}


def test_the_report_says_what_to_do_when_the_file_is_missing_empty_or_unreadable(tmp_path):
    path = tmp_path / usage.FILE
    with pytest.raises(ValueError, match="No usage is recorded"):
        report(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE unrelated(x)")
    with pytest.raises(ValueError, match="No usage is recorded"):
        report(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version=2")
    with pytest.raises(ValueError, match="usage format 2"):
        report(path)
    path.write_bytes(b"not a database, though long enough to look like a header" * 4)
    with pytest.raises(ValueError, match="delete it"):
        report(path)


def test_worklog_usage_prints_the_report_and_says_when_nothing_is_recorded(tmp_path, capsys):
    data = tmp_path / "data"
    assert cli.main(["--data-dir", str(data), "usage"]) == 1
    assert "No usage is recorded" in capsys.readouterr().err
    data.mkdir()
    log = Usage(data / usage.FILE)
    assert log.describe([("page", "today", policy.HUMAN), ("page", "work", policy.HUMAN)], __version__)
    log.record("page", "today", "person")
    assert log.flush()
    assert cli.main(["--data-dir", str(data), "usage", "--days", "7"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["window"]["days"] == 7
    assert [row["page"] for row in printed["pages"]["used"]] == ["today"]
    assert printed["pages"]["unused"] == [{"page": "work", "lastUsed": None}]
    for days in ("0", "3651", "week"):
        with pytest.raises(SystemExit):
            cli.main(["--data-dir", str(data), "usage", "--days", days])


def counts_in(data: Path) -> list[tuple]:
    with closing(sqlite3.connect(data / usage.FILE)) as conn:
        return conn.execute("SELECT surface,feature,actor,uses,failures FROM counts").fetchall()


def serve_once(tmp_path, monkeypatch, while_running) -> Path:
    """Runs serve on a data folder of its own with a stand-in for Uvicorn, which makes one call as the
    person, then while_running(data folder), then stops. Returns the data folder."""
    # serve sets these for the server it runs; monkeypatch puts them back afterwards.
    for name in ("WORKLOG_DATA_DIR", "WORKLOG_PORT", "WORKLOG_INSTANCE_ID"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("WORKLOG_AUTO_BACKUP", "0")
    data, local = tmp_path / "data", "http://127.0.0.1:8899"

    class Server:
        should_exit = False

        def __init__(self, app) -> None:
            self.app = app

        def run(self) -> None:
            with TestClient(self.app, base_url=local) as client:
                client.headers["Origin"] = local
                boot = client.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
                assert boot.status_code == 200
            while_running(data)

    monkeypatch.setattr(cli, "_server", lambda app, port: Server(app))
    cli.serve(data, 8899)
    return data


def test_serve_writes_what_it_serves_when_it_starts_and_the_counts_when_it_stops(tmp_path, monkeypatch):
    seen: list[list[tuple]] = []
    data = serve_once(tmp_path, monkeypatch, lambda data: seen.append(counts_in(data)))
    # While it ran, the watcher's minute had not come: the count reached the file at the stop.
    assert seen == [[]]
    assert counts_in(data) == BOOTSTRAP_COUNTED
    with closing(sqlite3.connect(data / usage.FILE)) as conn:
        features = conn.execute("SELECT surface,feature,scope FROM features").fetchall()
        assert ("api", "GET /work-items", policy.READ) in features and ("page", "guide", policy.HUMAN) in features
        assert dict(conn.execute("SELECT key,value FROM meta").fetchall()) == {
            "version": __version__, "timezone": "America/Chicago"}


def test_serve_adds_the_counts_to_the_file_each_period_while_it_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "FLUSH_S", 0.05)
    seen: list[list[tuple]] = []

    def wait_for_the_watcher(data: Path) -> None:
        deadline = time.monotonic() + 10
        while not (rows := counts_in(data)) and time.monotonic() < deadline:
            time.sleep(0.05)
        seen.append(rows)

    data = serve_once(tmp_path, monkeypatch, wait_for_the_watcher)
    assert seen == [BOOTSTRAP_COUNTED]
    # The flush at the stop found nothing left to add, so nothing counts twice.
    assert counts_in(data) == BOOTSTRAP_COUNTED
