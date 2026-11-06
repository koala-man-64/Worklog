# Worklog consolidation plan

The working plan for consolidating and cleaning up Worklog after its first 86 pull requests. It replaces [REMEDIATION.md](REMEDIATION.md), the plan from the 2026-10-28 audit, whose open items are folded in below.

The plan comes from a read-only audit of `main` at `e4a1d14` on 2026-11-04. Three reviewers, one each for architecture, the data model and the trust boundary, reported independently and then cross-examined each other's findings. Every finding a step depends on was verified against the code, and the runtime ones on an isolated instance with synthetic data.

## The short version

The core is sound: revisions that refuse stale saves, server-written history, idempotent writes, the table-agnostic export, offline restore and the guide-contract tests. Keep them.

The cost of building feature by feature is a feature tax. Each new concept re-copies the HTTP and SQL plumbing and edits by hand about ten registries that must agree with each other:

- agent scope rules
- the CLI allowlist
- proposal field lists
- apply contracts
- the search join
- restore schema shapes
- hand-typed responses
- the guides

Recent feature PRs edited between 7 and 17 shared central files each, and the defects below sit in the gaps between those copies.

The order is fixed:

1. Fix the defects in Phase 0.
2. Add one additive schema version.
3. Consolidate the registries and plumbing behind characterization tests, so nothing an agent sees changes by accident.
4. Turn to retention and the product surface.

## How to use this file

- Each step's PR updates only that step's `Status` line: its status, branch and PR link. Steps have separate sections, so parallel PRs don't conflict here.
- Status values: `Not started`, `In progress`, `In review`, `Merged`, `Blocked: <reason>`, `Dropped: <reason>`.
- Run one step per PR, from its own worktree. Use `claude/<topic>` branches for Claude sessions. Never implement in the checkout that serves the instance you use every day.
- Test against an isolated data directory on a port other than the default (README, *Development checks*).
- Steps marked *parallel* touch separate files. Never run two PRs against `backend/worklog/app.py`, `frontend/src/styles.css` or `README.md` at the same time.
- A step that changes what agents can call or read updates `docs/AGENT-GUIDE.md` in the same PR and says so in the PR title.
- Record new decisions in the decision table with the date and who decided.

## Decisions

| ID | Question | Decision | Affects | Decided |
|---|---|---|---|---|
| K1 | Is the human/agent split a guardrail for cooperating agents, or a security boundary? | A guardrail. There is no launcher secret for sessions. Steps that keep promises the docs make (chat delivery, an explicit read policy) still happen. | 0.5, 1.4, 3.x | 2026-11-04, Rudy |
| K2 (D5) | What may agents read? | Everything they read today, including attachment bytes, record history, imported HTML originals and people's contact details. The policy table lists these reads explicitly, and new routes start closed to agents until they are listed. | 3.3 | 2026-11-04, Rudy |
| K3 | Which agents receive what the person writes into a tracked chat? | An identity that registered with the chat receives it automatically. Any other claim shows as unconfirmed, and receives nothing until the person confirms it on the chat page. Credentials issued in Settings work through confirmation. | 0.5, 1.4 | 2026-11-04, Rudy |
| K4 (D2) | May reorder, progress and structure saves stop bumping the revisions of records they don't change? | Yes | 4.1 | 2026-11-04, Rudy |
| K5 | How old a backup must restore accept? | Every version from v1, including the nine sealed v1 shapes | 2.5 | 2026-11-04, Rudy |
| K6 | How much edit history is kept? | Thin display-only history older than 90 days, and keep each roadmap's last 20 versions. Never touch registration, proposal or Today state (see *Rules*). | 4.3 | 2026-11-04, Rudy |
| K7 | How are the agent pages organized? | Group the sidebar first. Decide whether Chats and Agents merge after using it. | 5.1 | 2026-11-04, Rudy |
| K8 | What happens to free-text repository labels on discussions and tracked chats? | They stay as read-only labels: new writes stop, exact matches are linked to catalog repositories, and the columns are never dropped. | 5.6 | 2026-11-04, Rudy |
| K9 | What happens to REMEDIATION.md? | It is superseded by this file and kept as the record of the 2026-10-28 audit. | 5.5a | 2026-11-04, Rudy |
| K10 (D4) | What are the agent quotas? | Per agent: 50 open drafts, 100 submitted proposals awaiting review, and per proposal 20 evidence files and 100 MB | 3.4 | 2026-11-04, Rudy |
| K11 (D7) | Which tool hash-locks Python dependencies? | pip-tools (`pip-compile --generate-hashes`), with separate runtime and dev lock files | 5.7 | 2026-11-04, Rudy |
| K12 | Should registering with a chat also wait for the person's confirmation? (from the security review of 1.4) | No: K3 stays as it is. A registered identity is bound to its chat with no confirmation, since agents are cooperating (K1), and the chat page names who registered each one. C-01 stays narrowed, not closed. | 1.4 | 2026-11-04, Rudy |
| K13 | Does a new database start from the baseline schema, or at v1 with every upgrade run? (from building 2.5) | From the baseline, at the current version. Each schema change then writes its DDL twice, in `schema.py` and in its upgrade step, and the seals fail on any difference (see 2.5). | 2.5 | 2026-11-05, Claude, while building 2.5 |
| D6 | Is a migration framework needed? | Closed as done: migrations v1 to v10 run in one transaction after a pre-migration backup. | — | 2026-11-04, Rudy |

