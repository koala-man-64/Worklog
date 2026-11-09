"""Schema v12 copies decision state out of event history into columns: who registered each
credential's identity and the chat it registered with, when each proposal was last submitted
and whether automatic approval applied it, and which stale-plan notices were sent. Upgrade,
the way back, restore validation, and decisions that no longer read event JSON."""

import secrets
import sqlite3
import uuid
from contextlib import closing

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
CHAT = "5c1d9e3a-7b2f-4c80-9d6e-0a1b2c3d4e5f"
# From version 12 to version 11, with Worklog stopped and after a manual backup. The README gave this way back
# while version 12 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
BEGIN;
DROP TABLE proposal_stale_notices;
ALTER TABLE proposals DROP COLUMN auto_approved;
ALTER TABLE proposals DROP COLUMN submitted_at;
ALTER TABLE communication_credentials DROP COLUMN chat_id;
ALTER TABLE communication_credentials DROP COLUMN registered_by_label;
ALTER TABLE communication_credentials DROP COLUMN registered_by;
PRAGMA user_version=11;
COMMIT;
"""


def to_v11(path):
    """Take a current database back to the v11 shape the documented way: to version 12 first."""
    from test_chat_confirmations import to_v12

    to_v12(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


class Instance:
    """A person's browser session and an agent's wire on one app, as in the other HTTP tests."""

    def __init__(self, directory):
        self.app = create_app(directory, testing=True)
        self.person = TestClient(self.app, base_url=ORIGIN)
        self.wire = TestClient(self.app, base_url=ORIGIN)

    def __enter__(self):
        self.person.__enter__()
        self.wire.__enter__()
        self.person.headers["Origin"] = ORIGIN
        boot = self.person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        self.person.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        return self

    def __exit__(self, *exc):
        self.wire.__exit__(*exc)
        self.person.__exit__(*exc)

    def call(self, method, path, body=None, token=None):
        client = self.wire if token else self.person
        headers = {"Idempotency-Key": str(uuid.uuid4()), **({"Authorization": "Bearer " + token} if token else {})}
        response = client.request(method, "/api/v1" + path, json=body, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()


def populate(i):
    """A registered identity, a submitted proposal, a withdrawn one that was submitted, and one
    approved automatically."""
    sponsor = i.call("POST", "/credentials", {"label": "Shared agents"})
    child_token = secrets.token_urlsafe(32)
    child = i.call("POST", "/agent-registrations", {"label": "Parser fix", "token": child_token, "chatId": CHAT},
                   sponsor["token"])
    item = i.call("POST", "/work-items", {"title": "Target"})

    def proposal(title):
        return i.call("POST", "/proposals", {"title": title, "rationale": "", "operations": [
            {"action": "item.update", "payload": {"id": item["id"], "revision": 1, "title": title}}]}, child_token)

    submitted = proposal("Submitted")
    submitted = i.call("POST", f"/proposals/{submitted['id']}/submit", {"revision": submitted["revision"]}, child_token)
    withdrawn = proposal("Withdrawn after submitting")
    withdrawn = i.call("POST", f"/proposals/{withdrawn['id']}/submit", {"revision": withdrawn["revision"]}, child_token)
    withdrawn = i.call("POST", f"/proposals/{withdrawn['id']}/withdraw", {"revision": withdrawn["revision"]}, child_token)
    i.call("PATCH", "/settings", {"requireProposalApproval": False})
    automatic = proposal("Approved automatically")
    automatic = i.call("POST", f"/proposals/{automatic['id']}/submit", {"revision": automatic["revision"]}, child_token)
    i.call("PATCH", "/settings", {"requireProposalApproval": True})
    return {"sponsor": sponsor, "child": child, "child_token": child_token, "submitted": submitted["id"],
            "withdrawn": withdrawn["id"], "automatic": automatic["id"]}


def shown(i, seeded):
    """What the person and another agent see of this state."""
    settings = i.call("GET", "/settings")
    credentials = {c["id"]: (c["registeredBy"], c["chatId"]) for c in settings["credentials"]}
    proposals = {p["id"]: (p["status"], p["submittedAt"] is not None, p["autoApproved"])
                 for p in i.call("GET", "/proposals")["items"]}
    other = i.call("POST", "/credentials", {"label": "Another agent " + uuid.uuid4().hex[:6]})
    seen_by_other = sorted(p["id"] for p in i.call("GET", "/proposals", token=other["token"])["items"])
    return credentials[seeded["child"]["id"]], proposals, seen_by_other


def test_a_v11_database_upgrades_with_the_same_decisions(tmp_path):
    with Instance(tmp_path) as i:
        seeded = populate(i)
        before = shown(i, seeded)
    path = tmp_path / "worklog.sqlite3"
    to_v11(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert recovery._schema_signature(conn) == sealed.shape(11)
        # A notice sent before v12, held only in its event.
        conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,before_json,after_json)"
                     " VALUES(?,?,'proposal.stale','local-user','You','human','2026-11-01T00:00:00+00:00','2026-10-31','null',?)",
                     (str(uuid.uuid4()), seeded["submitted"],
                      '{"proposalRevision": 2, "conflicts": [{"operation": 1, "currentRevision": 3}]}'))
        conn.commit()
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert [tuple(r) for r in conn.execute("SELECT proposal_id,proposal_revision,conflicts FROM proposal_stale_notices")] == [
            (seeded["submitted"], 2, "[[1,3]]")]
    with Instance(tmp_path) as i:
        after = shown(i, seeded)
    assert after == before
    assert {seeded["submitted"], seeded["withdrawn"], seeded["automatic"]} <= set(after[2])


def test_decisions_no_longer_read_event_json(tmp_path):
    with Instance(tmp_path) as i:
        seeded = populate(i)
        before = shown(i, seeded)
        # As a later compaction might: every registration and proposal event keeps its row but not its payload.
        with i.app.state.db.transaction() as conn:
            conn.execute("UPDATE events SET before_json='null',after_json='{}' "
                         "WHERE kind='credential.register' OR kind LIKE 'proposal.%'")
        after = shown(i, seeded)
        assert after == before
        assert seeded["withdrawn"] in after[2]
        # A registered identity can still retire itself; one issued in Settings still cannot.
        retired = i.call("POST", "/agent-registrations/retire", {}, seeded["child_token"])
        assert retired["retired"] is True


def test_the_way_back_to_v11_gives_its_exact_shape_and_upgrades_again(tmp_path):
    with Instance(tmp_path) as i:
        populate(i)
    path = tmp_path / "worklog.sqlite3"
    to_v11(path)
    recovery.validate_database(path)
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        registered = conn.execute("SELECT count(*) FROM communication_credentials WHERE registered_by IS NOT NULL").fetchone()[0]
        assert registered == 1


def test_a_v11_shape_cannot_claim_version_12_and_a_v12_shape_cannot_claim_11(tmp_path):
    from test_chat_confirmations import to_v12

    older, current = tmp_path / "older.sqlite3", tmp_path / "current.sqlite3"
    Database(older).initialize()
    to_v11(older)
    Database(current).initialize()
    to_v12(current)
    for path, version in ((older, 12), (current, 11)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={version}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
