"""People carry a title, a team, a description and contact details, and are tied to work items,
roadmaps and repositories, by the person directly or through an approved agent proposal."""

import io
import json
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
DANA = {"name": "Dana Kim", "title": "Site Reliability Engineer", "team": "Platform",
        "description": "Runs the ingest pipeline; ask her about Kafka.", "email": "dana.kim@contoso.com",
        "phone": "+1 (312) 555-0100 ext. 12", "chat": "@dana on Slack", "location": "Chicago (US Central)",
        "notes": "## 1:1s\n\nPrefers mornings."}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def write(c, method, path, payload=None, **kwargs):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **kwargs.pop("headers", {})}
    return c.request(method, "/api/v1" + path, headers=headers, json=payload, **kwargs)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def repository(c, name):
    return ok(write(c, "POST", "/repositories", {"name": name, "url": f"https://example.com/acme/{name}"}))


def tie(c, person, target_type, target, relationship=""):
    return write(c, "POST", "/person-links", {"personId": person["id"], "targetType": target_type,
                                             "targetId": target["id"], "relationship": relationship})


def test_a_person_keeps_title_team_description_and_contact_details(client):
    dana = ok(write(client, "POST", "/people", DANA))
    assert {k: dana[k] for k in DANA} == DANA
    assert ok(client.get(f"/api/v1/people/{dana['id']}"))["email"] == DANA["email"]
    listed, = ok(client.get("/api/v1/people"))["items"]
    assert {k: listed[k] for k in DANA} == DANA
    assert (listed["workItemCount"], listed["roadmapCount"], listed["repositoryCount"]) == (0, 0, 0)

    # A change sets only the fields it names, and single-line fields are trimmed.
    moved = ok(write(client, "PATCH", f"/people/{dana['id']}", {"revision": dana["revision"], "team": "  Data  ",
                                                                "location": " Denver ", "email": ""}))
    assert (moved["team"], moved["location"], moved["email"], moved["title"]) == ("Data", "Denver", "", DANA["title"])
    assert moved["description"] == DANA["description"] and moved["notes"] == DANA["notes"]

    # Search finds a person by any of their fields, and an archived person only in the archive.
    for words in ("Reliability", "Kafka", "Denver", "Slack", "1:1s", "Data"):
        hits = ok(client.get("/api/v1/search", params={"q": words}))["items"]
        assert [(h["type"], h["routeId"]) for h in hits] == [("person", dana["id"])], words
    contact = ok(write(client, "PATCH", f"/people/{dana['id']}", {"revision": moved["revision"], "email": DANA["email"]}))
    assert [h["routeId"] for h in ok(client.get("/api/v1/search", params={"q": "contoso"}))["items"]] == [dana["id"]]
    ok(write(client, "POST", f"/people/{dana['id']}/archive", {"revision": contact["revision"]}))
    assert ok(client.get("/api/v1/search", params={"q": "contoso"}))["total"] == 0
    assert ok(client.get("/api/v1/search", params={"q": "contoso", "archived": "true"}))["total"] == 1

    # The export keeps every field.
    with zipfile.ZipFile(io.BytesIO(client.get("/api/v1/export").content)) as archive:
        exported, = json.loads(archive.read("records/people.json"))
    assert {k: exported[k] for k in DANA} == {**DANA, "team": "Data", "location": "Denver"}


@pytest.mark.parametrize("field,value", [
    ("email", "dana"), ("email", "dana@contoso"), ("email", "dana@@contoso.com"), ("email", "dana kim@contoso.com"),
    ("email", "dana@contoso.com?bcc=eve@example.com"), ("email", "dana@contoso..com"), ("email", "dana@con\ntoso.com"),
    ("email", "<dana@contoso.com>"), ("email", "d" * 310 + "@contoso.com"),
    ("phone", "call her"), ("phone", "312\t555 0100"), ("phone", "5" * 101),
    ("title", "t" * 501), ("description", "d" * 10_001),
])
def test_contact_details_are_checked_before_they_are_kept(client, field, value):
    refused = write(client, "POST", "/people", {"name": "Dana Kim", field: value})
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["fields"] == {field: "invalid"}
    assert ok(client.get("/api/v1/people"))["total"] == 0


