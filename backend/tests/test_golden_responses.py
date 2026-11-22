"""What every read returns, and every write the golden world makes, pinned before responses are typed
(consolidation step 5.3, A-03).

The plan's *Rules* say typed responses keep today's shapes: booleans stored as 0 or 1, timestamps
with or without microseconds, columns holding JSON text, null and empty meaning different things,
event payloads carrying `_statusChanged`. So each read is pinned first. A world built through the
API holds a record of every kind; each GET route is read as the person, and as an agent, and its
answer, with IDs, times and today's date replaced by placeholders, must equal its file in
backend/tests/golden. Each write route the world calls is pinned the same way, by the answer of
its first call. Typing a route must leave its golden file as it is. After a deliberate change to
what a route returns, write the files again with

    ./.venv/Scripts/python.exe backend/tests/test_golden_responses.py

which builds the world twice and refuses to write unless both give the same answers. It writes each
file with its keys sorted, and only the files whose answer changed, so the diff shows only the
routes that changed.

A time's placeholder keeps its shape, `<time.ffffff+hh:mm>` for 2026-11-22T17:00:00.123456+00:00,
so a change to how times are written shows. The world runs on a clock of its own (steady_clock), so
its answers don't depend on how finely the machine's clock ticks.
"""

import difflib
import json
import os
import re
import sys
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

if __name__ == "__main__":
    # Run as a script: import this checkout's backend, from a clean environment.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    for name in [key for key in os.environ if key.upper().startswith("WORKLOG_")]:
        del os.environ[name]

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from worklog.app import create_app  # noqa: E402

ORIGIN = "http://127.0.0.1:8877"
GOLDEN = Path(__file__).with_name("golden")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?P<fraction>\.\d+)?(?P<offset>[+-]\d\d:\d\d|Z)?$")
# Values that change from world to world where no ID or time shows: a random avatar suggested, a
# feed cursor, which encodes the data ID, and a credential's token.
KEYED = {"suggested": "<avatar>", "cursor": "<cursor>", "token": "<token>"}
# Reads whose answer is not JSON: files and archives, pinned by their own tests.
NOT_JSON = {"/export"}
# Reads whose answer counts the golden world's own calls, by date: every new route or call, and every
# new day, would change it. test_usage.py pins it, and agent_route_matrix.json pins the agent's refusal.
COUNTED = {"/usage"}
# Reads that list every day of a window ending today, each by its date: every new day would change
# them. test_git_activity.py pins it, and agent_route_matrix.json pins the agent's refusal.
DATED = {"/pull-requests/activity"}


@contextmanager
def steady_clock(base: type[datetime] = datetime) -> Iterator[None]:
    """Every Worklog module reads the time from this clock while the world runs (T-19). It starts at
    the machine's time and moves on a microsecond each time it is read, so records are stamped in
    the order they are written and no two share a time. On a coarse clock, as on CI's Windows
    runners, two writes could share a time, and an answer sorted by time, then by kind, came out in
    another order. It never reads a whole second, where isoformat leaves the fraction out: that
    would change a time's shape. `base` lets a test change how its times are written."""
    lock = threading.Lock()
    last = [datetime.now(UTC)]

    class Steady(base):  # type: ignore[valid-type,misc]
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> "Steady":
            with lock:
                moment = last[0] + timedelta(microseconds=1)
                if moment.microsecond == 0:
                    moment += timedelta(microseconds=1)
                last[0] = moment
            local = moment.astimezone(tz) if tz is not None else moment.astimezone().replace(tzinfo=None)
            return cls.combine(local.date(), local.timetz())

    clocks = {module: clock for name, module in list(sys.modules.items())
              if (name == "worklog" or name.startswith("worklog."))
              and isinstance(clock := getattr(module, "datetime", None), type) and issubclass(clock, datetime)}
    for module in clocks:
        module.datetime = Steady  # type: ignore[attr-defined]
    try:
        yield
    finally:
        for module, clock in clocks.items():
            module.datetime = clock  # type: ignore[attr-defined]


