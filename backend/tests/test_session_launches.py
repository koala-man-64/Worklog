"""Exercise the browser entry point, persistence and handoff without launching real agents."""

import json
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog import session_launches
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
SESSION = "903fb118-fbe8-4241-85bf-d6270a0bfaee"


@pytest.fixture
def browser(tmp_path, monkeypatch):
    calls = []

    def prepare(provider, cwd, prompt):
        calls.append((provider, cwd, prompt))
        return SESSION, ""

    monkeypatch.setattr(session_launches, "_prepare_session", prepare)
    monkeypatch.setattr(session_launches, "_open_desktop", lambda *args: None)
    app = create_app(tmp_path / "data", testing=True)
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers["Origin"] = ORIGIN
        boot = client.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        client.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        response = client.post("/api/v1/work-items", json={"title": "Prepare the parser fix"},
                               headers={"Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 200, response.text
        yield client, response.json(), tmp_path, calls


def start(client, item, cwd, *, provider="codex", key="start-session-test", prompt="Fix the parser"):
    return client.post(f"/api/v1/work-items/{item['id']}/sessions",
                       json={"provider": provider, "cwd": str(cwd), "prompt": prompt},
                       headers={"Idempotency-Key": key})


def finished(client, identity):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/session-launches/{identity}")
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] != "starting":
            return result
        time.sleep(0.01)
    pytest.fail("The mocked preparation did not finish")


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_prepares_links_and_replays_without_a_second_session(browser, provider):
    client, item, cwd, calls = browser
    initial = start(client, item, cwd, provider=provider)
    assert initial.status_code == 200, initial.text
    ready = finished(client, initial.json()["id"])
    assert ready["status"] == "ready", ready
    assert ready["chat"]["ownerId"] == item["id"]
    assert ready["chat"]["chatId"] == SESSION
    assert ready["chat"]["provider"] == provider
    saved = client.get(f"/api/v1/work-items/{item['id']}").json()
    assert [c["chatId"] for c in saved["chats"]] == [SESSION]
    replay = start(client, item, cwd, provider=provider)
    assert replay.status_code == 200
    assert replay.json()["id"] == ready["id"]
    assert len(calls) == 1
    conflict = start(client, item, cwd, provider=provider, prompt="Another task")
    assert conflict.status_code == 409


def test_failed_handoff_keeps_link_and_open_does_not_prepare_again(browser, monkeypatch):
    client, item, cwd, calls = browser

    def fail(*args):
        raise OSError("private error content must not reach the browser")

    monkeypatch.setattr(session_launches, "_open_desktop", fail)
    response = start(client, item, cwd)
    failed = finished(client, response.json()["id"])
    assert failed["status"] == "failed"
    assert failed["chat"]["chatId"] == SESSION
    assert "private error" not in failed["message"]
    monkeypatch.setattr(session_launches, "_open_desktop", lambda *args: None)
    opened = client.post(f"/api/v1/session-launches/{failed['id']}/open", json={},
                         headers={"Idempotency-Key": "open-saved-session"})
    assert opened.status_code == 200, opened.text
    assert opened.json()["status"] == "ready"
    assert opened.json()["chat"]["id"] == failed["chat"]["id"]
    assert len(calls) == 1


def test_preparation_failure_is_durable_and_safe_to_retry(browser, monkeypatch):
    client, item, cwd, _ = browser
    calls = []

    def fail(*args):
        calls.append(args)
        raise RuntimeError("private provider diagnostic")

    monkeypatch.setattr(session_launches, "_prepare_session", fail)
    response = start(client, item, cwd)
    failed = finished(client, response.json()["id"])
    assert failed["status"] == "failed"
    assert failed["chat"] is None
    assert "private provider" not in failed["message"]
    assert start(client, item, cwd).json()["id"] == failed["id"]
    assert len(calls) == 1


def test_link_failure_preserves_provider_id_and_repairs_same_session(browser, monkeypatch):
    client, item, cwd, calls = browser
    work = client.app.state.work
    original = work.apply

    def fail_link(connection, actor, action, payload, **kwargs):
        if action == "chat.create":
            raise RuntimeError("simulated local link failure")
        return original(connection, actor, action, payload, **kwargs)

    monkeypatch.setattr(work, "apply", fail_link)
    response = start(client, item, cwd)
    assert response.status_code == 200, response.text
    failed = finished(client, response.json()["id"])
    assert failed["status"] == "failed"
    assert failed["chat"] is None
    assert failed["sessionId"] == SESSION
    assert client.get(f"/api/v1/work-items/{item['id']}").json()["chats"] == []
    monkeypatch.setattr(work, "apply", original)
    repaired = client.post(f"/api/v1/session-launches/{failed['id']}/open", json={},
                          headers={"Idempotency-Key": "repair-saved-session"})
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["status"] == "ready"
    assert repaired.json()["chat"]["chatId"] == SESSION
    assert len(calls) == 1


def test_restart_recovers_pending_handoff_without_preparing_again(browser):
    client, item, cwd, calls = browser
    response = start(client, item, cwd)
    assert response.status_code == 200, response.text
    ready = finished(client, response.json()["id"])
    # Simulate a process stopping after it saved the chat but before finishing the handoff.
    with client.app.state.db.transaction() as connection:
        row = connection.execute("SELECT * FROM receipts WHERE key=?", (ready["id"],)).fetchone()
        saved = json.loads(row["response_json"])
        saved["result"]["status"] = "starting"
        saved["processToken"] = "a previous server instance"
        connection.execute("UPDATE receipts SET response_json=? WHERE actor_id=? AND key=?",
                           (json.dumps(saved), row["actor_id"], row["key"]))
    recovered = client.get(f"/api/v1/session-launches/{ready['id']}").json()
    assert recovered["status"] == "failed"
    assert recovered["chat"]["chatId"] == SESSION
    assert "interrupt" in recovered["message"].lower()
    assert start(client, item, cwd).json()["status"] == "failed"
    assert len(calls) == 1


def test_duplicate_pending_request_never_starts_second_worker(browser, monkeypatch):
    client, item, cwd, _ = browser
    gate = threading.Event()
    calls = []

    def prepare(*args):
        calls.append(args)
        assert gate.wait(3)
        return SESSION, ""

    monkeypatch.setattr(session_launches, "_prepare_session", prepare)
    try:
        first = start(client, item, cwd).json()
        second = start(client, item, cwd).json()
        assert second["id"] == first["id"]
        assert second["status"] == "starting"
    finally:
        gate.set()
    assert finished(client, first["id"])["status"] == "ready"
    assert len(calls) == 1


@pytest.mark.parametrize("provider,cwd,prompt", [
    ("shell", ".", "Task"), ("codex", "relative/path", "Task"),
    ("claude", "", "Task"), ("codex", "C:\\missing-worklog-launch-folder", "Task"),
    ("claude", "\\\\server\\share", "Task"), ("codex", ".", "   "),
])
def test_invalid_launch_input_never_executes(browser, provider, cwd, prompt):
    client, item, _, calls = browser
    response = start(client, item, cwd, provider=provider, prompt=prompt)
    assert response.status_code == 422, response.text
    assert not calls


def test_agent_and_foreign_origin_cannot_launch(browser):
    client, item, cwd, calls = browser
    credential = client.post("/api/v1/credentials", json={"label": "Launcher scope test"},
                             headers={"Idempotency-Key": "launch-test-credential"}).json()
    path = f"/api/v1/work-items/{item['id']}/sessions"
    payload = {"provider": "codex", "cwd": str(cwd), "prompt": "Task"}
    agent = client.post(path, json=payload, headers={"Authorization": "Bearer " + credential["token"]})
    assert agent.status_code == 403
    assert agent.json()["error"]["code"] == "agent_scope"
    foreign = client.post(path, json=payload, headers={"Origin": "https://example.com"})
    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "origin"
    assert not calls


def test_archived_item_and_missing_key_do_not_launch(browser):
    client, item, cwd, calls = browser
    response = client.post(f"/api/v1/work-items/{item['id']}/sessions",
                           json={"provider": "codex", "cwd": str(cwd), "prompt": "Task"})
    assert response.status_code == 428
    archived = client.post(f"/api/v1/work-items/{item['id']}/archive", json={"revision": item["revision"]},
                           headers={"Idempotency-Key": "archive-launch-item"})
    assert archived.status_code == 200, archived.text
    response = start(client, item, cwd)
    assert response.status_code == 404, response.text
    assert not calls
