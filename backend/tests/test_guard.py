"""The request guard as plain ASGI (consolidation step 3.1, REM-22): it bounds a JSON body without
reading past its limit, leaves uploads and imports to their own limits, puts the security headers on
every response, and checks the path the router matches."""

import asyncio
import json
import uuid

from fastapi.testclient import TestClient
from worklog import guard
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


def started(tmp_path):
    """An app, the person's client with a session, and an agent's token."""
    app = create_app(tmp_path, testing=True)
    person = TestClient(app, base_url=ORIGIN, raise_server_exceptions=False)
    person.headers["Origin"] = ORIGIN
    boot = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
    person.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
    token = person.post("/api/v1/credentials", json={"label": "Guarded"}, headers={"Idempotency-Key": str(uuid.uuid4())}).json()["token"]
    return app, person, token


def call(app, method, path, headers, receive, root_path="", query=b""):
    """One request straight into the app as ASGI: what it sent back, as a status, headers and body."""
    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "root_path": root_path, "query_string": query,
             "headers": [(b"host", b"127.0.0.1:8877"), *headers], "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8877)}
    asyncio.run(app(scope, receive, send))
    if not sent:
        return None
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return start["status"], {name.decode().lower(): value.decode() for name, value in start["headers"]}, body


def test_a_json_body_past_its_limit_is_refused_before_the_rest_is_read(tmp_path):
    app, _, token = started(tmp_path)
    read = 0

    async def receive():
        # Twenty megabytes in one-megabyte chunks, with no Content-Length to refuse it by.
        nonlocal read
        read += 1
        return {"type": "http.request", "body": b"x" * 1_000_000, "more_body": read < 20}

    status, _, body = call(app, "POST", "/api/v1/proposals",
                           [(b"authorization", b"Bearer " + token.encode()), (b"content-type", b"application/json")], receive)
    assert status == 413 and json.loads(body)["error"]["code"] == "too_large"
    # The ninth megabyte passes the limit, and the other eleven are never read.
    assert guard.JSON_LIMIT == 8_000_000 and read == 9


def test_a_declared_length_past_the_limit_or_out_of_shape_is_refused_unread(tmp_path):
    app, _, token = started(tmp_path)

    async def receive():
        raise AssertionError("the body is read")

    for length, code in ((b"8000001", "too_large"), (b"-1", "content_length"), (b"many", "content_length")):
        status, _, body = call(app, "POST", "/api/v1/proposals", [(b"authorization", b"Bearer " + token.encode()),
                                                                  (b"content-length", length)], receive)
        assert (status, json.loads(body)["error"]["code"]) in {(413, "too_large"), (400, "content_length")}
        assert json.loads(body)["error"]["code"] == code


def test_a_client_that_leaves_before_its_body_arrives_is_answered_nothing(tmp_path):
    app, _, token = started(tmp_path)

    async def receive():
        return {"type": "http.disconnect"}

    agent = [(b"authorization", b"Bearer " + token.encode())]
    assert call(app, "POST", "/api/v1/proposals", agent, receive) is None
    # An upload reads its own body, past the guard, and is answered nothing either, not a 500.
    assert call(app, "POST", "/api/v1/attachments", agent, receive,
                query=b"ownerType=proposal&ownerId=missing&name=evidence.txt") is None


def test_uploads_and_imports_keep_their_own_limits(tmp_path):
    app, person, _ = started(tmp_path)
    item = person.post("/api/v1/work-items", json={"title": "Evidence"}, headers={"Idempotency-Key": str(uuid.uuid4())}).json()
    # Past the guard's limit for JSON, inside the upload's own.
    upload = person.post(f"/api/v1/attachments?ownerType=item&ownerId={item['id']}&name=big.bin", content=b"x" * 9_000_000,
                         headers={"Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/octet-stream"})
    assert upload.status_code == 200, upload.text
    imported = person.post("/api/v1/imports/preview?name=big.html", content=b"<p>" + b"x" * 6_000_000)
    assert imported.status_code == 413 and "5,000,000" in imported.json()["error"]["message"], imported.text


def test_every_response_carries_the_security_headers(tmp_path, monkeypatch):
    app, person, _ = started(tmp_path)
    item = person.post("/api/v1/work-items", json={"title": "Headers"}, headers={"Idempotency-Key": str(uuid.uuid4())}).json()

    def broken(*args, **kwargs):
        raise RuntimeError("unexpected")

    responses = {"ok": person.get("/api/v1/context"), "refused": TestClient(app, base_url=ORIGIN).get("/api/v1/context"),
                 "missing": person.get("/api/v1/nonexistent"), "page": person.get("/")}
    monkeypatch.setattr(app.state.work.items, "detail", broken)
    responses["failed"] = person.get(f"/api/v1/work-items/{item['id']}")
    assert {name: response.status_code for name, response in responses.items()} == {
        "ok": 200, "refused": 401, "missing": 404, "page": responses["page"].status_code, "failed": 500}
    assert responses["failed"].json()["error"]["code"] == "internal_error"
    for name, response in responses.items():
        for header, value in guard.SECURITY_HEADERS.items():
            assert response.headers.get(header) == value, (name, header)
        assert response.headers["cache-control"] == ("no-cache" if name == "page" else "no-store"), name


def test_the_guard_checks_the_path_the_router_matches_under_a_root_path(tmp_path):
    # Behind a root_path the router matches /api/... with the root taken off, and so does the guard:
    # checking the full path would leave the API unguarded there.
    app, _, token = started(tmp_path)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    status, _, body = call(app, "GET", "/r/api/v1/settings", [], receive, root_path="/r")
    assert status == 401 and json.loads(body)["error"]["code"] == "session"
    status, _, body = call(app, "GET", "/r/api/v1/settings", [(b"authorization", b"Bearer " + token.encode())], receive, root_path="/r")
    assert status == 403 and json.loads(body)["error"]["code"] == "agent_scope"
    status, _, _ = call(app, "GET", "/r/api/v1/context", [(b"authorization", b"Bearer " + token.encode())], receive, root_path="/r")
    assert status == 200
