# Developing Worklog

The checks to run on a change, how the Python dependencies are locked, and what to update in the guides when the app changes. The
[README](../README.md) covers installing Worklog, which a checkout needs before these run.

## Checks

Run `./scripts/Setup.ps1 -Dev` in the checkout first: the checks need pytest, ruff and mypy, which Setup leaves out without it.

```powershell
./.venv/Scripts/python.exe -m pytest
./.venv/Scripts/python.exe -m ruff check backend tools
./.venv/Scripts/python.exe -m mypy backend/worklog
# After backend API changes, regenerate the schema and client types:
./.venv/Scripts/python.exe tools/export_openapi.py
cd frontend
npm ci
npm run generate:api
npm run lint
npm run typecheck
npm test
npm run build
# Uses isolated ../.local/frontend-playwright data on WORKLOG_PORT (default 8877), which must be free.
# Beside a running instance on 8877, pick another port first: $env:WORKLOG_PORT = '8878'
npm run test:e2e
```

Use an isolated `WORKLOG_DATA_DIR` for manual or browser tests; never point tests at your personal database. Lifecycle smoke checks should cover start, repeated start, health/check, stop, refusal of an unrelated port listener, and a restart against the same data. Restore tests cover invalid candidates, an active server lock, credential revocation, and failed replacement preservation. Inspect readable export records and verify the manifest hashes when testing archive workflows.

## Dependencies

`requirements.lock` pins every package Worklog needs to run, and `requirements-dev.lock` adds the tools the checks run. Both hold each package's file hashes, and Setup and CI install them with `--require-hashes`, so pip installs exactly those files. Both come from `pyproject.toml` with pip-tools. To change a dependency, change its pin in `pyproject.toml`, then compile both locks. Run pip-tools from a virtual environment of its own, such as `pipx install pip-tools`, on Windows with CI's Python (3.14), since pip-compile resolves for the system it runs on: compiled elsewhere, the dev lock would lose `colorama`, which pytest needs on Windows. Run it with no user pip configuration, so the locks come from PyPI alone:

```powershell
$env:PIP_CONFIG_FILE = 'nul'
pip-compile --all-build-deps --allow-unsafe --generate-hashes --index-url=https://pypi.org/simple --no-emit-index-url --output-file=requirements.lock --strip-extras pyproject.toml
pip-compile --all-build-deps --allow-unsafe --extra=dev --generate-hashes --index-url=https://pypi.org/simple --no-emit-index-url --output-file=requirements-dev.lock --strip-extras pyproject.toml
```

pip-compile keeps a lock's other pins as they are; `--upgrade-package NAME` moves one. `test_dependency_locks.py` checks that both locks pin what `pyproject.toml` names, with hashes, and that the dev lock holds the runtime one as it is: when they part, give the package the runtime lock's pin in the dev lock with `--upgrade-package NAME==VERSION`. CI's advisory Dependency audit job runs `pip-audit` on the runtime lock, as it runs `npm audit` on the frontend's.

## Documentation

`docs/GUIDE.md` and `docs/AGENT-GUIDE.md`, which `worklog agent guide` prints, describe Worklog for people and for agents, and the in-app Guide page shows both. A change to a page, a label, a command or flag, a proposal operation, an error code or a limit updates the matching guide in the same pull request. `backend/tests/test_guides.py` and `frontend/src/Guide.test.tsx` fail when an agent command, proposal operation, error code or sidebar page is missing from its guide, when a documented command no longer parses, or when a guide quotes interface text the frontend no longer has. In both guides, straight double quotes are reserved for exact interface text. The agent guide is ASCII, and each guide links only to its own headings, because file links would not open in the app.
