"""The guides stay true to the code: agent commands, proposal operations, error codes and interface text."""

import html
import re
import shlex
from argparse import ArgumentParser, _SubParsersAction
from collections.abc import Iterator
from pathlib import Path

import pytest
from worklog import cli, policy
from worklog.proposals import FIELDS

ROOT = Path(__file__).resolve().parents[2]
AGENT_GUIDE = ROOT / "docs" / "AGENT-GUIDE.md"
GUIDE = ROOT / "docs" / "GUIDE.md"
FRONTEND = ROOT / "frontend" / "src"


def fenced(text: str, language: str) -> list[str]:
    """The lines of every ```language block."""
    lines, fence = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = None if fence is not None else stripped[3:].strip()
        elif fence == language:
            lines.append(stripped)
    return lines


def prose(text: str) -> str:
    """Text outside code blocks and inline code."""
    kept, fence = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
        elif not fence:
            kept.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(kept)


def subcommands(parser: ArgumentParser) -> dict[str, ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, _SubParsersAction):
            return dict(action.choices)
    return {}


def leaves(parser: ArgumentParser, path: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    children = subcommands(parser)
    if not children:
        yield path
    for name, child in children.items():
        yield from leaves(child, (*path, name))


def command_path(argv: list[str]) -> tuple[str, ...]:
    """The subcommands an argument list selects, skipping options and their values."""
    parser, path, index = cli.parser(), [], 0
    while index < len(argv) and subcommands(parser):
        token = argv[index]
        if token.startswith("-"):
            index += 1 + (parser._option_string_actions[token].nargs != 0)
            continue
        parser = subcommands(parser)[token]
        path.append(token)
        index += 1
    return tuple(path)


def test_agent_guide_is_ascii():
    # `worklog agent guide` prints it raw, so it must survive any console code page.
    AGENT_GUIDE.read_text(encoding="ascii")


def test_every_documented_command_parses_and_every_agent_command_is_documented():
    covered = set()
    for line in fenced(AGENT_GUIDE.read_text(encoding="ascii"), "powershell"):
        if not line.startswith("worklog "):
            continue
        argv = shlex.split(line)[1:]
        try:
            cli.parser().parse_args(argv)
        except SystemExit:
            pytest.fail(f"The agent guide documents a command the CLI refuses: {line}")
        covered.add(command_path(argv))
    agent = subcommands(cli.parser())["agent"]
    missing = sorted(" ".join(("agent", *leaf)) for leaf in leaves(agent) if ("agent", *leaf) not in covered)
    assert not missing, f"Add an example of each to docs/AGENT-GUIDE.md: {missing}"


def test_operations_table_matches_the_proposal_schema():
    def names(cell: str) -> set[str]:
        return set(re.findall(r"`([A-Za-z]+)`", cell))

    table = {}
    for line in AGENT_GUIDE.read_text(encoding="ascii").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and re.fullmatch(r"`[a-z]+\.[a-z]+`", cells[0]):
            table[cells[0].strip("`")] = (names(cells[1]), names(cells[2]))
    assert table == {action: (required, allowed - required) for action, (required, allowed) in FIELDS.items()}


def test_every_error_code_is_explained():
    codes = set()
    for path in (ROOT / "backend" / "worklog").glob("*.py"):
        # The status may be an expression, as in AppError(413 if content else 422, "file_size", ...).
        codes.update(re.findall(r'AppError\(\s*[^,"()]+,\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert len(codes) > 50, "The pattern no longer finds the codes Worklog raises"
    errors = AGENT_GUIDE.read_text(encoding="ascii").split("## 8. Errors", 1)[1].split("\n## ", 1)[0]
    missing = sorted(code for code in codes if f"`{code}`" not in errors)
    assert not missing, f"Explain these codes in section 8 of docs/AGENT-GUIDE.md: {missing}"


def test_the_readable_routes_name_every_read_the_table_keeps_for_the_person():
    """Consolidation step 3.3 (K2): agents read what policy.ROUTES lists, and the guide names the rest."""
    bullet = next(line for line in AGENT_GUIDE.read_text(encoding="ascii").splitlines()
                  if line.startswith("- **Readable routes.**"))
    named = {path.replace("/ID", "/{identity}") for path in re.findall(r"`(/[^`]*)`", bullet)} - {"/api/v1"}
    persons = {template for template, entry in policy.ROUTES.items() if entry.get("GET") == policy.HUMAN}
    assert named == persons, "Name in section 10 of docs/AGENT-GUIDE.md exactly the reads policy.ROUTES keeps for the person"


def test_agent_guide_command_needs_no_credential_or_server(monkeypatch, capsys):
    monkeypatch.delenv("WORKLOG_AGENT_TOKEN", raising=False)

    def unreachable(*args, **kwargs):
        raise AssertionError("`worklog agent guide` must not contact Worklog")

    monkeypatch.setattr(cli, "_open", unreachable)
    assert cli.main(["agent", "guide"]) == 0
    assert capsys.readouterr().out == AGENT_GUIDE.read_text(encoding="ascii")
    # --as names an identity the guide does not need, even one never registered.
    assert cli.main(["agent", "--as", "never-registered", "guide"]) == 0


def test_agent_guide_command_reports_a_missing_guide(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "AGENT_GUIDE", tmp_path / "AGENT-GUIDE.md")
    assert cli.main(["agent", "guide"]) == 1
    assert capsys.readouterr().err.startswith("Worklog: ")


@pytest.mark.parametrize("guide", [GUIDE, AGENT_GUIDE], ids=lambda path: path.name)
def test_quoted_interface_text_exists_in_the_frontend(guide):
    """Text in straight double quotes names something on screen, so it must still be there."""
    def flat(text: str) -> str:
        return " ".join(html.unescape(text).split())

    source = flat("\n".join(path.read_text(encoding="utf-8") for path in sorted(FRONTEND.glob("*.ts*"))
                            if not path.name.endswith((".test.ts", ".test.tsx")) and path.name != "generatedApi.ts"))
    quoted = {flat(phrase) for phrase in re.findall(r'"([^"\n]+)"', prose(guide.read_text(encoding="utf-8")))}
    missing = sorted(phrase for phrase in quoted if phrase not in source)
    assert not missing, f"{guide.name} quotes interface text the frontend no longer has: {missing}"
