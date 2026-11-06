"""Every released schema is sealed (consolidation step 2.5). Restore accepts a database only in a
shape some version shipped, by its digest, and builds nothing from today's code to compare it with.

backend/tests/schemas holds the DDL of each sealed shape. A schema change needs a new version and
its upgrade; then dump the new version's shape with `python backend/tests/test_sealed_schemas.py
"<what the version adds>"` and add the digest it prints to recovery.SEALED_SCHEMAS. Released dumps
are never rewritten.
"""

import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import pytest
from worklog import recovery
from worklog.db import Database
from worklog.migrations import SCHEMA_VERSION, UPGRADABLE_VERSIONS

DUMPS = Path(__file__).with_name("schemas")
SETTINGS = {"dataId": "11111111-1111-4111-8111-111111111111", "timezone": "America/Chicago", "backlogRevision": 1}
# How the tests' own builder, recovery._expected_schema, makes each version after v1.
FLAGS = {16: {}, 15: {"proposal_targets": False}, 14: {"waiting_status": False}, 13: {"task_positions": False},
         12: {"chat_confirmations": False},
         11: {"decision_state": False}, 10: {"search_keys": False},
         9: {"people": False}, 8: {"resources": False}, 7: {"scratchpads": False}, 6: {"provenance": False},
         5: {"remote_identity": False}, 4: {"tickets": False, "remote_identity": False},
         3: {"chat_messages": False, "tickets": False, "remote_identity": False},
         2: {"avatars": False, "chat_messages": False, "tickets": False, "remote_identity": False}}
FTS_SHADOWS = ("data", "idx", "content", "docsize", "config")


def dump(version):
    """Each sealed shape of a version: its recorded digest and its sqlite_master signature."""
    data = json.loads((DUMPS / f"v{version:02}.json").read_text(encoding="utf-8"))
    assert data["version"] == version
    return [(shape["digest"], {(kind, name): sql for kind, name, sql in shape["schema"]}) for shape in data["shapes"]]


# Run as a script, this file writes the dump of a version that has none yet, so it reads none.
SHAPES = [] if __name__ == "__main__" else [
    (version, digest, signature) for version in range(1, SCHEMA_VERSION + 1) for digest, signature in dump(version)]


def digest(conn):
    return recovery._schema_digest(recovery._schema_signature(conn))


def build(path, version, signature):
    """A database in a dumped shape, with the settings restore requires: its tables, then its
    indexes. FTS5 makes its own shadow tables, so the dump's are checked rather than run."""
    assert {kind for kind, _ in signature} <= {"table", "index"}
    virtual = [name for (kind, name), sql in signature.items() if kind == "table" and sql.startswith("CREATE VIRTUAL TABLE")]
    shadows = {f"{name}_{part}" for name in virtual for part in FTS_SHADOWS}
    with closing(sqlite3.connect(path)) as conn:
        for kind in ("table", "index"):
            for (entry_kind, name), sql in sorted(signature.items()):
                if entry_kind == kind and name not in shadows:
                    conn.execute(sql)
        for key, value in SETTINGS.items():
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value)))
        conn.execute(f"PRAGMA user_version={version}")
        conn.commit()
        assert recovery._schema_signature(conn) == signature


def test_every_released_version_is_sealed():
    assert set(recovery.SEALED_SCHEMAS) == UPGRADABLE_VERSIONS | {SCHEMA_VERSION} == set(range(1, SCHEMA_VERSION + 1))


def test_each_seal_is_the_digest_of_a_dumped_shape():
    for version, seals in recovery.SEALED_SCHEMAS.items():
        shapes = dump(version)
        assert [recovery._schema_digest(signature) for _, signature in shapes] == [recorded for recorded, _ in shapes]
        assert {recorded for recorded, _ in shapes} == seals, version


def test_a_new_database_has_the_current_sealed_shape(tmp_path):
    Database(tmp_path / "worklog.sqlite3").initialize()
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        assert digest(conn) in recovery.SEALED_SCHEMAS[SCHEMA_VERSION]


@pytest.mark.parametrize(("version", "sealed", "signature"), SHAPES, ids=[f"v{v}-{d[:8]}" for v, d, _ in SHAPES])
def test_restore_accepts_each_sealed_shape_and_upgrades_it_to_the_current_one(tmp_path, version, sealed, signature):
    path = tmp_path / "worklog.sqlite3"
    build(path, version, signature)
    recovery.validate_database(path)
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert digest(conn) in recovery.SEALED_SCHEMAS[SCHEMA_VERSION]
    recovery.validate_database(path)


def test_restore_refuses_a_shape_no_version_shipped(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    build(path, SCHEMA_VERSION, dump(SCHEMA_VERSION)[0][1])
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE INDEX notes_title ON notes(title)")
        conn.commit()
    with pytest.raises(ValueError, match="do not match this Worklog version"):
        recovery.validate_database(path)


def test_restore_refuses_a_sealed_shape_under_another_version(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    build(path, SCHEMA_VERSION - 1, dump(SCHEMA_VERSION)[0][1])
    with pytest.raises(ValueError, match="do not match this Worklog version"):
        recovery.validate_database(path)


def test_the_shapes_tests_build_from_todays_code_are_still_the_sealed_ones():
    # Tests build older databases with recovery._expected_schema, from today's schema strings and
    # migration functions. A change to either that alters a released shape fails here.
    for version, flags in FLAGS.items():
        assert recovery._schema_digest(recovery._expected_schema(**flags)) in recovery.SEALED_SCHEMAS[version], version
    assert {recovery._schema_digest(shape) for shape in recovery._compatible_schemas()[1:]} == recovery.SEALED_SCHEMAS[1]


def write_dump(version, shapes):
    """The dump format: one schema entry per line, sorted as the digest sorts them."""
    entries = []
    for shape, signature in shapes:
        rows = sorted((kind, name, sql) for (kind, name), sql in signature.items())
        body = ",\n".join("        " + json.dumps(list(row)) for row in rows)
        entries.append(f'    {{\n      "digest": "{recovery._schema_digest(signature)}",\n      "shape": {json.dumps(shape)},\n'
                       f'      "schema": [\n{body}\n      ]\n    }}')
    text = f'{{\n  "version": {version},\n  "shapes": [\n' + ",\n".join(entries) + "\n  ]\n}\n"
    (DUMPS / f"v{version:02}.json").write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    target = DUMPS / f"v{SCHEMA_VERSION:02}.json"
    if target.exists() or len(sys.argv) != 2:
        sys.exit(f"{target.name} is sealed: a schema change needs a new version" if target.exists()
                 else 'usage: python backend/tests/test_sealed_schemas.py "<what the version adds>"')
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "worklog.sqlite3"
        Database(path).initialize()
        with closing(sqlite3.connect(path)) as conn:
            current = recovery._schema_signature(conn)
    write_dump(SCHEMA_VERSION, [(sys.argv[1], current)])
    print(f"Wrote {target}; seal {SCHEMA_VERSION}: frozenset({{\"{recovery._schema_digest(current)}\"}})")
