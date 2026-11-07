"""Settings, credentials, backups and the export refuse agents at the route as well (consolidation
step 3.2, C-08). It is a second line behind the request guard: a mistake in policy.ROUTES that let
an agent through still gets 403 there, and changes nothing."""

import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from worklog import policy
from worklog.app import create_app, person_only

ORIGIN = "http://127.0.0.1:8877"
# Each is the person's in policy.ROUTES, and declares person_only.
THE_PERSONS = {
    ("GET", "/settings"), ("PATCH", "/settings"),
    ("POST", "/credentials"), ("POST", "/credentials/{identity}/rotate"), ("POST", "/credentials/{identity}/revoke"),
    ("POST", "/backups"), ("GET", "/export"),
}


@pytest.fixture
def started(tmp_path):
    """The app, the person's client with a session, and an agent's credential."""
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as person:
        person.headers["Origin"] = ORIGIN
        boot = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        person.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        credential = person.post("/api/v1/credentials", json={"label": "Second line"},
                                 headers={"Idempotency-Key": str(uuid.uuid4())}).json()
        yield app, person, credential


def test_each_of_the_persons_routes_declares_the_second_line(started):
    app, _, _ = started
    declared = {(method, route.path.removeprefix(policy.PREFIX))
                for route in app.routes if isinstance(route, APIRoute)
                if any(depends.dependency is person_only for depends in route.dependencies)
                for method in route.methods}
    assert declared == THE_PERSONS
    assert all(policy.ROUTES[template][method] == policy.HUMAN for method, template in THE_PERSONS)


def test_an_agent_the_table_lets_in_by_mistake_is_refused_at_the_route(started, monkeypatch):
    app, person, credential = started
    # The mistake: each of these opened to agents' reads.
    opened: dict[str, dict] = {}
    for method, template in THE_PERSONS:
        opened.setdefault(template, dict(policy.ROUTES[template]))[method] = policy.READ
    for template, entry in opened.items():
        monkeypatch.setitem(policy.ROUTES, template, entry)
    agent = TestClient(app, base_url=ORIGIN)
    bearer = {"Authorization": "Bearer " + credential["token"]}
    before = person.get("/api/v1/settings").json()
    calls = [("GET", "/settings", None), ("PATCH", "/settings", {"timezone": "UTC"}),
             ("POST", "/credentials", {"label": "Minted"}),
             ("POST", f"/credentials/{credential['id']}/rotate", {}),
             ("POST", f"/credentials/{credential['id']}/revoke", None),
             ("POST", "/backups", None), ("GET", "/export", None)]
    for method, path, body in calls:
        response = agent.request(method, "/api/v1" + path, json=body,
                                 headers={**bearer, "Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 403, (method, path, response.text)
        error = response.json()["error"]
        # The route's own refusal, so the guard let the call through.
        assert error["code"] == "agent_scope" and error["message"].startswith("Only the person"), (method, path, error)
    after = person.get("/api/v1/settings").json()
    assert (after["timezone"], after["credentials"], after["backups"]) == (
        before["timezone"], before["credentials"], before["backups"])
    # Neither rotated nor revoked: the credential still reads.
    assert agent.get("/api/v1/context", headers=bearer).status_code == 200
