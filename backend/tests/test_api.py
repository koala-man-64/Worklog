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


def test_withdrawn_unsubmitted_drafts_stay_private(client):
    credentials = [write(client, "POST", "/credentials", {"label": label}).json() for label in ("A", "B")]
    a, b = [{"Authorization": "Bearer " + credential["token"]} for credential in credentials]
    draft = write(client, "POST", "/proposals", {"title": "Abandoned idea", "rationale": "Never submitted", "operations": []}, headers=a).json()
    route = f"/attachments?ownerType=proposal&ownerId={draft['id']}&name=abandoned.txt"
    evidence = write(client, "POST", route, content=b"abandoned evidence", headers=a).json()
    withdrawn = write(client, "POST", f"/proposals/{draft['id']}/withdraw", {"revision": draft["revision"]}, headers=a)
    assert withdrawn.status_code == 200, withdrawn.text
    assert client.get(f"/api/v1/proposals/{draft['id']}", headers=b).status_code == 404
    assert draft["id"] not in {p["id"] for p in client.get("/api/v1/proposals", headers=b).json()["items"]}
    assert client.get(f"/api/v1/attachments?ownerType=proposal&ownerId={draft['id']}", headers=b).status_code == 404
    assert client.get(f"/api/v1/attachments/{evidence['id']}", headers=b).status_code == 404
    for identity in (draft["id"], evidence["id"]):
        assert client.get(f"/api/v1/history?entityId={identity}", headers=b).status_code == 404
    # The author and the human reviewer still see it.
    assert client.get(f"/api/v1/proposals/{draft['id']}", headers=a).status_code == 200
    assert client.get(f"/api/v1/proposals/{draft['id']}").status_code == 200
    # A proposal withdrawn after submission stays visible to other agents, as before.
    operations = [{"action": "item.create", "payload": {"title": "Reviewed"}}]
    shared = write(client, "POST", "/proposals", {"title": "Reviewed idea", "rationale": "Submitted first", "operations": operations}, headers=a).json()
    submitted = write(client, "POST", f"/proposals/{shared['id']}/submit", {"revision": shared["revision"]}, headers=a).json()
    write(client, "POST", f"/proposals/{shared['id']}/withdraw", {"revision": submitted["revision"]}, headers=a)
    assert client.get(f"/api/v1/proposals/{shared['id']}", headers=b).status_code == 200


def test_roadmap_work_items_create_attach_step_move_and_unlink(client):
    roadmap = write(client, "POST", "/roadmaps", {"title": "Platform roadmap", "steps": [{"title": "Phase 1"}]}).json()
    other = write(client, "POST", "/roadmaps", {"title": "Other roadmap", "steps": [{"title": "Other step"}]}).json()
    step_id = roadmap["steps"][0]["id"]
    other_step_id = other["steps"][0]["id"]

    # Create work item directly on roadmap without a step
    direct = item(client, title="Roadmap-level work", roadmapId=roadmap["id"])
    assert direct["roadmapId"] == roadmap["id"] and direct["stepId"] is None
    detail = client.get(f"/api/v1/work-items/{direct['id']}").json()
    assert detail["linkedRoadmap"]["id"] == roadmap["id"]
    assert detail["linkedStep"] is None

    # Create work item on a step and verify roadmapId is derived
    on_step = item(client, title="Step-level work", stepId=step_id)
    assert on_step["roadmapId"] == roadmap["id"] and on_step["stepId"] == step_id

    # Mismatched roadmapId and stepId is rejected
    mismatch = write(client, "POST", "/work-items", {"title": "Mismatch", "roadmapId": roadmap["id"], "stepId": other_step_id})
    assert mismatch.status_code == 422
    assert write(client, "POST", "/work-items", {"title": "Missing roadmap", "roadmapId": str(uuid.uuid4())}).status_code == 422

    # Roadmap detail includes both work items
    road_detail = client.get(f"/api/v1/roadmaps/{roadmap['id']}").json()
    assert [w["id"] for w in road_detail["workItems"]] == [direct["id"], on_step["id"]]
    assert client.get(f"/api/v1/work-items?roadmapId={roadmap['id']}").json()["total"] == 2

    # Attach existing unlinked item to roadmap, move to step, move back to roadmap level, then unlink
    existing = item(client, title="Existing backlog item")
    attached = write(client, "PATCH", f"/work-items/{existing['id']}", {"revision": existing["revision"], "roadmapId": roadmap["id"]}).json()
    assert attached["roadmapId"] == roadmap["id"] and attached["stepId"] is None
    moved_step = write(client, "PATCH", f"/work-items/{existing['id']}", {"revision": attached["revision"], "stepId": step_id}).json()
    assert moved_step["roadmapId"] == roadmap["id"] and moved_step["stepId"] == step_id
    roadmap_only = write(client, "PATCH", f"/work-items/{existing['id']}", {"revision": moved_step["revision"], "stepId": None}).json()
    assert roadmap_only["roadmapId"] == roadmap["id"] and roadmap_only["stepId"] is None
    unlinked = write(client, "PATCH", f"/work-items/{existing['id']}", {"revision": roadmap_only["revision"], "roadmapId": None}).json()
    assert unlinked["roadmapId"] is None and unlinked["stepId"] is None


