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
| D6 | Is a migration framework needed? | Closed as done: migrations v1 to v10 run in one transaction after a pre-migration backup. | — | 2026-11-04, Rudy |

## Rules the roundtable settled

These come from the cross-examination. Each one overturned a first proposal.

- **Events are state, not only history.**
  - Several features read event JSON at runtime:
    - registration sponsorship, the registered chat, registration limits and the revoke cascade (`registration.py`);
    - proposal visibility, submitted time, auto-approval and stale-notice de-duplication (`proposals.py`);
    - Today's "completed" and "blocked as of" lists, which replay item events (`services.py`).
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

Status: Not started · Branch: — · PR: —

Rotation binds a new credential id to the same agent identity. Proposal ownership compares credential ids, so the agent can no longer see, edit or submit its own drafts.

Fix: compare principals through `communication_credentials.agent_id`, with no schema change. Findings: C-06, B-16.

#### 0.5 · Mark chat recipients the chat isn't bound to

Status: Merged · Branch: `claude/chat-recipient-registration` · PR: [#93](https://github.com/koala-man-64/Worklog/pull/93)

Delivery of the person's chat messages matches on the chat ID a run reports, and any credential can report any chat ID.

Until 1.4 lands, the chat page marks every recipient whose identity didn't register with that chat. Findings: C-01.

#### 0.6 · Settings lists backups without hashing them

Status: Not started · Branch: — · PR: —

Every `GET /settings` hashes every backup file (`recovery.py`, `list_backups`), which takes seconds once backups total a few gigabytes. The page never shows the hashes.

Fix: list without hashes; the CLI `backups` command keeps them. Findings: B-05, REM-18.

#### 0.7 · Header search clears when a result is chosen

Status: Not started · Branch: — · PR: —

Search clears only on `hashchange` (`App.tsx`), so a result that points at the page already open leaves the results on screen.

Fix: clear on result click. Findings: A-11.

### Phase 1 · Schema v11, additive only

One PR that owns `migrations.py`, `db.py` and `recovery.py`. Rollback drops the additions and sets `user_version` to 10. A deploy that runs the migration on real data waits for the person's go-ahead.

#### 1.1 · Search keys and missing indexes

Status: Not started · Branch: — · PR: —

Add a `search_keys(entity_id PRIMARY KEY, doc, type, owner_id)` table, so index writes delete by rowid. Add indexes on the hot foreign-key columns: `tasks.item_id`, `file_references.item_id`, `work_items.step_id`, `work_items.roadmap_id`, `github_refs.chat_id` and `steps.parent_id`. Findings: B-01, B-10.

#### 1.2 · Dated, pruned receipts

Status: Not started · Branch: — · PR: —

Add `receipts.created_at` with an index, a 14-day prune, and a cap on stored response size. Receipts currently keep full responses forever, including chat-message texts handed to agents. Findings: B-03, REM-16.

#### 1.3 · State out of event JSON

Status: Not started · Branch: — · PR: —

Add two columns to `communication_credentials`, the registration sponsor and the registered chat, backfilled from `credential.register` events. Add a proposal `submitted_at` and a record of stale notices. Afterwards, no runtime path depends on event JSON for authorization. Findings: C-07, A-18, N-1.

#### 1.4 · Chats bound to identities

Status: Not started · Branch: — · PR: —

Implements K3. A registered chat binds automatically; the person confirms any other binding on the chat page; deliveries go to bound identities only, and the inbox is keyed by identity. Needs 1.3. Findings: C-01.

### Phase 2 · Registries and plumbing

No wire change, guarded by 0.2. Step 2.1 goes first, then 2.3 and 2.5 can run in parallel.

#### 2.1 · Shared HTTP and SQL plumbing

Status: Not started · Branch: — · PR: —

Move the plumbing into `http.py` and common helpers:

- the snapshot read (copied six times);
- the archive and recover guard (seven times);
- the FTS prefix builder (five times);
- chunked `IN` and the live-agent `EXISTS` (four times each).

Replace the generic route that is removed and re-appended at startup with explicit routes. Findings: A-05, A-07, B-14.

#### 2.2 · Named-column INSERTs

Status: Not started · Branch: — · PR: —

Positional `INSERT ... VALUES` ties code to physical column order. Name the columns everywhere, enforced by a test. This is a prerequisite for 4.4. Findings: B-07.

#### 2.3 · One operation registry

Status: Not started · Branch: — · PR: —

One proposal operation is defined in four places: `proposals.FIELDS`, the `dispatch` contracts, the request models and the `WorkService.apply` prefix router.

Fix: one registry with `agent_proposable` and `server_only` markers, and payloads validated at submit as well as at approve. Findings: A-01, C-09, C-16, REM-23.

#### 2.4 · One search module, then the WorkService split

Status: Not started · Branch: — · PR: —

`search.py` owns the index and global search; today discussions and tickets also insert into the index directly. Then split `WorkService` into items, people, supporting records and search, with a layering test. Needs 1.1. Findings: B-13, A-04, A-06.

#### 2.5 · Baseline schema and sealed digests

Status: Not started · Branch: — · PR: —

Add one baseline schema module. Seal a digest for every released version, with DDL dumps as test fixtures, so restore stops rebuilding old shapes by running today's migration code. Restore keeps accepting v1 onward (K5). Findings: B-06, B-08, A-14.

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

First a read-only `worklog doctor`: orphans, owner types, duplicate tracked chats and FTS integrity. Then v12 adds the missing CHECK and FK constraints to leaf tables only. Needs 2.2. Findings: B-09.

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
| B-13 | P3 | Three search-maintenance styles; no reindex | 2.4 |
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