class World:
    """A Worklog with a record of every kind, written through the API as the person and an agent."""

    def __init__(self, data: Path) -> None:
        self.app = create_app(data, testing=True)
        self.person = TestClient(self.app, base_url=ORIGIN)
        self.agent = TestClient(self.app, base_url=ORIGIN)
        self.ids: dict[str, str] = {}
        # The answer of the first call of each write route, by actor, method and route.
        self.writes: dict[str, object] = {}

    def __enter__(self) -> "World":
        self.person.__enter__()
        self.person.headers["Origin"] = ORIGIN
        boot = self.person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        self.person.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        # Worklog's today, in its own timezone, which the machine's date need not be (CI runs in UTC).
        self.today: str = boot.json()["today"]
        issued = self.write(self.person, "POST", "/credentials", {"label": "Builder", "avatar": "red-dots-antenna"})
        self.agent.headers["Authorization"] = "Bearer " + issued["token"]
        self.ids["credential"] = issued["id"]
        self.build()
        return self

    def __exit__(self, *exc: object) -> None:
        self.person.__exit__(*exc)

    def write(self, client: TestClient, method: str, path: str, body: object = None, **options: object) -> dict:
        headers = {"Idempotency-Key": str(uuid.uuid4()), **options.pop("headers", {})}  # type: ignore[dict-item]
        response = client.request(method, "/api/v1" + path, json=body, headers=headers, **options)  # type: ignore[arg-type]
        assert response.status_code == 200, (method, path, response.text)
        if method != "GET":
            actor = "agent" if client is self.agent else "person"
            self.writes.setdefault(f"{actor} {method} {self.route(method, path)}", response.json())
        return response.json()

    def route(self, method: str, path: str) -> str:
        """The route a call reaches, as OpenAPI names it: the first that matches, as the router takes it."""
        target = "/api/v1" + path.split("?", 1)[0]
        for route in self.app.routes:
            if isinstance(route, APIRoute) and method in route.methods and route.path_regex.match(target):
                return route.path.removeprefix("/api/v1")
        raise AssertionError(f"No route serves {method} {path}")

    def build(self) -> None:
        p, today = self.person, self.today
        ids = self.ids
        item = self.write(p, "POST", "/work-items", {"title": "Parser", "description": "Parse the plan"})
        ids["item"] = item["id"]
        self.write(p, "PATCH", f"/work-items/{item['id']}", {"revision": item["revision"], "status": "doing"})
        ids["other"] = self.write(p, "POST", "/work-items", {"title": "Release notes"})["id"]
        self.write(p, "PUT", f"/days/{today}/entries/{item['id']}", {"revision": 0, "planned": True, "worked": True,
                                                                    "outcome": "Half done"})
        ids["note"] = self.write(p, "POST", "/notes", {"ownerType": "item", "ownerId": item["id"], "title": "Scratch",
                                                       "text": "Notes"})["id"]
        ids["link"] = self.write(p, "POST", "/links", {"ownerType": "item", "ownerId": item["id"],
                                                       "url": "https://example.com/spec"})["id"]
        ids["reference"] = self.write(p, "POST", "/references", {"itemId": item["id"], "path": "C:/work/spec.md"})["id"]
        ids["attachment"] = self.write(p, "POST", f"/attachments?ownerType=item&ownerId={item['id']}&name=a.txt",
                                       content=b"text", headers={"Content-Type": "text/plain"})["id"]
        parent, child = str(uuid.uuid4()), str(uuid.uuid4())
        roadmap = self.write(p, "POST", "/roadmaps", {"title": "Plan", "steps": [
            {"id": parent, "title": "Parent"}, {"id": child, "title": "Child", "parentId": parent}]})
        ids.update(roadmap=roadmap["id"], step=child)
        self.write(p, "POST", f"/roadmaps/{roadmap['id']}/revisions", {"revision": roadmap["revision"], "title": "Plan 2",
                                                                       "steps": [{"id": parent, "title": "Parent"},
                                                                                 {"id": child, "title": "Child",
                                                                                  "parentId": parent}]})
        # A sub-roadmap, so the roadmap lists a child.
        ids["subroadmap"] = self.write(p, "POST", "/roadmaps", {"title": "Sub-plan", "parentId": roadmap["id"]})["id"]
        # The work item belongs to the roadmap's child step, so both sides show the link.
        current = self.write(p, "GET", f"/work-items/{item['id']}")
        self.write(p, "PATCH", f"/work-items/{item['id']}", {"revision": current["revision"], "roadmapId": roadmap["id"],
                                                             "stepId": child})
        ids["question"] = self.write(p, "POST", "/questions", {"roadmapId": roadmap["id"], "title": "Which parser?"})["id"]
        ids["decision"] = self.write(p, "POST", "/decisions", {"roadmapId": roadmap["id"], "title": "Use the HTML one",
                                                               "decidedDate": "2026-01-05"})["id"]
        ids["github"] = self.write(p, "POST", "/github", {"ownerType": "item", "ownerId": item["id"], "kind": "pr",
                                                          "url": "https://github.com/example/repo/pull/1"})["id"]
        ids["chat"] = self.write(p, "POST", "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": "chat-1",
                                                       "title": "Build"})["id"]
        ids["person"] = self.write(p, "POST", "/people", {"name": "Ada", "title": "Engineer"})["id"]
        ids["resource"] = self.write(p, "POST", "/resources", {"title": "Style", "summary": "How we write",
                                                               "body": "Plain words"})["id"]
        ids["repository"] = self.write(p, "POST", "/repositories", {"name": "worklog",
                                                                    "url": "https://github.com/example/worklog"})["id"]
        # The person tied to a work item, a roadmap and a repository, so each side's lists hold one.
        for kind, target in (("item", item["id"]), ("roadmap", roadmap["id"]), ("repository", ids["repository"])):
            self.write(p, "POST", "/person-links", {"personId": ids["person"], "targetType": kind, "targetId": target,
                                                    "relationship": "Reviewer"})
        ticket = self.write(p, "POST", "/tickets", {"title": "Crash on save"})
        ids["ticket"] = ticket["id"]
        ids["ticketRef"] = ticket.get("key") or f"T-{ticket['number']}"
        discussion = self.write(p, "POST", "/discussions", {"title": "Parser", "text": "Where do we start?",
                                                            "contexts": [{"type": "item", "id": item["id"]}]})
        ids["discussion"] = discussion["discussion"]["id"]
        ids["message"] = discussion["message"]["id"]
        # What an agent writes for itself: a run in the tracked chat, so the chats list pins an agent tied
        # to it, its participation, and a draft proposal.
        a = self.agent
        ids["agent"] = self.write(p, "GET", "/agents")["items"][0]["id"]
        run = self.write(a, "POST", "/agent-runs", {"clientRunKey": "golden", "displayName": "Builder",
                                                    "externalChatId": "chat-1"})
        ids["run"] = run["id"]
        ids["participation"] = self.write(a, "POST", "/participations", {
            "runId": run["id"], "itemId": item["id"], "contexts": [{"repositoryId": ids["repository"]}]})["id"]
        ids["proposal"] = self.write(a, "POST", "/proposals", {"title": "Rename", "rationale": "Clearer",
                                                               "operations": []})["id"]
        # The ticket names the work item, a label and the repository and is the agent's, which
        # comments on it, so the ticket's list and read pin each with an entry.
        self.write(p, "PATCH", f"/tickets/{ids['ticket']}", {
            "revision": ticket["revision"], "itemId": item["id"], "labels": ["parser"],
            "repositoryIds": [ids["repository"]], "assigneeId": ids["agent"]})
        self.write(a, "POST", f"/tickets/{ids['ticket']}/comments", {"text": "Seen it"})
        # The agent proposes a link on the work item and a question on the roadmap, and the person
        # approves it, so each record's proposal history lists one with its changes.
        proposed = self.write(a, "POST", "/proposals", {"title": "Track the spec", "rationale": "One place", "operations": [
            {"action": "link.create", "payload": {"ownerType": "item", "ownerId": item["id"],
                                                  "url": "https://example.com/plan"}},
            {"action": "question.create", "payload": {"roadmapId": roadmap["id"], "title": "Who reviews?"}}]})
        submitted = self.write(a, "POST", f"/proposals/{proposed['id']}/submit", {"revision": proposed["revision"]})
        self.write(p, "POST", f"/proposals/{proposed['id']}/approve", {"revision": submitted["revision"]})
        # The agent posts an update on its participation, so the updates and its activity list one.
        self.write(a, "POST", f"/participations/{ids['participation']}/updates", {"kind": "update",
                                                                                  "text": "Parser half done"})
        # It reports the pull request and commit it produced (v33), so owners names it by them.
        self.write(a, "POST", f"/participations/{ids['participation']}/outputs", {
            "pullRequests": ["https://github.com/example/worklog/pull/7"], "commits": ["abcdef1"]})
        # The agent owns a role on the work item and replies in its discussion from the tracked chat,
        # so the registry lists a role, a message with its chat, and the agent tied to that chat.
        role = self.write(a, "POST", "/discussions/roles", {"contextType": "item", "contextId": item["id"],
                                                            "label": "Parser owner"})
        self.write(a, "POST", f"/discussions/roles/{role['id']}/claim", {"revision": role["revision"]})
        self.write(a, "POST", f"/discussions/{ids['discussion']}/messages", {"text": "Starting with the tables",
                                                                              "chatId": "chat-1"})
        # The work item's discussion is tagged with a record of every other kind it takes and pinned.
        # It asks the agent to acknowledge one message, which the agent does, and the agent's role to
        # acknowledge another, which stays pending: the discussion reads then pin each shape of tag, a
        # met request with its receipt and a pending one. A request's slots list in the order of their
        # random IDs, so each names one.
        for kind, target in (("roadmap", roadmap["id"]), ("step", child), ("repository", ids["repository"]),
                             ("ticket", ids["ticket"]), ("person", ids["person"]),
                             ("resource", ids["resource"]), ("question", ids["question"]),
                             ("decision", ids["decision"])):
            self.write(p, "POST", f"/discussions/{ids['discussion']}/tags", {"type": kind, "id": target})
        thread = self.write(p, "GET", f"/discussions/{ids['discussion']}")["discussion"]
        self.write(p, "POST", f"/discussions/{ids['discussion']}/pin", {"revision": thread["revision"], "pinned": True})
        asked = self.write(p, "POST", f"/discussions/{ids['discussion']}/messages", {
            "text": "Confirm the table plan", "acknowledgement": {"targets": [{"kind": "agent", "id": ids["agent"]}]}})
        self.write(a, "POST", f"/discussions/messages/{asked['id']}/acknowledge",
                   {"targetIds": [asked["request"]["targets"][0]["id"]]})
        self.write(p, "POST", f"/discussions/{ids['discussion']}/messages", {
            "text": "Who reviews the parser?", "acknowledgement": {"targets": [{"kind": "role", "id": role["id"]}],
                                                               "deadlineSeconds": 3600}})


