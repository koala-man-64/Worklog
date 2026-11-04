"""Only search.py writes the search index.

The index's entity_id and owner_id columns are FTS5 UNINDEXED, so keying its rows by
something cheaper has to happen in one place. Historic migrations are exempt: they
backfill old schema shapes and must not depend on how today's module writes.
"""

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "worklog"
WRITES = re.compile(r"\b(INSERT\s+(OR\s+\w+\s+)?INTO|UPDATE(\s+OR\s+\w+)?|DELETE\s+FROM)\s+search_index\b", re.IGNORECASE)
EXEMPT = {"search.py", "migrations.py"}


def test_only_the_search_module_writes_the_index():
    writers = sorted(path.name for path in PACKAGE.glob("*.py")
                     if path.name not in EXEMPT and WRITES.search(path.read_text(encoding="utf-8")))
    assert writers == [], f"write the search index through worklog.search, not directly: {writers}"


def test_the_check_sees_a_direct_write():
    assert WRITES.search('c.execute("UPDATE search_index SET archived=? WHERE owner_id=?")')
    assert WRITES.search("DELETE FROM search_index WHERE entity_id=?")
    assert not WRITES.search("SELECT entity_id FROM search_index WHERE search_index MATCH ?")
