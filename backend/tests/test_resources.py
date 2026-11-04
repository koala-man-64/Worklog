"""Resources: the person writes reference pages; agents and the person read and search them."""

import io
import json
import uuid
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from worklog import cli, resources
from worklog.app import create_app
from worklog.common import AppError

ORIGIN = "http://127.0.0.1:8877"
ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def request(c, method, path, payload=None, agent=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]


def create(c, title="Release checklist", **fields):
    return ok(request(c, "POST", "/resources", {"title": title, **fields}))


def titles(page):
    return [r["title"] for r in page["items"]]


def test_the_person_writes_a_page_that_reads_back_whole_and_lists_by_title(client):
    page = create(client, "  Runbook: deploys  ", summary="How a release reaches production",
                  body="## Steps\n\n1. Tag the release\n2. Watch the pipeline\n")
    assert (page["title"], page["summary"], page["revision"], page["archived"]) == (
        "Runbook: deploys", "How a release reaches production", 1, False)
    assert page["body"].startswith("## Steps")
    create(client, "api conventions")
    create(client, "Coding standards", summary="Rules for every repository")
    listed = ok(request(client, "GET", "/resources"))
    # By title whatever its case, and without the pages' text.
    assert titles(listed) == ["api conventions", "Coding standards", "Runbook: deploys"]
    assert listed["total"] == 3 and all("body" not in r for r in listed["items"])
    shown = ok(request(client, "GET", f"/resources/{page['id']}"))
    assert shown["body"] == page["body"]
    assert [e["kind"] for e in shown["history"]] == ["resource.create"]
    # The detail's history names who changed what when; /history keeps the whole records.
    assert set(shown["history"][0]) == {"id", "kind", "actorId", "actorLabel", "actorKind", "createdAt"}
    full = ok(request(client, "GET", f"/history?entityId={page['id']}"))["items"]
    assert full[0]["after"]["body"] == page["body"]
    error(request(client, "GET", f"/resources/{ID}"), 404, "not_found")


def test_edits_check_the_revision_and_keep_the_fields_they_leave_out(client):
    page = create(client, summary="What we agreed", body="First draft")
    edited = ok(request(client, "PATCH", f"/resources/{page['id']}", {"revision": 1, "body": "Second draft"}))
    assert (edited["revision"], edited["title"], edited["summary"], edited["body"]) == (2, page["title"], "What we agreed", "Second draft")
    stale = error(request(client, "PATCH", f"/resources/{page['id']}", {"revision": 1, "title": "Lost update"}), 409, "conflict")
    assert stale["fields"] == {"expected": 1, "currentRevision": 2}
    # A summary is kept as typed, so a field saving mid-sentence never eats the space just typed.
    assert ok(request(client, "PATCH", f"/resources/{page['id']}", {"revision": 2, "summary": "What we "}))["summary"] == "What we "
    error(request(client, "PATCH", f"/resources/{page['id']}", {"revision": 3, "title": "   "}), 422, "validation")
    error(request(client, "PATCH", f"/resources/{page['id']}", {"revision": 3, "body": "x" * (resources.BODY_LIMIT + 1)}), 422, "validation")
    error(request(client, "PATCH", f"/resources/{page['id']}", {"revision": 3, "owner": "someone"}), 422, "validation")
    error(request(client, "POST", "/resources", {"title": "t" * (resources.TITLE_LIMIT + 1)}), 422, "validation")
    error(request(client, "POST", "/resources", {"title": "Short", "summary": "s" * (resources.SUMMARY_LIMIT + 1)}), 422, "validation")
    assert [e["kind"] for e in ok(request(client, "GET", f"/resources/{page['id']}"))["history"]] == [
        "resource.update", "resource.update", "resource.create"]


def test_a_retried_create_returns_the_same_page_and_a_reused_key_conflicts(client):
    key = str(uuid.uuid4())
    first = ok(request(client, "POST", "/resources", {"title": "Once"}, key=key))
    assert ok(request(client, "POST", "/resources", {"title": "Once"}, key=key))["id"] == first["id"]
    error(request(client, "POST", "/resources", {"title": "Twice"}, key=key), 409, "idempotency_conflict")
    assert titles(ok(request(client, "GET", "/resources"))) == ["Once"]
    response = client.post("/api/v1/resources", json={"title": "Keyless"})
    assert (response.status_code, response.json()["error"]["code"]) == (428, "idempotency")


def test_an_archived_page_leaves_the_list_reads_but_refuses_edits_until_recovered(client):
    page = create(client, "Old runbook")
    archived = ok(request(client, "POST", f"/resources/{page['id']}/archive", {"revision": 1}))
    assert (archived["archived"], archived["revision"]) == (True, 2)
    assert titles(ok(request(client, "GET", "/resources"))) == []
    assert titles(ok(request(client, "GET", "/resources?archived=true"))) == ["Old runbook"]
    assert ok(request(client, "GET", f"/resources/{page['id']}"))["archived"] is True
    error(request(client, "PATCH", f"/resources/{page['id']}", {"revision": 2, "body": "Edit"}), 409, "archived")
    error(request(client, "POST", f"/resources/{page['id']}/recover", {"revision": 1}), 409, "conflict")
    error(request(client, "POST", f"/resources/{page['id']}/delete", {"revision": 2}), 404, "not_found")
    recovered = ok(request(client, "POST", f"/resources/{page['id']}/recover", {"revision": 2}))
    assert (recovered["archived"], recovered["revision"]) == (False, 3)
    assert ok(request(client, "PATCH", f"/resources/{page['id']}", {"revision": 3, "body": "Edit"}))["body"] == "Edit"


