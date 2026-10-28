"""Integration tests use real SQLite transactions and the complete HTTP boundary."""

import hashlib
import io
import json
import sqlite3
import uuid
import zipfile
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert response.status_code == 200
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def write(c, method, path, payload=None, **kwargs):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **kwargs.pop("headers", {})}
    if payload is not None:
        kwargs["json"] = payload
    return c.request(method, "/api/v1" + path, headers=headers, **kwargs)


def item(c, **kwargs):
    r = write(c, "POST", "/work-items", {"title": "Build a durable workbench", **kwargs})
    assert r.status_code == 200, r.text
    return r.json()


def test_read_day_never_writes_and_multiday_activity_survives_rescheduling(client):
    db = client.app.state.db
    with db.read() as conn:
        before = conn.total_changes
        assert conn.execute("SELECT count(*) FROM days").fetchone()[0] == 0
    assert client.get("/api/v1/days/2026-10-28").json()["revision"] == 0
    with db.read() as conn:
        assert conn.total_changes == before
        assert conn.execute("SELECT count(*) FROM days").fetchone()[0] == 0
    w = item(client, day="2026-10-28")
    day = client.get("/api/v1/days/2026-10-28").json()
    e = day["entries"][0]
    r = write(client, "PUT", f"/days/2026-10-28/entries/{w['id']}", {"revision": e["revision"], "worked": True, "outcome": "Finished the first slice"})
    assert r.status_code == 200, r.text
    r = write(client, "POST", "/days/2026-10-28/reschedule", {"itemId": w["id"], "revision": r.json()["revision"], "targetDate": "2026-10-29", "targetRevision": 0})
    assert r.status_code == 200, r.text
    original = client.get("/api/v1/days/2026-10-28").json()["entries"][0]
    assert original["worked"] and original["outcome"] == "Finished the first slice"
    assert not original["planned"]
    following = client.get("/api/v1/days/2026-10-29").json()["entries"][0]
    assert following["planned"] and not following["worked"]
    assert client.get(f"/api/v1/work-items/{w['id']}").json()["status"] == "open"


def test_stale_edit_and_idempotent_creation(client):
    key = str(uuid.uuid4())
    first = write(client, "POST", "/work-items", {"title": "One identity"}, headers={"Idempotency-Key": key})
    second = write(client, "POST", "/work-items", {"title": "One identity"}, headers={"Idempotency-Key": key})
    assert first.json() == second.json()
    changed = write(client, "POST", "/work-items", {"title": "Changed request"}, headers={"Idempotency-Key": key})
    assert changed.status_code == 409
    w = first.json()
    assert write(client, "PATCH", f"/work-items/{w['id']}", {"revision": 1, "status": "doing"}).status_code == 200
    stale = write(client, "PATCH", f"/work-items/{w['id']}", {"revision": 1, "title": "Stale overwrite"})
    assert stale.status_code == 409
    assert client.get(f"/api/v1/work-items/{w['id']}").json()["title"] == "One identity"
    with client.app.state.db.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE kind='item.update'").fetchone()[0] == 1


def test_bad_inputs_and_reorder_conflict(client):
    assert client.get("/api/v1/days/2026-02-30").status_code == 422
    assert write(client, "POST", "/work-items", {"title": "Bad", "adoUrl": "javascript:alert(1)"}).status_code == 422
    assert write(client, "POST", "/work-items", {"title": "Bad", "unknown": "field"}).status_code == 422
    w = item(client, day="2026-10-28")
    for bad in (True, "1", -1):
        assert write(client, "PATCH", f"/work-items/{w['id']}", {"revision": bad, "status": "doing"}).status_code == 422
    day = client.get("/api/v1/days/2026-10-28").json()
    assert write(client, "POST", "/days/2026-10-28/reorder", {"revision": day["revision"], "ids": []}).status_code == 409


def test_next_focus_excludes_done_and_blocked(client):
    one = item(client, title="Blocked first", day="2026-10-28")
    two = item(client, title="Done second", day="2026-10-28")
    three = item(client, title="Actionable third", day="2026-10-28")
    for w, s in ((one, "blocked"), (two, "done")):
        assert write(client, "PATCH", f"/work-items/{w['id']}", {"revision": 1, "status": s}).status_code == 200
    assert client.get("/api/v1/days/2026-10-28").json()["nextItemId"] == three["id"]


