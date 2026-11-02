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


def test_shared_search_routes_discussion_history_and_respects_archive(client):
    created = write(client, "POST", "/discussions", {"text": "Searchable handoff information"})
    assert created.status_code == 200, created.text
    discussion = created.json()["discussion"]
    reply = write(client, "POST", f"/discussions/{discussion['id']}/messages", {"text": "Searchable successor reply"})
    assert reply.status_code == 200
    matches = client.get("/api/v1/search?q=Searchable").json()["items"]
    assert matches
    assert all(m["routeType"] == "discussions" and m["routeId"] == discussion["id"] for m in matches)
    current = client.get(f"/api/v1/discussions/{discussion['id']}").json()["discussion"]
    archived = write(client, "POST", f"/discussions/{discussion['id']}/archive", {"revision": current["revision"], "archived": True})
    assert archived.status_code == 200
    assert client.get("/api/v1/search?q=Searchable").json()["items"] == []
    assert client.get("/api/v1/search?q=Searchable&archived=true").json()["items"]


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


def test_only_the_person_turns_approval_off_and_submitting_then_applies(client):
    w = item(client)
    cred = write(client, "POST", "/credentials", {"label": "Review agent"}).json()
    agent = {"Authorization": "Bearer " + cred["token"]}

    def propose(title, revision=None):
        current = client.get(f"/api/v1/work-items/{w['id']}").json()["revision"]
        operations = [{"action": "item.update", "payload": {"id": w["id"], "revision": revision or current, "title": title}}]
        draft = write(client, "POST", "/proposals", {"title": title, "rationale": "", "operations": operations}, headers=agent).json()
        return draft, write(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, headers=agent)

    assert client.get("/api/v1/settings").json()["requireProposalApproval"] is True
    _, waiting = propose("Waiting")
    assert waiting.json()["status"] == "submitted"
    assert write(client, "PATCH", "/settings", {"requireProposalApproval": False}, headers=agent).status_code == 403
    for invalid in ("false", 0):
        assert write(client, "PATCH", "/settings", {"requireProposalApproval": invalid}).status_code == 422
    off = write(client, "PATCH", "/settings", {"requireProposalApproval": False})
    assert off.status_code == 200 and off.json()["requireProposalApproval"] is False
    # Saving the timezone leaves the choice alone.
    assert write(client, "PATCH", "/settings", {"timezone": "America/Chicago"}).json()["requireProposalApproval"] is False

    _, applied = propose("Applied")
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "approved" and applied.json()["autoApproved"] is True
    assert client.get(f"/api/v1/work-items/{w['id']}").json()["title"] == "Applied"
    # What was waiting when approval went off still waits for the person.
    assert client.get(f"/api/v1/proposals/{waiting.json()['id']}").json()["status"] == "submitted"
    history = client.get("/api/v1/history", params={"entityId": applied.json()["id"]}).json()["items"]
    assert [(e["actorId"], e["actorLabel"], e["actorKind"]) for e in history if e["kind"] == "proposal.approve"] == [
        ("automatic-approval", "Automatic approval", "human")]

    # A stale target refuses the submission rather than leaving it for review.
    stale_draft, stale = propose("Stale", revision=1)
    assert stale.status_code == 409 and stale.json()["error"]["message"].startswith("Not submitted")
    assert client.get(f"/api/v1/proposals/{stale_draft['id']}", headers=agent).json()["status"] == "draft"
    with client.app.state.db.read() as conn:
        assert [r[0] for r in conn.execute("SELECT actor_id FROM events WHERE kind='item.update'")] == [cred["id"]]
        changes = conn.execute("SELECT actor_id,before_json,after_json FROM events WHERE kind='settings.requireProposalApproval'").fetchall()
        assert [tuple(r) for r in changes] == [("local-user", "true", "false")]

    assert write(client, "PATCH", "/settings", {"requireProposalApproval": True}).json()["requireProposalApproval"] is True
    _, reviewed = propose("Reviewed")
    assert reviewed.json()["status"] == "submitted" and reviewed.json()["autoApproved"] is False


