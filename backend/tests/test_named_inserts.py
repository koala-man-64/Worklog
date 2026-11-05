"""Every INSERT names its columns (consolidation step 2.2, finding B-07).

A positional `INSERT INTO t VALUES(...)` depends on the table's physical column order, so a
migration that adds a column breaks it, or worse, shifts values into the wrong columns. This test
reads every string in the backend, implicit and `+` concatenation joined and f-strings with their
fields blanked, and fails on any INSERT without a column list.
"""

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "worklog"
POSITIONAL = re.compile(r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)\s+(?:VALUES|SELECT)\b", re.IGNORECASE)


def _text(node: ast.AST) -> str | None:
    """The SQL a string expression spells, with any f-string field as {}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left), _text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def positional_inserts() -> list[str]:
    found = []
    for path in sorted(BACKEND.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            text = _text(node)
            if text:
                found.extend(f"{path.name}:{node.lineno}: {match.group(0)}" for match in POSITIONAL.finditer(text))
    return sorted(set(found))


def test_every_insert_names_its_columns():
    assert positional_inserts() == []


def test_the_check_finds_a_positional_insert_however_the_string_is_built():
    sample = ast.parse('a = "INSERT OR IGNORE INTO t " "VALUES(?)"\nb = f"INSERT INTO {x} VALUES(?)"\n'
                       'c = "INSERT INTO u " + "SELECT 1"\nd = "INSERT INTO v(a) VALUES(?)"')
    texts = [_text(node) for node in ast.walk(sample)]
    hits = [m.group(1) for t in texts if t for m in POSITIONAL.finditer(t)]
    assert sorted(set(hits)) == ["t", "u"]
