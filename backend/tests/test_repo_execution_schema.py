"""Schema v35 migration, repository DAG topology, cycle rejection, and repository merge migration."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from test_step_update import client, ok, read, refused, write
from worklog import migrations
from worklog.db import Database
from worklog.doctor import doctor_database
from worklog.migrations import SCHEMA_VERSION

__all__ = ["client"]

SCHEMAS = Path(__file__).resolve().parent / "schemas"

# Version 35's documented downgrade to version 34: drop the six additive execution/wave tables,
# the participation step index, and the four additive columns.
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


def to_v34(path):
    """Take a current v35 database back to v34 the documented way."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK_V34)


def before_v35(rows):
    """A database's rows as they read before v35: its six additive tables left out while empty,
    steps/repositories/participations trimmed back to their v34 columns, and v35 step fields
    stripped from roadmap_versions snapshots."""
    kept = {table: values for table, values in rows.items() if table not in V35_TABLES or values}
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


def test_the_v35_upgrade_changes_no_record_and_the_way_back_gives_v34s_exact_shape(tmp_path):
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
    for table in V35_TABLES:
        assert after[table] == []
    assert before_v35(after) == before


def test_v34_upgrades_cleanly_to_v35_and_passes_doctor(tmp_path):
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
    report = doctor_database(db_path)
    assert report["ok"] is True, report
    assert report["checks"]["waves"]["problems"] == 0


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