def test_a_credential_revoked_while_its_submit_waits_applies_nothing(client, monkeypatch):
    import worklog.app as app_module
    w = item(client)
    cred = write(client, "POST", "/credentials", {"label": "Review agent"}).json()
    agent = {"Authorization": "Bearer " + cred["token"]}
    assert write(client, "PATCH", "/settings", {"requireProposalApproval": False}).status_code == 200
    draft = write(client, "POST", "/proposals", {"title": "Late change", "rationale": "", "operations": [
        {"action": "item.update", "payload": {"id": w["id"], "revision": 1, "title": "Late change"}}]}, headers=agent).json()
    read_body = app_module.bounded_body

    async def revoke_after_authentication(request, maximum):
        # The person's revoke commits after the middleware accepted the token.
        with client.app.state.db.transaction() as conn:
            conn.execute("UPDATE credentials SET revoked=1 WHERE id=?", (cred["id"],))
        return await read_body(request, maximum)

    monkeypatch.setattr(app_module, "bounded_body", revoke_after_authentication)
    late = write(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, headers=agent)
    monkeypatch.undo()
    assert late.status_code == 401, late.text
    assert client.get(f"/api/v1/work-items/{w['id']}").json()["title"] == w["title"]
    assert client.get(f"/api/v1/proposals/{draft['id']}").json()["status"] == "draft"


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


def test_roadmap_steps_are_assigned_to_agent_chats_with_provider_model_and_repos(client):
    roadmap = write(client, "POST", "/roadmaps", {"title": "Platform", "steps": [{"title": "API"}, {"title": "Web"}, {"title": "Docs"}]}).json()
    api, web, docs = (s["id"] for s in roadmap["steps"])
    other = write(client, "POST", "/roadmaps", {"title": "Other", "steps": [{"title": "Elsewhere"}]}).json()
    work_item = item(client, title="Loose work")

    chat = write(client, "POST", "/chats", {
        "ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": "a950ec9c", "title": "Build the API",
        "provider": " Anthropic ", "model": "claude-opus-5-5", "repos": ["acme/api", " acme/api", "acme/web"], "stepIds": [api, api],
    })
    assert chat.status_code == 200, chat.text
    chat = chat.json()
    assert (chat["provider"], chat["model"], chat["repos"], chat["stepIds"]) == ("Anthropic", "claude-opus-5-5", ["acme/api", "acme/web"], [api])
    assert client.get(f"/api/v1/chats/{chat['id']}").json()["stepIds"] == [api]
    detail = client.get(f"/api/v1/roadmaps/{roadmap['id']}").json()
    assert detail["chats"][0]["stepIds"] == [api] and detail["chats"][0]["repos"] == ["acme/api", "acme/web"]

    # Assignment is the chat's complete step list; other edits leave it alone.
    moved = write(client, "PATCH", f"/chats/{chat['id']}", {"revision": chat["revision"], "stepIds": [web, api]}).json()
    assert sorted(moved["stepIds"]) == sorted([api, web])
    renamed = write(client, "PATCH", f"/chats/{chat['id']}", {"revision": moved["revision"], "model": "claude-sonnet-5-5"}).json()
    assert sorted(renamed["stepIds"]) == sorted([api, web]) and renamed["provider"] == "Anthropic"
    dropped = write(client, "PATCH", f"/chats/{chat['id']}", {"revision": renamed["revision"], "stepIds": [web]}).json()
    assert dropped["stepIds"] == [web]
    api_history = [e["kind"] for e in client.get(f"/api/v1/steps/{api}/history").json()["items"]]
    assert "chat.assign" in api_history and "chat.unassign" in api_history
    assigned = next(e for e in client.get(f"/api/v1/steps/{web}/history").json()["items"] if e["kind"] == "chat.assign")
    assert assigned["after"]["title"] == "Build the API" and assigned["after"]["chatId"] == "a950ec9c"

    # Steps must be in the chat's own roadmap, and only roadmap chats take steps.
    foreign = write(client, "PATCH", f"/chats/{chat['id']}", {"revision": dropped["revision"], "stepIds": [other["steps"][0]["id"]]})
    assert foreign.status_code == 422
    missing = write(client, "PATCH", f"/chats/{chat['id']}", {"revision": dropped["revision"], "stepIds": [str(uuid.uuid4())]})
    assert missing.status_code == 422
    item_chat = write(client, "POST", "/chats", {"ownerType": "item", "ownerId": work_item["id"], "chatId": "c-2", "title": "Item chat", "stepIds": [api]})
    assert item_chat.status_code == 422
    for repos in (["acme/api", ""], "acme/api", [f"r{i}" for i in range(21)]):
        bad = write(client, "PATCH", f"/chats/{chat['id']}", {"revision": dropped["revision"], "repos": repos})
        assert bad.status_code == 422, repos

    # A retired step keeps its chats, but takes on no new ones.
    revised = write(client, "POST", f"/roadmaps/{roadmap['id']}/revisions", {
        "revision": roadmap["revision"], "title": "Platform",
        "steps": [{"id": api, "title": "API"}, {"id": docs, "title": "Docs"}],
    })
    assert revised.status_code == 200, revised.text
    kept = write(client, "PATCH", f"/chats/{chat['id']}", {"revision": dropped["revision"], "title": "Build the web app"})
    assert kept.status_code == 200 and kept.json()["stepIds"] == [web]
    late = write(client, "POST", "/chats", {"ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": "c-3", "title": "Late", "stepIds": [web]})
    assert late.status_code == 409

    hit = client.get("/api/v1/search?q=sonnet").json()["items"]
    assert hit and hit[0]["id"] == chat["id"] and hit[0]["routeId"] == roadmap["id"]

    # Agents propose their own assignment; it lands only on approval.
    cred = write(client, "POST", "/credentials", {"label": "Coding agent"}).json()
    headers = {"Authorization": "Bearer " + cred["token"]}
    operations = [{"action": "chat.create", "payload": {
        "ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": "codex-7", "title": "Write the docs",
        "provider": "OpenAI", "model": "gpt-5.5", "repos": ["acme/docs"], "stepIds": [docs]}}]
    proposal = write(client, "POST", "/proposals", {"title": "Claim docs", "rationale": "Starting on it", "operations": operations}, headers=headers)
    assert proposal.status_code == 200, proposal.text
    submitted = write(client, "POST", f"/proposals/{proposal.json()['id']}/submit", {"revision": proposal.json()["revision"]}, headers=headers).json()
    assert write(client, "POST", f"/proposals/{submitted['id']}/approve", {"revision": submitted["revision"]}).status_code == 200
    claimed = next(c for c in client.get(f"/api/v1/roadmaps/{roadmap['id']}").json()["chats"] if c["chatId"] == "codex-7")
    assert claimed["stepIds"] == [docs] and claimed["model"] == "gpt-5.5"


