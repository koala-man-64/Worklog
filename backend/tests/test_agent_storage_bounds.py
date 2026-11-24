"""Agent-driven storage has bounds (audit finding B7 for evidence, roadmap step 2.6; Rudy's answer of
2026-11-24 on evidence: kept 30 days).

The limits were per proposal (20 files, 100 MB) and per count of drafts (50). Withdrawing a draft freed
its place and kept its bytes, and nothing deleted evidence, so a looping agent added 100 MB a cycle;
each edit of a draft added to the history with no count. Now one identity's evidence on proposals
never approved has a total, a proposal takes a bounded number of revisions, and compact empties the
evidence of proposals withdrawn or rejected more than EVIDENCE_DAYS ago, keeping its row, name, size
and hash: its download answers 410 and it reads as removed."""

import io
import sqlite3
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from worklog import compaction, proposals, recovery
from worklog.app import create_app
from worklog.doctor import doctor_database

ORIGIN = "http://127.0.0.1:8877"


def connect(c):
    c.headers["Origin"] = ORIGIN
    boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
    c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
    return c


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, testing=True), base_url=ORIGIN) as c:
        connect(c)
        c.data = tmp_path
        yield c


def call(c, method, path, body=None, agent=None, expect=200, **options):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **options.pop("headers", {})}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    response = c.request(method, "/api/v1" + path, json=body, headers=headers, **options)
    assert response.status_code == expect, response.text
    return response.json()


def draft(c, agent, title="Plan"):
    return call(c, "POST", "/proposals", {"title": title, "rationale": "",
                                          "operations": [{"action": "item.create", "payload": {"title": "New"}}]},
                agent=agent)


def upload(c, agent, proposal, size, expect=200):
    return call(c, "POST", f"/attachments?ownerType=proposal&ownerId={proposal['id']}&name=evidence.txt", agent=agent,
                expect=expect, content=b"e" * size, headers={"Content-Type": "text/plain"})


@pytest.fixture
def agent(client):
    return call(client, "POST", "/credentials", {"label": "Looper"})


def test_one_identitys_evidence_on_proposals_never_approved_has_a_total(client, agent, monkeypatch):
    monkeypatch.setattr(proposals, "IDENTITY_EVIDENCE_BYTES", 100)
    first = draft(client, agent, "First")
    upload(client, agent, first, 60)
    # Withdrawing frees the draft's place but not its bytes.
    call(client, "POST", f"/proposals/{first['id']}/withdraw", {"revision": first["revision"]}, agent=agent)
    second = draft(client, agent, "Second")
    refused = upload(client, agent, second, 60, expect=429)
    assert refused["error"]["code"] == "agent_limit" and "never approved" in refused["error"]["message"]
    upload(client, agent, second, 30)


def test_evidence_on_an_approved_proposal_leaves_the_total(client, agent, monkeypatch):
    monkeypatch.setattr(proposals, "IDENTITY_EVIDENCE_BYTES", 100)
    done = draft(client, agent, "Done")
    upload(client, agent, done, 60)
    submitted = call(client, "POST", f"/proposals/{done['id']}/submit", {"revision": done["revision"]}, agent=agent)
    call(client, "POST", f"/proposals/{done['id']}/approve", {"revision": submitted["revision"]})
    upload(client, agent, draft(client, agent, "Next"), 60)


def test_a_proposal_takes_a_bounded_number_of_revisions(client, agent, monkeypatch):
    monkeypatch.setattr(proposals, "PROPOSAL_REVISIONS", 4)
    current = draft(client, agent)
    for n in range(3):
        current = call(client, "PATCH", f"/proposals/{current['id']}", {"revision": current["revision"], "title": f"Edit {n}"},
                       agent=agent)
    refused = call(client, "PATCH", f"/proposals/{current['id']}", {"revision": current["revision"], "title": "One more"},
                   agent=agent, expect=429)
    assert refused["error"]["code"] == "agent_limit" and "supersede" in refused["error"]["message"]


def set_closed(c, proposal_id, days_ago):
    stamp = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    with closing(sqlite3.connect(c.data / "worklog.sqlite3")) as conn:
        conn.execute("UPDATE proposals SET updated_at=? WHERE id=?", (stamp, proposal_id))
        conn.commit()


