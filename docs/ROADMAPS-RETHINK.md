# Trim Worklog and rethink roadmaps

The working plan for removing what Worklog doesn't use, then rebuilding roadmaps around how they are used. It follows [CONSOLIDATION.md](CONSOLIDATION.md), whose steps are all merged.

The plan comes from a review on 2026-11-10 of `main` at `92675f1` and of how the personal instance is used: read-only counts from its database and usage file, never record contents. The database's history starts on 2026-10-29. The usage file has counted page views and calls since 2026-11-08.

## The short version

Roadmaps are where Worklog gets used. 24 roadmaps hold 1,145 active steps, and agents keep them current: 278 approved progress changes, all 50 questions and decisions, and 253 links between tracked chats and steps. Little else gets used: 27 work items in all, 24 of them on steps; Today on 3 days; no release or reminder, ever.

The order:

1. Remove what nothing uses (Phase 0), so the rework doesn't carry it.
2. Rework the roadmap page and the agents' reads, with no schema change (Phase 1).
3. Change the model: a step opens into its own roadmap (Phase 2), steps take an order (Phase 3), and the outline edits in place (Phase 4).

## How to use this file

- Each step's PR updates only that step's `Status` line: its status, branch and PR link.
- Status values: `Not started`, `In progress`, `In review`, `Merged`, `Blocked: <reason>`, `Dropped: <reason>`.
- Run one step per PR, from its own worktree, on a `claude/<topic>` branch. Never implement in the checkout that serves the instance you use every day.
- Test against an isolated data directory on a port other than the default ([DEVELOPMENT.md](DEVELOPMENT.md), *Checks*).
- Phase 0 runs one step at a time: each edits `app.py`, the policy table, the schema and the guides. Never run two PRs against `backend/worklog/app.py`, `frontend/src/styles/base.css` or `README.md` at the same time.
- A step that drops a table or column is a schema version of its own. Check open PRs for the next version number first, write its DDL in `schema.py` and in its upgrade step (K13), seal it, and replace the way back in [UPGRADING.md](UPGRADING.md). Rudy approves each one before it merges.
- A step that changes what agents can call or read updates `docs/AGENT-GUIDE.md` in the same PR and says so in the PR title. Such a step, and every step that drops a table, gets an independent review before it merges.
- Record new decisions in the decision table with the date and who decided.

## What the review found

### Roadmaps

- 24 roadmaps, 21 of them nested under another, hold 1,145 active steps and 46 retired ones. Nearly all have about 5 phases of about 5 steps.
- Titles are sentences. The median step title is 91 characters and 680 steps run over 80; the median roadmap title is over 100. The median step description is 575 characters.
- The largest roadmap has 487 steps, four levels deep, and about 1 MB of step text. `roadmap show` returns all of it.
- Steps and roadmaps nest separately. A program roadmap's steps mirror its 12 sub-roadmaps (their titles match at 0.81 to 0.96), with their status set by hand: 13 of its 14 leaves are Doing, so its bar reads 1 of 14 whatever its sub-roadmaps do. Two pairs of roadmaps have identical step lists.
- All 59 Blocked steps are in the largest roadmap: 43 of one phase's 62 leaves, all 3 of another's, and 13 more across five phases.
- Agents raised all 18 questions, and 7 are open. Questions show only on the roadmap's overview, and the person has never answered one in Worklog.
- 46 of the 71 saved versions carry a written change note, so versions read as the plan's changelog; the rest say "Created" or nothing. No version has ever been restored.
- In use on roadmaps and steps: 119 notes, 87 links, 138 files, 14 people ties, and 253 chat-to-step links; the person made 129 of the assignments.
- Since counting began, the person opened Review 16 times, Roadmaps 13 and Discussions 4, and never opened People or Releases.

### The rest of Worklog

| Feature | Added | Use | Records today |
|---|---|---|---|
| HTML plan import | from the start | never previewed or imported | 0 |
| Work item checklists | from the start | no task ever added | 0 |
| End-of-day reflection | from the start | never written | 0 |
| Messaging agents from a chat | 2026-11-02 | no message ever sent | 0 messages, deliveries or confirmations |
| Agent ties | 2026-11-06 | set by agents on 2026-11-07 | 5 |
| Releases | 2026-11-06 | no release ever made | 0 releases, contents or checks |
| Reminders | 2026-11-06 | no reminder ever set | 0 |
| Tickets | 2026-11-02 | filed by agents on 2026-11-09 | 2, no comments |
| People | 2026-10-29 | added and tied by agents, 2026-11-04 to 2026-11-06 | 11 people, 14 ties |
| Resources | 2026-11-04 | written on 2026-11-05 | 2 |
| Today | from the start | 3 days | 8 entries, mostly saved by agents |

