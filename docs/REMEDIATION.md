# Worklog remediation plan

Tracking document for the findings of the 2026-10-28 audit of commit `d19a39e`. Work runs in sequential waves; inside a wave, streams run in parallel and own separate code. At audit time 87 backend and 16 frontend tests passed; the Playwright suite was not run because the personal instance held port 8877.

Waves 0–4 change no database schema and add no runtime dependency. Wave 5 (schema v2) is optional and gated on decision D6.

## How to use this file

- Each work item has a `Status` line. An item's PR updates only its own item section: status, owner, branch and PR link. Separate sections let parallel PRs merge without conflicts.
- The wave coordinator updates the dashboard, stream tables and wave exit checklist when a wave closes.
- Record decisions in the decision table with the date and who decided.
- Status values: `Not started`, `In progress`, `In review`, `Merged`, `Blocked: <reason>`, `Dropped: <reason>`.
- Lanes follow the owner's agent-routing convention: Lite (mechanical), Standard (routine), Critical (high risk; independent evidence at every stage).
- Sizes: S is up to half a day, M about a day, L about two days.

## Dashboard

| Wave | Theme | Entry gate | Streams | Items | Merged | Exit met | Effort | Elapsed |
|---|---|---|---|---|---|---|---|---|
| 0 | Foundation | D1 | 3 | 3 | 3 | Yes | ~2 d | ~1.5 d |
| 1 | High findings and isolated fixes | Wave 0 exit | 5 | 10 | 0 | No | ~5.5 d | ~2 d |
| 2 | Concurrency and session lifecycle | Wave 1 exit, D2 | 3 | 4 | 0 | No | ~4 d | ~3 d |
| 3 | Growth, scale and agent limits | Wave 2 exit, D3, D4 | 3 | 4 | 0 | No | ~5 d | ~2.5 d |
| 4 | Authorization and hardening | Wave 3 exit, D5, D7 | 3 | 4 | 0 | No | ~4.5 d | ~3.5 d |
| 5 | Schema v2 (optional) | Wave 4 exit, D6 | 1 | 2 | 0 | No | 3–4 d | ~4 d |

Waves 0–4 total about 21 engineer-days of effort and 12–13 working days elapsed with parallel streams, before review latency. Elapsed is the longest stream plus half a day of exit verification.

## Decisions

| ID | Decision | Recommendation | Blocks | Status | Decided (date, by) |
|---|---|---|---|---|---|
| D1 | How `main` is established | Fast-forward `main` to `d19a39e`; the audit stands in for reviewing a 21k-line root commit | Wave 0 | Decided: `main` at `d19a39e` | 2026-10-28, Rudy (Claude created it on his instruction) |
| D2 | Stop order and progress changes from bumping record revisions (reverses an explicit original design choice) | Yes; it is the root cause of the false conflicts | REM-13 | Open | |
| D3 | Breaking `/proposals` change: paginate, drop `operations` from list and `/context` responses | Yes; only the bundled UI and CLI consume it | REM-17 | Open | |
| D4 | Agent quotas | 50 drafts, 100 submitted awaiting review, 20 evidence files and 100 MB per proposal | REM-17 | Open | |
| D5 | Agent read scope | Allowlist: context, search, proposals, work items, days, roadmaps, steps, notes, tasks, references, history; deny attachment bytes and import originals | REM-21 | Open | |
| D6 | Schema v2 with migrations | Defer until Wave 4 exits; do it only if reflection conflicts or receipt growth still matter | Wave 5 | Open | |
| D7 | Hash-locked dependency tooling | `uv` or `pip-tools`, dev-only | REM-24 | Open | |

## Conventions

- One work item, one PR, one worktree. Never implement in the checkout that runs the personal instance; upgrade that instance only at wave exit.
- Branch names carry the item ID; Claude sessions use `claude/WORKLOG-REM-NN-<topic>`. Commit subjects start with `WORKLOG-REM-NN:`.
- Test first: the item's first commit adds a regression test that fails on current `main`.
- Sync with `git fetch --all --prune`. Once a branch is pushed, merge `main` into it instead of rebasing; never force-push.
- Streams in one wave own separate files or symbols. When two streams touch the same file they change different symbols, and the second PR merges `main` and re-runs CI before merging.
- A wave's PRs merge only after the previous wave's exit checklist is complete. Development may start earlier.
- Independent review where an item says so. An agent review never replaces Rudy's approval on a protected gate.
- Verify against an isolated data directory, and after REM-03 on a port other than 8877. Never point tests at `%LOCALAPPDATA%\Worklog`.
- Upgrade the personal instance at wave exit per the README: stop, manual backup, update, `Setup.ps1`, start, `check`. Keep the previous checkout and backup until `check` and one day of normal use pass.
- Rollback: Waves 0–4 change no schema, so rollback is the previous checkout. Wave 5 rollback requires the automatic pre-migration backup.

## Waves and streams

### Wave 0 — Foundation

Entry gate: D1 decided.

| Stream | Items, in order | Owns | Owner |
|---|---|---|---|
| 0A Repo governance | REM-01 | `main`, branch protection, `docs/IMPLEMENTATION.md` | Rudy, Claude |
| 0B CI | REM-02 | `.github/workflows/ci.yml` | Claude |
| 0C Port configuration | REM-03 | `cli.py` host/port constants, lifecycle commands and `AgentClient` default; `scripts/Worklog.ps1`; `frontend/playwright.config.ts`; README run section | Claude |