def concrete(template: str, ids: dict[str, str], today: str) -> str:
    """The template with this world's records in it, and the query parameters a read needs."""
    owner = {"/attachments": f"?ownerType=item&ownerId={ids['item']}", "/history": f"?entityId={ids['item']}",
             "/coordination-overlaps": f"?itemId={ids['item']}", "/owners": f"?ref={ids['ticketRef']}",
             "/search": "?q=parser", "/repositories/resolve": "?url=https://github.com/example/worklog"}
    by_prefix = [("/work-items/", "item"), ("/notes/", "note"), ("/links/", "link"),
                 ("/references/", "reference"), ("/attachments/", "attachment"), ("/roadmaps/", "roadmap"),
                 ("/steps/", "step"), ("/questions/", "question"), ("/decisions/", "decision"),
                 ("/github/", "github"), ("/chats/", "chat"), ("/people/", "person"), ("/resources/", "resource"),
                 ("/repositories/", "repository"),
                 ("/discussions/messages/", "message"),
                 ("/discussions/participants/", "agent"), ("/discussions/", "discussion"), ("/agents/", "agent"),
                 ("/agent-runs/", "run"), ("/participations/", "participation"), ("/proposals/", "proposal")]
    path = template
    if "{identity}" in path:
        name = next((key for prefix, key in by_prefix if path.startswith(prefix)), None)
        path = path.replace("{identity}", ids.get(name or "", "00000000-0000-4000-8000-000000000000"))
    path = path.replace("{date}", today).replace("{ref}", ids["ticketRef"]).replace("{number}", "1")
    return path + owner.get(template, "")