def test_notes_checklists_archive_and_search(client):
    w = item(client)
    note = write(client, "POST", "/notes", {"ownerType": "item", "ownerId": w["id"], "title": "Evidence", "text": "Unicode résumé and details"})
    assert note.status_code == 200, note.text
    n = note.json()
    task = write(client, "POST", "/tasks", {"itemId": w["id"], "text": "Verify output"})
    assert task.status_code == 200, task.text
    assert write(client, "PATCH", f"/tasks/{task.json()['id']}", {"revision": 1, "done": True}).json()["done"]
    assert client.get("/api/v1/search?q=resume").json()["total"] == 1
    assert write(client, "POST", f"/notes/{n['id']}/archive", {"revision": n["revision"]}).status_code == 200
    assert client.get("/api/v1/search?q=resume").json()["total"] == 0
    assert write(client, "POST", f"/notes/{n['id']}/recover", {"revision": 2}).status_code == 200
    details = client.get(f"/api/v1/work-items/{w['id']}").json()
    assert details["notes"][0]["text"] == "Unicode résumé and details"


def test_attachment_hash_and_active_content_download_policy(client):
    w = item(client)
    data = b"<script>alert('inert')</script>"
    r = write(client, "POST", f"/attachments?ownerType=item&ownerId={w['id']}&name=../../unsafe.svg", content=data, headers={"Content-Type": "image/svg+xml"})
    assert r.status_code == 200, r.text
    file = r.json()
    assert file["name"] == "unsafe.svg"
    assert file["sha256"] == hashlib.sha256(data).hexdigest()
    r = client.get(f"/api/v1/attachments/{file['id']}")
    assert r.content == data
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert write(client, "POST", f"/attachments?ownerType=item&ownerId={w['id']}&name=empty.txt", content=b"").status_code == 422
    assert write(client, "POST", f"/attachments?ownerType=item&ownerId={w['id']}&name=big.txt", content=b"x", headers={"Content-Length": "25000001"}).status_code == 413


def test_authentication_origin_csrf_and_source_exposure(client):
    for path in ("/server.py", "/backend/worklog/app.py", "/data/worklog.sqlite3", "/../backend/worklog/app.py"):
        assert client.get(path).status_code == 404
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/v1/work-items", headers={"Origin": "http://evil.example"}).status_code == 403
    assert write(client, "POST", "/work-items", {"title": "CSRF"}, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    with TestClient(client.app, base_url=ORIGIN) as anonymous:
        assert anonymous.get("/api/v1/work-items").status_code == 401
        assert anonymous.post("/api/v1/session/bootstrap", json={}).status_code == 403
        assert anonymous.get("/health").json()["ready"]


def test_agent_proposal_scope_atomic_conflict_and_idempotent_approval(client):
    w = item(client)
    cred = write(client, "POST", "/credentials", {"label": "Review agent"}).json()
    headers = {"Authorization": "Bearer " + cred["token"]}
    assert write(client, "POST", "/work-items", {"title": "Forbidden"}, headers=headers).status_code == 403
    assert client.get("/api/v1/settings", headers=headers).status_code == 403
    payload = {"title": "Advance work", "rationale": "The first slice is verified", "operations": [{"action": "item.update", "payload": {"id": w["id"], "revision": 1, "status": "doing"}}]}
    r = write(client, "POST", "/proposals", payload, headers=headers)
    assert r.status_code == 200, r.text
    proposal = r.json()
    submitted = write(client, "POST", f"/proposals/{proposal['id']}/submit", {"revision": proposal["revision"]}, headers=headers)
    assert submitted.status_code == 200, submitted.text
    rev = submitted.json()["revision"]
    assert write(client, "POST", f"/proposals/{proposal['id']}/approve", {"revision": rev}, headers=headers).status_code == 403
    key = str(uuid.uuid4())
    approved = write(client, "POST", f"/proposals/{proposal['id']}/approve", {"revision": rev}, headers={"Idempotency-Key": key})
    assert approved.status_code == 200, approved.text
    repeat = write(client, "POST", f"/proposals/{proposal['id']}/approve", {"revision": rev}, headers={"Idempotency-Key": key})
    assert approved.json() == repeat.json()
    assert client.get(f"/api/v1/work-items/{w['id']}").json()["status"] == "doing"
    with client.app.state.db.read() as conn:
        changes = conn.execute("SELECT actor_id FROM events WHERE kind='item.update'").fetchall()
        assert [r[0] for r in changes] == [cred["id"]]
    assert write(client, "POST", f"/credentials/{cred['id']}/revoke", {}).status_code == 200
    assert client.get("/api/v1/context", headers=headers).status_code == 401


def test_injected_failure_rolls_back_item_and_event(client, monkeypatch):
    import worklog.services as services
    w = item(client)
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected storage failure")
    monkeypatch.setattr(services, "event", fail)
    r = write(client, "PATCH", f"/work-items/{w['id']}", {"revision": 1, "status": "done"})
    assert r.status_code == 503
    actual = client.get(f"/api/v1/work-items/{w['id']}").json()
    assert actual["revision"] == 1 and actual["status"] == "open"


def test_backup_export_original_imports_and_references(client):
    original = b"<!doctype html><title>A plan</title><h2>Build</h2><p>Useful text</p><script>bad()</script>"
    preview = write(client, "POST", "/imports/preview?name=source.html", content=original).json()
    assert "bad()" not in json.dumps(preview)
    committed = write(client, "POST", f"/imports/{preview['id']}/commit", {"title": preview["title"], "overview": preview["overview"], "steps": preview["steps"]})
    assert committed.status_code == 200, committed.text
    download = client.get(f"/api/v1/imports/{preview['id']}/download")
    assert download.content == original and download.headers["content-disposition"].startswith("attachment;")
    assert write(client, "POST", "/backups", {}).status_code == 200
    export = client.get("/api/v1/export")
    assert export.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export.content)) as z:
        assert "manifest.json" in z.namelist()
        assert any(z.read(name) == original for name in z.namelist() if name.endswith(".html"))