def test_nested_roadmaps_over_http_and_agent_proposals(client):
    parent = write(client, "POST", "/roadmaps", {"title": "Platform"}).json()
    child = write(client, "POST", "/roadmaps", {"title": "Storage", "parentId": parent["id"]})
    assert child.status_code == 200, child.text
    child = child.json()
    detail = client.get(f"/api/v1/roadmaps/{child['id']}").json()
    assert detail["parentId"] == parent["id"]
    assert detail["ancestors"] == [{"id": parent["id"], "title": "Platform", "archived": False}]
    assert client.get(f"/api/v1/roadmaps/{parent['id']}").json()["children"] == [{"id": child["id"], "title": "Storage", "archived": False}]
    assert [r["parentId"] for r in client.get("/api/v1/roadmaps").json()["items"]] == [None, parent["id"]]

    # Placement only changes through move; a revision carrying parentId is rejected.
    sneaky = write(client, "POST", f"/roadmaps/{child['id']}/revisions", {"revision": child["revision"], "title": "Storage", "parentId": None})
    assert sneaky.status_code == 422
    assert write(client, "POST", f"/roadmaps/{child['id']}/move", {"revision": child["revision"]}).status_code == 422
    cycle = write(client, "POST", f"/roadmaps/{parent['id']}/move", {"revision": parent["revision"], "parentId": child["id"]})
    assert cycle.status_code == 422
    top = write(client, "POST", f"/roadmaps/{child['id']}/move", {"revision": child["revision"], "parentId": None})
    assert top.status_code == 200, top.text
    assert top.json()["parentId"] is None

    cred = write(client, "POST", "/credentials", {"label": "Planner agent"}).json()
    headers = {"Authorization": "Bearer " + cred["token"]}
    operations = [{"action": "roadmap.move", "payload": {"id": child["id"], "revision": top.json()["revision"], "parentId": parent["id"]}},
                  {"action": "roadmap.create", "payload": {"title": "Backups", "parentId": parent["id"]}}]
    proposal = write(client, "POST", "/proposals", {"title": "Group storage work", "rationale": "Same platform", "operations": operations}, headers=headers)
    assert proposal.status_code == 200, proposal.text
    submitted = write(client, "POST", f"/proposals/{proposal.json()['id']}/submit", {"revision": proposal.json()["revision"]}, headers=headers).json()
    approved = write(client, "POST", f"/proposals/{submitted['id']}/approve", {"revision": submitted["revision"]})
    assert approved.status_code == 200, approved.text
    children = client.get(f"/api/v1/roadmaps/{parent['id']}").json()["children"]
    assert [c["title"] for c in children] == ["Storage", "Backups"]
    bad = write(client, "POST", "/proposals", {"title": "Bad", "rationale": "Missing parent", "operations": [
        {"action": "roadmap.move", "payload": {"id": child["id"], "revision": 1}}]}, headers=headers)
    assert bad.status_code == 422