## Rules the roundtable settled

These come from the cross-examination. Each one overturned a first proposal.

- **Events are state, not only history.**
  - Several features read event JSON at runtime:
    - registration sponsorship, the registered chat, registration limits and the revoke cascade (`registration.py`);
    - proposal visibility, submitted time, auto-approval and stale-notice de-duplication (`proposals.py`);
    - Today's "completed" and "blocked as of" lists, and each day entry's `statusOnDay`, which replay item events (`items.py`).
  - Move that state into columns (step 1.3) before any compaction.
  - Compaction thins only an allowlist of display-only kinds, and keeps `{id, title, status, _statusChanged}` on item events.
  - Never delete an event row: `discussion_changes.event_id` references it.
- **Proposal fields come from one registry with explicit markers.** Every operation says whether agents may propose it and which fields are server-only. Never derive what agents may supply from the request models: `AttachmentCreate.proposalId` is set by Worklog, and `ReferenceCreate`, which carries a local path, is not proposable.
- **Characterization comes first.** The policy table must reproduce today's agent route matrix exactly before anything replaces the predicates. Only K2's "new routes start closed" changes what agents see.
- **Read snapshots stay short.** Take one only for reads that run several queries, and never around an export: under the rollback journal, a long reader blocks commits.
- **Repository labels are deprecated before they are backfilled.** Stop accepting new free-text labels first, then link exact matches. Keep the label columns.
- **The coordination `handoff` kind:** stop accepting it at the API and leave the CHECK constraint alone. Removing it would rebuild a table for no integrity gain.
- **Typed responses keep today's shapes.**
  - Booleans are stored as 0/1, and timestamps are strings with or without microseconds.
  - Several columns hold JSON text.
  - Nullable and empty differ in meaning.
  - Event payloads carry the `_statusChanged` key.
  - Add golden-response tests for each route and actor kind before typing a route.

## Roadmap

### Phase 0 · Safety nets and quick fixes

No schema change. All steps are parallel.

#### 0.1 · Tests stop reading the developer's environment