def test_roadmap_questions_are_answered_and_decisions_are_recorded(client):
    roadmap = write(client, "POST", "/roadmaps", {"title": "Storage roadmap", "steps": [{"title": "Pick a store"}]}).json()

    # A question starts open; answering it needs an answer, and reopening keeps that answer.
    asked = write(client, "POST", "/questions", {"roadmapId": roadmap["id"], "title": " Which region hosts replicas? ", "details": "Latency budget is **40 ms**."})
    assert asked.status_code == 200, asked.text
    question = asked.json()
    assert (question["roadmapId"], question["title"], question["status"], question["answer"]) == (roadmap["id"], "Which region hosts replicas?", "open", "")
    assert write(client, "POST", "/questions", {"roadmapId": roadmap["id"], "title": "  "}).status_code == 422
    assert write(client, "POST", "/questions", {"roadmapId": str(uuid.uuid4()), "title": "Orphan"}).status_code == 404
    assert write(client, "POST", "/questions", {"roadmapId": roadmap["id"], "title": "Born answered", "status": "answered"}).status_code == 422
    unanswered = write(client, "PATCH", f"/questions/{question['id']}", {"revision": question["revision"], "status": "answered"})
    assert unanswered.status_code == 422 and unanswered.json()["error"]["fields"] == {"answer": "required"}
    answered = write(client, "PATCH", f"/questions/{question['id']}", {"revision": question["revision"], "status": "answered", "answer": "Frankfurt, beside the primary"}).json()
    assert (answered["status"], answered["answer"], answered["details"]) == ("answered", "Frankfurt, beside the primary", "Latency budget is **40 ms**.")
    assert write(client, "PATCH", f"/questions/{question['id']}", {"revision": question["revision"], "title": "Stale"}).status_code == 409
    reopened = write(client, "PATCH", f"/questions/{question['id']}", {"revision": answered["revision"], "status": "open"}).json()
    assert (reopened["status"], reopened["answer"]) == ("open", "Frankfurt, beside the primary")
    assert client.get(f"/api/v1/questions/{question['id']}").json()["revision"] == reopened["revision"]
    kinds = [e["kind"] for e in client.get(f"/api/v1/history?entityId={question['id']}").json()["items"]]
    assert kinds.count("question.update") == 2 and "question.create" in kinds

    # Decisions default to today in the configured timezone and list newest first.
    today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    first = write(client, "POST", "/decisions", {"roadmapId": roadmap["id"], "title": "Use SQLite", "rationale": "One file, no server.", "decidedDate": "2026-09-01"}).json()
    second = write(client, "POST", "/decisions", {"roadmapId": roadmap["id"], "title": "Keep the rollback journal"}).json()
    assert (first["decidedDate"], second["decidedDate"], second["rationale"]) == ("2026-09-01", today, "")
    assert write(client, "POST", "/decisions", {"roadmapId": roadmap["id"], "title": "Bad date", "decidedDate": "2026-02-30"}).status_code == 422
    assert write(client, "POST", "/decisions", {"roadmapId": roadmap["id"], "title": ""}).status_code == 422
    revised = write(client, "PATCH", f"/decisions/{first['id']}", {"revision": first["revision"], "rationale": "One file, no server, easy backups."}).json()
    assert (revised["title"], revised["decidedDate"], revised["rationale"]) == ("Use SQLite", "2026-09-01", "One file, no server, easy backups.")
    # A blank date never quietly redates a decision, and neither record can move to another roadmap.
    blank = write(client, "PATCH", f"/decisions/{first['id']}", {"revision": revised["revision"], "decidedDate": " "})
    assert blank.status_code == 422 and blank.json()["error"]["fields"] == {"decidedDate": "required"}
    assert client.get(f"/api/v1/decisions/{first['id']}").json()["decidedDate"] == "2026-09-01"
    other = write(client, "POST", "/roadmaps", {"title": "Other roadmap"}).json()
    for path, record in ((f"/decisions/{first['id']}", revised), (f"/questions/{question['id']}", reopened)):
        assert write(client, "PATCH", path, {"revision": record["revision"], "roadmapId": other["id"]}).status_code == 422

    detail = client.get(f"/api/v1/roadmaps/{roadmap['id']}").json()
    assert [q["id"] for q in detail["questions"]] == [question["id"]]
    assert [d["id"] for d in detail["decisions"]] == [second["id"], first["id"]]

    # Search finds questions by their answers and decisions by their rationale, routed to the roadmap.
    for term, record in (("Frankfurt", question), ("backups", first)):
        hit = client.get(f"/api/v1/search?q={term}").json()["items"]
        assert hit and hit[0]["id"] == record["id"] and (hit[0]["routeType"], hit[0]["routeId"]) == ("roadmaps", roadmap["id"])

    # Archiving the roadmap hides both from active search without touching their own archive flags.
    archived_road = write(client, "POST", f"/roadmaps/{roadmap['id']}/archive", {"revision": detail["revision"]}).json()
    for term in ("Frankfurt", "backups"):
        assert client.get(f"/api/v1/search?q={term}").json()["total"] == 0
        assert client.get(f"/api/v1/search?q={term}&archived=true").json()["total"] == 1
    write(client, "POST", f"/roadmaps/{roadmap['id']}/recover", {"revision": archived_road["revision"]})

    # Archive and recover through the shared supporting-record commands; archived ones leave search and recovered ones return.
    archived = write(client, "POST", f"/decisions/{first['id']}/archive", {"revision": revised["revision"]}).json()
    assert archived["archived"] is True
    assert client.get("/api/v1/search?q=backups").json()["total"] == 0
    assert write(client, "POST", f"/decisions/{first['id']}/recover", {"revision": archived["revision"]}).json()["archived"] is False
    assert client.get("/api/v1/search?q=backups").json()["total"] == 1
    parked = write(client, "POST", f"/questions/{question['id']}/archive", {"revision": reopened["revision"]}).json()
    assert parked["archived"] is True and client.get("/api/v1/search?q=Frankfurt").json()["total"] == 0
    recovered = write(client, "POST", f"/questions/{question['id']}/recover", {"revision": parked["revision"]}).json()
    assert recovered["archived"] is False and client.get("/api/v1/search?q=Frankfurt").json()["total"] == 1
    with zipfile.ZipFile(io.BytesIO(client.get("/api/v1/export").content)) as archive:
        assert {"records/roadmap_questions.json", "records/roadmap_decisions.json"} <= set(archive.namelist())

    # Agents cannot write them directly, but can propose them for approval.
    cred = write(client, "POST", "/credentials", {"label": "Planning agent"}).json()
    headers = {"Authorization": "Bearer " + cred["token"]}
    assert write(client, "POST", "/questions", {"roadmapId": roadmap["id"], "title": "Direct"}, headers=headers).status_code == 403
    operations = [
        {"action": "question.create", "payload": {"roadmapId": roadmap["id"], "title": "Do we shard by tenant?"}},
        {"action": "decision.create", "payload": {"roadmapId": roadmap["id"], "title": "Defer sharding", "rationale": "Below 10 GB.", "decidedDate": "2026-10-30"}},
    ]
    proposal = write(client, "POST", "/proposals", {"title": "Planning notes", "rationale": "From the design review", "operations": operations}, headers=headers)
    assert proposal.status_code == 200, proposal.text
    bad = write(client, "POST", "/proposals", {"title": "Bad", "rationale": "x", "operations": [{"action": "question.create", "payload": {"roadmapId": roadmap["id"], "title": "x", "status": "answered"}}]}, headers=headers)
    assert bad.status_code == 422
    submitted = write(client, "POST", f"/proposals/{proposal.json()['id']}/submit", {"revision": proposal.json()["revision"]}, headers=headers).json()
    assert write(client, "POST", f"/proposals/{submitted['id']}/approve", {"revision": submitted["revision"]}).status_code == 200
    detail = client.get(f"/api/v1/roadmaps/{roadmap['id']}").json()
    assert any(q["title"] == "Do we shard by tenant?" and q["status"] == "open" for q in detail["questions"])
    assert any(d["title"] == "Defer sharding" and d["decidedDate"] == "2026-10-30" for d in detail["decisions"])

    # The answer rule holds at approval too: an unanswered "answered" proposal applies nothing.
    unanswered = write(client, "POST", "/proposals", {"title": "Close it", "rationale": "Settled", "operations": [
        {"action": "question.update", "payload": {"id": question["id"], "revision": recovered["revision"], "status": "answered", "answer": " "}}]}, headers=headers).json()
    unanswered = write(client, "POST", f"/proposals/{unanswered['id']}/submit", {"revision": unanswered["revision"]}, headers=headers).json()
    assert write(client, "POST", f"/proposals/{unanswered['id']}/approve", {"revision": unanswered["revision"]}).status_code == 422
    unchanged = client.get(f"/api/v1/questions/{question['id']}").json()
    assert (unchanged["status"], unchanged["revision"]) == ("open", recovered["revision"])


