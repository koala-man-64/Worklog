"""Every released schema is sealed (consolidation step 2.5). Restore accepts a database only in a
shape some version shipped, by its digest, and builds nothing from today's code to compare it with.
A new database starts from schema.BASELINE at the current version, and every older shape upgrades
to the same one.

backend/tests/schemas holds the DDL of each sealed shape. A schema change needs a new version: its
upgrade in migrations.py and the same change in schema.BASELINE. Then dump the new version's shape
with `python backend/tests/test_sealed_schemas.py "<what the version adds>"`, which writes it only
when a new database and the previous version's shape, upgraded, agree, and add the digest it prints
to recovery.SEALED_SCHEMAS. Released dumps are never rewritten.
"""

import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import pytest
import sealed
from worklog import recovery
from worklog.db import Database
from worklog.migrations import SCHEMA_VERSION, UPGRADABLE_VERSIONS

# Run as a script, this file writes the dump of a version that has none yet, so it reads none.
SHAPES = [] if __name__ == "__main__" else [
    (version, digest, signature) for version in range(1, SCHEMA_VERSION + 1) for digest, _, signature in sealed.dump(version)]


def digest(conn):
    return recovery._schema_digest(recovery._schema_signature(conn))


def contents(path):
    """The version, the shape and every row of a database, apart from its own dataId."""
    with closing(sqlite3.connect(path)) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        rows = {table: sorted(conn.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr) for table in tables}
        rows["settings"] = [row for row in rows["settings"] if row[0] != "dataId"]
        return conn.execute("PRAGMA user_version").fetchone()[0], recovery._schema_signature(conn), rows


def test_every_released_version_is_sealed():
    assert set(recovery.SEALED_SCHEMAS) == UPGRADABLE_VERSIONS | {SCHEMA_VERSION} == set(range(1, SCHEMA_VERSION + 1))


def test_each_seal_is_the_digest_of_a_dumped_shape():
    for version, seals in recovery.SEALED_SCHEMAS.items():
        shapes = sealed.dump(version)
        assert [recovery._schema_digest(signature) for _, _, signature in shapes] == [recorded for recorded, _, _ in shapes]
        assert {recorded for recorded, _, _ in shapes} == seals, version


def test_a_new_database_has_the_current_sealed_shape(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert digest(conn) in recovery.SEALED_SCHEMAS[SCHEMA_VERSION]
    recovery.validate_database(path)


def test_a_new_database_is_the_one_an_upgrade_from_an_empty_v1_gives(tmp_path):
    # Until step 2.5 a new database was made at v1 and upgraded. Starting from the baseline instead
    # gives the same database: its version, its shape and every row, FTS5's own included.
    created, upgraded = tmp_path / "created" / "worklog.sqlite3", tmp_path / "upgraded" / "worklog.sqlite3"
    Database(created).initialize()
    upgraded.parent.mkdir()
    sealed.build(upgraded, 1)
    Database(upgraded).initialize()
    assert contents(created) == contents(upgraded)


@pytest.mark.parametrize(("version", "recorded", "signature"), SHAPES, ids=[f"v{v}-{d[:8]}" for v, d, _ in SHAPES])
def test_restore_accepts_each_sealed_shape_and_upgrades_it_to_the_current_one(tmp_path, version, recorded, signature):
    path = tmp_path / "worklog.sqlite3"
    sealed.build(path, version, signature)
    recovery.validate_database(path)
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    recovery.validate_database(path)


def test_restore_refuses_a_shape_no_version_shipped(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    sealed.build(path, SCHEMA_VERSION)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE INDEX notes_title ON notes(title)")
        conn.commit()
    with pytest.raises(ValueError, match="do not match this Worklog version"):
        recovery.validate_database(path)


def test_restore_refuses_a_sealed_shape_under_another_version(tmp_path):
    # A version can change data alone, as v24 does, and keep the shape before it: claim the current
    # shape under the newest version that shipped another.
    path = tmp_path / "worklog.sqlite3"
    older = max(version for version in range(1, SCHEMA_VERSION)
                if all(signature != sealed.current() for _, _, signature in sealed.dump(version)))
    sealed.build(path, older, sealed.current())
    with pytest.raises(ValueError, match="do not match this Worklog version"):
        recovery.validate_database(path)


def write_dump(version, shapes):
    """The dump format: one schema entry per line, sorted as the digest sorts them."""
    entries = []
    for shape, signature in shapes:
        rows = sorted((kind, name, sql) for (kind, name), sql in signature.items())
        body = ",\n".join("        " + json.dumps(list(row)) for row in rows)
        entries.append(f'    {{\n      "digest": "{recovery._schema_digest(signature)}",\n      "shape": {json.dumps(shape)},\n'
                       f'      "schema": [\n{body}\n      ]\n    }}')
    text = f'{{\n  "version": {version},\n  "shapes": [\n' + ",\n".join(entries) + "\n  ]\n}\n"
    (sealed.DUMPS / f"v{version:02}.json").write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    target = sealed.DUMPS / f"v{SCHEMA_VERSION:02}.json"
    if target.exists() or len(sys.argv) != 2:
        sys.exit(f"{target.name} is sealed: a schema change needs a new version" if target.exists()
                 else 'usage: python backend/tests/test_sealed_schemas.py "<what the version adds>"')
    with tempfile.TemporaryDirectory() as folder:
        created, upgraded = Path(folder) / "created" / "worklog.sqlite3", Path(folder) / "upgraded" / "worklog.sqlite3"
        Database(created).initialize()
        upgraded.parent.mkdir()
        sealed.build(upgraded, SCHEMA_VERSION - 1)
        Database(upgraded).initialize()
        with closing(sqlite3.connect(created)) as conn, closing(sqlite3.connect(upgraded)) as other:
            current = recovery._schema_signature(conn)
            if recovery._schema_signature(other) != current:
                sys.exit(f"schema.BASELINE and the upgrade from v{SCHEMA_VERSION - 1} build different shapes: make them agree")
    write_dump(SCHEMA_VERSION, [(sys.argv[1], current)])
    print(f"Wrote {target}; seal {SCHEMA_VERSION}: frozenset({{\"{recovery._schema_digest(current)}\"}})")