Coordination: REM-01 creates `main` before 0B and 0C open PRs, and turns on required checks after REM-02 merges. Streams 1A and 1B may start development during this wave.

Exit checklist:

- [x] `main` exists on origin, is the default branch, and requires a PR with green CI (protected 2026-10-28)
- [x] CI is green on `main` for backend, frontend, OpenAPI drift and e2e ([run 40785191133](https://github.com/koala-man-64/Worklog/actions/runs/40785191133))
- [x] The e2e suite runs locally on a port other than 8877 while the personal instance is running (REM-03, #4)

### Wave 1 — High findings and isolated fixes

Entry gate: Wave 0 exit checklist complete.

| Stream | Items, in order | Owns | Owner |
|---|---|---|---|
| 1A Item editing | REM-04 | `Work.tsx` `ItemDetail` ADO field; `hooks.ts` record-key normalization; `components.tsx` `DraftField`; item e2e spec | |
| 1B Roadmap editor | REM-05 | `Roadmaps.tsx` `StructureEditor` | |
| 1C Backend robustness and privacy | REM-06, then REM-07 | `plans.py` `_validate_steps`; `app.py` `protect` CSRF check and error handling; `proposals.py` visibility; new `tests/test_robustness.py` | |
| 1D Link safety | REM-08 and REM-09, in parallel | `components.tsx` `Markdown`; `plans.py` `preview` URL handling | |
| 1E Small fixes | REM-10, REM-11, REM-12, REM-27 | `cli.py` export command; `Review.tsx` `OperationDiff`; `pyproject.toml`; README; `scripts/Setup.ps1` | |

Coordination: 1A and 1D both edit `components.tsx` (`DraftField` vs `Markdown`); 1C and 1D both edit `plans.py` (`_validate_steps` vs `preview`).

Exit checklist:

- [ ] All ten items merged, each with its regression test
- [ ] Browser check on an isolated instance: the ADO two-tab scenario keeps the newer value, and a stale structure draft offers Discard and Rebase
- [ ] Personal instance upgraded and `check` passes

### Wave 2 — Concurrency and session lifecycle

Entry gate: Wave 1 exit checklist complete; D2 decided.

| Stream | Items, in order | Owns | Owner |
|---|---|---|---|
| 2A Server revision semantics | REM-13 | `services.py` `reorder`, `move_backlog`, `_entry_apply`; `plans.py` `apply` (progress, revise) | |
| 2B Client conflict resolution | REM-14, then REM-16 | `hooks.ts`; `components.tsx` `DraftField` conflict panel; `Today.tsx` entry controls; `Work.tsx` status and archive; `api.ts` idempotency-key policy; new `maintenance.py`; `cli.py` watcher; `app.py` startup hook | |
| 2C Session and day lifecycle | REM-15 | `app.py` `protect` session expiry; `api.ts` `request` error handling; `App.tsx` session query; `Today.tsx` default date | |

Coordination: 2B and 2C both edit `api.ts` (`pendingRequest` vs `request` error handling), `Today.tsx` (entry controls vs default date) and `app.py` (startup hook vs `protect`).

Exit checklist:

- [ ] Scripted browser scenarios show no conflict panel: tick Worked then type an outcome; reorder the backlog while editing a description; type a reflection after an outcome autosave
- [ ] An open tab keeps saving after a server stop and start without a reload
- [ ] Today rolls over at local midnight (fake-clock test)
- [ ] Autosaves create no receipts, and the receipts table stays bounded
- [ ] Personal instance upgraded and `check` passes

### Wave 3 — Growth, scale and agent limits

Entry gate: Wave 2 exit checklist complete; D3 and D4 decided.

| Stream | Items, in order | Owns | Owner |
|---|---|---|---|
| 3A Proposal API and quotas | REM-17 | `proposals.py` `list`, `create`, `check_upload`; `app.py` `/proposals` and `/context` routes; `Review.tsx` list; `types.ts` `Proposal`; `cli.py` `agent proposals`; `docs/openapi.json`; `generatedApi.ts`; `docs/API.md`; review e2e spec | |
| 3B Export and backups | REM-18 | `recovery.py` `export_database`, `list_backups`; `app.py` `/export` and `/settings` routes; `cli.py` export command | |
| 3C Imports and maintenance | REM-19, then REM-20 | `plans.py` `preview` limits; `maintenance.py` preview purge and compaction; `cli.py` `compact` command and watcher; README maintenance section | |

Coordination: 3A and 3B both edit `app.py` (separate routes); 3A, 3B and 3C all edit `cli.py` (separate commands). 3C builds on the `maintenance.py` module created by REM-16.

Exit checklist:

- [ ] Export of a fixture with at least 200 MB of blobs stays under the memory bound, and the manifest verifies
- [ ] `/proposals` is paginated, `/context` is bounded, and quotas are enforced
- [ ] Compaction verified on an isolated copy of a recent backup (run or approved by Rudy)
- [ ] Personal instance upgraded, `check` passes, and Rudy decides whether to run `worklog compact`

### Wave 4 — Authorization and hardening

Entry gate: Wave 3 exit checklist complete; D5 and D7 decided.

| Stream | Items, in order | Owns | Owner |
|---|---|---|---|
| 4A Authorization and middleware | REM-21, then REM-22 | `app.py` middleware and route scopes; `services.py` `owner` and `apply` guard; `plans.py` `apply` guard; new `tests/test_authorization.py` | |
| 4B Proposal validation | REM-23 | `models.py` operation contracts; `proposals.py` create, update and submit; `app.py` `dispatch` | |
| 4C Supply chain | REM-24 | `pyproject.toml`; lock files; `scripts/Setup.ps1`; CI workflow install step; README setup section | |

Coordination: 4A and 4B both edit `app.py` (middleware vs `dispatch`). 4C edits the workflow created by REM-02.

Exit checklist:

- [ ] Every API route declares a scope, and the enumeration test fails closed
- [ ] Independent security review signed off
- [ ] Clean install with `--require-hashes` succeeds on a fresh runner
- [ ] Personal instance upgraded and `check` passes

### Wave 5 — Schema v2 (optional)

Entry gate: Wave 4 exit checklist complete; D6 decided yes.

| Stream | Items, in order | Owns | Owner |
|---|---|---|---|
| 5A Schema v2 | REM-25, then REM-26 | `db.py` `initialize`; `recovery.py` restore and expected signatures; `maintenance.py` receipt TTL; `services.py` reflection; `app.py` day payload; `Today.tsx` reflection draft; OpenAPI and generated types | |

Exit checklist:

- [ ] A fully populated v1 fixture migrates with identical canonical manifests
- [ ] An interrupted migration leaves v1 intact, and a v1 backup restores into v2 code
- [ ] Rudy approves migrating the personal instance after a verified pre-migration backup

## Work items

### Wave 0

#### REM-01 · Establish `main` and protect it

Status: Merged · Owner: Rudy, Claude · Branch: `claude/WORKLOG-REM-01-implementation-doc` · PR: [#5](https://github.com/koala-man-64/Worklog/pull/5)

Fixes G1 · Stream 0A · Lite · S · Depends on D1 · Review: none (repository administration)

- Create `main` per D1 (recommended: `d19a39e`) and make it the default branch.
- Retarget open PRs, including the one that adds this file, to `main`.
- After REM-02 merges, require a PR and the CI checks on `main`.
- Correct [IMPLEMENTATION.md](IMPLEMENTATION.md), which says no remote exists, and mention CI.

Done when:

- [x] `main` exists on origin and is the default branch (created at `d19a39e`)
- [x] Direct pushes are blocked and the CI checks are required (PR required; Backend, Frontend and End-to-end required and up to date; admins included; no force pushes or deletion)
- [x] IMPLEMENTATION.md is accurate (#5)

#### REM-02 · CI on `windows-latest`

Status: Merged · Owner: Claude · Branch: `claude/WORKLOG-REM-02-ci` (deleted) · PR: [#2](https://github.com/koala-man-64/Worklog/pull/2)

Fixes G2 · Stream 0B · Standard · M · Depends on REM-01 (`main` created) · Review: standard

- Run on pull requests and pushes to `main`.
- Backend: locked install; pytest, ruff, mypy.
- OpenAPI drift: run `tools/export_openapi.py` and `npm run generate:api`, then `git diff --exit-code docs/openapi.json frontend/src/generatedApi.ts`.
- Frontend: `npm ci`, lint, typecheck, vitest, build.
- Playwright Chromium e2e against an isolated data directory, uploading traces on failure.
- Non-blocking dependency audit: `npm audit --omit=dev` now, and `pip-audit` once REM-24 adds dev tooling.
- Cache pip and npm downloads.

Done when:

- [x] Every job is green on `main` ([run 40785191133](https://github.com/koala-man-64/Worklog/actions/runs/40785191133))
- [x] A deliberately stale `openapi.json` fails the drift job (probe #3)
- [x] e2e traces are uploaded when the suite fails (probe #3)

#### REM-03 · Configurable port

Status: Merged · Owner: Claude · Branch: `claude/WORKLOG-REM-03-port` (deleted) · PR: [#4](https://github.com/koala-man-64/Worklog/pull/4)

Fixes G3 · Stream 0C · Standard · S–M · Review: standard

- [cli.py](../backend/worklog/cli.py): `--port` and `WORKLOG_PORT` replace the fixed `PORT` for `serve`, `start`, `stop`, `check` and the `AgentClient` default. `create_app` already derives the Host and Origin allowlist from `WORKLOG_PORT`.
- [Worklog.ps1](../scripts/Worklog.ps1) opens the configured port.
- [playwright.config.ts](../frontend/playwright.config.ts) derives `baseURL` and the web-server port from the same setting.
- The README documents running a second, isolated instance for testing.

Done when:

- [x] An instance on 8878 with isolated data starts, checks and stops while 8877 runs (local, see #4)
- [x] `npm run test:e2e` passes with `WORKLOG_PORT=8878` (local: 9 passed)
- [x] The Host and Origin allowlist follows the configured port (pytest)

### Wave 1

#### REM-04 · Save the ADO link through a draft field

Status: Merged · Owner: Claude · Branch: `claude/WORKLOG-REM-04-ado-draft` · PR: [#13](https://github.com/koala-man-64/Worklog/pull/13)

Fixes H1 · Stream 1A · Standard · S · Review: standard

- [Work.tsx](../frontend/src/Work.tsx) `ItemDetail`: replace the uncontrolled ADO input with a single-line `DraftField` saving through `commands.updateItem(id, { revision, adoUrl: value || null })`.
- [hooks.ts](../frontend/src/hooks.ts) `useDraft`: extend the record-key normalization to `item-(title|description|ado)-` so the link shares the item's acknowledged-revision chain.
- [components.tsx](../frontend/src/components.tsx) `DraftField`: for 4xx errors other than 409, show the server's message (for example an invalid URL) instead of "Save failed".

Done when:

- [x] A server-side change while the page is open updates the field (`Work.test.tsx`)
- [x] Leaving the field without editing sends no request (`Work.test.tsx`)
- [x] A stale draft shows the conflict panel and never writes silently (`Work.test.tsx`)
- [x] An invalid URL shows the server's validation message (`Work.test.tsx`)
- [x] e2e with two pages: B changes the link, A tabs through the field, and B's value persists (`worklog.spec.ts`)

#### REM-05 · Recoverable roadmap structure drafts

Status: Merged · Owner: Claude · Branch: `claude/WORKLOG-REM-05-structure-drafts` · PR: [#15](https://github.com/koala-man-64/Worklog/pull/15)

Fixes H2 and N1 · Stream 1B · Standard · M · Review: standard

- [Roadmaps.tsx](../frontend/src/Roadmaps.tsx) `StructureEditor` stores `baseStepIds` with every new draft.
- On mount, if the draft's base revision differs from the roadmap's, show a stale-draft banner with Discard and Rebase.
- Rebase adopts the current revision, adds back server steps missing from `baseStepIds` (created elsewhere since the base) so they aren't silently retired, and keeps steps the user removed retired. Older drafts without `baseStepIds` list the server-only steps and ask about each.
- Clone steps on initialization and in `move()` so editor state never mutates TanStack cache objects.

Done when:

- [x] A stale draft shows the banner (saving is disabled until it's resolved)
- [x] Discard removes the stored draft and loads the current structure
- [x] Rebase keeps steps added elsewhere and saves without a 409 (steps the user removed stay retired)
- [x] `move()` leaves the cached roadmap steps unchanged
- [x] e2e: changing step progress while the editor is open no longer strands the draft

#### REM-06 · No unhandled 500 errors

Status: Merged · Owner: Claude · Branch: `claude/WORKLOG-REM-06-no-500s` · PR: [#8](https://github.com/koala-man-64/Worklog/pull/8)

Fixes L1 · Stream 1C · Standard · S–M · Review: standard plus a light security review

- [plans.py](../backend/worklog/plans.py) `_validate_steps`: `parentId` must be a string or null before tree validation (422 otherwise).
- [app.py](../backend/worklog/app.py) `protect`: compare CSRF tokens as UTF-8 bytes; catch unexpected exceptions, log a correlation ID, and return the standard 500 error envelope so security headers are always set.
- New `backend/tests/test_robustness.py` sends lists, dicts, ints, nulls and non-ASCII strings into every field of every write route.

Done when:

- [x] `parentId: []` returns 422 (create, revise, import commit and proposal approval)
- [x] A non-ASCII `X-CSRF-Token` returns 403 with security headers
- [x] The sweep produces no 5xx, and every error uses the envelope (`test_robustness.py`)

#### REM-07 · Withdrawn drafts stay private

Status: Merged · Owner: Claude · Branch: `claude/WORKLOG-REM-07-withdrawn-privacy` · PR: [#11](https://github.com/koala-man-64/Worklog/pull/11)

Fixes L2 · Stream 1C, after REM-06 · Standard · S · Review: standard plus a light security review

- [proposals.py](../backend/worklog/proposals.py) `_visible` and `ProposalService.list`: an agent sees another agent's proposal only if a `proposal.submit` event exists for it. Events are indexed by entity, so the check is cheap.
- Evidence and history inherit the rule through `proposals.detail`.

Done when:

- [x] Agent B gets 404 for agent A's draft that was withdrawn without being submitted, for its attachments and for its history (also absent from B's list)
- [x] A proposal withdrawn after submission stays visible, as before

#### REM-08 · Safe link rendering

Status: Merged · Owner: Claude (stream 1D helper) · Branch: `claude/WORKLOG-REM-08-safe-links` · PR: [#10](https://github.com/koala-man-64/Worklog/pull/10)

Fixes L3 (frontend) · Stream 1D · Standard · S · Review: standard plus a light security review

- [components.tsx](../frontend/src/components.tsx) `Markdown`: resolve each `href` with `new URL(href, location.origin)`; allow `http`, `https`, `mailto` and `#` routes. Same-origin links open in the same tab; any other origin opens a new tab with `noopener noreferrer`.

Done when:

- [x] A vitest table covers `//evil.example`, `/\evil.example`, `JaVaScRiPt:alert(1)`, `#today`, `/`, `mailto:a@b.c` and `https://example.com` (components.test.tsx table; 2 rows fail on main, all pass after; vitest 23 passed, eslint and tsc clean)

#### REM-09 · Normalize links in HTML imports

Status: Merged · Owner: Claude (stream 1D helper) · Branch: `claude/WORKLOG-REM-09-import-links` · PR: [#6](https://github.com/koala-man-64/Worklog/pull/6)

Fixes L3 (backend) · Stream 1D · Standard · S · Review: standard

- [plans.py](../backend/worklog/plans.py) `preview`: rewrite protocol-relative URLs to `https:`, and percent-encode `(`, `)`, `<`, `>` and whitespace in kept `href` and `src` values before markdownify, so a crafted URL can't emit extra Markdown links.

Done when:

- [x] The href `https://x/a) [c](javascript:alert(1)` yields no `javascript:` link (test_import_href_cannot_smuggle_an_extra_markdown_link; no `](javascript:` in output)
- [x] `//host/path` becomes `https://host/path` (test_import_protocol_relative_and_whitespace_urls_are_normalized; pytest 93 passed, ruff and mypy clean)

#### REM-10 · Atomic CLI export

Status: Not started · Owner: — · Branch: — · PR: —

Fixes N2 · Stream 1E · Standard · S · Review: standard

- [cli.py](../backend/worklog/cli.py) `export`: write to a temporary file in the target folder, then publish with a link or rename that never overwrites. A failure leaves no file behind; an existing target is still refused.

Done when:

- [ ] An injected export failure leaves nothing at the target path
- [ ] An existing target is refused and the temporary file is cleaned up

#### REM-11 · Path-safe proposal review

Status: Merged · Owner: Claude (stream 1E helper) · Branch: `claude/WORKLOG-REM-11-review-paths` · PR: [#9](https://github.com/koala-man-64/Worklog/pull/9)

Fixes L6 (frontend) · Stream 1E · Standard · S · Review: standard

- [Review.tsx](../frontend/src/Review.tsx) `OperationDiff`: check that target IDs are UUIDs and dates are `YYYY-MM-DD` before fetching, encode each path segment, and show "Malformed target" otherwise.

Done when:

- [x] A payload with `date: "../export"` sends no request and shows the warning — `Review.test.tsx` asserts no request and the 'Malformed target' warning for `date: "../export"` and six malformed-ID cases; fails on main (7 of 14), passes after

#### REM-12 · Packaging and docs corrections

Status: Not started · Owner: — · Branch: — · PR: —

Fixes N3 and N4 · Stream 1E · Lite · S · Review: standard

- [pyproject.toml](../pyproject.toml): declare `beautifulsoup4==4.15.0`.
- [README.md](../README.md): replace the WAL wording with rollback-journal wording, and warn not to open exported `imports/*.html` files in a browser.

Done when:

- [ ] `pip check` is clean and the README matches `db.py`

#### REM-27 · Setup survives redirected output on Windows PowerShell 5.1

Status: Not started · Owner: — · Branch: — · PR: —

Fixes N7 · Stream 1E · Lite · S · Review: standard

- [Setup.ps1](../scripts/Setup.ps1) sets `$ErrorActionPreference = "Stop"`. Under Windows PowerShell 5.1 with output redirected, that turns harmless native stderr, such as an npm config warning, into a terminating error. Scope the preference to cmdlets and rely on the script's existing `$LASTEXITCODE` checks for native commands.

Done when:

- [ ] `Setup.ps1` completes under Windows PowerShell 5.1 with all output redirected while npm prints warnings
- [ ] A failing native command still stops the script with its message

### Wave 2

#### REM-13 · Decouple revision numbers on the server

Status: Not started · Owner: — · Branch: — · PR: —

Fixes M1 (server) · Stream 2A · Standard · M · Depends on D2 · Review: independent (concurrency)

- [services.py](../backend/worklog/services.py): backlog reorder and move stop bumping work-item revisions (`backlogRevision` already guards order). Day reorder stops bumping entry revisions (the day revision still guards order). Entry upserts bump the day revision only when they create an entry (reorder already rejects membership mismatches).
- [plans.py](../backend/worklog/plans.py): `progress.set` stops bumping the roadmap revision; a revise bumps a step's revision only when its title, description, parent, position or retired flag changes.

Done when:

- [ ] Reordering leaves item and entry revisions unchanged
- [ ] Editing an outcome leaves the day revision unchanged, and a reflection saved afterwards succeeds
- [ ] Changing progress leaves the roadmap revision unchanged
- [ ] A revise leaves unchanged steps' revisions alone
- [ ] Existing stale-order and membership tests still pass

#### REM-14 · Field-level conflict resolution in the client

Status: Not started · Owner: — · Branch: — · PR: —

Fixes M1 (client) · Stream 2B · Standard · M–L · Depends on REM-04 · Review: independent (concurrency)

- [hooks.ts](../frontend/src/hooks.ts) `useDraft` stores the base text with the base revision. On a 409, after the refetch:
  - current value equals the draft: mark it saved;
  - current value equals the base text: nobody changed this field, so rebase and retry once;
  - otherwise show the conflict panel.
  - Drafts without a base text keep today's behaviour.
- New `useRecordMutation(recordKey)`: non-draft writes (status, planned/worked, archive/recover) go through the same per-record queue, record acknowledged revision changes, and apply the same retry rule. Wire it into the [Today.tsx](../frontend/src/Today.tsx) entry controls and the [Work.tsx](../frontend/src/Work.tsx) status and archive actions.

Done when:

- [ ] Ticking a box and then typing on the same entry raises no conflict
- [ ] When another tab changed a different field, the draft rebases silently
- [ ] When another tab changed the same field, the conflict panel appears
- [ ] A lost response followed by another edit resolves without a conflict
- [ ] Creation at revision 0 and data-identity isolation are unaffected

#### REM-15 · Session and day lifecycle

Status: Not started · Owner: — · Branch: — · PR: —

Fixes M5 · Stream 2C · Standard · M · Review: standard

- [app.py](../backend/worklog/app.py) `protect`: extend a session's expiry on each authenticated request.
- [api.ts](../frontend/src/api.ts) `request`: on 401 `session` or 403 `csrf`, re-bootstrap once and retry. This is safe because the middleware rejected the request before any handler ran. If `dataId` changed, abort and keep the drafts.
- [App.tsx](../frontend/src/App.tsx) and [Today.tsx](../frontend/src/Today.tsx): compute today from the server timezone with `Intl`, on focus and at local midnight. An undated Today page moves to the new day and says so.

Done when:

- [ ] A 401 triggers exactly one bootstrap and one retry; a second 401 shows an error instead of looping
- [ ] Fake-clock tests cover Chicago midnight and both DST transition days
- [ ] Session expiry extends while the tab is active (pytest)
- [ ] e2e: after the server stops and starts, an open tab saves without a reload

#### REM-16 · Bounded idempotency receipts

Status: Not started · Owner: — · Branch: — · PR: —

Fixes M2 (part 1) · Stream 2B, after REM-14 · Standard · S–M · Review: standard plus a light data review (deletes rows)

- [api.ts](../frontend/src/api.ts): send `Idempotency-Key` only for commands that aren't revision-guarded (creates, approval, uploads, import commit, credential issue), matching [API.md](API.md).
- New `backend/worklog/maintenance.py` with `prune_receipts(conn, max_rows=10_000, max_bytes=50_000_000)`. It keeps the newest receipts by rowid, so no schema change is needed; the newest row is never deleted, so rowid order stays monotonic. It runs at startup and from the daily watcher in [cli.py](../backend/worklog/cli.py).

Done when:

- [ ] A sequence of autosaves creates no receipts
- [ ] A create retried inside the window replays the original response
- [ ] Pruning respects both limits and never removes the newest row

### Wave 3

#### REM-17 · Proposal pagination and quotas

Status: Not started · Owner: — · Branch: — · PR: —

Fixes M3 · Stream 3A · Standard · L · Depends on D3, D4 · Review: independent (public API and authorization)

- `GET /proposals`: `limit` and `offset` (default 50, maximum 200); status and visibility filtered in SQL instead of loading every row ([proposals.py](../backend/worklog/proposals.py) `ProposalService.list`); list items return `operationCount` instead of `operations`. Detail is unchanged.
- `/context` returns only summaries of submitted proposals, capped at 50 ([app.py](../backend/worklog/app.py)).
- Per-credential quotas (D4 values) in `create` and `check_upload`, returning 429 with the codes `proposal_quota` and `evidence_quota`.
- Update [Review.tsx](../frontend/src/Review.tsx) (list uses `operationCount` and pages), [types.ts](../frontend/src/types.ts) `Proposal`, and the CLI (`agent proposals --limit --offset`). Regenerate `openapi.json` and `generatedApi.ts`, and update [API.md](API.md).

Done when:

- [ ] Agents never see another agent's drafts, at any offset
- [ ] Quotas hold at their boundaries: the limit succeeds and one more returns 429
- [ ] List responses carry no operations
- [ ] The review-and-approve e2e flow passes

#### REM-18 · Streaming export and cheaper backup listing

Status: Not started · Owner: — · Branch: — · PR: —

Fixes M4 · Stream 3B · Standard · M · Depends on REM-10 · Review: independent (archive integrity)

- [recovery.py](../backend/worklog/recovery.py) `export_database` streams to a temporary ZIP. Rows are written as JSON array elements and BLOBs are read in 1 MB chunks with `Connection.blobopen`. Content is hashed while writing, following the `_offline_evidence` pattern, and the manifest goes last.
- The `/export` route returns a `FileResponse` that deletes the temporary file afterwards; the CLI reuses REM-10's publish step.
- `/settings` uses `list_backups(hashes=False)`, since the UI doesn't show hashes; the CLI `backups` command keeps them.

Done when:

- [ ] A fixture with at least 200 MB of blobs exports with a tracemalloc peak under 64 MB, and its manifest verifies
- [ ] `/settings` never reads backup contents
- [ ] The existing export tests pass unchanged

#### REM-19 · Import limits and preview retention

Status: Not started · Owner: — · Branch: — · PR: —

Fixes L4 · Stream 3C · Standard · S–M · Depends on REM-16 · Review: standard plus a light data review

- [plans.py](../backend/worklog/plans.py) `preview`: pre-scan the raw bytes and reject more than 5,000 headings or 100,000 elements, with a 422 stating the count. Truncate titles to 1,000 bytes with a warning.
- `maintenance.py` `purge_previews(conn, older_than_days=7)` removes uncommitted imports at startup and daily.

Done when:

- [ ] A hostile 5 MB file is rejected in under a second
- [ ] An oversized title is truncated with a warning
- [ ] The purge removes only uncommitted rows older than the cutoff

#### REM-20 · Offline `worklog compact`

Status: Not started · Owner: — · Branch: — · PR: —

Fixes M2 (part 2) · Stream 3C, after REM-19 · Standard · M · Depends on REM-16 · Review: independent (data integrity)

- New [cli.py](../backend/worklog/cli.py) command. While holding the server lock it:
  1. takes a pre-compaction backup;
  2. runs `prune_receipts` and `purge_previews`;
  3. runs `VACUUM`;
  4. validates integrity, foreign keys and schema;
  5. reports the bytes reclaimed.
- Document it in the README.

Done when:

- [ ] Canonical-table export manifests are identical before and after
- [ ] The command refuses to run while the server is running
- [ ] A failure leaves the original database intact

### Wave 4

#### REM-21 · Fail-closed authorization

Status: Not started · Owner: — · Branch: — · PR: —

Fixes L5 · Stream 4A · Critical · L · Depends on D5 · Review: independent security review (an Opus child gives the final verdict)

- The middleware keeps authentication only. Authorization moves to per-route scope dependencies: `human`, `agent:read`, `agent:propose`. Routes without a scope deny agents.
- Apply the D5 agent read allowlist.
- Service-layer second line: canonical `apply` accepts an agent actor only with an approval capability set by `dispatch`. [services.py](../backend/worklog/services.py) `WorkService.owner` raises for agents instead of `pass`.
- New `backend/tests/test_authorization.py`.

Done when:

- [ ] An enumeration test fails on any API route without a scope
- [ ] A parametrized test covers bypass attempts: trailing slash, duplicate query parameters, `/approve/`, lowercase `bearer`, Basic auth, `imports/preview`
- [ ] Calling the services directly with an agent actor raises
- [ ] Approvals still attribute operations to the proposing agent

#### REM-22 · Pure-ASGI request guard

Status: Not started · Owner: — · Branch: — · PR: —

Fixes N6 · Stream 4A, after REM-21 · Standard · M · Review: independent (security)

- Replace `BaseHTTPMiddleware` and the private `request._body` with an ASGI middleware that enforces body limits by wrapping `receive` and adds security headers to every response, including errors.

Done when:

- [ ] A chunked body over the limit gets 413 without the whole body being buffered
- [ ] Security headers are present on 2xx, 4xx and 5xx responses
- [ ] Upload and import routes keep their own limits

#### REM-23 · Validate proposal payloads at submit

Status: Not started · Owner: — · Branch: — · PR: —

Fixes L6 (server) · Stream 4B · Standard · S–M · Review: standard

- Move the operation contracts from `dispatch` in [app.py](../backend/worklog/app.py) to [models.py](../backend/worklog/models.py).
- `ProposalService` validates payload values (contracts plus UUID and date checks) at create, update and submit without applying them. Approval keeps its own checks.

Done when:

- [ ] A malformed `date` or `id` is rejected at submit with field errors
- [ ] Valid proposals and approval behaviour are unchanged

#### REM-24 · Hash-locked dependencies

Status: Not started · Owner: — · Branch: — · PR: —

Fixes N5 · Stream 4C · Standard · S–M · Depends on D7 · Review: standard

- Separate runtime and dev lock files with hashes, generated by the D7 tool.
- [Setup.ps1](../scripts/Setup.ps1) installs the runtime set with `--require-hashes` and adds dev tools only with `-Dev`.
- CI installs from the dev lock; update the README.

Done when:

- [ ] A clean install on a fresh CI runner succeeds with `--require-hashes`
- [ ] The runtime environment contains no pytest, mypy or ruff

### Wave 5 (optional)

#### REM-25 · Schema v2 and migration framework

Status: Not started · Owner: — · Branch: — · PR: —

Fixes M2 (residual) and enables REM-26 · Stream 5A · Critical · L · Depends on D6 · Review: independent (data integrity)

- [db.py](../backend/worklog/db.py) `initialize`: take a pre-migration backup (the backup kind already exists), migrate v1 to v2 in one transaction, then set `user_version=2`.
- [recovery.py](../backend/worklog/recovery.py): keep an expected schema signature per version. When restoring an older valid backup, migrate the staged copy before validating it.
- Add `receipts.created_at`, so age-based expiry replaces REM-16's rowid window.

Done when:

- [ ] A fully populated v1 fixture migrates with identical canonical manifests
- [ ] An interrupted migration leaves v1 intact and restartable
- [ ] A v1 backup restores into v2 code, and v1 code refuses a v2 database

#### REM-26 · Separate reflection revision

Status: Not started · Owner: — · Branch: — · PR: —

Fixes M1 (residual) · Stream 5A, after REM-25 · Standard · M · Review: independent (interface and concurrency)

- `days.reflection_revision` guards `PUT /days/{date}/reflection`, and `GET /days/{date}` returns `reflectionRevision`.
- The [Today.tsx](../frontend/src/Today.tsx) reflection draft uses it. Regenerate the OpenAPI schema and client types.

Done when:

- [ ] Reorders and new entries no longer conflict with a reflection draft
- [ ] Concurrent reflection edits still conflict

## Findings index

Line references are at `d19a39e` and will drift as fixes land.

| ID | Severity | Finding | Evidence | Fixed by |
|---|---|---|---|---|
| H1 | High | The ADO link input silently reverts values that changed elsewhere | `Work.tsx:290`; read | REM-04 |
| H2 | High | A stale roadmap structure draft can be neither saved nor discarded | `Roadmaps.tsx:381`, `Roadmaps.tsx:499`, `plans.py:213`; read | REM-05 |
| M1 | Medium | Revision numbers shared across independent changes cause false conflicts | `services.py:245`, `:249`, `:278`, `:280`; `plans.py:213`; probe | REM-13, REM-14, REM-26 |
| M2 | Medium | Idempotency receipts grow without bound | `app.py:223`, `api.ts:56`; probe: 2.0 MB after 20 autosaves | REM-16, REM-20, REM-25 |
| M3 | Medium | An agent can exhaust resources; the proposal list is unpaginated | `proposals.py:97`, `app.py:502`; probe: 58 MB responses | REM-17 |
| M4 | Medium | Export is built in memory, and Settings hashes every backup | `recovery.py:433`, `recovery.py:145`; read plus timing | REM-18 |
| M5 | Medium | Long-lived tabs break, and Today is frozen at page load | `app.py:251`, `api.ts:87`, `App.tsx:25`; read | REM-15 |
| L1 | Low | Unhandled 500 errors from a list-typed `parentId` and a non-ASCII CSRF header | `plans.py:178`, `app.py:189`; probe | REM-06 |
| L2 | Low | A draft withdrawn without submission becomes readable by every agent | `proposals.py:82`; probe | REM-07 |
| L3 | Low | `//host` links are treated as internal; import sanitizing has gaps | `components.tsx:35`, `plans.py:292`; read plus probe | REM-08, REM-09 |
| L4 | Low | Import preview is unbounded, and previews are never purged | `plans.py:276`; probe: 1 MB took 5.4 s | REM-19 |
| L5 | Low | Agent limits live only in middleware; `owner()` is a bare `pass`; agents can read every GET | `app.py:176`, `services.py:71`; read plus probe | REM-21 |
| L6 | Low | Proposal values go unvalidated until approval, and the review page builds paths from them | `Review.tsx:183`, `proposals.py:44`; read | REM-11, REM-23 |
| N1 | Minor | The structure editor mutates cached steps | `Roadmaps.tsx:520` | REM-05 |
| N2 | Minor | A failed CLI export leaves a 0-byte file | `cli.py:418` | REM-10 |
| N3 | Minor | The README claims WAL, but the database uses a rollback journal | `db.py:70` | REM-12 |
| N4 | Minor | `bs4` is imported but not declared | `plans.py:12` | REM-12 |
| N5 | Minor | The lock file has no hashes and installs dev tools into the runtime | `requirements.lock`, `scripts/Setup.ps1` | REM-24 |
| N6 | Minor | The middleware relies on Starlette's private `request._body` | `app.py:195` | REM-22 |
| N7 | Minor | `Setup.ps1` aborts under Windows PowerShell 5.1 when output is redirected and npm writes a warning to stderr | `scripts/Setup.ps1:3`; reproduced while building the REM-03 worktree | REM-27 |
| G1 | Process | origin has no `main`, and IMPLEMENTATION.md is out of date | `git ls-remote` | REM-01 |
| G2 | Process | There is no CI | Repository tree | REM-02 |
| G3 | Process | e2e can't run beside the personal instance because the port is fixed | `cli.py:35`, `playwright.config.ts` | REM-03 |

## Risks

| Risk | Affects | Mitigation |
|---|---|---|
| Auto-resolution hides a real conflict | REM-14 | Limited to single-field values; any differing value shows the conflict panel; independent review |
| The `/proposals` change breaks an unknown consumer | REM-17 | Only the bundled UI and CLI are known consumers; documented in API.md |
| Pruning or compaction deletes the wrong rows | REM-16, REM-19, REM-20 | Only derived or temporary tables are touched; pre-compaction backup; tests compare export manifests |
| The authorization rewrite opens or closes the wrong route | REM-21, REM-22 | The route-enumeration test fails closed; the bypass attempts stay as a regression test |
| A migration corrupts personal data | REM-25 | Critical lane, automatic pre-migration backup, a test migrating a populated v1 fixture, and Rudy's approval |

## Not planned

- Session cookie not scoped to the port: any process able to replay it can already forge its own bootstrap, which falls inside the accepted same-OS-user threat model.
- Switching SQLite to WAL: not needed; REM-12 corrects the documentation instead.

## Change log

| Date | Change |
|---|---|
| 2026-10-28 | Plan created from the audit of `d19a39e`: waves 0–5, work items REM-01 to REM-26, decisions D1 to D7 |
| 2026-10-28 | Wave 0 started: REM-02 (#2) and REM-03 (#4) in review, REM-01 awaiting D1. Found N7 while building a worktree; tracked as REM-27 in Wave 1 |
| 2026-10-28 | D1 decided: `main` created at `d19a39e` and made the default branch. REM-02 (#2) and REM-03 (#4) merged; branch protection and REM-01's IMPLEMENTATION.md fix pending |
| 2026-10-28 | Wave 0 closed: branch protection enabled on `main` with Rudy's approval, and REM-01 (#5) merged. Wave 1's entry gate is met |
