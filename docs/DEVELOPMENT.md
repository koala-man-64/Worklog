# Developing Worklog

The checks to run on a change, and what to update in the guides when the app changes. The
[README](../README.md) covers installing Worklog, which a checkout needs before these run.

## Checks

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

## Documentation

`docs/GUIDE.md` and `docs/AGENT-GUIDE.md`, which `worklog agent guide` prints, describe Worklog for people and for agents, and the in-app Guide page shows both. A change to a page, a label, a command or flag, a proposal operation, an error code or a limit updates the matching guide in the same pull request. `backend/tests/test_guides.py` and `frontend/src/Guide.test.tsx` fail when an agent command, proposal operation, error code or sidebar page is missing from its guide, when a documented command no longer parses, or when a guide quotes interface text the frontend no longer has. In both guides, straight double quotes are reserved for exact interface text. The agent guide is ASCII, and each guide links only to its own headings, because file links would not open in the app.
