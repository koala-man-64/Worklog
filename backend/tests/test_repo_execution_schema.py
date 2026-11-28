"""Schema v35-v38 migrations, repository topology, dependency cycles, and merge migration."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import pytest
from test_step_update import client, ok, read, refused, write
from worklog import migrations
from worklog.db import Database
from worklog.doctor import doctor_database
from worklog.migrations import SCHEMA_VERSION

__all__ = ["client"]

SCHEMAS = Path(__file__).resolve().parent / "schemas"

# Version 36's documented downgrade to version 35. This is the only downgrade SQL kept in the
# guide; it drops push state and reply correlation, while already submitted provider messages may
# remain in their destination chats.
WAY_BACK_V35 = """\
BEGIN;
DROP TABLE push_deliveries;
DROP TABLE push_subscriptions;
DROP TABLE push_enrollments;
ALTER TABLE discussion_messages DROP COLUMN reply_to_message_id;
PRAGMA user_version=35;
COMMIT;
"""

# Historical version 35 to 34 downgrade: drop the six additive execution/wave tables, the
# participation step index, and the four additive columns. Older upgrade tests still use it after
# going through WAY_BACK_V35.
WAY_BACK_V34 = """\
UPDATE roadmap_versions SET snapshot=json_set(snapshot,'$.steps',json((SELECT json_group_array(json(json_remove(value,'$.workClass','$.repositoryIds','$.waveIds'))) FROM json_each(snapshot,'$.steps')))) WHERE snapshot LIKE '%"workClass"%' OR snapshot LIKE '%"repositoryIds"%';
DROP TABLE wave_repins;
DROP TABLE wave_repositories;
DROP TABLE wave_steps;
DROP TABLE waves;
DROP TABLE step_repositories;
DROP TABLE repository_dependencies;
DROP INDEX participations_step;
ALTER TABLE participations DROP COLUMN step_id;
ALTER TABLE repositories DROP COLUMN tier;
ALTER TABLE repositories DROP COLUMN wip_cap;
ALTER TABLE steps DROP COLUMN work_class;
PRAGMA user_version=34;
"""

V35_TABLES = (
    "repository_dependencies",
    "step_repositories",
    "waves",
    "wave_steps",
    "wave_repositories",
    "wave_repins",
)
V36_TABLES = ("push_enrollments", "push_subscriptions", "push_deliveries")
V37_TABLES = ("dependencies",)
V38_COLUMNS = ("caller_worktree_path", "worktree_claim_key")
V39_TABLES = ("deliverables", "deliverable_steps", "deliverable_acceptances")


def to_v38(path):
    """Drop the v39 deliverable tables only when empty to recover the v38 fixture shape."""
    with closing(sqlite3.connect(path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if version == 38 and not set(V39_TABLES) & tables:
            return
        if version != 39 or not set(V39_TABLES) <= tables:
            raise ValueError(f"Expected a v38 or v39 schema shape, got version {version}")
        populated = [table for table in V39_TABLES if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()]
        if populated:
            raise ValueError("The v39 deliverable tables must be empty for the v38 fixture: " + ", ".join(populated))
        conn.execute("BEGIN")
        conn.execute("DROP TABLE deliverable_acceptances")
        conn.execute("DROP TABLE deliverable_steps")
        conn.execute("DROP TABLE deliverables")
        conn.execute("PRAGMA user_version=38")
        conn.commit()


def to_v37(path):
    """Project a v39/v38 fixture back to v37 only while its additive records and lease fields are empty."""
    with closing(sqlite3.connect(path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 39:
        to_v38(path)
    with closing(sqlite3.connect(path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(repository_leases)")}
        if version == 38 and set(V38_COLUMNS) <= columns:
            if conn.execute(
                "SELECT 1 FROM repository_leases WHERE caller_worktree_path IS NOT NULL "
                "OR worktree_claim_key IS NOT NULL LIMIT 1"
            ).fetchone():
                raise ValueError("The v38 caller worktree fields must be empty for the v37 fixture")
            conn.execute("DROP INDEX repository_leases_active_claim_key")
            conn.execute("ALTER TABLE repository_leases DROP COLUMN worktree_claim_key")
            conn.execute("ALTER TABLE repository_leases DROP COLUMN caller_worktree_path")
            conn.execute("PRAGMA user_version=37")
            conn.commit()
        elif version != 37 or set(V38_COLUMNS) & columns:
            raise ValueError(f"Expected a v37 or v38 schema shape, got version {version}")


def to_v36(path):
    """Project a v39/v38 fixture back through v37 while the added tables and fields are empty."""
    with closing(sqlite3.connect(path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 36:
            return
    to_v37(path)
    with closing(sqlite3.connect(path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if version == 37 and set(V37_TABLES) <= tables:
            if conn.execute("SELECT count(*) FROM dependencies").fetchone()[0]:
                raise ValueError("The v37 dependency table must be empty for the v36 fixture")
            conn.execute("DROP TABLE dependencies")
            conn.execute("PRAGMA user_version=36")
            conn.commit()
        elif version != 36 or set(V37_TABLES) & tables:
            raise ValueError(f"Expected a v36 or v37 schema shape, got version {version}")


def to_v35(path):
    """Take a current v39 database back through the v38, v37 and v36 way-backs."""
    to_v36(path)
    with closing(sqlite3.connect(path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        message_columns = {row[1] for row in conn.execute("PRAGMA table_info(discussion_messages)")}
        has_v36_shape = set(V36_TABLES) <= tables and "reply_to_message_id" in message_columns
        has_v35_shape = not set(V36_TABLES) & tables and "reply_to_message_id" not in message_columns
        if version == 36 and has_v36_shape:
            conn.executescript(WAY_BACK_V35)
        elif version != 35 or not has_v35_shape:
            raise ValueError(f"Expected a v35 or v36 schema shape, got version {version}")


def test_v36_way_back_sql_matches_the_upgrade_guide():
    guide = Path(__file__).resolve().parents[2] / "docs" / "UPGRADING.md"
    assert WAY_BACK_V35.strip() in guide.read_text(encoding="utf-8")


def to_v34(path):
    """Take a current v39 database back to v34 through its historical way-backs."""
    to_v35(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK_V34)


def before_v38(rows):
    """Rows present in v37, excluding the v39 tables and empty v38 lease fields."""
    kept = {table: values for table, values in rows.items() if table not in V39_TABLES}
    if "repository_leases" in kept:
        kept["repository_leases"] = [row[:-2] for row in kept["repository_leases"]]
    return kept


def before_v37(rows):
    """Rows present in v36, excluding v37 dependencies, v38 lease fields, and v39 tables."""
    return {table: values for table, values in before_v38(rows).items() if table not in V37_TABLES}


def before_v36(rows):
    """Rows present in v35, excluding v36 push state/reply correlation and v37 dependencies."""
    kept = {table: values for table, values in before_v38(rows).items()
            if table not in V36_TABLES and table not in V37_TABLES}
    if "discussion_messages" in kept:
        kept["discussion_messages"] = [row[:-1] for row in kept["discussion_messages"]]
    return kept


def before_v35(rows):
    """Rows present before v35, with all later additions removed from the projection."""
    kept = {table: values for table, values in before_v38(rows).items()
            if table not in V36_TABLES and table not in V37_TABLES and table not in V35_TABLES}
    if "discussion_messages" in kept:
        kept["discussion_messages"] = [row[:-1] for row in kept["discussion_messages"]]
    if "steps" in kept:
        kept["steps"] = [row[:-1] for row in kept["steps"]]
    if "repositories" in kept:
        kept["repositories"] = [row[:-2] for row in kept["repositories"]]
    if "participations" in kept:
        kept["participations"] = [row[:-1] for row in kept["participations"]]
    if "roadmap_versions" in kept:
        cleaned = []
        for row in kept["roadmap_versions"]:
            raw = row[-1]
            if isinstance(raw, str) and ('"workClass"' in raw or '"repositoryIds"' in raw or '"waveIds"' in raw):
                snap = json.loads(raw)
                if isinstance(snap, dict) and isinstance(snap.get("steps"), list):
                    snap["steps"] = [
                        {k: v for k, v in step.items() if k not in {"workClass", "repositoryIds", "waveIds"}}
                        for step in snap["steps"]
                    ]
                cleaned.append((*row[:-1], json.dumps(snap, separators=(",", ":"))))
            else:
                cleaned.append(row)
        kept["roadmap_versions"] = cleaned
    return kept


def test_v37_upgrade_adds_an_empty_table_and_preserves_the_v36_shape(tmp_path):
    import sealed
    from test_sealed_schemas import contents

    db_path = tmp_path / "upgrade-v36.sqlite3"
    Database(db_path).initialize()
    to_v36(db_path)
    version, signature, before = contents(db_path)
    assert (version, signature) == (36, sealed.shape(36))

    Database(db_path).initialize()
    version, signature, after = contents(db_path)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    assert after["dependencies"] == []
    assert before_v37(after) == before


def test_v38_preserves_pathless_managed_and_historical_lease_rows(tmp_path):
    import sealed
    from test_sealed_schemas import contents

    db_path = tmp_path / "upgrade-v37-leases.sqlite3"
    sealed.build(db_path, 37)
    stamp = "2026-11-26T12:00:00+00:00"
    shared_path = "C:/shared/.worklog-slots/repo/slot-2"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executemany(
            "INSERT INTO repositories(id,name,created_at,updated_at) VALUES(?,?,?,?)",
            [("repo-a", "Repository A", stamp, stamp), ("repo-b", "Repository B", stamp, stamp)],
        )
        conn.executemany(
            "INSERT INTO repository_leases(id,repository_id,slot_index,worktree_path,branch,state,queue_seq,"
            "requested_at,last_heartbeat_at,released_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                ("pathless", "repo-a", 1, "", "", "active", 1, stamp, stamp, None),
                ("managed", "repo-a", 2, shared_path, "feat/current", "active", 2, stamp, stamp, None),
                ("historical", "repo-a", 2, shared_path, "feat/old", "released", 3, stamp, stamp, stamp),
                # v37's path index is repository-scoped, so this legacy duplicate must not make
                # the additive v38 claim-key index fail during migration.
                ("other-repo", "repo-b", 1, shared_path, "feat/other", "active", 1, stamp, stamp, None),
            ],
        )
        conn.commit()
    before_version, before_shape, before = contents(db_path)
    assert (before_version, before_shape) == (37, sealed.shape(37))

    Database(db_path).initialize()

    version, signature, after = contents(db_path)
    assert (version, signature) == (39, sealed.current())
    assert all(after[table] == [] for table in V39_TABLES)
    assert before_v38(after) == before
    assert all(row[-2:] == (None, None) for row in after["repository_leases"])

    to_v37(db_path)
    version, signature, downgraded = contents(db_path)
    assert (version, signature) == (37, sealed.shape(37))
    assert downgraded == before


def test_v38_claim_key_is_global_partial_and_caller_path_is_separate(tmp_path):
    db_path = tmp_path / "claim-key-index.sqlite3"
    Database(db_path).initialize()
    stamp = "2026-11-26T12:00:00+00:00"
    claim_key = "canonical:C:/shared/task"

    def insert(conn, identity, repository, state, *, key=claim_key, caller_path="C:/caller/task",
               managed_path=""):
        conn.execute(
            "INSERT INTO repository_leases(id,repository_id,worktree_path,caller_worktree_path,"
            "worktree_claim_key,state,queue_seq,requested_at,last_heartbeat_at,released_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (identity, repository, managed_path, caller_path, key, state, 1, stamp, stamp,
             stamp if state == "released" else None),
        )

    with closing(sqlite3.connect(db_path)) as conn:
        insert(conn, "active", "repo-a", "active")
        conn.commit()
        for index, state in enumerate(("offered", "active", "expired_dirty"), start=1):
            with pytest.raises(sqlite3.IntegrityError):
                insert(conn, f"duplicate-{index}", f"repo-{index}", state,
                       caller_path=f"C:/other/{index}")
            conn.rollback()
        insert(conn, "released", "repo-b", "released", caller_path="C:/released/task")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, "two-path-fields", "repo-c", "active", key="another-key",
                   caller_path="C:/caller/task", managed_path="C:/managed/task")
        conn.rollback()
        assert conn.execute(
            "SELECT count(*) FROM repository_leases WHERE worktree_claim_key=?", (claim_key,)
        ).fetchone()[0] == 2


def test_v38_way_back_refuses_nonempty_caller_worktree_fields(tmp_path):
    db_path = tmp_path / "wayback-v38-with-claims.sqlite3"
    Database(db_path).initialize()
    stamp = "2026-11-26T12:00:00+00:00"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO repository_leases(id,repository_id,caller_worktree_path,worktree_claim_key,state,"
            "queue_seq,requested_at,last_heartbeat_at) VALUES(?,?,?,?,?,?,?,?)",
            ("caller", "repo-a", "C:/caller/task", "canonical:C:/caller/task",
             "active", 1, stamp, stamp),
        )
        conn.commit()

    with pytest.raises(ValueError, match="v38 caller worktree fields"):
        to_v37(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 38
        assert conn.execute(
            "SELECT caller_worktree_path,worktree_claim_key FROM repository_leases WHERE id='caller'"
        ).fetchone() == ("C:/caller/task", "canonical:C:/caller/task")


def test_the_latest_way_back_chain_gives_v34s_exact_shape_and_upgrades_again(tmp_path):
    import sealed
    from test_sealed_schemas import contents

    db_path = tmp_path / "wayback-v35.sqlite3"
    Database(db_path).initialize()
    to_v34(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 34
    _, signature, before = contents(db_path)
    assert signature == sealed.shape(34)
    Database(db_path).initialize()
    version, signature, after = contents(db_path)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    for table in (*V35_TABLES, *V36_TABLES, *V37_TABLES):
        assert after[table] == []
    assert before_v35(after) == before


def test_v36_way_back_drops_push_state_and_reupgrade_does_not_replay_it(tmp_path):
    import sealed
    from test_sealed_schemas import contents

    db_path = tmp_path / "wayback-v36.sqlite3"
    Database(db_path).initialize()
    stamp = "2026-11-26T00:00:00+00:00"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("INSERT INTO credentials(id,label,token_hash,created_at) VALUES('credential','Agent','hash',?)", (stamp,))
        conn.execute("INSERT INTO communication_agents(id,label,created_at,avatar) "
                     "VALUES('agent','Agent',?,'red-dots-antenna')", (stamp,))
        conn.execute("INSERT INTO communication_credentials(credential_id,agent_id,registered_by,registered_by_label,chat_id,directory) "
                     "VALUES('credential','agent','sponsor','Sponsor','chat','C:/work')")
        conn.execute("INSERT INTO discussions(id,title,created_at,updated_at) VALUES('discussion','Thread',?,?)", (stamp, stamp))
        conn.execute("INSERT INTO discussion_messages(id,discussion_id,text,author_id,author_label,author_kind,created_at) "
                     "VALUES('ask','discussion','Ask','agent','Agent','agent',?)", (stamp,))
        conn.execute("INSERT INTO discussion_messages(id,discussion_id,text,author_id,author_label,author_kind,created_at,reply_to_message_id) "
                     "VALUES('reply','discussion','Reply','agent','Agent','agent',?,'ask')", (stamp,))
        for identity, kind in (("event-pending", "discussion.message"), ("event-submitted", "ticket.close"),
                               ("event-uncertain", "run.end")):
            conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,before_json,after_json) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (identity, identity, kind, "agent", "Agent", "agent", stamp, "2026-11-26", "{}", "{}"))
        conn.execute("INSERT INTO push_enrollments(id,credential_id,provider,chat_id,created_at,updated_at) "
                     "VALUES('enrollment','credential','codex','chat',?,?)", (stamp, stamp))
        conn.execute("INSERT INTO push_subscriptions(id,enrollment_id,subject_type,subject_id,created_at,updated_at) "
                     "VALUES('watch','enrollment','run','run-id',?,?)", (stamp, stamp))
        conn.executemany("INSERT INTO push_deliveries(id,enrollment_id,source_event_id,kind,subject_type,subject_id,"
                         "discussion_id,message_id,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         [("delivery-pending", "enrollment", "event-pending", "discussion.ask", "discussion", "discussion",
                           "discussion", "ask", "pending", stamp, stamp),
                          ("delivery-submitted", "enrollment", "event-submitted", "ticket.status", "ticket", "ticket-id",
                           None, None, "submitted", stamp, stamp),
                          ("delivery-uncertain", "enrollment", "event-uncertain", "run.end", "run", "run-id",
                           None, None, "uncertain", stamp, stamp)])
        conn.commit()
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT state FROM push_deliveries ORDER BY id").fetchall() == [
            ("pending",), ("submitted",), ("uncertain",)]
        assert conn.execute("SELECT reply_to_message_id FROM discussion_messages WHERE id='reply'").fetchone()[0] == "ask"

    to_v35(db_path)
    _, v35_signature, v35_rows = contents(db_path)
    assert v35_signature == sealed.shape(35)
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 35
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not set(V36_TABLES) & tables
        columns = {row[1] for row in conn.execute("PRAGMA table_info(discussion_messages)")}
        assert "reply_to_message_id" not in columns
        assert conn.execute("SELECT count(*) FROM events WHERE id LIKE 'event-%'").fetchone()[0] == 3

    Database(db_path).initialize()
    version, signature, reupgraded_rows = contents(db_path)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    for table in (*V36_TABLES, *V37_TABLES):
        assert reupgraded_rows[table] == []
    for table, rows in v35_rows.items():
        if table == "discussion_messages":
            assert [row[:-1] for row in reupgraded_rows[table]] == rows
            assert [row[-1] for row in reupgraded_rows[table]] == [None] * len(rows)
        else:
            assert reupgraded_rows[table] == rows, table


def test_v34_upgrades_cleanly_to_current_and_passes_doctor(tmp_path):
    db_path = tmp_path / "upgrade-v34.sqlite3"
    v34_spec = json.loads((SCHEMAS / "v34.json").read_text(encoding="utf-8"))
    entries = v34_spec["shapes"][0]["schema"]
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        for kind, name, sql in entries:
            if kind == "table" and not name.startswith("search_index_") and name != "sqlite_sequence":
                conn.execute(sql)
        for kind, name, sql in entries:
            if kind == "index" and not name.startswith("sqlite_"):
                conn.execute(sql)
        conn.executemany(
            "INSERT INTO settings(key,value) VALUES(?,?)",
            [
                ("dataId", json.dumps(str(uuid.uuid4()))),
                ("timezone", json.dumps("UTC")),
                ("backlogRevision", "1"),
            ],
        )
        conn.execute("PRAGMA user_version = 34")
        conn.commit()
    db = Database(db_path)
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        step_cols = {r[1] for r in conn.execute("PRAGMA table_info(steps)")}
        repo_cols = {r[1] for r in conn.execute("PRAGMA table_info(repositories)")}
        part_cols = {r[1] for r in conn.execute("PRAGMA table_info(participations)")}
        assert "work_class" in step_cols
        assert {"wip_cap", "tier"} <= repo_cols
        assert "step_id" in part_cols
        assert conn.execute("SELECT count(*) FROM dependencies").fetchone()[0] == 0
    report = doctor_database(db_path)
    assert report["ok"] is True, report
    assert report["checks"]["waves"]["problems"] == 0


def test_v35_upgrades_populated_data_without_changing_existing_records(client):
    import sealed
    from test_sealed_schemas import contents

    upstream = ok(write(client, "POST", "/repositories", {
        "name": "migration-upstream", "url": "https://example.test/migration-upstream",
    }))
    consumer = ok(write(client, "POST", "/repositories", {
        "name": "migration-consumer", "url": "https://example.test/migration-consumer",
        "upstreamIds": [upstream["id"]],
    }))
    step_id = str(uuid.uuid4())
    roadmap = ok(write(client, "POST", "/roadmaps", {
        "title": "Populated v35 migration", "overview": "",
        "steps": [{"id": step_id, "title": "Adopt the contract", "position": 0,
                   "repositoryIds": [consumer["id"]]}],
    }))
    ok(write(client, "POST", "/work-items", {"title": "Work item preserved"}))
    ok(write(client, "POST", "/tickets", {"title": "Ticket preserved"}))
    wave = ok(write(client, "POST", f"/roadmaps/{roadmap['id']}/waves", {
        "name": "Migration wave", "stepIds": [step_id],
        "repositories": [{"repositoryId": upstream["id"]}, {"repositoryId": consumer["id"]}],
    }))
    assert wave["stepIds"] == [step_id]

    db_path = client.app.state.db.path
    to_v35(db_path)
    version, signature, before = contents(db_path)
    assert (version, signature) == (35, sealed.shape(35))
    assert before["repository_dependencies"]
    assert before["step_repositories"]
    assert before["waves"]
    assert before["wave_steps"]

    Database(db_path).initialize()
    version, signature, after = contents(db_path)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    assert after["dependencies"] == []
    assert before_v36(after) == before
    assert doctor_database(db_path)["ok"] is True


def test_repository_dependencies_reject_cycles_and_merge_migrates_execution_records(client):
    r_a = ok(write(client, "POST", "/repositories", {"name": "contracts", "tier": 1, "wipCap": 1}))
    r_b = ok(write(client, "POST", "/repositories", {
        "name": "runtime-common", "tier": 1, "wipCap": 1, "upstreamIds": [r_a["id"]],
    }))
    r_c = ok(write(client, "POST", "/repositories", {
        "name": "control-plane", "tier": 2, "wipCap": 1, "upstreamIds": [r_b["id"]],
    }))
    r_dup = ok(write(client, "POST", "/repositories", {
        "name": "control-plane-dup", "tier": 2, "upstreamIds": [r_a["id"]],
    }))

    # Verify derived downstreamIds.
    assert ok(read(client, f"/repositories/{r_a['id']}"))["downstreamIds"] == sorted([r_b["id"], r_dup["id"]])
    assert ok(read(client, f"/repositories/{r_b['id']}"))["downstreamIds"] == [r_c["id"]]

    # Self-dependency and 3-node cycle (A -> B -> C -> A) are rejected with 422.
    refused(write(client, "PATCH", f"/repositories/{r_a['id']}", {
        "revision": r_a["revision"], "upstreamIds": [r_a["id"]],
    }), 422, "validation")
    refused(write(client, "PATCH", f"/repositories/{r_a['id']}", {
        "revision": r_a["revision"], "upstreamIds": [r_c["id"]],
    }), 422, "validation")

    # Link a step, wave, and freeze/re-pin to r_dup, then merge r_dup into r_c.
    step_id = str(uuid.uuid4())
    rm = ok(write(client, "POST", "/roadmaps", {
        "title": "Merge Test",
        "overview": "",
        "steps": [{"id": step_id, "title": "Adopt contracts", "position": 0, "repositoryIds": [r_dup["id"]]}],
    }))
    w = ok(write(client, "POST", f"/roadmaps/{rm['id']}/waves", {
        "name": "Wave 1",
        "stepIds": [step_id],
        "repositories": [{"repositoryId": r_a["id"], "tier": 1}, {"repositoryId": r_dup["id"], "tier": 2}],
    }))
    w = ok(write(client, "POST", f"/waves/{w['id']}/status", {"revision": w["revision"], "status": "active"}))
    ok(write(client, "POST", f"/waves/{w['id']}/freeze", {
        "revision": w["revision"], "repositoryId": r_a["id"], "freezeStatus": "frozen", "frozenRef": "v1.0",
    }))

    r_dup_now = ok(read(client, f"/repositories/{r_dup['id']}"))
    r_c_now = ok(read(client, f"/repositories/{r_c['id']}"))
    ok(write(client, "POST", f"/repositories/{r_dup['id']}/merge", {
        "revision": r_dup_now["revision"],
        "intoId": r_c["id"],
        "intoRevision": r_c_now["revision"],
    }))

    # Step repo link, dependency (A -> C), wave_repositories, and wave_repins all moved to survivor r_c cleanly.
    assert ok(read(client, f"/steps/{step_id}"))["repositoryIds"] == [r_c["id"]]
    c_after = ok(read(client, f"/repositories/{r_c['id']}"))
    assert set(c_after["upstreamIds"]) == {r_a["id"], r_b["id"]}
    w_after = ok(read(client, f"/waves/{w['id']}"))
    assert r_c["id"] in {r["repositoryId"] for r in w_after["repositories"]}
    assert r_dup["id"] not in {r["repositoryId"] for r in w_after["repositories"]}
    assert any(rp["upstreamRepoId"] == r_a["id"] and rp["consumerRepoId"] == r_c["id"] for rp in w_after["repins"])
    assert doctor_database(client.db_path)["ok"] is True
