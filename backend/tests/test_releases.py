"""Releases (schema v19): what ships together and when, the work items, roadmaps, steps and GitHub
references each holds, the changes those make up, and the checks of who needs to know, what to
test and what to prepare first. Agents read releases and change none. The upgrade, restore and the
way back are in test_release_recovery.py."""

import io
import json
import uuid
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from test_release_recovery import dangling, person
from worklog import cli
from worklog.app import create_app
from worklog.common import AppError, today

ORIGIN = "http://127.0.0.1:8877"
ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def client(tmp_path):
    with person(tmp_path) as c:
        yield c


def call(c, method, path, payload=None, agent=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def refused(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text


def release(c, title="November release", **fields):
    return ok(call(c, "POST", "/releases", {"title": title, **fields}))


def detail(c, identity):
    return ok(c.get("/api/v1/releases/" + identity))


def hold(c, release_id, target_type, target_id):
    return call(c, "POST", "/release-contents", {"releaseId": release_id, "targetType": target_type, "targetId": target_id})


def check(c, release_id, kind, text, **fields):
    return ok(call(c, "POST", "/release-checks", {"releaseId": release_id, "kind": kind, "text": text, **fields}))


def item(c, title="Export button"):
    return ok(call(c, "POST", "/work-items", {"title": title}))


def github(c, owner_type, owner_id, number, **fields):
    return ok(call(c, "POST", "/github", {"ownerType": owner_type, "ownerId": owner_id, "kind": "pr",
                                          "url": f"https://github.com/acme/app/pull/{number}", **fields}))


def roadmap(c, title="Exports"):
    """A roadmap with a parent step over two leaves."""
    parent, first, second = (str(uuid.uuid4()) for _ in range(3))
    created = ok(call(c, "POST", "/roadmaps", {"title": title, "steps": [
        {"id": parent, "title": "CSV"}, {"id": first, "title": "Writer", "parentId": parent},
        {"id": second, "title": "Download", "parentId": parent}]}))
    return ok(c.get("/api/v1/roadmaps/" + created["id"])), (parent, first, second)


def days(offset):
    return (date.fromisoformat(today("America/Chicago")) + timedelta(days=offset)).isoformat()


def test_a_release_is_dated_or_not_and_planned_until_released_or_cancelled(client):
    undated = release(client, "Next platform release")
    assert {k: undated[k] for k in ("title", "releaseDate", "status", "archived", "revision")} == {
        "title": "Next platform release", "releaseDate": None, "status": "planned", "archived": False, "revision": 1}
    dated = release(client, "  November release  ", releaseDate="2026-11-20", description="Exports ship.")
    assert (dated["title"], dated["releaseDate"], dated["description"]) == ("November release", "2026-11-20", "Exports ship.")
    # Null makes a release not dated; an omitted date stays.
    moved = ok(call(client, "PATCH", "/releases/" + dated["id"], {"revision": 1, "releaseDate": None}))
    assert (moved["releaseDate"], moved["revision"]) == (None, 2)
    kept = ok(call(client, "PATCH", "/releases/" + dated["id"], {"revision": 2, "title": "Late November"}))
    assert (kept["releaseDate"], kept["title"]) == (None, "Late November")
    for status in ("released", "cancelled", "planned"):
        kept = ok(call(client, "PATCH", "/releases/" + dated["id"], {"revision": kept["revision"], "status": status}))
        assert kept["status"] == status
    for bad in ("2026-02-30", "2026-1-6", "", "next week", 20261120):
        refused(call(client, "POST", "/releases", {"title": "R", "releaseDate": bad}), 422, "validation")
    refused(call(client, "POST", "/releases", {"title": "   "}), 422, "validation")
    refused(call(client, "POST", "/releases", {"title": "R", "status": "shipped"}), 422, "validation")
    refused(call(client, "PATCH", "/releases/" + dated["id"], {"revision": 1, "title": "Stale"}), 409, "conflict")
    refused(call(client, "PATCH", "/releases/" + str(uuid.uuid4()), {"revision": 1, "title": "Gone"}), 404, "not_found")
    refused(client.post("/api/v1/releases", json={"title": "No key"}), 428, "idempotency")


def test_an_archived_release_is_read_only_until_recovered(client):
    r = release(client)
    work = item(client)
    entry = check(client, r["id"], "test", "Run the regression suite")
    archived = ok(call(client, "POST", f"/releases/{r['id']}/archive", {"revision": 1}))
    assert archived["archived"] is True
    refused(call(client, "PATCH", "/releases/" + r["id"], {"revision": 2, "title": "Changed"}), 409, "archived")
    refused(hold(client, r["id"], "item", work["id"]), 409, "archived")
    refused(call(client, "POST", "/release-checks", {"releaseId": r["id"], "kind": "test", "text": "More"}), 409, "archived")
    refused(call(client, "PATCH", "/release-checks/" + entry["id"], {"revision": 1, "status": "done"}), 409, "archived")
    assert ok(client.get("/api/v1/releases?archived=true"))["items"][0]["id"] == r["id"]
    assert ok(client.get("/api/v1/releases"))["total"] == 0
    recovered = ok(call(client, "POST", f"/releases/{r['id']}/recover", {"revision": 2}))
    assert recovered["archived"] is False
    ok(hold(client, r["id"], "item", work["id"]))


def test_a_release_holds_work_items_roadmaps_steps_and_github_references(client, tmp_path):
    r = release(client)
    work = item(client)
    plan, (parent, first, second) = roadmap(client)
    pr = github(client, "roadmap", plan["id"], 9)
    added = ok(hold(client, r["id"], "item", work["id"]))
    assert (added["targetType"], added["changed"]) == ("item", True)
    # Holding a record twice changes nothing and records nothing.
    assert ok(hold(client, r["id"], "item", work["id"]))["changed"] is False
    for target_type, target_id in (("roadmap", plan["id"]), ("step", parent), ("step", first), ("github", pr["id"])):
        ok(hold(client, r["id"], target_type, target_id))
    step = ok(client.get(f"/api/v1/steps/{first}"))
    ok(call(client, "PATCH", f"/steps/{first}/progress", {"revision": step["revision"], "status": "done"}))
    held = detail(client, r["id"])
    assert [(i["id"], i["title"], i["status"]) for i in held["items"]] == [(work["id"], "Export button", "open")]
    assert [(m["id"], m["steps"], m["stepsDone"]) for m in held["roadmaps"]] == [(plan["id"], 2, 1)]
    # A parent step's progress follows its leaves, as the roadmap shows it.
    assert {s["id"]: s["status"] for s in held["steps"]} == {parent: "doing", first: "done"}
    assert [s["roadmapTitle"] for s in held["steps"]] == ["Exports", "Exports"]
    assert [(c["id"], c["direct"]) for c in held["changes"]] == [(pr["id"], True)]
    assert [e["kind"] for e in held["history"]].count("release.add") == 5
    assert dangling(tmp_path / "worklog.sqlite3") == []
    removed = ok(call(client, "POST", "/release-contents/remove", {"releaseId": r["id"], "targetType": "step", "targetId": first}))
    assert removed["changed"] is True
    assert ok(call(client, "POST", "/release-contents/remove", {"releaseId": r["id"], "targetType": "step",
                                                                "targetId": first}))["changed"] is False
    assert [s["id"] for s in detail(client, r["id"])["steps"]] == [parent]
    # Leaving a step out of a revision retires it; the release still shows it, marked retired.
    revised = ok(call(client, "POST", f"/roadmaps/{plan['id']}/revisions", {
        "revision": ok(client.get("/api/v1/roadmaps/" + plan["id"]))["revision"], "title": "Exports",
        "steps": [{"id": first, "title": "Writer"}, {"id": second, "title": "Download"}]}))
    assert revised["id"] == plan["id"]
    shown = detail(client, r["id"])["steps"]
    assert [(s["id"], s["retired"]) for s in shown] == [(parent, True)]
    # Holding changes no revision: not the release's, not the work item's.
    assert detail(client, r["id"])["revision"] == 1
    assert ok(client.get("/api/v1/work-items/" + work["id"]))["revision"] == work["revision"]


def test_a_release_refuses_what_does_not_exist_is_archived_or_is_retired(client):
    r = release(client)
    for target_type in ("item", "roadmap", "step", "github"):
        refused(hold(client, r["id"], target_type, str(uuid.uuid4())), 404, "not_found")
    refused(hold(client, r["id"], "ticket", str(uuid.uuid4())), 422, "validation")
    refused(hold(client, str(uuid.uuid4()), "item", item(client)["id"]), 404, "not_found")
    old = item(client, "Old work")
    ok(call(client, "POST", f"/work-items/{old['id']}/archive", {"revision": old["revision"]}))
    refused(hold(client, r["id"], "item", old["id"]), 409, "archived")
    pr = github(client, "item", item(client, "Live work")["id"], 3)
    ok(call(client, "POST", f"/github/{pr['id']}/archive", {"revision": pr["revision"]}))
    refused(hold(client, r["id"], "github", pr["id"]), 409, "archived")
    # A PR on archived work, or on an archived roadmap, is archived with it.
    shelved = item(client, "Shelved work")
    on_shelved = github(client, "item", shelved["id"], 6)
    ok(call(client, "POST", f"/work-items/{shelved['id']}/archive", {"revision": shelved["revision"]}))
    refused(hold(client, r["id"], "github", on_shelved["id"]), 409, "archived")
    shelf, _ = roadmap(client, "Shelved plan")
    on_shelf = github(client, "roadmap", shelf["id"], 8)
    ok(call(client, "POST", f"/roadmaps/{shelf['id']}/archive", {"revision": shelf["revision"]}))
    refused(hold(client, r["id"], "github", on_shelf["id"]), 409, "archived")
    plan, (parent, first, second) = roadmap(client)
    ok(call(client, "POST", f"/roadmaps/{plan['id']}/revisions", {
        "revision": plan["revision"], "title": "Exports", "steps": [{"id": first, "title": "Writer"}]}))
    refused(hold(client, r["id"], "step", second), 409, "conflict")
    current = ok(client.get("/api/v1/roadmaps/" + plan["id"]))
    ok(call(client, "POST", f"/roadmaps/{plan['id']}/archive", {"revision": current["revision"]}))
    refused(hold(client, r["id"], "roadmap", plan["id"]), 409, "archived")
    refused(hold(client, r["id"], "step", first), 409, "archived")
    assert detail(client, r["id"])["items"] == []


def test_changes_are_the_releases_own_references_and_the_active_ones_on_its_work_and_roadmaps(client):
    r = release(client)
    work, other = item(client), item(client, "Docs")
    chat = ok(call(client, "POST", "/chats", {"ownerType": "item", "ownerId": work["id"], "title": "Export button (Claude)",
                                              "chatId": str(uuid.uuid4()), "provider": "claude", "model": "opus"}))
    made = github(client, "item", work["id"], 1, title="Add the export button", chatId=chat["id"])
    gone = github(client, "item", work["id"], 2)
    ok(call(client, "POST", f"/github/{gone['id']}/archive", {"revision": gone["revision"]}))
    plan, _ = roadmap(client)
    planned = github(client, "roadmap", plan["id"], 3)
    single = github(client, "item", other["id"], 4)
    unshipped = github(client, "item", other["id"], 5)
    for target_type, target_id in (("item", work["id"]), ("roadmap", plan["id"]), ("github", single["id"]),
                                   ("github", made["id"])):
        ok(hold(client, r["id"], target_type, target_id))
    changes = {c["id"]: c for c in detail(client, r["id"])["changes"]}
    # Each once, though one is held both itself and through its work item; an archived one on the
    # work stays out, and the other item's second PR isn't held at all.
    assert set(changes) == {made["id"], planned["id"], single["id"]}
    assert gone["id"] not in changes and unshipped["id"] not in changes
    assert {k: changes[made["id"]][k] for k in ("ownerType", "ownerTitle", "chatTitle", "provider", "model", "direct")} == {
        "ownerType": "item", "ownerTitle": "Export button", "chatTitle": "Export button (Claude)",
        "provider": "claude", "model": "opus", "direct": True}
    assert (changes[planned["id"]]["ownerTitle"], changes[planned["id"]]["direct"]) == ("Exports", False)
    assert (changes[single["id"]]["url"], changes[single["id"]]["reference"]) == ("https://github.com/acme/app/pull/4", "acme/app#4")
    # A reference the release holds itself stays listed when archived, and says so.
    current = ok(client.get(f"/api/v1/github/{single['id']}"))
    ok(call(client, "POST", f"/github/{single['id']}/archive", {"revision": current["revision"]}))
    assert {c["id"]: c["archived"] for c in detail(client, r["id"])["changes"]}[single["id"]] is True
    # Archiving a work item the release holds takes its PRs out of the changes, but one the release
    # holds itself stays, counted as archived with its work.
    current = ok(client.get("/api/v1/work-items/" + work["id"]))
    ok(call(client, "POST", f"/work-items/{work['id']}/archive", {"revision": current["revision"]}))
    after = {c["id"]: c for c in detail(client, r["id"])["changes"]}
    assert set(after) == {made["id"], planned["id"], single["id"]}
    assert (after[made["id"]]["direct"], after[made["id"]]["archived"]) == (True, True)
    ok(call(client, "POST", "/release-contents/remove", {"releaseId": r["id"], "targetType": "github", "targetId": made["id"]}))
    assert made["id"] not in {c["id"] for c in detail(client, r["id"])["changes"]}


def test_checks_say_who_needs_to_know_what_to_test_and_what_to_prepare(client):
    r = release(client)
    ana = ok(call(client, "POST", "/people", {"name": "Ana", "team": "Support"}))
    tell = check(client, r["id"], "notify", "Tell support about the export button", personId=ana["id"])
    assert {k: tell[k] for k in ("kind", "status", "personId", "personName", "dueDate", "archived")} == {
        "kind": "notify", "status": "open", "personId": ana["id"], "personName": "Ana", "dueDate": None, "archived": False}
    test = check(client, r["id"], "test", "Export a 10,000-row CSV", details="On staging, *with* real data.")
    prep = check(client, r["id"], "prepare", "Request the outbound firewall rule", dueDate="2026-11-13", status="waiting")
    assert (prep["status"], prep["dueDate"]) == ("waiting", "2026-11-13")
    done = ok(call(client, "PATCH", "/release-checks/" + tell["id"], {"revision": 1, "status": "done"}))
    assert (done["status"], done["revision"], done["personId"]) == ("done", 2, ana["id"])
    moved = ok(call(client, "PATCH", "/release-checks/" + test["id"], {"revision": 1, "kind": "prepare", "dueDate": "2026-11-12"}))
    assert (moved["kind"], moved["dueDate"], moved["details"]) == ("prepare", "2026-11-12", "On staging, *with* real data.")
    cleared = ok(call(client, "PATCH", "/release-checks/" + prep["id"], {"revision": 1, "dueDate": None, "status": "blocked"}))
    assert (cleared["dueDate"], cleared["status"]) == (None, "blocked")
    # Someone archived keeps a check that names them, but takes no new one.
    ok(call(client, "POST", f"/people/{ana['id']}/archive", {"revision": ana["revision"]}))
    kept = ok(call(client, "PATCH", "/release-checks/" + tell["id"], {"revision": 2, "text": "Tell support and sales"}))
    assert (kept["personId"], kept["personArchived"]) == (ana["id"], True)
    refused(call(client, "POST", "/release-checks", {"releaseId": r["id"], "kind": "notify", "text": "Tell Ana",
                                                     "personId": ana["id"]}), 404, "not_found")
    unnamed = ok(call(client, "PATCH", "/release-checks/" + tell["id"], {"revision": 3, "personId": None}))
    assert unnamed["personId"] is None
    for payload in ({"kind": "announce", "text": "T"}, {"kind": "test", "text": " "}, {"kind": "test", "text": "T", "status": "passed"},
                    {"kind": "test", "text": "T", "dueDate": "2026-13-01"}, {"kind": "test", "text": "T", "personId": str(uuid.uuid4())}):
        response = call(client, "POST", "/release-checks", {"releaseId": r["id"], **payload})
        assert response.status_code in {404, 422}, (payload, response.text)
    refused(call(client, "PATCH", "/release-checks/" + prep["id"], {"revision": 1, "status": "done"}), 409, "conflict")
    archived = ok(call(client, "POST", f"/release-checks/{prep['id']}/archive", {"revision": 2}))
    assert archived["archived"] is True
    refused(call(client, "PATCH", "/release-checks/" + prep["id"], {"revision": 3, "status": "done"}), 409, "archived")
    assert ok(call(client, "POST", f"/release-checks/{prep['id']}/recover", {"revision": 3}))["archived"] is False
    assert ok(client.get("/api/v1/release-checks/" + prep["id"]))["text"] == "Request the outbound firewall rule"
    refused(client.get("/api/v1/release-checks/" + str(uuid.uuid4())), 404, "not_found")


def test_checks_list_by_due_date_and_past_due_ones_say_so(client):
    r = release(client)
    later = check(client, r["id"], "prepare", "Later", dueDate=days(9))
    undated = check(client, r["id"], "test", "Whenever")
    late = check(client, r["id"], "prepare", "Late", dueDate=days(-1))
    finished = check(client, r["id"], "prepare", "Finished late", dueDate=days(-2), status="done")
    checks = detail(client, r["id"])["checks"]
    assert [c["id"] for c in checks] == [finished["id"], late["id"], later["id"], undated["id"]]
    assert {c["id"]: c["pastDue"] for c in checks} == {
        finished["id"]: False, late["id"]: True, later["id"]: False, undated["id"]: False}


def test_the_list_shows_upcoming_releases_soonest_first_and_not_dated_ones_last(client):
    soon = release(client, "Soon", releaseDate=days(7))
    later = release(client, "Later", releaseDate=days(30))
    undated = release(client, "Someday")
    late = release(client, "Late", releaseDate=days(-3))
    shipped = release(client, "Shipped", releaseDate=days(-20), status="released")
    shipped_earlier = release(client, "Shipped earlier", releaseDate=days(-40), status="released")
    dropped = release(client, "Dropped", status="cancelled")
    upcoming = ok(client.get("/api/v1/releases"))
    assert [r["title"] for r in upcoming["items"]] == ["Late", "Soon", "Later", "Someday"]
    assert {r["title"]: r["pastDue"] for r in upcoming["items"]} == {"Late": True, "Soon": False, "Later": False, "Someday": False}
    every = ok(client.get("/api/v1/releases?status=all"))["items"]
    assert [r["title"] for r in every] == ["Late", "Soon", "Later", "Someday", "Shipped", "Shipped earlier", "Dropped"]
    assert [r["id"] for r in ok(client.get("/api/v1/releases?status=released"))["items"]] == [shipped["id"], shipped_earlier["id"]]
    assert [r["id"] for r in ok(client.get("/api/v1/releases?status=cancelled"))["items"]] == [dropped["id"]]
    refused(client.get("/api/v1/releases?status=shipped"), 422, "validation")
    assert {soon["id"], later["id"], undated["id"], late["id"]} == {r["id"] for r in upcoming["items"]}
    assert ok(client.get("/api/v1/releases?limit=2&offset=1"))["items"][0]["title"] == "Soon"


def test_each_release_counts_its_work_and_its_open_checks(client):
    r, other = release(client), release(client, "Other")
    done, open_, old = item(client, "Done"), item(client, "Open"), item(client, "Archived")
    ok(call(client, "PATCH", "/work-items/" + done["id"], {"revision": done["revision"], "status": "done"}))
    for work in (done, open_, old):
        ok(hold(client, r["id"], "item", work["id"]))
    ok(call(client, "POST", f"/work-items/{old['id']}/archive", {"revision": old["revision"]}))
    check(client, r["id"], "notify", "Tell support")
    check(client, r["id"], "notify", "Told sales", status="done")
    check(client, r["id"], "test", "Smoke test", status="waiting", dueDate=days(-1))
    gone = check(client, r["id"], "prepare", "Feature flag", dueDate=days(-5))
    ok(call(client, "POST", f"/release-checks/{gone['id']}/archive", {"revision": 1}))
    counts = {x["id"]: x["counts"] for x in ok(client.get("/api/v1/releases"))["items"]}
    assert counts[r["id"]] == {"items": 2, "itemsDone": 1, "notify": 1, "test": 1, "prepare": 0, "pastDue": 1}
    assert counts[other["id"]] == {"items": 0, "itemsDone": 0, "notify": 0, "test": 0, "prepare": 0, "pastDue": 0}


def test_a_record_lists_the_releases_that_hold_it(client):
    work = item(client)
    first, second, third = release(client, "First"), release(client, "Second", status="released"), release(client, "Third")
    ok(hold(client, first["id"], "item", work["id"]))
    ok(hold(client, second["id"], "item", work["id"]))
    holding = ok(client.get(f"/api/v1/releases?targetType=item&targetId={work['id']}&status=all"))
    assert [r["id"] for r in holding["items"]] == [first["id"], second["id"]]
    assert third["id"] not in {r["id"] for r in holding["items"]}
    refused(client.get("/api/v1/releases?targetId=" + work["id"]), 422, "validation")


def test_search_finds_releases_and_their_checks_and_opens_the_release(client):
    r = release(client, "Exports release", description="The CSV export ships.")
    entry = check(client, r["id"], "prepare", "Rotate the vault secret", details="Before the window opens.")
    alone = check(client, r["id"], "test", "Vault failover drill")

    def found(q, archived=False, types=""):
        params = [("q", q), ("archived", str(archived).lower()), *(("type", kind) for kind in types.split(",") if kind)]
        listed = ok(client.get("/api/v1/search", params=params))
        return {(x["id"], x["type"], x["routeType"], x["routeId"]) for x in listed["items"]}

    assert found("CSV export") == {(r["id"], "release", "releases", r["id"])}
    assert found("vault") == {(entry["id"], "release_check", "releases", r["id"]),
                              (alone["id"], "release_check", "releases", r["id"])}
    assert [x["id"] for x in ok(client.get("/api/v1/releases?q=vault+secret"))["items"]] == [r["id"]]
    # Archiving the release hides its checks with it; recovering it brings back only those not
    # archived on their own.
    ok(call(client, "POST", f"/release-checks/{alone['id']}/archive", {"revision": 1}))
    ok(call(client, "POST", f"/releases/{r['id']}/archive", {"revision": 1}))
    assert found("vault") == set()
    assert {x[0] for x in found("vault", archived=True)} == {entry["id"], alone["id"]}
    ok(call(client, "POST", f"/releases/{r['id']}/recover", {"revision": 2}))
    assert {x[0] for x in found("vault")} == {entry["id"]}
    assert {x[0] for x in found("vault", archived=True)} == {alone["id"]}
    # The pickers keep only the types they offer.
    plan, _ = roadmap(client, "Vault roadmap")
    assert {x[1] for x in found("vault", types="roadmap,step")} == {"roadmap"}
    assert {x[1] for x in found("vault")} >= {"roadmap", "release_check"}
    # The list's search passes over a check archived on its own, but an archived release still
    # matches its own words in the archive.
    assert ok(client.get("/api/v1/releases?q=failover"))["items"] == []
    assert [x["id"] for x in ok(client.get("/api/v1/releases?q=rotate"))["items"]] == [r["id"]]
    ok(call(client, "POST", f"/releases/{r['id']}/archive", {"revision": 3}))
    assert [x["id"] for x in ok(client.get("/api/v1/releases?q=exports&archived=true"))["items"]] == [r["id"]]


def test_history_holds_the_changes_to_the_release_and_its_checks_newest_first(client):
    r = release(client)
    work = item(client)
    ok(hold(client, r["id"], "item", work["id"]))
    entry = check(client, r["id"], "test", "Smoke test")
    ok(call(client, "PATCH", "/release-checks/" + entry["id"], {"revision": 1, "status": "done"}))
    ok(call(client, "POST", "/release-contents/remove", {"releaseId": r["id"], "targetType": "item", "targetId": work["id"]}))
    history = detail(client, r["id"])["history"]
    assert [(e["kind"], e["entityId"]) for e in history] == [
        ("release.remove", r["id"]), ("check.update", entry["id"]), ("check.create", entry["id"]),
        ("release.add", r["id"]), ("release.create", r["id"])]
    assert all(set(e) == {"id", "entityId", "kind", "actorId", "actorLabel", "actorKind", "createdAt"} for e in history)
    full = ok(client.get("/api/v1/history", params={"entityId": r["id"]}))["items"]
    assert {"targetType": "item", "targetId": work["id"], "releaseId": r["id"]} in [e["before"] for e in full]


def test_agents_read_releases_and_change_none(client):
    agent = ok(call(client, "POST", "/credentials", {"label": "Implementer"}))
    r = release(client)
    work = item(client)
    ok(hold(client, r["id"], "item", work["id"]))
    entry = check(client, r["id"], "test", "Smoke test")
    assert ok(call(client, "GET", "/releases", agent=agent))["items"][0]["id"] == r["id"]
    assert ok(call(client, "GET", "/releases/" + r["id"], agent=agent))["items"][0]["id"] == work["id"]
    assert ok(call(client, "GET", "/release-checks/" + entry["id"], agent=agent))["id"] == entry["id"]
    for method, path, payload in (
            ("POST", "/releases", {"title": "Mine"}),
            ("PATCH", "/releases/" + r["id"], {"revision": 1, "title": "Changed"}),
            ("POST", f"/releases/{r['id']}/archive", {"revision": 1}),
            ("POST", "/release-contents", {"releaseId": r["id"], "targetType": "item", "targetId": work["id"]}),
            ("POST", "/release-contents/remove", {"releaseId": r["id"], "targetType": "item", "targetId": work["id"]}),
            ("POST", "/release-checks", {"releaseId": r["id"], "kind": "test", "text": "Mine"}),
            ("PATCH", "/release-checks/" + entry["id"], {"revision": 1, "status": "done"}),
            ("POST", f"/release-checks/{entry['id']}/archive", {"revision": 1})):
        refused(call(client, method, path, payload, agent=agent), 403, "agent_scope")
    context = ok(call(client, "GET", "/context", agent=agent))
    assert [x["id"] for x in context["releases"]["items"]] == [r["id"]]
    other = item(client, "Other work")
    assert ok(call(client, "GET", "/context?itemId=" + other["id"], agent=agent))["releases"]["items"] == []
    assert [x["id"] for x in ok(call(client, "GET", "/context?itemId=" + work["id"], agent=agent))["releases"]["items"]] == [r["id"]]


def test_only_the_person_changes_a_release_whatever_path_reaches_the_service(tmp_path):
    # The middleware refuses agents' writes; the dispatch refuses any other caller that isn't the
    # person, unless an approved proposal makes the change, which none can yet.
    app = create_app(tmp_path, testing=True)
    agent = {"id": "agent-credential", "label": "Implementer", "kind": "agent"}
    with TestClient(app, base_url=ORIGIN):
        for action, payload in (("release.create", {"title": "Mine"}),
                                ("check.create", {"releaseId": ID, "kind": "test", "text": "Mine"})):
            with pytest.raises(AppError) as refusal, app.state.db.transaction() as c:
                app.state.work.apply(c, agent, action, payload)
            assert (refusal.value.status, refusal.value.code) == (403, "agent_scope")


@pytest.fixture
def requests(monkeypatch):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", "private-agent-token")
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    captured = []

    def respond(request, **kwargs):
        captured.append(request)
        return io.BytesIO(json.dumps({"items": [], "total": 0}).encode())

    monkeypatch.setattr(cli, "_open", respond)
    return captured


def sent(request):
    return request.method, urlsplit(request.full_url).path.removeprefix("/api/v1"), parse_qs(urlsplit(request.full_url).query)


@pytest.mark.parametrize("arguments,route,query", [
    (["releases", "list"], "/releases", {"status": ["planned"], "archived": ["false"], "limit": ["50"], "offset": ["0"]}),
    (["releases", "list", "--status", "all", "--q", "export", "--item", ID, "--archived", "--limit", "5", "--offset", "10"],
     "/releases", {"status": ["all"], "q": ["export"], "targetType": ["item"], "targetId": [ID], "archived": ["true"],
                   "limit": ["5"], "offset": ["10"]}),
    (["releases", "show", ID], f"/releases/{ID}", {}),
])
def test_cli_reads(requests, arguments, route, query):
    assert cli.main(["agent", *arguments]) == 0
    assert sent(requests[-1]) == ("GET", route, query)


@pytest.mark.parametrize("method,path", [
    ("POST", "/releases"), ("PATCH", f"/releases/{ID}"), ("POST", f"/releases/{ID}/archive"),
    ("POST", "/release-contents"), ("POST", "/release-contents/remove"), ("POST", "/release-checks"),
    ("PATCH", f"/release-checks/{ID}"), ("GET", "/releases/not-an-id"), ("GET", f"/releases/{ID}/history"),
])
def test_cli_cannot_reach_a_release_write_or_an_unrecognized_route(requests, method, path):
    with pytest.raises(ValueError, match="outside"):
        cli.AgentClient().request(method, path, {})
    assert not requests


def test_an_agent_lists_and_reads_a_release_against_the_application(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    app = create_app(tmp_path / "server", testing=True)
    with TestClient(app, base_url=ORIGIN) as c, TestClient(app, base_url=ORIGIN) as wire:
        c.headers["Origin"] = ORIGIN
        c.headers["X-CSRF-Token"] = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={}).json()["csrfToken"]
        r = release(c, releaseDate="2026-11-20")
        work = item(c)
        ok(hold(c, r["id"], "item", work["id"]))
        check(c, r["id"], "prepare", "Request the firewall rule")
        agent = ok(call(c, "POST", "/credentials", {"label": "Implementer"}))
        monkeypatch.setenv("WORKLOG_AGENT_TOKEN", agent["token"])

        def bridge(sent_request, **kwargs):
            # The CLI's request as sent: no browser origin, no session cookie.
            response = wire.request(sent_request.get_method(), sent_request.full_url, content=sent_request.data,
                                    headers=dict(sent_request.header_items()))
            if response.status_code >= 400:
                raise HTTPError(sent_request.full_url, response.status_code, "rejected", {}, io.BytesIO(response.content))
            return io.BytesIO(response.content)

        monkeypatch.setattr(cli, "_open", bridge)

        def run(*args):
            assert cli.main(["agent", *args]) == 0
            out, err = capsys.readouterr()
            assert agent["token"] not in out + err
            return json.loads(out)

        listed = run("releases", "list", "--item", work["id"])
        assert [(x["id"], x["counts"]["prepare"]) for x in listed["items"]] == [(r["id"], 1)]
        shown = run("releases", "show", r["id"])
        assert ([x["id"] for x in shown["items"]], [x["text"] for x in shown["checks"]]) == (
            [work["id"]], ["Request the firewall rule"])
        assert [x["id"] for x in run("context", "--item", work["id"])["releases"]["items"]] == [r["id"]]
