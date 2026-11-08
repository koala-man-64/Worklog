"""Worklog's modules in one order, each importing only modules before it (consolidation step 2.4).

Domain modules used to import each other inside functions to get round import cycles, so the
code no longer said which depended on which. ORDER below is that dependency order. A module may
import, at the top or inside a function, only modules listed before it. EXCEPTIONS are the
imports that still go up, each with its reason. The test fails when one of them stops going up,
so the list can only shrink.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "worklog"

ORDER = [
    # Nothing of Worklog's below them, the current schema and the route policy among them, then the
    # record kinds ties and discussion tags name.
    "__init__", "common", "models", "avatars", "transcripts", "active_sessions", "maintenance", "schema", "policy",
    "record_kinds",
    # Search, scratchpads, repository links, request shapes, the operation registry, the names a
    # proposal gives its new records, and what an applied change counts toward.
    "search", "scratchpad", "repository_links", "coordination_models", "discussion_models", "operations",
    "proposal_names", "proposal_targets",
    # Roadmaps, then the database, which opens, upgrades and validates the file.
    "plans", "db",
    # Records that stand on their own, and the agent identities that discussions and chats share.
    "resources", "tickets", "reminders", "discussions", "chat_messages",
    # Agent runs and participations with the repository catalog, then agent registration.
    "coordination", "registration", "registry",
    # Schema history, proposals, then backup and restore, offline compaction and the read-only doctor.
    "migrations", "proposals", "recovery", "compaction", "doctor",
    # Work: the shapes the domains share, supporting records, work items, people, agent ties, releases,
    # and the router.
    "records", "support", "items", "people", "agent_links", "releases", "services",
    # HTTP: the routes of each domain, the request guard in front of them all, and the app.
    "agent_link_api", "chat_message_api", "coordination_api", "discussion_api", "registry_api", "release_api",
    "reminder_api", "resource_api", "ticket_api", "guard", "app",
    # The command line.
    "cli",
]

EXCEPTIONS = {
    ("db", "migrations"): "Database.initialize upgrades an older file with migrations.upgrade.",
    ("db", "recovery"): "Database.initialize validates an existing file, and backs it up before upgrading it.",
    ("coordination", "support"): "A registered identity's chat is tracked on the work its run reports through the "
                                 "supporting-record command, so it is checked, indexed and recorded like any other.",
}


def imports(path: Path) -> list[tuple[str, int]]:
    """Every Worklog module this one imports, anywhere in it, with the line."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            parts = (node.module or "").split(".")
            if node.level == 1:
                names = parts[:1] if node.module else [alias.name for alias in node.names]
            elif node.level == 0 and parts[0] == "worklog":
                names = parts[1:2] or [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                found.append((name if (PACKAGE / f"{name}.py").exists() else "__init__", node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "worklog":
                    found.append((parts[1] if len(parts) > 1 else "__init__", node.lineno))
    return found


def upward() -> dict[tuple[str, str], list[int]]:
    rank = {name: position for position, name in enumerate(ORDER)}
    result: dict[tuple[str, str], list[int]] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        for target, line in imports(path):
            if rank[target] >= rank[path.stem]:
                result.setdefault((path.stem, target), []).append(line)
    return result


def test_every_module_has_its_place_in_the_order():
    modules = {path.stem for path in PACKAGE.glob("*.py")}
    assert len(ORDER) == len(set(ORDER))
    assert set(ORDER) == modules, "Place each new module in ORDER after the modules it imports"


def test_no_module_imports_one_after_it_except_the_listed_ones():
    violations = [f"{module}.py:{line} imports {target}"
                  for (module, target), lines in upward().items() if (module, target) not in EXCEPTIONS
                  for line in lines]
    assert violations == [], "Import only from modules earlier in ORDER, or move the code down"


def test_each_exception_still_imports_up():
    stale = sorted(set(EXCEPTIONS) - set(upward()))
    assert stale == [], "These imports no longer go up: remove them from EXCEPTIONS"