@pytest.mark.parametrize("email", ["dana@contoso.com", "Dana.Kim+work@mail.contoso.co.uk", "o'brien@example.ie", "zoë@exämple.de"])
def test_addresses_people_actually_have_are_kept(client, email):
    assert ok(write(client, "POST", "/people", {"name": "Dana Kim", "email": f"  {email} "}))["email"] == email


def test_people_are_tied_to_repositories_from_either_side(client):
    dana = ok(write(client, "POST", "/people", DANA))
    eli = ok(write(client, "POST", "/people", {"name": "Eli Park", "title": "Engineer"}))
    api, web = repository(client, "api"), repository(client, "web")

    first = ok(tie(client, dana, "repository", api, "Maintainer"))
    assert (first["personTitle"], first["personTeam"], first["relationship"]) == (DANA["title"], DANA["team"], "Maintainer")
    ok(tie(client, eli, "repository", api, "Reviewer"))
    ok(tie(client, dana, "repository", web))
    # Tying again changes the role on the same tie.
    again = ok(tie(client, dana, "repository", api, "Owner"))
    assert again["id"] == first["id"]

    detail = ok(client.get(f"/api/v1/repositories/{api['id']}"))
    assert [(p["name"], p["relationship"], p["personTitle"]) for p in detail["people"]] == [
        ("Dana Kim", "Owner", DANA["title"]), ("Eli Park", "Reviewer", "Engineer")]
    person = ok(client.get(f"/api/v1/people/{dana['id']}"))
    assert [(r["repository"]["name"], r["relationship"]) for r in person["repositories"]] == [("api", "Owner"), ("web", "")]
    assert next(p for p in ok(client.get("/api/v1/people"))["items"] if p["id"] == dana["id"])["repositoryCount"] == 2
    # Each tie, and each change to one, is in the repository's history as in the person's.
    history = ok(client.get("/api/v1/history", params={"entityId": api["id"]}))["items"]
    assert [e["kind"] for e in history].count("person.link") == 3

    # An archived repository keeps its ties, and their roles, but takes no new one until recovered.
    archived = ok(write(client, "POST", f"/repositories/{web['id']}/archive", {"revision": web["revision"]}))
    refused = tie(client, eli, "repository", web)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "archived"
    assert refused.json()["error"]["fields"] == {"repositoryId": web["id"]}
    assert ok(tie(client, dana, "repository", web, "Former owner"))["relationship"] == "Former owner"
    assert next(p for p in ok(client.get("/api/v1/people"))["items"] if p["id"] == dana["id"])["repositoryCount"] == 1
    ok(write(client, "POST", "/person-links/unlink", {"personId": dana["id"], "targetType": "repository", "targetId": web["id"]}))
    ok(write(client, "POST", f"/repositories/{web['id']}/recover", {"revision": archived["revision"]}))
    assert ok(tie(client, eli, "repository", web))["relationship"] == ""

    missing = write(client, "POST", "/person-links", {"personId": dana["id"], "targetType": "repository", "targetId": "nope"})
    assert missing.status_code == 404
    # A tie to a work item's ID is not a tie to a repository.
    work = ok(write(client, "POST", "/work-items", {"title": "Ship ingest"}))
    assert write(client, "POST", "/person-links", {"personId": dana["id"], "targetType": "repository", "targetId": work["id"]}).status_code == 404
    assert write(client, "POST", "/person-links", {"personId": dana["id"], "targetType": "ticket", "targetId": work["id"]}).status_code == 422


