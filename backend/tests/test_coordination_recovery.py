"""Versioned migration and recovery retain attribution without restoring presence."""

import hashlib
import io
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest
from worklog import migrations, recovery
from worklog.db import SCHEMA, Database
from worklog.discussions import SCHEMA as DISCUSSION_SCHEMA
from worklog.discussions import bind_credential
from worklog.plans import SCHEMA as PLAN_SCHEMA
from worklog.proposals import SCHEMA as PROPOSAL_SCHEMA

STAMP = "2026-01-01T00:00:00+00:00"


def legacy_database(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(PLAN_SCHEMA + PROPOSAL_SCHEMA + SCHEMA + DISCUSSION_SCHEMA)
        for key, value in {"dataId": "11111111-1111-4111-8111-111111111111", "timezone": "America/Chicago", "backlogRevision": 1}.items():
            conn.execute("INSERT INTO settings VALUES(?,?)", (key, json.dumps(value)))
        conn.execute("INSERT INTO credentials VALUES('credential','Existing','unchanged-hash',?,0)", (STAMP,))
        conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES('item','Work',0,?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO proposals(id,title,rationale,status,revision,actor_id,actor_label,actor_kind,operations,created_at,updated_at) VALUES('proposal','Draft','','draft',1,'credential','Existing','agent','[]',?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO receipts VALUES('credential','retry-key','request-hash','{\"unchanged\": true}')")
        conn.execute("INSERT INTO events VALUES('event','item','item.create','credential','Existing','agent',?,'2026-01-01','null','{}')", (STAMP,))
        conn.execute("INSERT INTO agent_chats(id,owner_type,owner_id,chat_id,title,created_date,created_at,updated_at,repos) VALUES('chat','item','item','external','Chat','2026-01-01',?,?,?)", (STAMP, STAMP, '["Ambiguous repo"]'))
        conn.execute("PRAGMA user_version=1")
        conn.commit()
    recovery.validate_database(path)


def test_v1_upgrade_preserves_actor_receipts_legacy_names_and_pre_upgrade_snapshot(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    legacy_database(path)
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert recovery._schema_signature(conn) == recovery._expected_schema()
        assert tuple(conn.execute("SELECT id,token_hash,revoked FROM credentials").fetchone()) == ("credential", "unchanged-hash", 0)
        assert tuple(conn.execute("SELECT actor_id,status FROM proposals").fetchone()) == ("credential", "draft")
        assert tuple(conn.execute("SELECT actor_id,key,request_hash,response_json FROM receipts").fetchone()) == ("credential", "retry-key", "request-hash", '{"unchanged": true}')
        assert conn.execute("SELECT actor_id FROM events").fetchone()[0] == "credential"
        assert conn.execute("SELECT repos FROM agent_chats").fetchone()[0] == '["Ambiguous repo"]'
        assert conn.execute("SELECT count(*) FROM repositories").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM chat_repositories").fetchone()[0] == 0
    snapshots = list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with zipfile.ZipFile(io.BytesIO(recovery.export_database(snapshots[0]))) as archive:
        assert json.loads(archive.read("manifest.json"))["schemaVersion"] == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert recovery._schema_signature(conn) == recovery._expected_schema(coordination=False)
    Database(path).initialize()
    assert len(list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))) == 1


def test_coordination_migration_failure_rolls_back_all_tables_and_version(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    legacy_database(path)
    execute = migrations.execute_schema

    def fail_after_coordination(conn, schema):
        execute(conn, schema)
        if schema == migrations.COORDINATION_SCHEMA:
            raise RuntimeError("Injected coordination migration failure")

    monkeypatch.setattr(migrations, "execute_schema", fail_after_coordination)
    with pytest.raises(RuntimeError, match="Injected"):
        Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert recovery._schema_signature(conn) == recovery._expected_schema(coordination=False)
        assert conn.execute("SELECT count(*) FROM communication_credentials").fetchone()[0] == 0
    assert len(list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))) == 1


def test_pre_migration_backup_failure_prevents_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    legacy_database(path)

    def fail(*args):
        raise OSError("Cannot preserve migration snapshot")

    monkeypatch.setattr(recovery, "backup_database", fail)
    with pytest.raises(OSError, match="snapshot"):
        Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert recovery._schema_signature(conn) == recovery._expected_schema(coordination=False)


@pytest.mark.parametrize("current_shape,version", [(True, 1), (False, 2)])
def test_schema_version_cannot_disguise_another_shape(tmp_path, current_shape, version):
    path = tmp_path / "worklog.sqlite3"
    if current_shape:
        Database(path).initialize()
    else:
        legacy_database(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(f"PRAGMA user_version={version}")
    with pytest.raises(ValueError, match="schema definitions"):
        recovery.validate_database(path)


def test_restore_and_export_preserve_coordination_snapshots_but_end_live_presence(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    legacy_database(path)
    db = Database(path)
    db.initialize()
    snapshot_contexts = [{"repositoryId": "repo", "checkoutPath": "C:/old", "worktreePath": "C:/worktree", "branch": "old-branch"}]
    with db.transaction() as conn:
        bind_credential(conn, "credential", "Existing")
        conn.execute("INSERT INTO repositories(id,name,created_at,updated_at) VALUES('repo','Repository',?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO item_repositories VALUES('item','repo',?)", (STAMP,))
        conn.execute("INSERT INTO chat_repositories VALUES('chat','repo',?)", (STAMP,))
        conn.execute("INSERT INTO agent_runs(id,agent_id,credential_id,client_run_key,display_name,created_at,updated_at) VALUES('run','credential','credential','session','Named run',?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO participations(id,run_id,item_id,description,created_at,updated_at,last_seen_at) VALUES('participation','run','item','Parallel activity',?,?,?)", (STAMP, STAMP, STAMP))
        conn.execute("INSERT INTO participation_contexts VALUES('participation',0,'repo','C:/new','C:/new-worktree','new-branch')")
        conn.execute("INSERT INTO participation_contexts VALUES('participation',1,'repo','C:/other','','another-branch')")
        conn.execute("INSERT INTO coordination_updates VALUES('update','participation','run','credential','credential','Earlier name','handoff','Original update',NULL,?,?)", (json.dumps(snapshot_contexts), STAMP))
        conn.execute("INSERT INTO coordination_update_repositories VALUES('update','repo')")
        conn.execute("INSERT INTO agent_runs(id,agent_id,credential_id,client_run_key,display_name,created_at,updated_at,ended_at,end_reason) VALUES('ended-run','credential','credential','previous-session','Finished run',?,?,?,'completed')", (STAMP, STAMP, STAMP))
        conn.execute("INSERT INTO participations(id,run_id,item_id,created_at,updated_at,last_seen_at,released_at,release_reason) VALUES('released','ended-run','item',?,?,?,?,'completed')", (STAMP, STAMP, STAMP, STAMP))
    archive_data = recovery.export_database(path)
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schemaVersion"] == 2
        updates = json.loads(archive.read("records/coordination_updates.json"))
        assert updates[0]["item_id"] is None
        assert json.loads(updates[0]["contexts_json"]) == snapshot_contexts
        assert json.loads(archive.read("records/coordination_update_repositories.json")) == [{"update_id": "update", "repository_id": "repo"}]
        assert json.loads(archive.read("records/credentials.json"))[0]["token_hash"].startswith("[redacted")
    backup = recovery.backup_database(path, tmp_path / "backups")
    target = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(target, tmp_path / "backups" / backup["name"], tmp_path / "restore-backups")
    with Database(target).read() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
        run = conn.execute("SELECT * FROM agent_runs WHERE id='run'").fetchone()
        participation = conn.execute("SELECT * FROM participations WHERE id='participation'").fetchone()
        assert run["ended_at"] and run["end_reason"] == "restore"
        assert participation["released_at"] and participation["release_reason"] == "restore"
        assert participation["last_seen_at"] == STAMP
        assert conn.execute("SELECT revoked FROM credentials").fetchone()[0] == 1
        update = conn.execute("SELECT * FROM coordination_updates").fetchone()
        assert update["author_label"] == "Earlier name"
        assert update["item_id"] is None
        assert json.loads(update["contexts_json"]) == snapshot_contexts
        assert conn.execute("SELECT count(*) FROM participation_contexts").fetchone()[0] == 2
        assert conn.execute("SELECT actor_id FROM proposals").fetchone()[0] == "credential"
        assert tuple(conn.execute("SELECT ended_at,end_reason,revision FROM agent_runs WHERE id='ended-run'").fetchone()) == (STAMP, "completed", 1)
        assert tuple(conn.execute("SELECT released_at,release_reason,revision FROM participations WHERE id='released'").fetchone()) == (STAMP, "completed", 1)
    with db.read() as conn:
        assert conn.execute("SELECT ended_at FROM agent_runs WHERE id='run'").fetchone()[0] is None
        assert conn.execute("SELECT released_at FROM participations WHERE id='participation'").fetchone()[0] is None
        assert conn.execute("SELECT revoked FROM credentials").fetchone()[0] == 0


def populated_main_database(path):
    """Main a04b437's sealed v1 shape, populated across the integration boundary."""
    legacy_database(path)
    with Database(path).transaction() as conn:
        # This literal was computed from main's definitions, independently of v2.
        assert recovery._schema_digest(recovery._schema_signature(conn)) == "c5ae7406cdd63ddfa0cd7f3a18f2cc74f898936ef42c3b72909424c2d2e5a272"
        bind_credential(conn, "credential", "Renamed agent profile")
        conn.execute("INSERT INTO roadmaps(id,title,overview,revision,created_at,updated_at) VALUES('roadmap','Plan','Overview',4,?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO steps(id,roadmap_id,title,description,position,revision) VALUES('step','roadmap','Step','Details',0,2)")
        conn.execute("UPDATE work_items SET roadmap_id='roadmap',step_id='step',revision=3 WHERE id='item'")
        conn.execute("INSERT INTO roadmap_questions(id,roadmap_id,title,details,answer,status,revision,created_at,updated_at) VALUES('question','roadmap','Why?','Context','Answer','answered',5,?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO roadmap_decisions(id,roadmap_id,title,rationale,decided_date,revision,created_at,updated_at) VALUES('decision','roadmap','Chosen','Reason','2026-01-01',7,?,?)", (STAMP, STAMP))
        conn.execute("UPDATE agent_chats SET owner_type='roadmap',owner_id='roadmap',provider='codex',model='existing-model',revision=4 WHERE id='chat'")
        conn.execute("INSERT INTO chat_steps VALUES('chat','step',?)", (STAMP,))
        conn.execute("INSERT INTO discussions(id,title,revision,created_at,updated_at) VALUES('discussion','Discussion',3,?,?)", (STAMP, STAMP))
        conn.execute("INSERT INTO discussion_contexts VALUES('discussion','roadmap','roadmap')")
        conn.execute("INSERT INTO discussion_messages(id,discussion_id,text,author_id,author_label,author_kind,created_at,chat_id) VALUES('message','discussion','Keep this','credential','Historical label','agent',?,'external')", (STAMP,))
        payload = b"\x00retained attachment\xff"
        conn.execute("INSERT INTO attachments(id,owner_type,owner_id,name,mime,size,sha256,content,archived,created_at) VALUES('blob','item','item','data.bin','application/octet-stream',?,?,?,1,?)", (len(payload), hashlib.sha256(payload).hexdigest(), payload, STAMP))
    return records_by_table(path)


def records_by_table(path, tables=None):
    with closing(sqlite3.connect(path)) as conn:
        if tables is None:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {table: sorted(conn.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr) for table in tables}


@pytest.mark.parametrize("operation", ["startup", "restore"])
def test_populated_current_main_upgrades_without_losing_records(tmp_path, operation):
    source = tmp_path / "source" / "worklog.sqlite3"
    source.parent.mkdir()
    before = populated_main_database(source)
    if operation == "startup":
        target = source
        Database(target).initialize()
        snapshots = list((source.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))
        assert len(snapshots) == 1
        assert records_by_table(snapshots[0], before) == before
        recovery.validate_database(snapshots[0])
    else:
        target = tmp_path / "target" / "worklog.sqlite3"
        recovery.restore_database(target, source, tmp_path / "backups")
        # Candidate files are read-only, and all original identities remain in them.
        assert records_by_table(source, before) == before
    after = records_by_table(target, before)
    if operation == "restore":
        assert after["settings"] != before["settings"]
        assert dict(after["settings"])["dataId"] != dict(before["settings"])["dataId"]
        assert {k: v for k, v in after["settings"] if k != "dataId"} == {k: v for k, v in before["settings"] if k != "dataId"}
        assert after["credentials"] == [(*before["credentials"][0][:-1], 1)]
        after["settings"] = before["settings"]
        after["credentials"] = before["credentials"]
    assert after == before
    recovery.validate_database(target)
    with Database(target).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        for table in ("repositories", "item_repositories", "chat_repositories", "agent_runs", "participations", "coordination_updates"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_failed_restore_upgrade_preserves_candidate_and_active_database(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate.sqlite3"
    original_records = populated_main_database(candidate)
    target = tmp_path / "active" / "worklog.sqlite3"
    Database(target).initialize()
    active_records = records_by_table(target)
    execute = migrations.execute_schema

    def fail_after_coordination(conn, schema):
        execute(conn, schema)
        if schema == migrations.COORDINATION_SCHEMA:
            raise RuntimeError("Injected restore migration failure")

    monkeypatch.setattr(migrations, "execute_schema", fail_after_coordination)
    with pytest.raises(RuntimeError, match="Injected restore"):
        recovery.restore_database(target, candidate, tmp_path / "backups")
    assert records_by_table(candidate) == original_records
    assert records_by_table(target) == active_records
    assert not list(target.parent.glob(".restore-*.sqlite3"))
    assert not (target.parent / ".restore-in-progress.json").exists()
    with closing(sqlite3.connect(candidate)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    recovery.validate_database(candidate)
    recovery.validate_database(target)
