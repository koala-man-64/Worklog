"""Released schemas as tests read them, from the dumps in schemas/ (consolidation step 2.5).

A test that needs an older database builds it in a shape its version shipped in, and checks a
database against the shape a version sealed. Neither comes from today's code: the dumps are the
record recovery.SEALED_SCHEMAS seals, and test_sealed_schemas.py checks every digest against them.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from worklog import recovery
from worklog.migrations import SCHEMA_VERSION

DUMPS = Path(__file__).with_name("schemas")
SETTINGS = {"dataId": "11111111-1111-4111-8111-111111111111", "timezone": "America/Chicago", "backlogRevision": 1}
FTS_SHADOWS = ("data", "idx", "content", "docsize", "config")

Signature = dict[tuple[str, str], str]


def dump(version: int) -> list[tuple[str, str, Signature]]:
    """Each sealed shape of a version, as its dump records it: digest, description and sqlite_master."""
    data = json.loads((DUMPS / f"v{version:02}.json").read_text(encoding="utf-8"))
    assert data["version"] == version
    return [(entry["digest"], entry["shape"], {(kind, name): sql for kind, name, sql in entry["schema"]})
            for entry in data["shapes"]]


def shape(version: int, index: int = 0) -> Signature:
    """A shape a version shipped in. Only v1 shipped in more than one, and its last comes first."""
    return dump(version)[index][2]


def current() -> Signature:
    """The current version's shape: a new database has it, and so does every upgraded one."""
    return shape(SCHEMA_VERSION)


def build(path: Path, version: int, signature: Signature | None = None) -> None:
    """A database at a version, in the shape it shipped in unless another is given, with the settings
    restore requires: its tables, then its indexes. FTS5 makes its own shadow tables, so the dump's
    are checked rather than run."""
    signature = shape(version) if signature is None else signature
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