def test_compact_empties_the_evidence_of_proposals_closed_long_ago(tmp_path):
    with TestClient(create_app(tmp_path, testing=True), base_url=ORIGIN) as c:
        connect(c)
        c.data = tmp_path
        agent = call(c, "POST", "/credentials", {"label": "Looper"})
        old = draft(c, agent, "Withdrawn long ago")
        old_file = upload(c, agent, old, 500)
        call(c, "POST", f"/proposals/{old['id']}/withdraw", {"revision": old["revision"]}, agent=agent)
        recent = draft(c, agent, "Rejected lately")
        recent_file = upload(c, agent, recent, 500)
        submitted = call(c, "POST", f"/proposals/{recent['id']}/submit", {"revision": recent["revision"]}, agent=agent)
        call(c, "POST", f"/proposals/{recent['id']}/reject", {"revision": submitted["revision"]})
        open_draft = draft(c, agent, "Still a draft")
        open_file = upload(c, agent, open_draft, 500)
        set_closed(c, old["id"], compaction.EVIDENCE_DAYS + 1)
        set_closed(c, recent["id"], compaction.EVIDENCE_DAYS - 1)
        set_closed(c, open_draft["id"], compaction.EVIDENCE_DAYS + 5)
    database = tmp_path / "worklog.sqlite3"
    result = compaction.compact_database(database, tmp_path / "backups")
    assert (result["evidenceEmptied"], result["evidenceBytesReclaimed"]) == (1, 500)
    with closing(sqlite3.connect(database)) as conn:
        rows = {r[0]: r[1:] for r in conn.execute("SELECT id,name,size,sha256,length(content) FROM attachments")}
    assert rows[old_file["id"]] == ("evidence.txt", 500, old_file["sha256"], 0)
    assert rows[recent_file["id"]][3] == 500 and rows[open_file["id"]][3] == 500
    # The export and the doctor take a database that holds an emptied file.
    with zipfile.ZipFile(io.BytesIO(recovery.export_database(database))) as archive:
        assert archive.testzip() is None
    assert doctor_database(database)["ok"] is True
    with TestClient(create_app(tmp_path, testing=True), base_url=ORIGIN) as c:
        connect(c)
        gone = c.get(f"/api/v1/attachments/{old_file['id']}")
        assert gone.status_code == 410 and gone.json()["error"]["code"] == "removed"
        listed = c.get(f"/api/v1/attachments?ownerType=proposal&ownerId={old['id']}").json()["items"]
        assert [(f["id"], f["removed"]) for f in listed] == [(old_file["id"], True)]
        assert c.get(f"/api/v1/attachments/{recent_file['id']}").status_code == 200
    # A second compaction has nothing more to empty.
    assert compaction.compact_database(database, tmp_path / "backups")["evidenceEmptied"] == 0



def test_an_agents_reopen_meets_the_revision_bound_too(client, agent, monkeypatch):
    monkeypatch.setattr(proposals, "PROPOSAL_REVISIONS", 4)
    current = draft(client, agent)
    current = call(client, "POST", f"/proposals/{current['id']}/submit", {"revision": current["revision"]}, agent=agent)
    current = call(client, "POST", f"/proposals/{current['id']}/reopen", {"revision": current["revision"]}, agent=agent)
    current = call(client, "POST", f"/proposals/{current['id']}/submit", {"revision": current["revision"]}, agent=agent)
    refused = call(client, "POST", f"/proposals/{current['id']}/reopen", {"revision": current["revision"]}, agent=agent,
                   expect=429)
    assert refused["error"]["code"] == "agent_limit"


def test_compact_leaves_an_approved_copy_of_evidence_whole(tmp_path):
    with TestClient(create_app(tmp_path, testing=True), base_url=ORIGIN) as c:
        connect(c)
        c.data = tmp_path
        agent = call(c, "POST", "/credentials", {"label": "Looper"})
        item = call(c, "POST", "/work-items", {"title": "Target"})
        attaching = draft(c, agent, "Attach it")
        evidence = upload(c, agent, attaching, 400)
        attaching = call(c, "PATCH", f"/proposals/{attaching['id']}", {"revision": attaching["revision"], "operations": [
            {"action": "attachment.create",
             "payload": {"ownerType": "item", "ownerId": item["id"], "attachmentId": evidence["id"]}}]}, agent=agent)
        submitted = call(c, "POST", f"/proposals/{attaching['id']}/submit", {"revision": attaching["revision"]}, agent=agent)
        call(c, "POST", f"/proposals/{attaching['id']}/approve", {"revision": submitted["revision"]})
        set_closed(c, attaching["id"], compaction.EVIDENCE_DAYS + 10)
    database = tmp_path / "worklog.sqlite3"
    assert compaction.compact_database(database, tmp_path / "backups")["evidenceEmptied"] == 0
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT length(content) FROM attachments WHERE owner_type='item'").fetchone() == (400,)
        assert conn.execute("SELECT length(content) FROM attachments WHERE id=?", (evidence["id"],)).fetchone() == (400,)