def normalized(value: object, names: dict[str, str], today: str) -> object:
    """value with each ID named by its first appearance, each time as <time>, and today as <today>."""
    if isinstance(value, dict):
        return {key: KEYED[key] if key in KEYED and isinstance(item, str) else normalized(item, names, today)
                for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item, names, today) for item in value]
    if isinstance(value, str):
        if moment := TIME.match(value):
            return time_shape(moment)
        value = UUID.sub(lambda match: names.setdefault(match.group(0), f"<id{len(names) + 1}>"), value)
        return value.replace(today, "<today>")
    return value


def time_shape(moment: re.Match[str]) -> str:
    """A time's placeholder, keeping its shape: whether it has a fraction and of how many digits, and
    whether its offset is written +hh:mm, as Z or not at all."""
    fraction = "." + "f" * (len(moment["fraction"]) - 1) if moment["fraction"] else ""
    offset = moment["offset"] or ""
    if offset not in ("", "Z"):
        offset = offset[0] + "hh:mm"
    return f"<time{fraction}{offset}>"


def answers(world: World) -> dict[str, dict]:
    """Each GET route's answer, as the person and as the agent, and the first answer of each write
    route the world called, keyed by actor, method and route."""
    result: dict[str, dict] = {key: {"status": 200, "body": normalized(body, {}, world.today)}
                               for key, body in world.writes.items()}
    for template, operations in sorted(world.app.openapi()["paths"].items()):
        route = template.removeprefix("/api/v1")
        if "get" not in operations or not template.startswith("/api/v1") or route in NOT_JSON | COUNTED | DATED:
            continue
        for actor, client in (("person", world.person), ("agent", world.agent)):
            response = client.get("/api/v1" + concrete(route, world.ids, world.today))
            body = response.json() if "json" in response.headers.get("content-type", "") else response.text
            result[f"{actor} GET {route}"] = {"status": response.status_code, "body": normalized(body, {}, world.today)}
    return result