def test_repository_links_survive_legacy_chat_edits_and_filter_work(client):
    repo = write(client, "POST", "/repositories", {"name": "Workbench"}).json()
    rid = repo["id"]
    linked = item(client, repositoryIds=[rid])
    item(client)
    filtered = client.get("/api/v1/work-items", params={"repositoryId": rid}).json()
    assert [(row["id"], row["repositoryIds"]) for row in filtered["items"]] == [(linked["id"], [rid])]

    chat = write(client, "POST", "/chats", {"ownerType": "item", "ownerId": linked["id"],
                 "chatId": str(uuid.uuid4()), "title": "Run", "repos": ["legacy"],
                 "repositoryIds": [rid]}).json()
    proposal = write(client, "POST", "/proposals", {"title": "Update chat context", "rationale": "Recorded name",
                     "operations": [{"action": "chat.update", "payload": {"id": chat["id"],
                                     "revision": chat["revision"], "repos": ["other legacy name"]}}]}).json()
    path = "/proposals/" + proposal["id"]
    submitted = write(client, "POST", path + "/submit", {"revision": proposal["revision"]}).json()
    approved = write(client, "POST", path + "/approve", {"revision": submitted["revision"]})
    assert approved.status_code == 200, approved.text
    saved = client.get("/api/v1/chats/" + chat["id"]).json()
    assert saved["repos"] == ["other legacy name"] and saved["repositoryIds"] == [rid]

    archive = write(client, "POST", f"/repositories/{rid}/archive", {"revision": repo["revision"]})
    assert archive.status_code == 200, archive.text
    assert client.get("/api/v1/work-items/" + linked["id"]).json()["repositoryIds"] == [rid]
    denied = write(client, "POST", "/work-items", {"title": "Invalid archived link", "repositoryIds": [rid]})
    assert denied.status_code == 422
    assert client.get("/api/v1/work-items", params={"q": "Invalid archived link"}).json()["total"] == 0


def test_catalog_proposals_require_review_and_rollback_as_a_unit(client):
    token = write(client, "POST", "/credentials", {"label": "Catalog proposer"}).json()["token"]
    bearer = {"Authorization": "Bearer " + token}
    assert write(client, "POST", "/repositories", {"name": "Direct"}, headers=bearer).status_code == 403
    w = item(client)
    proposal = write(client, "POST", "/proposals", {"title": "Atomic catalog change", "rationale": "Review",
                     "operations": [{"action": "repository.create", "payload": {"name": "Tentative"}},
                                    {"action": "item.update", "payload": {"id": w["id"], "revision": w["revision"],
                                     "repositoryIds": [str(uuid.uuid4())]}}]}, headers=bearer).json()
    path = "/proposals/" + proposal["id"]
    submitted = write(client, "POST", path + "/submit", {"revision": proposal["revision"]}, headers=bearer).json()
    response = write(client, "POST", path + "/approve", {"revision": submitted["revision"]})
    assert response.status_code == 422, response.text
    assert client.get("/api/v1/repositories", params={"q": "Tentative"}).json()["items"] == []
    assert client.get("/api/v1/work-items/" + w["id"]).json()["revision"] == w["revision"]