Status: Merged · Branch: `claude/test-isolation` · PR: [#88](https://github.com/koala-man-64/Worklog/pull/88)

`create_app` reads `WORKLOG_PORT` and `WORKLOG_DEV` even when testing (`app.py:155-161`). With `WORKLOG_PORT` set, as the README suggests before e2e runs, the backend suite fails with 221 errors.

Fix: a `conftest.py` clears every `WORKLOG_*` variable for each test, and points `WORKLOG_DATA_DIR` at a temporary folder, so a default data directory never reaches real data. Findings: A-17.

#### 0.2 · Pin the agent route matrix in a test

Status: Merged · Branch: `claude/test-isolation` · PR: [#88](https://github.com/koala-man-64/Worklog/pull/88)

Add a spec-driven test that records, for every API operation, whether an agent credential is allowed, refused or in need of its own check. It snapshots today's behaviour, changes none, and is the safety net for Phase 3. Findings: C-03.

#### 0.3 · One search-index scan per roadmap save

Status: Merged · Branch: `claude/roadmap-save-index` · PR: [#89](https://github.com/koala-man-64/Worklog/pull/89)

The `search_index` columns `entity_id` and `owner_id` are FTS5 `UNINDEXED`, so `DELETE ... WHERE entity_id=?` scans the whole index. `refresh_plans` bulk-deletes and then runs that scan again for every step.

Measured on a 467-step roadmap: 0.5 s with about 1 MB of index text, 6.4 s with about 5.5 MB, and 18.1 s with about 14 MB, all inside the write lock.

Fix: insert directly after the bulk delete. Findings: B-01.

#### 0.4 · Rotating a credential keeps the agent's drafts

Status: Merged · Branch: `claude/rotation-keeps-drafts` · PR: [#90](https://github.com/koala-man-64/Worklog/pull/90)

Rotation binds a new credential id to the same agent identity. Proposal ownership compares credential ids, so the agent can no longer see, edit or submit its own drafts.

Fix: compare principals through `communication_credentials.agent_id`, with no schema change. Findings: C-06, B-16.

#### 0.5 · Mark chat recipients the chat isn't bound to

Status: Merged · Branch: `claude/chat-recipient-registration` · PR: [#93](https://github.com/koala-man-64/Worklog/pull/93)

Delivery of the person's chat messages matches on the chat ID a run reports, and any credential can report any chat ID.

Until 1.4 lands, the chat page marks every recipient whose identity didn't register with that chat. Findings: C-01.

#### 0.6 · Settings lists backups without hashing them

Status: Merged · Branch: `claude/settings-backup-hashes` · PR: [#92](https://github.com/koala-man-64/Worklog/pull/92)

Every `GET /settings` hashes every backup file (`recovery.py`, `list_backups`), which takes seconds once backups total a few gigabytes. The page never shows the hashes.

Fix: list without hashes; the CLI `backups` command keeps them. Findings: B-05, REM-18.

#### 0.7 · Header search clears when a result is chosen

Status: Merged · Branch: `claude/search-clears-on-choice` · PR: [#94](https://github.com/koala-man-64/Worklog/pull/94)

Search clears only on `hashchange` (`App.tsx`), so a result that points at the page already open leaves the results on screen.

Fix: clear on result click. Findings: A-11.

### Phase 1 · Schemas v11 to v13, additive only

Each of these is a separate migration with its own PR and its own independent review. The splits were made on 2026-11-04, while building v11 and v12.

- v11 (steps 1.1 and 1.2) adds derived data only, and its documented way back drops it.
- v12 (step 1.3) copies decision state out of event JSON into columns.
- v13 (step 1.4) binds chats to identities. It changes who receives the person's messages, so it gets a security review rather than a data review.

Each migration's PR owns `migrations.py`, `schema.py`, `db.py` and `recovery.py` while it is open. A deploy that runs a migration on real data waits for the person's go-ahead.

v11 and v12 went live together on 2026-11-04, after a dry run of the upgrade on a copy of the newest backup. v13 went live the same evening, after its own dry run, and steps 2.1 to 2.3 followed it. Step 2.4 went live on 2026-11-05, and the first half of step 2.5 that morning too, after a dry run of its new restore checks on a copy of the newest backup.

#### 1.0 · One writer for the search index

Status: Merged · Branch: `claude/one-search-writer` · PR: [#95](https://github.com/koala-man-64/Worklog/pull/95)

This step was added on 2026-11-04 while preparing 1.1. Keyed search rows only work if every writer keeps the keys in step. Before this step, discussions, tickets, repository merges and `WorkService` all wrote `search_index` directly.

`search.py` now owns every write, unchanged in behaviour. `test_search_writes.py` fails when another module writes the table; historic migrations are exempt. No schema change. Findings: B-13.

#### 1.1 · Search keys and missing indexes

Status: Merged · Branch: `claude/schema-v11` · PR: [#96](https://github.com/koala-man-64/Worklog/pull/96)

Add a `search_keys(entity_id PRIMARY KEY, doc, type, owner_id)` table, so index writes delete by rowid. Add indexes on the hot foreign-key columns: `tasks.item_id`, `file_references.item_id`, `work_items.step_id`, `work_items.roadmap_id`, `github_refs.chat_id` and `steps.parent_id`. Findings: B-01, B-10.

#### 1.2 · Dated, pruned receipts

Status: Merged · Branch: `claude/schema-v11` · PR: [#96](https://github.com/koala-man-64/Worklog/pull/96)

Add `receipts.created_at` with an index, and prune receipts older than 14 days at startup and hourly. Receipts kept full responses forever, including chat-message texts handed to agents. Existing receipts take the upgrade's time, so a retry in flight keeps replaying for the whole window.

A cap on stored response size is deferred. A capped receipt cannot replay its response, so revision-guarded writes should stop taking receipts first (REM-14). Findings: B-03, REM-16.

#### 1.3 · State out of event JSON

Status: Merged · Branch: `claude/schema-v12` · PR: [#97](https://github.com/koala-man-64/Worklog/pull/97)

In v12, `communication_credentials` gains the registration sponsor (identity and label) and the registered chat. Proposals gain `submitted_at` and `auto_approved`, and `proposal_stale_notices` records the notices sent. All of it is backfilled from the events, which stay as they are.

Afterwards no runtime decision reads event JSON. A test blanks the registration and proposal event payloads and checks that nothing Worklog shows or decides changes. Event kinds and local dates still count registrations per day, which compaction keeps. Findings: C-07, A-18, N-1.

#### 1.4 · Chats bound to identities

Status: Merged · Branch: `claude/schema-v13` · PR: [#98](https://github.com/koala-man-64/Worklog/pull/98)

In v13. Implements K3. An identity is bound to the chat it registered with (the v12 column), and `chat_confirmations` holds each other binding the person confirmed, keyed by the chat's own ID, so one confirmation covers every record that tracks the chat. Sending, inbox hand-out and proposal notices reach bound identities only. The chat page lists any other run reporting the chat's ID as not confirmed, with Confirm, and a confirmed agent with Remove. Each agent there says where it comes from (issued in Settings, or registered by which agent), since labels are the agents' own. The upgrade ends deliveries still waiting for an identity bound to no chat they were sent to. Needs 1.3. Findings: C-01.

C-01 is narrowed, not closed. Registering with a chat stays the agent's own statement, as K1 has it: any credential can register an identity with a chat's ID, which any agent can read, and that identity is bound with no confirmation. The person kept it that way on 2026-11-04 (K12).

### Phase 2 · Registries and plumbing

Guarded by 0.2. Nothing changes on the wire except what a step says: 2.1's plain 404 and 405 for requests no route serves, and 2.3's value check at submit. Step 2.1 goes first, then 2.3 and 2.5 can run in parallel.

#### 2.1 · Shared HTTP and SQL plumbing

Status: Merged · Branch: `claude/shared-plumbing` · PR: [#99](https://github.com/koala-man-64/Worklog/pull/99)

The plumbing that was copied from module to module now has one home each. The counts are from the inventory taken while building the step, and several were higher than first planned:

- **Snapshot read:** `Database.snapshot` replaces six of the seven copies. Discussions also check their caller's identity inside the snapshot. `/context` keeps its own reads, because one snapshot there would hold the lock longer (B-14).
- **Search prefix builder:** `search.prefix_match` replaces all six copies. Tickets, resources and discussions keep refusing a query over 2,000 bytes; work, people and global search take any length.
- **Chunked `IN`:** `common.rows_in` replaces five copies.
- **Live-agent clause:** `common.LIVE_AGENT` replaces five copies. The avatar backfill that runs inside every upgrade keeps its own frozen copy, so a later change to the rule cannot change old upgrades. A run's or a request's own credential check is a different rule and stays separate.
- **Archive and recover guards:** the seven stay where they are. Each answers an unknown command with its own message, so a shared helper would only move two lines.

The generic `POST /{resource}/{identity}/{verb}` route, which was removed and re-appended at startup so it would not shadow other routes, is replaced by 18 literal archive and recover routes. A request no route serves now gets the framework's plain 404, or 405 for a path another method serves, instead of Worklog's error envelope. No client sends one. No other request changes. A test now fails if any route is shadowed by one registered before it. Findings: A-05, A-07, B-14.

#### 2.2 · Named-column INSERTs

Status: Merged · Branch: `claude/named-inserts` · PR: [#100](https://github.com/koala-man-64/Worklog/pull/100)

Positional `INSERT ... VALUES` ties code to physical column order. All 32 now name their columns, in the order their values already came. `test_named_inserts.py` fails on any INSERT without a column list. It reads every string in the backend, with implicit and `+` concatenation joined and f-string fields blanked, so a statement split across lines is still found. This is a prerequisite for 4.4. Findings: B-07.

#### 2.3 · One operation registry

Status: Merged · Branch: `claude/operation-registry` · PR: [#101](https://github.com/koala-man-64/Worklog/pull/101)

One proposal operation was defined in five places: `proposals.FIELDS`, the `dispatch` contracts, the `models.Operation` literal, the request models and the `WorkService.apply` prefix router. The inventory found that the five agreed on every action's keys except three, and each of those three was deliberate.

What changed:
- **One definition.** `operations.py` defines each proposable operation once, by the request model the person's own route takes, with its path keys and the three deliberate differences: `note.create` requires `text`, `roadmap.revise` requires `steps`, and only Worklog sets `attachment.create`'s `proposalId`.
- **Derived from it.** `proposals.FIELDS` is derived from the registry with unchanged key sets, because approve re-checks stored proposals against them. Dispatch validates by the registry and refuses any action it doesn't define.
- **Pinned to it.** A test pins the `Operation` literal to the registry.
- **Server-only operations.** These are the archive and recover commands and `roadmap.restore`. They are listed as `SERVER_ONLY`, and no proposal can carry one.
- **Submit checks values.** While approval is on, submit checks every operation's values as approve does, with the same numbered error. Checks only the record can make still wait for approve.

The person's own routes keep their own body models, and the prefix router stays, since it copies nothing. Findings: A-01, C-09, C-16, REM-23.

#### 2.4 · Global search in the search module, then the WorkService split

Status: Merged · Branches: `claude/global-search`, `claude/workservice-split` · PRs: [#102](https://github.com/koala-man-64/Worklog/pull/102), [#103](https://github.com/koala-man-64/Worklog/pull/103)

Index writes moved to `search.py` in 1.0. Here the global search query, the 13-way join, moves there too. Then split `WorkService` into items, people, supporting records and search, with a layering test. Needs 1.1. Findings: B-13, A-04, A-06.

What changed:
- **Global search (#102).** `search.global_search` runs the header's search, and `search.index_plans` indexes a roadmap and its steps again after a change. Both moved unchanged. `test_global_search.py` pins:
  - what search finds: a record of every kind under every owner it can have, and the page each result opens;
  - what each archived or retired parent hides, and what it leaves, such as work on an archived roadmap's step.

  It passed before the move, and it fails when the query loses any of five rules it depends on.
- **The split (#103).** `WorkService` held about a thousand lines across every domain. Now:
  - `records.py` holds the record shapes and lookups the domains share, and the one map from each kind of owner to its table;
  - `support.py` holds supporting records, `items.py` work items and their days, and `people.py` people;
  - `WorkService` keeps only `apply`, which sends each command to the module that owns it.

  An AST comparison shows each moved function unchanged apart from names, with two exceptions:
  - the owner check lost a branch that did nothing;
  - the proposal check it made for uploads moved into `upload`, at the same point in the order.
- **Layering (#103).** `test_layering.py` lists every module in one order. It fails on any import, at the top of a module or inside a function, of a module later in the order. Four imports still go up, each listed with its reason:
  - three from `db`, which reaches migrations, proposals and recovery while it opens the file;
  - one from `coordination`, which tracks a registered identity's chat as a supporting record.

  To get there:
  - registration's reads of who registered whom moved to `discussions.py`, which defines the identity tables;
  - two value checks moved to `common.py`.

  Function-local imports outside the schema modules and the CLI fell from 16 to 1.

#### 2.5 · Baseline schema and sealed digests

Status: Merged · Branches: `claude/sealed-schemas`, `claude/baseline-schema` · PRs: [#104](https://github.com/koala-man-64/Worklog/pull/104), PR_LINK

Add one baseline schema module. Seal a digest for every released version, with DDL dumps as test fixtures, so restore stops rebuilding old shapes by running today's migration code. Restore keeps accepting v1 onward (K5). Findings: B-06, B-08, A-14.

What changed:
- **Sealed digests (#104).** `recovery.SEALED_SCHEMAS` seals each shape a version shipped in: nine for v1, and one for each later version. `backend/tests/schemas` holds each shape's DDL. Restore accepts a database whose version and digest are sealed, the same shapes it accepted before, and no longer builds older shapes from today's migration code.
- **The baseline (PR_NUMBER).** `schema.py` holds every table and index of the current version, each statement as an upgraded database records it, since a seal covers the text. Under K13 a new database starts from it at the current version. Before, it was created at v1 and run through every upgrade, the v10 column rename and the v15 table rebuild among them.
- **One home for the history.** The DDL used to be spread over eight modules. Now `migrations.py` holds every older piece, each upgrade step keeping the DDL its version shipped with, and the domain modules hold none. `db` no longer reaches `proposals`, so two upward imports from `db` remain in the layering test.
- **Old shapes come from the dumps.** `backend/tests/sealed.py` builds an older database from its version's dump and gives the shape each version sealed. `recovery._expected_schema` and `_compatible_schemas`, which rebuilt released shapes from today's code for tests, are gone. So are the strings only they used, the v1 tables new databases started from; the v1 dump keeps them.

`test_sealed_schemas.py` checks:
- every digest against its dump, and that every version is sealed;
- that a new database has the current sealed shape, and holds exactly what an empty v1 database holds once upgraded;
- that each sealed shape, built from its dump, is accepted by restore and upgrades to the current one, so an upgrade step changed after it shipped fails;
- that a changed shape, or a sealed shape under another version, is refused.

A schema change is a new version: its upgrade step, the same change in `schema.py`, and a new seal. The dump script writes a new version's dump only when a new database and the previous version's shape, upgraded, agree.

### Phase 3 · One authorization policy

The table observes first and enforces afterwards. Every read agents have today stays allowed (K2).

#### 3.1 · Policy table on the routes

Status: Not started · Branch: — · PR: —

Each route declares its scope. The table drives the middleware decision and checks the CLI allowlist, and replaces the seven predicate sites. Move the request guard to pure ASGI. Needs 0.2 and 2.1. Findings: C-03, A-02, C-14, REM-21, REM-22.

#### 3.2 · Human-only scope on sensitive routes

Status: Not started · Branch: — · PR: —

Add a route-level human scope to settings, credentials, backups and export, as a second line behind the middleware. Findings: C-08.

#### 3.3 · Explicit agent reads

Status: Not started · Branch: — · PR: —

List every read agents keep (K2) as allowed, and deny agents any route that isn't listed. Update AGENT-GUIDE's "may GET every route" in the same PR. Findings: C-04, C-15.

#### 3.4 · Quotas and proposal paging

Status: Not started · Branch: — · PR: —

Add K10's quotas, counted from live tables, not from events or receipts, and paginate `/proposals`. Findings: C-05, REM-17.

### Phase 4 · Write amplification and retention

Stop the growth before reclaiming space.

#### 4.1 · Roadmap save diff

Status: Not started · Branch: — · PR: —

A structure save rewrites every step, bumps every step's revision, and puts two full trees into the roadmap event.

Fix, under K4:

- touch only the steps that changed;
- record `{versionNumber, changedStepIds}` instead of the trees;
- skip a version identical to the last one;
- stop reorder and progress from bumping unrelated revisions.

Findings: B-02, REM-13, REM-26.

#### 4.2 · Backups off the request path

Status: Not started · Branch: — · PR: —

The daily backup now runs from the watcher only, not on the first write of the day. Backup hashes are cached, and export streams instead of holding blobs twice in memory. Findings: B-05, C-17, REM-18.

#### 4.3 · Offline compact

Status: Not started · Branch: — · PR: —

Add `worklog compact`, with K6's retention, the allowlist from *Rules*, a purge of uncommitted plan imports, then `VACUUM`. It runs offline and takes a pre-compaction backup. Needs 1.3 and 4.1. Findings: B-04, B-12, REM-19, REM-20.

#### 4.4 · Doctor, then constraints

Status: Not started · Branch: — · PR: —

First a read-only `worklog doctor`, checking:

- orphans and owner types;
- duplicate tracked chats;
- FTS integrity;
- search keys that disagree with their rows. Nothing in the schema ties `search_keys` to `search_index`, and restore checks only the schema (from the review of #96).

Then a later schema version adds the missing CHECK and FK constraints to leaf tables only. Needs 2.2. Findings: B-09.

### Phase 5 · Product surface and agent contract v2

#### 5.1 · Grouped sidebar

Status: Not started · Branch: — · PR: —

Group the sidebar into four sections:

- Plan: Today, Work, Tickets, Roadmaps
- Directory: Repositories, People, Resources
- Agents: Agents, Chats, Discussions, Review
- System: Settings, Guide

Implements K7. Findings: A-10.

#### 5.2 · One name for agent identities

Status: Not started · Branch: — · PR: —

Settings calls them "Stable discussion profiles", while the guide says agent identity. The table keeps its name; only the interface and docs change. Findings: A-15.

#### 5.3 · Typed responses, then typed commands

Status: Not started · Branch: — · PR: —

None of the 146 operations has a typed response; the frontend keeps seven hand-written type files and three data-access styles. Type responses route by route, under golden tests (see *Rules*), then route every write through typed commands and delete the hand-written types. Findings: A-03, A-13.

#### 5.4 · Frontend layout

Status: Not started · Branch: — · PR: —

Split `components.tsx` into `ui/`, `sections/` and `pages/`, with one pager instead of seven, and split `styles.css` per page. `styles.css` changed in 44 of the first 86 commits. Findings: A-12, A-19.

#### 5.5a · This plan in the repository

Status: Merged · Branch: `claude/consolidation-plan` · PR: the one that added this file

Implements K9.

#### 5.5 · Docs trimmed to their jobs

Status: Not started · Branch: — · PR: —

Trim the README to overview, install and run. Move each version's upgrade and downgrade notes into `docs/UPGRADING.md`, and keep downgrade SQL for the latest version only. Mark `IMPLEMENTATION.md` historical. Remove the design-audit log from the top of `styles.css`. Findings: A-16, B-15.

#### 5.6 · Agent contract v2

Status: Not started · Branch: — · PR: —

Under K8:

- Stop accepting free-text repository labels, link exact matches, and keep the rest.
- Stop accepting new coordination `handoff` updates.
- Decide whether runs and participations collapse into one record.

Announce each in AGENT-GUIDE. Findings: A-08, B-11, A-09.

#### 5.7 · Hash-locked dependencies

Status: Not started · Branch: — · PR: —

Use pip-tools with separate runtime and dev lock files, installed with `--require-hashes`, and add an advisory `pip-audit` job (K11). Findings: C-12, REM-24.

## Findings index

Severity is the final rating after cross-examination. P1 means fix now, P2 the next iteration, P3 when convenient. The prefix names the reviewer: A for architecture, B for data model, C for trust boundary, N for a finding added during cross-examination.

| ID | Severity | Finding | Step |
|---|---|---|---|
| A-01 | P1 | One proposal operation is defined in four places | 2.3 |
| A-02 / C-03 | P1 | Agent scope rules live in seven places plus a CLI copy; reads are allowed by default; no route-enumeration test | 0.2, 3.1 |
| A-03 | P1 | None of the 146 responses is typed | 5.3 |
| A-04 | P1 | `WorkService` is a god class; the kind→table map is copied three times; search is a 13-way join | 2.4 |
| A-05 | P1 | Plumbing re-copied per router; `/context` is not one snapshot | 2.1 |
| A-07 | P1 | Correctness depends on route order | 2.1 |
| B-01 | P1 | Every search-index write scans the whole index | 0.3, 1.1 |
| B-02 | P1 | A roadmap save rewrites every step, about 6.5 MB for a large one | 4.1 |
| B-03 | P1 | Receipts are unbounded, undated and keep full responses | 1.2 |
| B-04 | P1 | Events keep full before/after JSON for every autosave | 4.3 |
| N-1 | P1 | Events double as a state store | 1.3 |
| C-01 | P1 | A claimed chat ID receives the person's messages to another agent's chat | 0.5, 1.4 |
| A-06 | P2 | No module layering | 2.4 |
| A-10 | P2 | Agent activity is spread over four pages | 5.1 |
| A-11 | P2 | Search results stay on screen for a result on the open page | 0.7 |
| A-12 | P2 | `components.tsx` grab-bag; seven pagers | 5.4 |
| A-13 | P2 | Three data-access styles | 5.3 |
| A-15 | P2 | Five chat identifiers; identity naming drift | 1.3, 5.2 |
| A-17 | P2 | Tests read the developer's environment | 0.1 |
| A-19 | P2 | Hot shared files collide under parallel PRs | 5.4, 5.5 |
| B-05 | P2 | Backup, export and hash costs grow with blob bytes | 0.6, 4.2 |
| B-06 | P2 | Restore validation is rebuilt from live migration code | 2.5 |
| B-07 | P2 | Positional INSERTs | 2.2 |
| B-08 | P2 | Schema spread over eight modules and spliced as strings | 2.5 |
| B-09 | P2 | Constraint gaps covered only in code | 4.4 |
| B-10 | P2 | 23 foreign-key columns without an index | 1.1 |
| B-11 / A-08 | P2 | Two repository vocabularies | 5.6 |
| B-12 | P2 | Append-only tables without a purge | 4.3 |
| C-04 / C-15 | P2 | Agent reads are implicit, including through search snippets (kept by K2) | 3.3 |
| C-05 | P2 | No agent quotas except on registration | 3.4 |
| C-06 / B-16 / N-3 | P2 | Two principal namespaces; rotation orphans drafts and breaks receipt replay | 0.4 |
| A-09 | P3 | Two handoff mechanisms | 5.6 |
| A-16 | P3 | Docs drift at the edges | 5.5 |
| B-13 | P3 | Three search-maintenance styles; no reindex | 1.0, 2.4 |
| B-14 | P3 | Multi-query reads are not snapshot-consistent | 2.1 |
| B-15 | P3 | Hand-written downgrade SQL in the README | 5.5 |
| C-02 | P3 | Any local process can start a human session (accepted under K1) | — |
| C-08 | P3 | Older handlers have no second check | 3.2 |
| C-10 | P3 | Agent-chosen registration tokens and labels are weakly constrained | — |
| C-11 | P3 | The session cookie is not scoped to the port | — |
| C-12 | P3 | Dependency locks without hashes | 5.7 |
| C-13 | P3 | The Markdown `untrusted` flag is opt-in | — |
| C-14 | P3 | The request guard relies on Starlette's private `request._body` | 3.1 |

## Keep

These look like consolidation candidates but stay:

- Tickets apart from work items: agents write tickets directly, while items change only by proposal.
- The four message tables (chat messages, discussion messages, ticket comments, coordination updates). Share UI components, not storage.
- People apart from agent identities, resources apart from repository documents, and roadmap questions and decisions apart from discussions.
- The polymorphic owner tables, and the three repository link tables with real foreign keys.
- One process and one SQLite file with BLOBs inside, under the rollback journal.
- `mutate`, `require_revision` and `event`; the table-agnostic export; the pre-migration backup and transactional upgrade.
- The guide-contract tests, and the committed `openapi.json` and `generatedApi.ts` with their drift checks.
- These defenses:
  - Host, Origin, SameSite and CSRF checks;
  - deny-by-default agent writes plus the service-level checks;
  - approval revision-checked in one transaction;
  - token hashing and redaction;
  - forced downloads;
  - Markdown rendering with HTML skipped;
  - the UUID-only transcript reader;
  - `extra="forbid"`;
  - the lone-surrogate guard;
  - Actions pinned by SHA;
  - the CLI's own allowlist, checked against the server once 3.1 lands.

## REMEDIATION.md items

| Item | Now |
|---|---|
| REM-13, REM-26 | 4.1 |
| REM-14 | Kept; easier after 4.1 removes false conflicts |
| REM-15 | Session renewal shipped in #66; what remains is Today rolling over at local midnight |
| REM-16 | 1.2 |
| REM-17 | 3.4 |
| REM-18 | 0.6 (listing), 4.2 (export) |
| REM-19, REM-20 | 4.3 |
| REM-21, REM-22 | 3.1 |
| REM-23 | 2.3 |
| REM-24 | 5.7 |
| REM-25 | Done by v1 to v10 (D6) |

## Change log

- 2026-11-04: Plan created from the consolidation audit of `e4a1d14`; decisions K1 to K11 recorded; REMEDIATION.md superseded.
- 2026-11-04: Phase 1 split into v11 to v13 while building it, and step 1.0 added. v11 and v12 deployed together. K12 recorded from the security review of step 1.4.
- 2026-11-04: Steps 2.1 to 2.3 deployed. Step 2.4 split into two PRs while building it: global search first, then the `WorkService` split.
- 2026-11-04: Step 2.5 split into two PRs while building it: sealed digests first, then the baseline schema module.
- 2026-11-05: Schema v14, an order for each work item's tasks, added outside the plan for a product request, with its seal and documented way back. Step 2.5's baseline schema module starts from v14.
- 2026-11-05: Schema v15, a Waiting status for work items, added outside the plan for a product request, with its seal and documented way back. It rebuilds `work_items` to widen its status CHECK. Step 2.5's baseline schema module starts from v15.
- 2026-11-05: K13 recorded while building step 2.5's baseline module: a new database starts from the baseline schema at the current version.