def test_merging_a_repository_moves_the_people_tied_to_it(client):
    dana = ok(write(client, "POST", "/people", DANA))
    eli = ok(write(client, "POST", "/people", {"name": "Eli Park"}))
    old, kept = repository(client, "api-old"), repository(client, "api")
    ok(tie(client, dana, "repository", old, "Maintainer"))
    ok(tie(client, eli, "repository", old, "Reviewer"))
    ok(tie(client, eli, "repository", kept, "Owner"))
    old, kept = ok(client.get(f"/api/v1/repositories/{old['id']}")), ok(client.get(f"/api/v1/repositories/{kept['id']}"))
    merged = ok(write(client, "POST", f"/repositories/{old['id']}/merge",
                      {"revision": old["revision"], "intoId": kept["id"], "intoRevision": kept["revision"]}))
    assert merged["mergedInto"] == kept["id"]
    # Eli was on both: he keeps the survivor's tie and its role.
    assert [(p["name"], p["relationship"]) for p in ok(client.get(f"/api/v1/repositories/{kept['id']}"))["people"]] == [
        ("Dana Kim", "Maintainer"), ("Eli Park", "Owner")]
    assert ok(client.get(f"/api/v1/repositories/{old['id']}"))["people"] == []
    event = next(e for e in ok(client.get("/api/v1/history", params={"entityId": old["id"]}))["items"] if e["kind"] == "repository.merge")
    assert event["after"]["moved"]["people"] == 2
    # A merged record answers with the one it was merged into.
    refused = tie(client, dana, "repository", old)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "repository_merged"


def test_agents_propose_person_details_and_repository_ties(client):
    cred = ok(write(client, "POST", "/credentials", {"label": "People agent"}))
    agent = {"Authorization": "Bearer " + cred["token"]}
    api = repository(client, "api")

    def approve(operations):
        draft = ok(write(client, "POST", "/proposals", {"title": "People", "rationale": "", "operations": operations}, headers=agent))
        submitted = ok(write(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, headers=agent))
        return ok(write(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]}))

    # Agents never write people directly.
    assert write(client, "POST", "/people", DANA, headers=agent).status_code == 403
    approve([{"action": "person.create", "payload": DANA}])
    dana, = ok(client.get("/api/v1/people", headers=agent))["items"]
    assert {k: dana[k] for k in DANA} == DANA
    approve([{"action": "person.link", "payload": {"personId": dana["id"], "targetType": "repository",
                                                   "targetId": api["id"], "relationship": "Maintainer"}},
             {"action": "person.update", "payload": {"id": dana["id"], "revision": dana["revision"], "phone": "+1 312 555 0199"}}])
    assert [(p["name"], p["relationship"]) for p in ok(client.get(f"/api/v1/repositories/{api['id']}", headers=agent))["people"]] == [
        ("Dana Kim", "Maintainer")]
    assert ok(client.get(f"/api/v1/people/{dana['id']}"))["phone"] == "+1 312 555 0199"

    # The old field name, and a contact detail that would not work, are refused before anything changes.
    old = write(client, "POST", "/proposals", {"title": "Old", "rationale": "", "operations": [
        {"action": "person.create", "payload": {"name": "Fay", "role": "Ops"}}]}, headers=agent)
    assert old.status_code == 422 and "person.create" in old.json()["error"]["message"]
    draft = ok(write(client, "POST", "/proposals", {"title": "Bad email", "rationale": "", "operations": [
        {"action": "person.update", "payload": {"id": dana["id"], "revision": 2, "email": "dana"}}]}, headers=agent))
    submitted = ok(write(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, headers=agent))
    refused = write(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]})
    assert refused.status_code == 422 and refused.json()["error"]["fields"]["fields"] == {"email": "invalid"}
    assert ok(client.get(f"/api/v1/people/{dana['id']}"))["email"] == DANA["email"]


def test_roadmaps_are_found_by_the_words_of_their_title(client):
    for title in ("Reliability roadmap", "Ingest reliability", "Hiring plan"):
        ok(write(client, "POST", "/roadmaps", {"title": title}))
    found = ok(client.get("/api/v1/roadmaps", params={"q": "RELIABILITY"}))
    assert sorted(r["title"] for r in found["items"]) == ["Ingest reliability", "Reliability roadmap"] and found["total"] == 2
    assert [r["title"] for r in ok(client.get("/api/v1/roadmaps", params={"q": "ingest rel"}))["items"]] == ["Ingest reliability"]
    assert ok(client.get("/api/v1/roadmaps", params={"q": "  "}))["total"] == 3