def test_people_directory_and_roadmap_work_item_links(client):
    alice = write(client, "POST", "/people", {"name": "Alice Rivera", "role": "Staff Engineer", "notes": "Platform architecture lead"}).json()
    bob = write(client, "POST", "/people", {"name": "Bob Chen", "role": "Product Manager"}).json()
    assert alice["name"] == "Alice Rivera" and alice["role"] == "Staff Engineer"

    roadmap = write(client, "POST", "/roadmaps", {"title": "Reliability roadmap", "steps": [{"title": "SLO rollout"}]}).json()
    work_item = item(client, title="Define service SLOs", roadmapId=roadmap["id"])

    # Tie Alice and Bob to both the roadmap and the work item with optional role labels
    r_link1 = write(client, "POST", "/person-links", {"personId": alice["id"], "targetType": "roadmap", "targetId": roadmap["id"], "relationship": "Owner"}).json()
    r_link2 = write(client, "POST", "/person-links", {"personId": bob["id"], "targetType": "roadmap", "targetId": roadmap["id"], "relationship": "Stakeholder"}).json()
    w_link1 = write(client, "POST", "/person-links", {"personId": alice["id"], "targetType": "item", "targetId": work_item["id"], "relationship": "Owner"}).json()
    w_link2 = write(client, "POST", "/person-links", {"personId": bob["id"], "targetType": "item", "targetId": work_item["id"], "relationship": "Reviewer"}).json()
    assert r_link1["relationship"] == "Owner" and r_link2["relationship"] == "Stakeholder"
    assert w_link1["relationship"] == "Owner" and w_link2["relationship"] == "Reviewer"

    # Upserting an existing link updates its relationship label
    updated_link = write(client, "POST", "/person-links", {"personId": bob["id"], "targetType": "item", "targetId": work_item["id"], "relationship": "Approver"}).json()
    assert updated_link["id"] == w_link2["id"] and updated_link["relationship"] == "Approver"

    # Roadmap detail and Work item detail include linked people
    road_detail = client.get(f"/api/v1/roadmaps/{roadmap['id']}").json()
    assert [(p["name"], p["relationship"]) for p in road_detail["people"]] == [("Alice Rivera", "Owner"), ("Bob Chen", "Stakeholder")]
    assert [(p["name"], p["relationship"]) for p in road_detail["workItems"][0]["people"]] == [("Alice Rivera", "Owner"), ("Bob Chen", "Approver")]

    item_detail = client.get(f"/api/v1/work-items/{work_item['id']}").json()
    assert [(p["name"], p["relationship"]) for p in item_detail["people"]] == [("Alice Rivera", "Owner"), ("Bob Chen", "Approver")]

    # Filter work items and roadmaps by personId
    assert client.get(f"/api/v1/work-items?personId={alice['id']}").json()["total"] == 1
    assert client.get(f"/api/v1/roadmaps?personId={alice['id']}").json()["total"] == 1

    # Person detail returns tied roadmaps and work items, and directory counts reflect active links
    alice_detail = client.get(f"/api/v1/people/{alice['id']}").json()
    assert len(alice_detail["roadmaps"]) == 1 and alice_detail["roadmaps"][0]["relationship"] == "Owner"
    assert len(alice_detail["workItems"]) == 1 and alice_detail["workItems"][0]["relationship"] == "Owner"
    people_list = client.get("/api/v1/people").json()["items"]
    alice_summary = next(p for p in people_list if p["id"] == alice["id"])
    assert alice_summary["roadmapCount"] == 1 and alice_summary["workItemCount"] == 1

    # Search finds person and routes to #people/{id}
    search_hit = client.get("/api/v1/search?q=Rivera").json()["items"][0]
    assert search_hit["type"] == "person" and search_hit["routeType"] == "people" and search_hit["routeId"] == alice["id"]

    # Update, archive, recover person, and unlink
    patched = write(client, "PATCH", f"/people/{alice['id']}", {"revision": alice["revision"], "role": "Principal Engineer"}).json()
    assert patched["role"] == "Principal Engineer"
    archived = write(client, "POST", f"/people/{alice['id']}/archive", {"revision": patched["revision"]}).json()
    assert archived["archived"] is True
    assert client.get("/api/v1/search?q=Rivera").json()["total"] == 0
    recovered = write(client, "POST", f"/people/{alice['id']}/recover", {"revision": archived["revision"]}).json()
    assert recovered["archived"] is False

    unlinked = write(client, "POST", "/person-links/unlink", {"personId": bob["id"], "targetType": "item", "targetId": work_item["id"]})
    assert unlinked.status_code == 200
    assert [p["name"] for p in client.get(f"/api/v1/work-items/{work_item['id']}").json()["people"]] == ["Alice Rivera"]