The files that Phase 0 deletes outright hold about 9,100 of the codebase's 77,000 lines of code and tests (12%), before its edits to shared files. The only records it drops are the 5 agent ties.

## Decisions

| ID | Question | Decision | Affects | Decided |
|---|---|---|---|---|
| R1 | What goes? | The HTML plan import, work item checklists, the end-of-day reflection, messaging agents from a chat, agent ties, Releases and Reminders: each with its pages and sections, routes, tables, agent commands, proposal operations and guide sections. This retires K3 and K12 (messages in a chat), K14 (reminders) and K2's read of imported HTML originals. | 0.1 to 0.7 | 2026-11-10, Rudy |
| R2 | Do tickets, people and resources go too? | Not yet. Revisit them with a month of usage counts, from 2026-12-08. | — | 2026-11-10, Rudy |

## Open questions

| ID | Question | Shapes |
|---|---|---|
| Q1 | In the largest roadmap, do the Blocked steps mean "not yet", waiting on an earlier phase, or stuck? | 3.1 |
| Q2 | Were the two pairs of identical roadmaps meant to show one plan in two places, or are they copies to archive? | 2.1 |
| Q3 | Does Today stay? Nothing here depends on it, and steps on Today are parked until it is settled. | Parked |

## Steps

### Phase 0 · Remove what nothing uses

Smallest first, one at a time. Each step takes its feature out of everywhere it appears: the interface, the API, the agent CLI and proposals, search, backup, export and restore, the guides, `docs/API.md`, and the OpenAPI schema with its generated client. It drops the feature's tables in a schema version, which an older backup also passes through when restored.

#### 0.1 · Remove the HTML plan import

Status: Not started

- On Roadmaps: "Bring in an HTML plan", with its preview, "Create from selected steps" and "Attach original as reference". On a roadmap: "Original plan references" and "Download original".
- `POST /imports/preview`, `POST /imports/{identity}/commit` and `GET /imports/{identity}/download`, and `preview`, `import_commit` and `import_download` in `plans.py`.
- The `plan_imports` table, and its handling in backups, exports, restore, compaction and the request guard's upload limits.
- `beautifulsoup4` and `markdownify`, two of the six direct runtime dependencies, and with them `soupsieve` and `six`. Relock both lock files ([DEVELOPMENT.md](DEVELOPMENT.md), *Dependencies*).

Why: never previewed or imported, and all 24 roadmaps were made another way. It is also the only code that parses uploaded HTML.

#### 0.2 · Remove the end-of-day reflection

Status: Not started

"End-of-day reflection (Markdown)" on Today, its route, and the reflection in a day's read and in the agent context, which changes what agents read. Drop `days.reflection` and `days.reflection_revision` (v21), and check what `days.revision` still guards before touching anything else in `days`.

Why: never written.

#### 0.3 · Remove work item checklists

Status: Not started

"Tasks" on a work item's page and on Today's entries (`sections/Checklist.tsx`), the task routes and proposal operations, and the `tasks` table, ordered since v14. This changes what agents can call.

Why: no task was ever added.

#### 0.4 · Remove Reminders

Status: Not started

The Reminders page and its count in the sidebar, "Reminders" on Today and on each record's page (`RecordReminders`), `reminders.py` and `reminder_api.py`, the agent's `reminders` commands, reminders in search, and the `reminders` table. The files it owns hold about 2,600 lines. This changes what agents can call, and retires K14.

Why: no reminder was ever set, by the person or an agent.

#### 0.5 · Remove Releases

Status: Not started

The Releases page, "Add to release" and the "Releases" sections on work items, roadmaps and steps, release checks, `releases.py` and `release_api.py`, the `release.*` proposal operations, a release as a discussion tag (no discussion has one), releases in search, and the `releases`, `release_contents` and `release_checks` tables. The files it owns hold about 3,200 lines. GitHub references stay; only the release's "Changes" view, which gathered them, goes. This changes what agents can call.

Why: no release was ever made.

#### 0.6 · Remove agent ties

Status: Not started

The "Agents" ties on record pages (`AgentTies`) and on the Agents and People pages, `agent_links.py` and `agent_link_api.py`, the agent's `ties` commands, and the `agent_links` table with its 5 rows. The files it owns hold about 1,900 lines. This changes what agents can call.