def test_chicago_midnight_and_dst_dates_are_explicit():
    tz = ZoneInfo("America/Chicago")
    assert datetime(2026, 10, 29, 4, 59, tzinfo=UTC).astimezone(tz).date().isoformat() == "2026-10-28"
    assert datetime(2026, 10, 29, 5, 0, tzinfo=UTC).astimezone(tz).date().isoformat() == "2026-10-29"
    early = datetime(2026, 11, 1, 6, 30, tzinfo=UTC).astimezone(tz)
    late = datetime(2026, 11, 1, 7, 30, tzinfo=UTC).astimezone(tz)
    assert early.date() == late.date() and early.utcoffset() != late.utcoffset()


def test_draft_history_and_evidence_are_private_across_agent_credentials(client):
    credentials = [write(client, "POST", "/credentials", {"label": label}).json() for label in ("A", "B")]
    a, b = [{"Authorization": "Bearer " + credential["token"]} for credential in credentials]
    proposal = write(client, "POST", "/proposals", {"title": "Private draft", "rationale": "Secret rationale", "operations": []}, headers=a).json()
    route = f"/attachments?ownerType=proposal&ownerId={proposal['id']}&name=privatefilename.txt"
    evidence = write(client, "POST", route, content=b"private draft", headers=a).json()
    assert client.get(f"/api/v1/proposals/{proposal['id']}", headers=b).status_code == 404
    for identity in (proposal["id"], evidence["id"]):
        assert client.get(f"/api/v1/history?entityId={identity}", headers=b).status_code == 404
    assert client.get(f"/api/v1/attachments/{evidence['id']}", headers=b).status_code == 404
    write(client, "POST", f"/attachments/{evidence['id']}/archive", {"revision": 1})
    write(client, "POST", f"/attachments/{evidence['id']}/recover", {"revision": 2})
    assert client.get("/api/v1/search?q=privatefilename", headers=b).json()["total"] == 0


def test_archive_parent_hides_supporting_search_and_recovery_restores(client):
    w = item(client)
    write(client, "POST", "/notes", {"ownerType": "item", "ownerId": w["id"], "text": "parentsecretphrase"})
    assert client.get("/api/v1/search?q=parentsecretphrase").json()["total"] == 1
    write(client, "POST", f"/work-items/{w['id']}/archive", {"revision": 1})
    assert client.get("/api/v1/search?q=parentsecretphrase").json()["total"] == 0
    assert client.get("/api/v1/search?q=parentsecretphrase&archived=true").json()["total"] == 1
    write(client, "POST", f"/work-items/{w['id']}/recover", {"revision": 2})
    assert client.get("/api/v1/search?q=parentsecretphrase").json()["total"] == 1


