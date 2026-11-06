"""Upgrade and restore boundaries for durable communication identities."""

import io
import json
import sqlite3
import uuid
import zipfile
from contextlib import closing

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import avatars, migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
LEGACY_SCHEMAS = [signature for _, _, signature in sealed.dump(1)]


def test_legacy_matrix_covers_every_sealed_v1_shape():
    assert len(LEGACY_SCHEMAS) == 9
    assert {recovery._schema_digest(schema) for schema in LEGACY_SCHEMAS} == recovery.SEALED_SCHEMAS[1]


def legacy_database(path, schema):
    sealed.build(path, 1, schema)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO credentials VALUES ('old-agent','Existing agent','unchanged-token-hash','2026-01-01',0)")
        conn.commit()
    recovery.validate_database(path)


@pytest.mark.parametrize("schema", LEGACY_SCHEMAS, ids=lambda schema: recovery._schema_digest(schema)[:8])
def test_every_supported_legacy_schema_upgrades_and_restores(tmp_path, schema):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    legacy_database(path, schema)
    db = Database(path)
    db.initialize()
    recovery.validate_database(path)
    with db.read() as conn:
        assert recovery._schema_signature(conn) == sealed.current()
        credential = conn.execute("SELECT token_hash,revoked FROM credentials WHERE id='old-agent'").fetchone()
        assert tuple(credential) == ("unchanged-token-hash", 0)
        agent_id = conn.execute("SELECT agent_id FROM communication_credentials WHERE credential_id='old-agent'").fetchone()[0]
        label, avatar = conn.execute("SELECT label,avatar FROM communication_agents WHERE id=?", (agent_id,)).fetchone()
        assert label == "Existing agent" and avatars.is_avatar(avatar)
    snapshots = list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert recovery._schema_signature(conn) == schema
    db.initialize()
    assert len(list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))) == 1
    restored = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(restored, snapshots[0], tmp_path / "restore-backups")
    with Database(restored).read() as conn:
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("SELECT revoked FROM credentials").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM communication_credentials").fetchone()[0] == 1