def test_search_and_the_list_filter_find_pages_by_their_words(client):
    deploys = create(client, "Deploys", summary="Production releases", body="Wait for the canary before widening.")
    create(client, "Style", body="Prefer small functions.")
    # Global search routes a page to the Resources tab.
    found = ok(request(client, "GET", "/search?q=canary"))["items"]
    assert [(r["type"], r["title"], r["routeType"], r["routeId"]) for r in found] == [
        ("resource", "Deploys", "resources", deploys["id"])]
    assert found[0]["snippet"].startswith("Production releases")
    for q, expected in (("canar", ["Deploys"]), ("production", ["Deploys"]), ("small functions", ["Style"]),
                        ("STYLE", ["Style"]), ("missing", []), ("", ["Deploys", "Style"])):
        assert titles(ok(request(client, "GET", "/resources?q=" + q))) == expected, q
    # Edits re-index the page, and archiving moves it to archived results only.
    ok(request(client, "PATCH", f"/resources/{deploys['id']}", {"revision": 1, "body": "Roll out by region."}))
    assert ok(request(client, "GET", "/search?q=canary"))["total"] == 0
    ok(request(client, "POST", f"/resources/{deploys['id']}/archive", {"revision": 2}))
    assert ok(request(client, "GET", "/search?q=region"))["total"] == 0
    assert [r["routeId"] for r in ok(request(client, "GET", "/search?q=region&archived=true"))["items"]] == [deploys["id"]]
    assert titles(ok(request(client, "GET", "/resources?archived=true&q=region"))) == ["Deploys"]


def test_agents_read_pages_and_see_them_in_context_but_never_write_them(client):
    page = create(client, "Coding standards", summary="Rules for every repository", body="Type everything.")
    agent = ok(request(client, "POST", "/credentials", {"label": "Implementer"}))
    assert titles(ok(request(client, "GET", "/resources", agent=agent))) == ["Coding standards"]
    assert ok(request(client, "GET", f"/resources/{page['id']}", agent=agent))["body"] == "Type everything."
    context = ok(request(client, "GET", "/context", agent=agent))
    # Context names each page and what it is for; the text is one show away.
    assert [(r["title"], r["summary"]) for r in context["resources"]["items"]] == [("Coding standards", "Rules for every repository")]
    assert "body" not in context["resources"]["items"][0]
    for method, path, payload in (("POST", "/resources", {"title": "Mine"}),
                                  ("PATCH", f"/resources/{page['id']}", {"revision": 1, "body": "Changed"}),
                                  ("POST", f"/resources/{page['id']}/archive", {"revision": 1}),
                                  ("POST", f"/resources/{page['id']}/recover", {"revision": 1})):
        error(request(client, method, path, payload, agent=agent), 403, "agent_scope")
    assert ok(request(client, "GET", f"/resources/{page['id']}"))["revision"] == 1


def test_the_service_refuses_an_agent_actor_however_it_is_reached(tmp_path):
    app = create_app(tmp_path, testing=True)
    service, agent = resources.ResourceService(), {"id": "agent", "label": "Agent", "kind": "agent"}
    with app.state.db.transaction() as c:
        page = service.create(c, {"id": "local-user", "label": "You", "kind": "human"}, {"title": "Guarded"})
    for write in (lambda c: service.create(c, agent, {"title": "Mine"}),
                  lambda c: service.update(c, agent, page["id"], {"revision": 1, "body": "Changed"}),
                  lambda c: service.set_archived(c, agent, page["id"], {"revision": 1}, True)):
        with pytest.raises(AppError) as refused, app.state.db.transaction() as c:
            write(c)
        assert (refused.value.status, refused.value.code) == (403, "agent_scope")


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
    (["resources", "list"], "/resources", {"archived": ["false"], "limit": ["50"], "offset": ["0"]}),
    (["resources", "list", "--q", "release checks", "--archived", "--limit", "5", "--offset", "10"], "/resources",
     {"q": ["release checks"], "archived": ["true"], "limit": ["5"], "offset": ["10"]}),
    (["resources", "show", ID], f"/resources/{ID}", {}),
])
def test_cli_reads(requests, arguments, route, query):
    assert cli.main(["agent", *arguments]) == 0
    assert sent(requests[-1]) == ("GET", route, query)


@pytest.mark.parametrize("method,path", [
    ("POST", "/resources"), ("PATCH", f"/resources/{ID}"), ("POST", f"/resources/{ID}/archive"),
    ("POST", f"/resources/{ID}/recover"), ("DELETE", f"/resources/{ID}"), ("GET", "/resources/not-an-id"),
    ("GET", f"/resources/{ID}/history"), ("GET", "/resources/"),
])
def test_cli_cannot_reach_a_resource_write_or_an_unrecognized_route(requests, method, path):
    with pytest.raises(ValueError, match="outside"):
        cli.AgentClient().request(method, path, {})
    assert not requests


def test_an_agent_lists_and_reads_a_page_against_the_application(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    app = create_app(tmp_path / "server", testing=True)
    with TestClient(app, base_url=ORIGIN) as person, TestClient(app, base_url=ORIGIN) as wire:
        person.headers["Origin"] = ORIGIN
        person.headers["X-CSRF-Token"] = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={}).json()["csrfToken"]
        page = ok(request(person, "POST", "/resources", {"title": "Release checks", "summary": "Before every tag",
                                                         "body": "- Run the e2e suite twice\n"}))
        agent = ok(request(person, "POST", "/credentials", {"label": "Implementer"}))
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

        listed = run("resources", "list", "--q", "e2e")
        assert [(r["id"], r["summary"]) for r in listed["items"]] == [(page["id"], "Before every tag")]
        assert run("resources", "show", page["id"])["body"] == "- Run the e2e suite twice\n"
        assert [r["title"] for r in run("context")["resources"]["items"]] == ["Release checks"]