def test_import_commit_and_search_are_one_idempotent_transaction(client, monkeypatch):
    original = b"<h2>Atomic imported work</h2><p>Preserve it</p>"
    preview = write(client, "POST", "/imports/preview?name=atomic.html", content=original).json()
    payload = {"title": preview["title"], "overview": preview["overview"], "steps": preview["steps"]}
    refresh = client.app.state.work.refresh_plans
    def fail(conn, *args):
        raise sqlite3.OperationalError("injected search update failure")
    monkeypatch.setattr(client.app.state.work, "refresh_plans", fail)
    key = str(uuid.uuid4())
    r = write(client, "POST", f"/imports/{preview['id']}/commit", payload, headers={"Idempotency-Key": key})
    assert r.status_code == 503
    assert client.get("/api/v1/roadmaps").json()["items"] == []
    monkeypatch.setattr(client.app.state.work, "refresh_plans", refresh)
    first = write(client, "POST", f"/imports/{preview['id']}/commit", payload, headers={"Idempotency-Key": key})
    second = write(client, "POST", f"/imports/{preview['id']}/commit", payload, headers={"Idempotency-Key": key})
    assert first.status_code == 200 and second.json() == first.json()
    assert client.get("/api/v1/search?q=Atomic").json()["total"] >= 1


def test_archive_preserves_daily_actuals_and_status_summary_is_historical(client, monkeypatch):
    import worklog.common as common
    day = ["2026-10-28"]
    monkeypatch.setattr(common, "today", lambda *args: day[0])
    w = item(client, day=day[0], worked=True)
    write(client, "PATCH", f"/work-items/{w['id']}", {"revision": 1, "status": "blocked"})
    first = client.get("/api/v1/days/2026-10-28").json()
    assert len(first["summary"]["blocked"]) == 1
    day[0] = "2026-10-29"
    write(client, "PATCH", f"/work-items/{w['id']}", {"revision": 2, "status": "done"})
    first = client.get("/api/v1/days/2026-10-28").json()
    assert len(first["summary"]["blocked"]) == 1 and first["summary"]["completed"] == []
    assert len(client.get("/api/v1/days/2026-10-29").json()["summary"]["completed"]) == 1
    write(client, "POST", f"/work-items/{w['id']}/archive", {"revision": 3})
    archived = client.get("/api/v1/days/2026-10-28").json()
    assert archived["entries"][0]["item"]["archived"]
    assert len(archived["summary"]["worked"]) == 1 and archived["nextItemId"] is None


def test_paged_work_move_and_search_route_ownership(client):
    values = [item(client, title=f"Ordered {i}") for i in range(3)]
    page = client.get("/api/v1/work-items?view=backlog&limit=1&offset=1").json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert page["items"][0]["isBacklog"]
    r = write(client, "POST", f"/work-items/{values[1]['id']}/move", {"revision": page["collectionRevision"], "direction": "up"})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/work-items?view=backlog&limit=1").json()["items"][0]["id"] == values[1]["id"]
    assert client.get("/api/v1/work-items?q=Ordered&limit=1").json()["total"] == 3
    note = write(client, "POST", "/notes", {"ownerType": "item", "ownerId": values[0]["id"], "text": "routableuniquenote"}).json()
    result = client.get("/api/v1/search?q=routableuniquenote").json()["items"][0]
    assert result["id"] == note["id"] and result["routeType"] == "work" and result["routeId"] == values[0]["id"]


def test_interrupted_restore_startup_fails_before_opening_database(tmp_path):
    from worklog.common import AppError
    path = tmp_path / "worklog.sqlite3"
    path.write_bytes(b"original recovery evidence")
    (tmp_path / ".restore-in-progress.json").write_text('{"state":"replacing"}', encoding="utf-8")
    with pytest.raises(AppError, match="restore was interrupted"):
        create_app(tmp_path, testing=True)
    assert path.read_bytes() == b"original recovery evidence"


def test_backup_storage_failure_is_actionable(client, monkeypatch):
    import worklog.app as application
    def fail(*args, **kwargs):
        raise OSError("test failure, no user data")
    monkeypatch.setattr(application, "backup_database", fail)
    response = write(client, "POST", "/backups", {})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_error"


def test_host_and_origin_allowlist_follow_the_configured_port(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKLOG_PORT", "8878")
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url="http://127.0.0.1:8878") as c:
        assert c.get("/health").status_code == 200
        assert c.get("/health", headers={"Host": "127.0.0.1:8877"}).status_code == 400
        bootstrap = {"X-Worklog-Bootstrap": "1"}
        own = c.post("/api/v1/session/bootstrap", json={},
                     headers={**bootstrap, "Origin": "http://127.0.0.1:8878"})
        assert own.status_code == 200
        default = c.post("/api/v1/session/bootstrap", json={},
                         headers={**bootstrap, "Origin": "http://127.0.0.1:8877"})
        assert default.status_code == 403