Settle in the step: the overlap check reads ties (`registry.py`, `overlaps`), and a repository merge moves them (`coordination.py`). Participations, discussion roles and tracked chats say who works where, and they stay; "Agents working here" on a roadmap reads those, not ties.

Why: 5 ties, all set by agents on one day; chats on steps say the same.

#### 0.7 · Remove messaging agents from a chat

Status: Not started

The send box on Chats (`ChatOutbox`), the confirmations in `ChatTies.tsx`, `chat_messages.py` and `chat_message_api.py`, the agent's `inbox` command, the agent registry's `message` and `confirmed` sources, and the `chat_messages`, `chat_message_deliveries` and `chat_confirmations` tables. The files it owns hold about 1,400 lines. Registering with a chat, `chats track`, runs and participations stay. This changes what agents can call, and retires K3 and K12. K17 stands: runs still hold participations.

Why: no message was ever sent.

### Phase 1 · The roadmap page and the agents' reads

No schema change.

#### 1.1 · Agent reads that fit in a context

Status: Not started

`roadmap outline ID` gives each step's ID, title, status, parent and position, without text. `step show ID` adds the step's ancestors, siblings and tracked chats. This changes what agents can read.

Why: `roadmap show` returns every step's full text, about 1 MB for the largest roadmap, to find one step.

#### 1.2 · Work from the step

Status: Not started

A step's page adds and attaches work items. The overview's work item forms go, with their step picker, which lists every step of the roadmap: 487 on the largest.

Why: attaching work to a step means leaving it for the overview and finding the step again.

#### 1.3 · A roadmap page with a job

Status: Not started

- A status board is the default view, with:
  - progress for each phase;
  - Now: the steps in progress, with their agents;
  - Next: the open steps in outline order, until 3.1 works out readiness;
  - Needs you: the open questions, blocked steps and proposals waiting on this roadmap;
  - Recently done.
- The outline is the second view.
- The overview's sections move into tabs:
  - Questions and decisions;
  - Material: the notes, links, files, chats and GitHub references, with archived ones behind a filter;
  - History: the activity, applied proposals, and versions with their notes and "Restore".
- Goes: the raw-JSON version viewer, the revision counter in the header and the person filter on the list. "Retired steps" becomes a toggle in the outline, and "Add sub-roadmap" and "Nested under" move to the header until 2.1 replaces them.
- Open questions count beside Roadmaps in the sidebar.

Why: the overview stacks about 19 sections, most of them empty; one progress bar hides where each phase stands; and agents' questions sit under the overview text.

#### 1.4 · Short titles

Status: Not started

Titles show on one line in the outline, the status board and the sidebar, and in full on hover. The agent guide asks for step titles under about 60 characters, with the detail in the description. A one-time pass proposes shorter titles with `step.update`, for the person to approve.

Why: the median step title is 91 characters.

### Phase 2 · One tree

#### 2.1 · A step opens into its own roadmap

Status: Not started

A roadmap can hang off a step, whose status then rolls up from that roadmap's steps. A step gets "Open as roadmap", and Move puts a roadmap under a step. A one-time pass proposes a link from each existing sub-roadmap to the parent step whose title matches it, for the person to confirm. Schema change; it changes what agents can read and call. Q2 shapes it.

Why: a parent's steps shadow its sub-roadmaps by hand, and drift from them.

### Phase 3 · Order

#### 3.1 · Dependencies and readiness

Status: Not started

Phases run in order unless marked parallel, and a step can depend on others. Worklog works out which steps are ready and what each waits on, and `roadmap next` lists the ready ones. Blocked stays for real impediments and asks for a reason. Schema change; it changes what agents can read and call. Q1 shapes it.

Why: nothing says what comes next, and Blocked stands in for "not yet".

### Phase 4 · Edit in place

#### 4.1 · Step-level structure changes and an outline that edits in place

Status: Not started

`step.create`, `step.move` and `step.retire`, as routes and proposal operations, so the outline edits in place (Enter, Tab, drag) and agents working on other steps never conflict. `roadmap.revise` stays for bulk restructures. The structure editor's drafts and rebase go once nothing needs them. The step decides how versions are cut once saves are this small, and keeps their notes. This changes what agents can call.

Why: adding, moving or retiring one step takes a whole-plan save today, which is why structure has its own editor with drafts, rebase and version notes.

## Parked

- Steps on Today: Today has 8 entries in all, and whether it stays is open (Q3).
- Checklists on steps: work item checklists were never used, and 0.3 removes them; sub-steps do that job.

## Change log

- 2026-11-10: Plan created from a review of `92675f1` and of the personal instance's usage. R1 and R2 recorded.