def test_links_agent_chats_and_github_commits_and_prs_on_items_and_roadmaps(client):
    roadmap = write(client, "POST", "/roadmaps", {"title": "Core roadmap", "steps": [{"title": "Step 1"}]}).json()
    work_item = item(client, title="Ship feature", roadmapId=roadmap["id"])

    # Web links with descriptions on work item and roadmap
    item_link = write(client, "POST", "/links", {
        "ownerType": "item",
        "ownerId": work_item["id"],
        "url": "https://example.com/design-doc",
        "title": "Architecture RFC",
        "description": "Detailed design and trade-offs for the new subsystem",
    }).json()
    road_link = write(client, "POST", "/links", {
        "ownerType": "roadmap",
        "ownerId": roadmap["id"],
        "url": "https://example.com/roadmap-brief",
        "title": "Product brief",
        "description": "Quarterly goals and KPIs",
    }).json()
    assert item_link["title"] == "Architecture RFC" and road_link["title"] == "Product brief"
    assert write(client, "POST", "/links", {"ownerType": "item", "ownerId": work_item["id"], "url": "javascript:alert(1)"}).status_code == 422

    # Agent chat metadata (title, chatId, createdDate) on work item and roadmap
    item_chat = write(client, "POST", "/chats", {
        "ownerType": "item",
        "ownerId": work_item["id"],
        "chatId": "conv-abc-123",
        "title": "Debug race condition in worker",
        "createdDate": "2026-10-28",
    }).json()
    road_chat = write(client, "POST", "/chats", {
        "ownerType": "roadmap",
        "ownerId": roadmap["id"],
        "chatId": "conv-road-999",
        "title": "Scope Q4 milestones",
        "createdDate": "2026-10-29",
    }).json()
    assert item_chat["chatId"] == "conv-abc-123" and item_chat["createdDate"] == "2026-10-28"

    # GitHub commit and pull request (with auto-inferred reference and chat association)
    pr_ref = write(client, "POST", "/github", {
        "ownerType": "item",
        "ownerId": work_item["id"],
        "kind": "pr",
        "url": "https://github.com/acme/worklog/pull/142",
        "title": "Fix worker race condition",
        "description": "Adds lock around queue drain",
        "chatId": item_chat["id"],
    }).json()
    commit_ref = write(client, "POST", "/github", {
        "ownerType": "roadmap",
        "ownerId": roadmap["id"],
        "kind": "commit",
        "url": "https://github.com/acme/worklog/commit/9f8e7d6c5b4a3210",
        "title": "Initial milestone scaffolding",
        "chatId": road_chat["id"],
    }).json()
    assert pr_ref["reference"] == "acme/worklog#142" and pr_ref["chatTitle"] == "Debug race condition in worker"
    assert commit_ref["reference"] == "acme/worklog@9f8e7d6" and commit_ref["chatTitle"] == "Scope Q4 milestones"

    # Verify work item detail and roadmap detail return links, chats, and github refs
    item_detail = client.get(f"/api/v1/work-items/{work_item['id']}").json()
    assert len(item_detail["links"]) == 1 and item_detail["links"][0]["description"].startswith("Detailed design")
    assert len(item_detail["chats"]) == 1 and item_detail["chats"][0]["chatId"] == "conv-abc-123"
    assert len(item_detail["github"]) == 1 and item_detail["github"][0]["reference"] == "acme/worklog#142"

    road_detail = client.get(f"/api/v1/roadmaps/{roadmap['id']}").json()
    assert len(road_detail["links"]) == 1 and road_detail["links"][0]["title"] == "Product brief"
    assert len(road_detail["chats"]) == 1 and road_detail["chats"][0]["chatId"] == "conv-road-999"
    assert len(road_detail["github"]) == 1 and road_detail["github"][0]["reference"] == "acme/worklog@9f8e7d6"

    # Search indexes links, chats, and github refs and routes to their owning work item or roadmap
    assert client.get("/api/v1/search?q=subsystem").json()["items"][0]["routeId"] == work_item["id"]
    assert client.get("/api/v1/search?q=conv-abc-123").json()["items"][0]["routeId"] == work_item["id"]
    assert client.get("/api/v1/search?q=9f8e7d6").json()["items"][0]["routeId"] == roadmap["id"]

    # Update, archive, and recover
    patched_link = write(client, "PATCH", f"/links/{item_link['id']}", {"revision": item_link["revision"], "description": "Updated RFC summary"}).json()
    assert patched_link["description"] == "Updated RFC summary"
    assert write(client, "POST", f"/github/{pr_ref['id']}/archive", {"revision": pr_ref["revision"]}).json()["archived"] is True
    assert write(client, "POST", f"/github/{pr_ref['id']}/recover", {"revision": 2}).json()["archived"] is False
