"""The Python dependency locks (consolidation step 5.7, K11; findings C-12 and REM-24).
requirements.lock pins what Worklog needs to run, and requirements-dev.lock adds the tools the checks
run. Each pins every package with its file hashes, so Setup and CI install them with
--require-hashes. Both come from pyproject.toml (docs/DEVELOPMENT.md, *Dependencies*), so they must
pin what it names, and the dev lock must hold the runtime one as it is."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+?)\s*\\?$")
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")
# What the runtime leaves out (REM-24): the checks' own tools, and httpx, which only the tests' client uses.
CHECK_TOOLS = {"pytest", "ruff", "mypy", "httpx"}


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock(name: str) -> dict[str, tuple[str, frozenset[str]]]:
    """Each package a lock pins, by its normalized name: its version and the hashes it accepts."""
    pins: dict[str, tuple[str, frozenset[str]]] = {}
    current = None
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        if found := REQUIREMENT.match(line):
            current = normalized(found.group(1))
            assert current not in pins, f"{name} pins {current} twice"
            pins[current] = (found.group(2), frozenset())
        elif line.strip() and not line.lstrip().startswith("#"):
            assert current and line.startswith(" "), f"{name}: an unexpected line: {line!r}"
            version, hashes = pins[current]
            pins[current] = (version, hashes | set(HASH.findall(line)))
    return pins


def pinned(requirements: list[str]) -> dict[str, str]:
    """pyproject.toml's requirements, each an exact pin."""
    result = {}
    for requirement in requirements:
        name, separator, version = requirement.partition("==")
        assert separator and version, f"pyproject.toml pins {requirement!r} exactly"
        result[normalized(name)] = version
    return result


def test_every_package_is_pinned_with_its_hashes():
    for name in ("requirements.lock", "requirements-dev.lock"):
        pins = lock(name)
        assert pins, name
        assert {package for package, (_, hashes) in pins.items() if not hashes} == set(), name


def test_the_runtime_lock_leaves_the_checks_tools_out_and_the_dev_lock_holds_it_as_it_is():
    runtime, dev = lock("requirements.lock"), lock("requirements-dev.lock")
    assert not CHECK_TOOLS & set(runtime)
    assert CHECK_TOOLS <= set(dev)
    assert {package: dev.get(package) for package in runtime} == runtime


def test_the_locks_pin_what_pyproject_names():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime, dev = lock("requirements.lock"), lock("requirements-dev.lock")
    # The build backend too: Setup installs the project with --no-build-isolation.
    needed = pinned(project["project"]["dependencies"]) | pinned(project["build-system"]["requires"])
    tools = pinned(project["project"]["optional-dependencies"]["dev"])
    assert {package: runtime[package][0] for package in needed if package in runtime} == needed
    assert {package: dev[package][0] for package in tools if package in dev} == tools
