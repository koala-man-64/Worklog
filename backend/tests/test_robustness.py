"""Malformed input must produce a 4xx error envelope, never an unhandled 5xx."""

import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
CONFUSED = [[], {}, 7, None, "éü中"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def send(c, method, path, body=None, **headers):
    return c.request(method, "/api/v1" + path, json=body,
                     headers={"Idempotency-Key": str(uuid.uuid4()), **headers})


def assert_handled(response, context):
    assert response.status_code < 500, (context, response.status_code, response.text[:300])
    if response.status_code >= 400:
        assert "code" in response.json()["error"], (context, response.text[:300])


@pytest.fixture
def records(client):
    item = send(client, "POST", "/work-items", {"title": "Sweep target"}).json()
    return {
        "item": item,
        "note": send(client, "POST", "/notes", {"ownerType": "item", "ownerId": item["id"], "text": "n"}).json(),
        "task": send(client, "POST", "/tasks", {"itemId": item["id"], "text": "t"}).json(),
        "roadmap": send(client, "POST", "/roadmaps", {"title": "Plan", "steps": [{"title": "Step"}]}).json(),
        "proposal": send(client, "POST", "/proposals", {"title": "P", "rationale": "", "operations": []}).json(),
    }


def fill(template, records):
    """Turn an OpenAPI path template into a concrete path that reaches the handler."""
    ids = {"work-items": records["item"]["id"], "notes": records["note"]["id"],
           "tasks": records["task"]["id"], "roadmaps": records["roadmap"]["id"],
           "steps": records["roadmap"]["steps"][0]["id"], "proposals": records["proposal"]["id"]}
    if template.startswith("/{resource}"):
        return f"/notes/{records['note']['id']}/archive"
    resource = template.split("/")[1]
    verb = "submit" if resource == "proposals" else "archive"
    return (template.replace("{identity}", ids.get(resource, str(uuid.uuid4())))
            .replace("{verb}", verb).replace("{date}", "2026-10-28")
            .replace("{item_id}", records["item"]["id"]).replace("{number}", "1"))


def baseline(schema, records):
    """A request that passes validation, so a single confused field reaches deeper code."""
    known = {"ownerType": "item", "ownerId": records["item"]["id"], "itemId": records["item"]["id"],
             "targetDate": "2026-10-29", "direction": "up", "status": "open",
             "timezone": "America/Chicago", "number": 1, "revision": 1}
    by_type = {"string": "x", "integer": 1, "boolean": False, "array": [], "object": {}}
    return {name: known.get(name, by_type.get(prop.get("type"), "x"))
            for name, prop in schema.get("properties", {}).items() if name in schema.get("required", [])}


def test_write_routes_reject_type_confused_fields(client, records):
    spec = client.app.openapi()
    schemas = spec["components"]["schemas"]
    checked = 0
    for template, operations in spec["paths"].items():
        for method, operation in operations.items():
            content = operation.get("requestBody", {}).get("content", {}).get("application/json")
            if method not in {"post", "put", "patch"} or not content:
                continue
            schema = schemas[content["schema"]["$ref"].rsplit("/", 1)[-1]]
            path = fill(template.removeprefix("/api/v1"), records)
            bodies = [[], "text"] + [{**baseline(schema, records), name: value}
                                     for name in schema.get("properties", {}) for value in CONFUSED]
            for body in bodies:
                assert_handled(send(client, method.upper(), path, body), (method, template, body))
                checked += 1
    assert checked > 150


@pytest.mark.parametrize("field", ["id", "title", "description", "parentId", "position"])
def test_roadmap_steps_reject_type_confused_fields(client, records, field):
    roadmap_id = records["roadmap"]["id"]
    for value in CONFUSED:
        step = {"title": "Step", field: value}
        created = send(client, "POST", "/roadmaps", {"title": "R", "steps": [step]})
        assert_handled(created, ("create", field, value))
        revision = client.get(f"/api/v1/roadmaps/{roadmap_id}").json()["revision"]
        revised = send(client, "POST", f"/roadmaps/{roadmap_id}/revisions",
                       {"revision": revision, "title": "R", "steps": [step]})
        assert_handled(revised, ("revise", field, value))
        preview = client.post("/api/v1/imports/preview?name=x.html", content=b"<h2>Imported</h2>").json()
        committed = send(client, "POST", f"/imports/{preview['id']}/commit", {"title": "R", "steps": [step]})
        assert_handled(committed, ("import", field, value))


def test_malformed_step_in_approved_proposal_is_rejected_cleanly(client):
    token = send(client, "POST", "/credentials", {"label": "sweep"}).json()["token"]
    agent = {"Authorization": "Bearer " + token}
    for value in ([], {}):
        operations = [{"action": "roadmap.create",
                       "payload": {"title": "R", "steps": [{"title": "S", "parentId": value}]}}]
        draft = send(client, "POST", "/proposals", {"title": "Bad step", "rationale": "", "operations": operations},
                     **agent).json()
        submitted = send(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]},
                         **agent).json()
        approval = send(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]})
        assert approval.status_code == 422, approval.text
    assert client.get("/api/v1/roadmaps").json()["total"] == 0


def test_non_ascii_csrf_token_is_rejected_with_security_headers(client, records):
    response = client.request("PATCH", f"/api/v1/work-items/{records['item']['id']}",
                              json={"revision": 1, "title": "t"},
                              headers={"X-CSRF-Token": "été".encode()})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf"
    assert "content-security-policy" in response.headers


def test_unexpected_failure_returns_envelope_with_security_headers(client, records, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(client.app.state.work, "detail", broken)
    response = client.get(f"/api/v1/work-items/{records['item']['id']}")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "content-security-policy" in response.headers