@pytest.mark.parametrize("schema", LEGACY_SCHEMAS, ids=lambda schema: recovery._schema_digest(schema)[:8])
def test_failed_discussion_migration_rolls_back_and_preserves_snapshot(tmp_path, monkeypatch, schema):
    path = tmp_path / "worklog.sqlite3"
    legacy_database(path, schema)
    original_migrate = migrations.add_discussions

    def fail_after_schema(conn):
        original_migrate(conn)
        raise RuntimeError("Synthetic migration failure")

    monkeypatch.setattr(migrations, "add_discussions", fail_after_schema)
    with pytest.raises(RuntimeError, match="Synthetic"):
        Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert recovery._schema_signature(conn) == schema
        assert conn.execute("SELECT token_hash FROM credentials").fetchone()[0] == "unchanged-token-hash"
    snapshot = next((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    # SQLite backup legitimately changes header counters; compare durable content.
    recovery.validate_database(snapshot)
    with closing(sqlite3.connect(snapshot)) as conn:
        assert recovery._schema_signature(conn) == schema
        assert conn.execute("SELECT token_hash FROM credentials").fetchone()[0] == "unchanged-token-hash"


def human_client(app):
    client = TestClient(app, base_url=ORIGIN)
    client.headers["Origin"] = ORIGIN
    response = client.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
    client.headers["X-CSRF-Token"] = response.json()["csrfToken"]
    return client


def post(client, path, body, key=None, token=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if token:
        headers["Authorization"] = "Bearer " + token
    return client.post("/api/v1" + path, json=body, headers=headers)


def test_rotation_preserves_identity_and_retry_namespace_then_restore_revokes(tmp_path):
    app = create_app(tmp_path / "live", testing=True)
    with human_client(app) as client:
        issued = post(client, "/credentials", {"label": "Implementation"}).json()
        replay_key = str(uuid.uuid4())
        body = {"text": "Keep this handoff across token rotation."}
        first = post(client, "/discussions", body, replay_key, issued["token"])
        assert first.status_code == 200, first.text
        rotation_key = str(uuid.uuid4())
        rotated_response = post(client, f"/credentials/{issued['id']}/rotate", {}, rotation_key)
        assert rotated_response.status_code == 200, rotated_response.text
        rotated = rotated_response.json()
        assert rotated["agentId"] == issued["agentId"]
        assert rotated["id"] != issued["id"]
        assert post(client, f"/credentials/{issued['id']}/rotate", {}, rotation_key).json()["token"] is None
        assert post(client, "/discussions", body, token=issued["token"]).status_code == 401
        replay = post(client, "/discussions", body, replay_key, rotated["token"])
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        conflict = post(client, "/discussions", {"text": "different"}, replay_key, rotated["token"])
        assert conflict.status_code == 409
        # New communication credentials do not inherit old private proposal ownership.
        denied = post(client, f"/credentials/{rotated['id']}/rotate", {}, token=rotated["token"])
        assert denied.status_code == 403
        cursor = client.get("/api/v1/discussions/changes").json()["cursor"]
        discussion_id = first.json()["discussion"]["id"]
    backup = recovery.backup_database(app.state.db.path, tmp_path / "backups")
    restored_dir = tmp_path / "restored"
    recovery.restore_database(restored_dir / "worklog.sqlite3", tmp_path / "backups" / backup["name"], tmp_path / "restore-backups")
    restored_app = create_app(restored_dir, testing=True)
    with human_client(restored_app) as client:
        assert client.get(f"/api/v1/discussions/{discussion_id}").json()["messages"][0]["authorId"] == issued["agentId"]
        stale = client.get("/api/v1/discussions/changes", params={"cursor": cursor})
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "cursor_reset"
        assert post(client, "/discussions", body, token=rotated["token"]).status_code == 401
        reissued = post(client, f"/credentials/{rotated['id']}/rotate", {}).json()
        assert reissued["agentId"] == issued["agentId"]
        assert post(client, "/discussions", {"text": "Resumed after restore"}, token=reissued["token"]).status_code == 200


def test_restore_preserves_handoff_and_receipts_then_successor_manages_request(tmp_path):
    app = create_app(tmp_path / "live", testing=True)
    with human_client(app) as client:
        def send(path, body, token=None):
            response = post(client, path, body, token=token)
            assert response.status_code == 200, response.text
            return response.json()

        original = send("/credentials", {"label": "Original"})
        successor = send("/credentials", {"label": "Successor"})
        item = send("/work-items", {"title": "Continued work"})
        role = send("/discussions/roles", {"contextType": "item", "contextId": item["id"], "label": "Implementation"})
        assert role["contextTitle"] == "Continued work"
        role = send(f"/discussions/roles/{role['id']}/claim", {"revision": role["revision"]}, original["token"])
        incoming = send("/discussions", {"text": "Confirm receipt", "acknowledgement": {"targets": [{"kind": "role", "id": role["id"]}]}})["message"]
        received = send(f"/discussions/messages/{incoming['id']}/acknowledge", {"targetIds": [incoming["request"]["targets"][0]["id"]]}, original["token"])
        outgoing = send("/discussions", {"text": "Human input still needed", "onBehalfOfRoleId": role["id"], "acknowledgement": {"targets": [{"kind": "human", "id": "local-user"}]}}, original["token"])["message"]
        offered = send(f"/discussions/roles/{role['id']}/offer", {"revision": role["revision"], "successorId": successor["agentId"], "note": "Continue this context"}, original["token"])
        transferred = send(f"/discussions/roles/{role['id']}/accept", {"revision": offered["revision"]}, successor["token"])
        assert transferred["generation"] == role["generation"] + 1
    with zipfile.ZipFile(io.BytesIO(recovery.export_database(app.state.db.path))) as exported:
        roles = json.loads(exported.read("records/discussion_roles.json"))
        assert roles[0]["owner_id"] == successor["agentId"]
        receipts = json.loads(exported.read("records/discussion_acknowledgements.json"))
        assert receipts[0]["actor_id"] == original["agentId"]
    backup = recovery.backup_database(app.state.db.path, tmp_path / "backups")
    restored_dir = tmp_path / "restored"
    recovery.restore_database(restored_dir / "worklog.sqlite3", tmp_path / "backups" / backup["name"], tmp_path / "restore-backups")
    with human_client(create_app(restored_dir, testing=True)) as client:
        restored_role = client.get("/api/v1/discussions/roles").json()["items"][0]
        assert restored_role == transferred
        restored_receipt = client.get(f"/api/v1/discussions/messages/{incoming['id']}").json()
        assert restored_receipt["request"]["receipts"] == received["request"]["receipts"]
        assert restored_receipt["request"]["state"] == "satisfied"
        reissued = post(client, f"/credentials/{successor['id']}/rotate", {}).json()
        cancelled = post(client, f"/discussions/messages/{outgoing['id']}/cancel", {}, token=reissued["token"])
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["authorId"] == original["agentId"]
        assert cancelled.json()["request"]["state"] == "cancelled"