def built(clock: type[datetime] = datetime) -> dict[str, dict]:
    with (steady_clock(clock), tempfile.TemporaryDirectory(prefix="worklog-golden-") as folder,
          World(Path(folder)) as world):
        return answers(world)


def slug(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", key).strip("-").lower() + ".json"


def test_every_read_answers_as_its_golden_file():
    current = built()
    files = {path.name for path in GOLDEN.glob("*.json")}
    assert files == {slug(key) for key in current}, "Write the golden files again: the reads have changed"
    changed = differences(current, GOLDEN)
    # The differing answers in full, as a diff: a failure on CI is read from its log alone.
    assert changed == {}, "These reads answer differently from their golden files:\n" + "\n".join(changed.values())


def test_a_change_to_how_times_are_written_shows():
    class Seconds(datetime):
        def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
            return super().isoformat(sep, "seconds")

    changed = differences(built(Seconds), GOLDEN)
    assert "person GET /work-items/{identity}" in changed
    assert "<time+hh:mm>" in changed["person GET /work-items/{identity}"]


def test_the_world_stamps_its_writes_in_order_on_a_coarse_clock(monkeypatch):
    import worklog.common as common

    class Coarse(datetime):
        """A machine clock that ticks once a second, so readings in one second share a time."""

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> "Coarse":
            return super().now(tz).replace(microsecond=0)

    monkeypatch.setattr(common, "datetime", Coarse)
    assert len({common.now() for _ in range(1000)}) < 1000
    with steady_clock():
        readings = [common.now() for _ in range(1000)]
    assert common.datetime is Coarse
    assert readings == sorted(set(readings))
    assert all(TIME.match(reading)["fraction"] for reading in readings)  # type: ignore[index]


def test_regenerating_writes_only_the_files_that_changed(tmp_path):
    answer = {"status": 200, "body": {"b": 1, "a": "<time.ffffff+hh:mm>"}}
    assert write_golden({"person GET /x": answer, "person GET /y": answer}, tmp_path) == 2
    text = (tmp_path / "person-get-x.json").read_text(encoding="utf-8")
    assert text.index('"a"') < text.index('"b"')
    (tmp_path / "person-get-gone.json").write_text("{}", encoding="utf-8")
    changed = {"status": 200, "body": {"b": 2, "a": "<time.ffffff+hh:mm>"}}
    assert write_golden({"person GET /x": answer, "person GET /y": changed}, tmp_path) == 1
    assert (tmp_path / "person-get-x.json").read_text(encoding="utf-8") == text
    assert sorted(path.name for path in tmp_path.iterdir()) == ["person-get-x.json", "person-get-y.json"]


def canonical(answer: object) -> str:
    """An answer as JSON text with keys sorted: key order is not part of an answer, but Python holds
    true equal to 1 and false to 0, and an answer that turned one into the other has changed."""
    return json.dumps(answer, sort_keys=True, ensure_ascii=False)


def rendered(answer: object) -> str:
    """An answer as its golden file holds it."""
    return json.dumps(answer, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def differences(current: dict[str, dict], folder: Path) -> dict[str, str]:
    """Each answer that differs from its file in folder, with a diff of the two."""
    changed: dict[str, str] = {}
    for key, answer in current.items():
        path = folder / slug(key)
        held = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        if canonical(held) != canonical(answer):
            diff = difflib.unified_diff(rendered(held).splitlines(), rendered(answer).splitlines(),
                                        f"golden/{slug(key)}", key, lineterm="", n=2)
            changed[key] = "\n".join(diff)
    return changed


def write_golden(current: dict[str, dict], folder: Path) -> int:
    """Write each answer whose file holds anything else, and delete the files of reads that are
    gone; a file already holding its answer is left alone. Returns how many it wrote."""
    folder.mkdir(exist_ok=True)
    names = {slug(key) for key in current}
    for stale in folder.glob("*.json"):
        if stale.name not in names:
            stale.unlink()
    written = 0
    for key, answer in current.items():
        path, text = folder / slug(key), rendered(answer)
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8", newline="\n")
            written += 1
    return written


if __name__ == "__main__":
    first, second = built(), built()
    differing = sorted(key for key in first if canonical(first[key]) != canonical(second.get(key)))
    if differing or set(first) != set(second):
        sys.exit(f"Two worlds answered differently, so a placeholder is missing: {differing}")
    print(f"Wrote {write_golden(first, GOLDEN)} of {len(first)} golden files to {GOLDEN}")
