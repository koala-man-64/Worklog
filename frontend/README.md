# Worklog interface

React, TypeScript and Vite with TanStack Query. All dependencies are pinned in package.json and package-lock.json; installation uses the existing configured npm registry. System Node/npm are used for application development; document-authoring bundled runtimes are not needed.

```powershell
npm ci
npm run generate:api
npm run typecheck
npm run lint
npm test
npm run build
```

The backend serves `dist/` at its single origin. For Vite development, start the backend with `WORKLOG_DEV=1` and run `npm run dev`; `/api` proxies to 127.0.0.1:8877. Browser writes require the local bootstrap cookie and CSRF header.

```powershell
npx playwright install chromium
npm run build
npm run test:e2e
```

E2E starts a disposable backend at 127.0.0.1:8877 with data in `.local/frontend-playwright`, using the repository virtual environment. An occupied port fails the run instead of reusing another instance. Tests create work and roadmaps. To test an already-running **disposable** backend, explicitly set `WORKLOG_BASE_URL`. Never point the tests at a personal worklog.

`npm run generate:api` regenerates request models from `docs/openapi.json`; refresh that schema from the root with `.venv/Scripts/python.exe tools/export_openapi.py`. `commands.ts` uses generated payload types for the actual create and autosave requests. TypeScript is pinned to the latest compatible 5.x version because openapi-typescript requires that peer range.

Drafts remain in browser localStorage under `worklog:<dataId>:draft:*` with their original base revisions. Saves serialize per record. A 409 retains the draft and offers a comparison with the saved value and explicit resolution; no global optimistic rollback is used. Roadmap structure drafts are retained under `worklog:<dataId>:roadmap-structure:*`. Pending mutation idempotency keys survive network failures in sessionStorage; only SHA-256 request fingerprints and random keys are stored there. Proposal approval requires an explicit review decision. Tokens are held only in component memory and disappear on dismissal or navigation. Work lists page at 100 rows; the Today picker is bounded and searches existing work on demand.

Drafts, acknowledged revision chains, query caches and pending request keys are isolated by the bootstrap data identity. Switching data folders or restoring a backup keeps old browser drafts isolated. Storage denial or quota exhaustion keeps edits in memory, shows a keep-this-tab-open warning, and permits a server save. Day entries can be reordered by native drag handles or arrow buttons; archived actuals remain readable.
