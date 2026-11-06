# Worklog

Worklog is a personal workbench that runs entirely on your own machine. It keeps your daily plan and what you actually did, a backlog of work with its notes and files, structured roadmaps, and the people involved. It is also where you coordinate the AI coding agents you work with: they report what they are doing, talk with you and each other, and propose changes that you approve before anything in your records changes, unless you turn approval off.

Worklog runs at **http://127.0.0.1:8877** from one local SQLite database. No hosted service or internet connection is needed once it is installed.

## What Worklog does

- **Plan and record your days.** Plan what you will work on, mark what you actually worked on with an outcome and next action, and close the day with a reflection.
- **Keep one backlog, stack ranked.** Work items carry a status, a stack rank, a Markdown description, tasks with checkboxes, a scratchpad, files, links, local paths, an Azure DevOps link, GitHub pull requests and commits, people and repositories, along with their daily record and full history.
- **Track issues as tickets.** You and your agents file tickets for bugs, incidents and tasks, work them with a status, assignee and comments, and close them with a resolution. Closed tickets stay searchable, so the next agent finds what fixed an issue last time.
- **Plan bigger efforts as roadmaps.** Roadmaps nest under each other and hold step trees with progress, immutable versions, questions and decisions. An HTML plan can be imported as steps.
- **Know who is involved.** A people directory keeps each person's title, team, description, contact details and notes, and ties them to work items, roadmaps and repositories, with their role on each.
- **Keep reference pages.** Resources are Markdown pages you write once for yourself and your agents, such as how you like work done, conventions and runbooks. You read them on the Resources page, agents read them from the command line, and they change them only through proposals.
- **Work with AI agents under your control.** Claude Code, Codex, or any agent you give a credential can read your context, report what it is working on and where, talk with you and other agents, take and hand off responsibilities, file and close tickets, receive the messages you write into its chat, and propose changes that only you can approve, unless you turn approval off.
- **Tie agents to anything.** Tie an agent to a work item, roadmap, step, question, decision, repository, person, resource, discussion, tracked chat, proposal, ticket or another agent, with its role there, such as Maintainer or Reviewer. An agent can tie itself too, and change or remove only the ties it set. Each record's page lists the agents tied to it, each agent's page lists everything it is tied to, and an agent reads its own ties in its context.
- **See who is working on what.** Chats shows the conversations Claude Code and Codex keep for the chats you track, Agents gathers each agent's runs, ties and reports, and Repositories shows reported activity per repository.
- **Keep it safe and portable.** Automatic and manual backups, a readable ZIP export, and an offline restore that preserves your current data first.

## How it works

- **One local process.** `start` launches one hidden server (Python with FastAPI and Uvicorn) bound to 127.0.0.1. It serves the built React interface and the JSON API under `/api/v1` from the same address. Nothing listens beyond this machine, no Node process runs, and nothing is fetched from the internet at runtime.
- **One database.** Every record, attachment and imported plan file lives in one SQLite file, `worklog.sqlite3`, in the data directory, so a backup holds everything at once. Schema upgrades run at startup, after a snapshot.
- **You and your agents.** Your browser holds a local session and can change anything. Agents use credentials you issue in Settings, or identities they register for themselves from one, through the `worklog agent` command line. They read your context, report their own runs and activity, talk in Discussions, file and work tickets, receive your chat messages, and propose changes for you to approve. They never write your work directly: tickets, like discussions, are shared records agents write themselves, as are the ties an agent sets for itself, while work items, days, roadmaps, people, repositories and resources, your reference pages, change only through proposals you approve. An agent changes or removes only the ties it set; a tie you set is yours. One proposal can create several records and connect them, such as a person, the repository they own and a ticket against it, by naming each new record for the changes after it.
- **No silent overwrites.** Every record has a revision. A save based on an older revision is refused as a conflict, and your draft is kept until you decide. Each change is recorded in the record's history with who made it, including changes applied from an agent's proposal. Each work item and roadmap also lists the proposals applied to it, numbered in the order they were applied, and each agent's page the proposals it made.
- **Agent transcripts stay where they are.** Chats reads the transcripts Claude Code and Codex keep on this machine and never writes them. Agents cannot read them through Worklog.
- **Snapshots happen on their own.** A backup is taken at startup and once a day while Worklog runs, and before any upgrade or restore.

## Install from source on Windows

Install Python **3.12 or newer** and a current Node.js LTS with npm. Node is needed only to install/build the frontend, not to run the installed application. From PowerShell in this checkout:

```powershell
./scripts/Setup.ps1
./scripts/Worklog.ps1 start
./scripts/Worklog.ps1 check
```

Setup creates `.venv`, installs the exact versions in `requirements.lock`, installs this project without resolving dependencies again, then runs `npm ci` using `frontend/package-lock.json` and builds `frontend/dist`. It needs internet access to fetch packages. If Python has a different executable name, use `./scripts/Setup.ps1 -PythonCommand python3.12`. Respect your organization's PowerShell script policy; do not disable it globally.

Run Setup in every checkout and git worktree, so each has its own `.venv` and `frontend/node_modules`; with a warm package cache it takes about a minute. Do not link those folders to another checkout's. Setup replaces them, so it stops while they link into another checkout or another worktree links into them, and names each link; remove one with `rmdir`, which leaves its target alone. `./scripts/Setup.ps1 -CheckOnly` runs just that check.

After setup, runtime requires Python and the checkout/build files, with **no Node process and no internet connection**. The app does not fetch fonts, scripts, or remote plan contents at runtime. Links such as Azure DevOps URLs open externally only when you choose them.

## Run and stop

```powershell
./scripts/Worklog.ps1 start
./scripts/Worklog.ps1 check
./scripts/Worklog.ps1 stop
# Equivalent installed CLI:
./.venv/Scripts/worklog.exe start
./.venv/Scripts/python.exe -m worklog.cli check
```

`start` launches one hidden Uvicorn worker bound to loopback, serving the frontend and API. Windows Python's virtual environment redirector also remains as a hidden parent process. On Windows the server breaks away from the job objects of the shell that ran `start`, so ending that shell, or the agent session that owns it, leaves the server running. Windows lets it leave each job that allows breakaway, starting with `start`'s own, and keeps it, without an error, in the first job that does not, such as the job of the app that runs the shell. When `start`'s own job forbids breakaway, the server starts inside that job and ends with it; `start` then says so in a `warning` field and in `server.log`. It waits up to 60 seconds for `/health`, which leaves room for a slow cold start, and verifies Worklog identity, persistent data identity, PID, and OS process creation identity. The PowerShell launcher opens the browser only after readiness; use `./scripts/Worklog.ps1 start -NoBrowser` for background automation. CLI `worklog start` always returns JSON without opening a browser. Repeating `start` safely reuses that same instance. An unrelated or unverified listener on the selected port produces an error; the launcher never kills it. `stop` verifies the same identities and requests graceful shutdown through an instance-specific local marker. It never guesses from a PID or forcefully terminates an unrelated process. Shutdown gives any request still open up to 5 seconds, so `stop` completes within its 15-second wait. Pages left open carry on when Worklog is back: they start a new session by themselves. If the data changed meanwhile, as after a restore, they ask you to reload instead. If startup fails, inspect `server.log` in the data directory. The `serve` command is the internal foreground server entry point.

The default port is 8877. Choose another loopback port with `-Port` (launcher), `--port` (CLI) or `WORKLOG_PORT`, for example to run a disposable test instance beside your everyday one. An explicit option wins over `WORKLOG_PORT`. Use the same port and data directory for every command against an instance; the browser address and the agent CLI follow the selected port.

```powershell
./scripts/Worklog.ps1 start -Port 8878 -DataDir 'D:/Scratch/worklog-test' -NoBrowser
./scripts/Worklog.ps1 check -Port 8878 -DataDir 'D:/Scratch/worklog-test'
./scripts/Worklog.ps1 stop -Port 8878 -DataDir 'D:/Scratch/worklog-test'
```

Data defaults to **`%LOCALAPPDATA%/Worklog`**, outside the checkout. The SQLite database (`worklog.sqlite3`) includes attachment and import bytes. Backups, log, lock, and launcher state files live alongside it. Do not put a live database in a network share or cloud-sync folder. Choose a data directory consistently for every command:

```powershell
$env:WORKLOG_DATA_DIR = 'D:/Private/Worklog'
./scripts/Worklog.ps1 start
# Or a one-command override:
./scripts/Worklog.ps1 check -DataDir 'D:/Private/Worklog'
./.venv/Scripts/python.exe -m worklog.cli --data-dir 'D:/Private/Worklog' check
```

Only one managed server or offline restore can own a data directory. OS locks release after a crash; do not delete lock files to try to bypass a running instance. `check` requires a matching instance and readiness identity, not merely an open TCP port.

## Guides

- **[docs/GUIDE.md](docs/GUIDE.md)** is the guide for people: getting started, how Worklog behaves, how to do each task, every page and its controls, and troubleshooting. Worklog also shows it on its **Guide** page, offline like everything else.
- **[docs/AGENT-GUIDE.md](docs/AGENT-GUIDE.md)** is the contract for AI agents: the rules they follow, every `worklog agent` command, the proposal operations, every error code with the response it needs, and the limits. Agents print it with `./.venv/Scripts/worklog.exe agent guide`, which needs no credential and no running Worklog.
- **[docs/API.md](docs/API.md)** is the HTTP contract, and `docs/openapi.json` its generated schema.

## Working with agents

Create a labeled credential in Settings and give its one-time token to your agents through the `WORKLOG_AGENT_TOKEN` environment variable, never in a command argument, file, log or shell history. Then tell each agent to run `worklog agent guide` before it uses Worklog. Agents register an identity of their own for each chat, report their runs and repository activity, write tickets and discussions, and change your work, days, roadmaps, people, repositories and resources only through proposals you approve on Review, unless you turn approval off. The guide's *Bring an AI agent in* walks through it. Revoke a credential in Settings when its agents no longer need access.

## Backup, export, restore, and upgrade

Settings provides manual backup and portable export. CLI equivalents:

```powershell
./scripts/Worklog.ps1 backup
./scripts/Worklog.ps1 backups
./scripts/Worklog.ps1 export 'D:/Archives/worklog-export.zip'
./scripts/Worklog.ps1 stop
./scripts/Worklog.ps1 restore 'D:/Archives/worklog-manual-EXAMPLE.sqlite3'
./scripts/Worklog.ps1 start
```

Choose an actual filename from `backups`; `EXAMPLE` is a placeholder. Backups use SQLite's online backup API, which takes a consistent snapshot while the database is in use. The database uses SQLite's rollback journal, not WAL. A temporary snapshot is validated before atomic publication. An automatic backup runs at startup and once each configured calendar day while the managed server remains running (checked every minute); set `WORKLOG_AUTO_BACKUP=0` to turn automatic backups off. Daily backups retain the most recent **14**, pre-migration backups retain **3**, and manual and pre-restore backups are not automatically pruned. A portable ZIP contains readable Markdown, table records as JSON, every binary attachment/import blob, and a manifest with SHA-256 hashes. Agent credential hashes are redacted from exports. Exported `imports/*.html` files are the original untrusted HTML: inspect them as text and never open them in a browser. Export is an interchange/archive format; restore takes a SQLite backup, not the ZIP. CLI export refuses to overwrite an existing file.

Restore is offline: it acquires the server's exclusive OS lock, reads the candidate without modifying it, checks SQLite integrity, foreign keys, supported schema version, all required tables, and the exact trusted schema definitions (including constraints and indexes), stages a separate replacement, revokes all restored agent credentials, and snapshots the current database before an atomic replacement. Extra triggers or altered definitions are rejected. A corrupt or incompatible candidate leaves the active database intact. An active managed server or busy SQLite writer blocks restore. After restore, issue new agent credentials in Settings. The backup's settings come back with it, including whether agent proposals need your approval, so check that choice there too. Never replace a running SQLite file by copying it or deleting its journal sidecar.

A damaged active database does not prevent recovery from a valid backup. Before probing or replacing it, offline restore preserves the original main file and any WAL, SHM, or journal sidecars byte-for-byte in a hashed `worklog-pre-restore-raw-*.zip` evidence archive. A healthy original also receives a normal SQLite pre-restore backup. Raw evidence is not itself a valid SQLite restore candidate; retain it for investigation. Sidecars are moved aside only during final replacement and restored if replacement fails. These evidence archives are not automatically pruned.

Every restore creates a new data generation identity while staging the replacement, in addition to revoking restored credentials. Browser drafts and queued edits are scoped to that identity, so an edit based on the previous database cannot silently target a restored record whose ID and revision happen to match. The candidate backup remains unchanged. The next start records the new identity and creates a fresh automatic snapshot for that generation.

Restore writes a durable `.restore-in-progress.json` marker before moving sidecars or replacing the main file. Startup refuses that directory if a power failure interrupts restoration. Keep the evidence archives and rerun the explicit offline `restore` command with a verified SQLite candidate; the retry preserves earlier marker/quarantine evidence before completing recovery. Do not delete the marker to force a startup. It clears only after verified replacement and cleanup, or a verified rollback to the original files.

For an upgrade, stop Worklog, make a manual backup with the installed version, update the checkout, run `Setup.ps1`, then start and check the application. Use the same data directory and port throughout. The first startup snapshots a supported version 1 to 16 database and upgrades it to version 17 in one transaction. Version 17 adds agent ties: which records each agent is tied to, with its role on each, and whether you or the agent set the tie. It starts with none and changes no record. Version 16 records which work items and roadmaps each approved proposal was applied to, so their pages list those proposals in order. The upgrade fills that in for the proposals already approved, from their changes, and changes no record. A work item or roadmap an agent's proposal created is found from its history. One a proposal you wrote yourself created isn't, since you also create them directly. Version 15 adds Waiting to a work item's statuses, for work that waits on someone else; tickets and roadmap steps keep Open, Doing, Blocked and Done, and the upgrade changes no record. Version 14 lets you put each work item's tasks in your own order: every task keeps the place it was listed in, and no task counts as changed by the upgrade. Version 13 binds chats to agent identities: what you write into a chat reaches an agent that registered with that chat, and any other agent whose run reports the chat's ID only once you confirm it on the chat page. A message still waiting for an agent bound to neither ends at the upgrade, as not confirmed. Version 12 copies state that Worklog read from event history into columns: who registered each agent identity and the chat it registered with, when each proposal was last submitted and whether automatic approval applied it, and which stale-plan notices were sent. The events themselves stay as they are. Version 11 adds only derived data: a key for each search entry, so a save no longer reads the whole search index; indexes for frequent lookups; and a date on each retry receipt, so receipts older than 14 days are pruned at startup and every hour. Existing work, roadmap questions and decisions, discussions, data identity, and agent credentials are preserved, every existing agent identity receives a generated avatar, chats start with no messages from you, and the ticket and resource lists start empty. Repositories are keyed by remote: when several records already name one remote, all are kept and every one but the oldest active record is marked as its duplicate. Each existing repository takes its registrant from its history and counts as reviewed. Each work item's active notes become one scratchpad: they fold into its oldest note, oldest first, each title as a `## heading`, and the others are archived whole, so a scratchpad that would pass the 1,000,000-byte note limit keeps what fits and says how many notes stay only in the archive. Existing chat repository labels remain labels; link them to catalog repositories explicitly. Each person's "Title or team" becomes their team, with the new title, description and contact details empty, and their ties to work and roadmaps stay as they were. An agent's proposal that is still open and sets that field is carried over to the team, under a new revision, so its agent reads it again before changing it. `./scripts/Run.ps1` does the stop, backup, setup and start in one step after you update the checkout, passing the same `-DataDir` and `-Port` to each. Its backup is taken before the new version first starts, so it is the pre-upgrade backup; if the stop or backup fails, it ends before setup. It runs Setup's shared-folder check first, so a refusal leaves Worklog running.

Keep the previous version and its manual backup until verification succeeds. To roll back, stop Worklog, switch to the previous version and run its setup, then use that version’s `restore` command with the pre-upgrade backup. Start and check Worklog again. Restoring returns the data to the time of the snapshot, creates a new data identity, and revokes the restored credentials.

From version 17 to version 16, a way back keeps every record but the agent ties, which version 16 doesn't have. Each tie's history stays in the activity records of its agent and its record, and upgrading again starts with no ties. Export first to keep a readable copy (`records/agent_links.json`). With Worklog stopped and after a manual backup, run this on the database, stopping at the first error (the `sqlite3` shell's `-bail`), then start the version 16 checkout:

```sql
BEGIN;
DROP TABLE agent_links;
PRAGMA user_version=16;
COMMIT;
```

From version 16 to version 15, after the way back above when you start from version 17, a way back keeps every record. It drops only the list of what each approved proposal was applied to, which version 15 doesn't have. Upgrading again fills that list in from the approved proposals, those approved under version 15 included, as the upgrade above describes. With Worklog stopped and after a manual backup, run this on the database, stopping at the first error (the `sqlite3` shell's `-bail`), then start the version 15 checkout:

```sql
BEGIN;
DROP TABLE proposal_targets;
PRAGMA user_version=15;
COMMIT;
```

From version 15 to version 14, after the way back above when you start from version 16, a way back keeps everything but the Waiting status: work that waits goes back to Blocked, and its activity records still show when it started waiting. Version 14 refuses to approve an open agent proposal that sets work waiting; reject it, or have its agent withdraw or change it. With Worklog stopped and after a manual backup, run this on the database, stopping at the first error (the `sqlite3` shell's `-bail`), then start the version 14 checkout. It runs as one transaction whose COMMIT refuses while a reference to a work item would dangle, so with `-bail` a failure changes nothing:

```sql
PRAGMA foreign_keys=ON;
BEGIN;
PRAGMA defer_foreign_keys=ON;
UPDATE work_items SET status='blocked' WHERE status='waiting';
CREATE TEMP TABLE work_items_v15 AS SELECT rowid AS kept_rowid,* FROM work_items;
DROP TABLE work_items;
CREATE TABLE work_items(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','doing','blocked','done')),ado_url TEXT,step_id TEXT REFERENCES steps(id),backlog_order INTEGER NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, roadmap_id TEXT REFERENCES roadmaps(id));
INSERT INTO work_items(rowid,id,title,description,status,ado_url,step_id,backlog_order,archived,revision,created_at,updated_at,roadmap_id) SELECT kept_rowid,id,title,description,status,ado_url,step_id,backlog_order,archived,revision,created_at,updated_at,roadmap_id FROM temp.work_items_v15 ORDER BY kept_rowid;
DROP TABLE temp.work_items_v15;
CREATE INDEX work_items_step ON work_items(step_id);
CREATE INDEX work_items_roadmap ON work_items(roadmap_id);
PRAGMA user_version=14;
COMMIT;
```

From version 14 to version 13, after the way back above when you start from version 15, a way back keeps everything but the order you gave each work item's tasks: version 13 lists them by when they were added, and each move stays in the activity records. With Worklog stopped and after a manual backup, run this on the database with SQLite 3.35 or newer, stopping at the first error (the `sqlite3` shell's `-bail`), then start the version 13 checkout:

```sql
BEGIN;
ALTER TABLE tasks DROP COLUMN position;
PRAGMA user_version=13;
COMMIT;
```

From version 13 to version 12, after the way back above when you start from version 14, a way back drops only the agents you confirmed in chats. Version 12 has no confirmations: it delivers to every open run that reports a chat's ID. After upgrading again, confirm those agents on their chat pages. A message version 13 didn't deliver because its agent wasn't confirmed shows as plainly expired, since version 12 has no such reason. With Worklog stopped and after a manual backup, run this on the database, stopping at the first error (the `sqlite3` shell's `-bail`), then start the version 12 checkout:

```sql
BEGIN;
UPDATE chat_message_deliveries SET expire_reason=NULL WHERE expire_reason='unconfirmed';
DROP TABLE chat_confirmations;
PRAGMA user_version=12;
COMMIT;
```

From version 12 to version 11, after the way back above when you start from version 13, a way back keeps everything: version 11 reads the same state from the events, which version 12 leaves in place. With Worklog stopped and after a manual backup, run this on the database with SQLite 3.35 or newer, stopping at the first error (the `sqlite3` shell's `-bail`), then start the version 11 checkout:

```sql
BEGIN;
DROP TABLE proposal_stale_notices;
ALTER TABLE proposals DROP COLUMN auto_approved;
ALTER TABLE proposals DROP COLUMN submitted_at;
ALTER TABLE communication_credentials DROP COLUMN chat_id;
ALTER TABLE communication_credentials DROP COLUMN registered_by_label;
ALTER TABLE communication_credentials DROP COLUMN registered_by;
PRAGMA user_version=11;
COMMIT;
```

From version 11 to version 10, after the way back above when you start from version 12, a way back keeps everything: version 11 adds only search keys, lookup indexes and receipt dates, which the next upgrade builds again. With Worklog stopped and after a manual backup, run this on the database with SQLite 3.35 or newer, stopping at the first error (the `sqlite3` shell's `-bail`), then start the version 10 checkout:

```sql
BEGIN;
DROP TABLE search_keys;
DROP INDEX tasks_item;
DROP INDEX file_references_item;
DROP INDEX work_items_step;
DROP INDEX work_items_roadmap;
DROP INDEX github_refs_chat;
DROP INDEX steps_parent;
DROP INDEX receipts_created;
ALTER TABLE receipts DROP COLUMN created_at;
PRAGMA user_version=10;
COMMIT;
```

From version 10 to version 9, after the way back above when you start from version 11, a way back keeps everything but people's titles, descriptions and contact details and their ties to repositories. Each person's team goes back to "Title or team". Export first to keep a readable copy (`records/people.json` and `records/person_links.json`). Version 9 can neither approve nor edit an open agent proposal that sets the team or one of the new fields; reject it, or have its agent withdraw it. With Worklog stopped and after a manual backup, run this on the database with SQLite 3.35 or newer, stopping at the first error (the `sqlite3` shell's `-bail`), then start the version 9 checkout. It runs as one transaction, so a statement that fails changes nothing:

```sql
BEGIN;
DELETE FROM person_links WHERE target_type='repository';
ALTER TABLE person_links RENAME TO person_links_v10;
CREATE TABLE person_links(id TEXT PRIMARY KEY,person_id TEXT NOT NULL REFERENCES people(id),target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap')),target_id TEXT NOT NULL,relationship TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(person_id,target_type,target_id));
INSERT INTO person_links SELECT * FROM person_links_v10;
DROP TABLE person_links_v10;
CREATE INDEX person_links_target ON person_links(target_type,target_id);
CREATE INDEX person_links_person ON person_links(person_id);
ALTER TABLE people DROP COLUMN title;
ALTER TABLE people DROP COLUMN description;
ALTER TABLE people DROP COLUMN email;
ALTER TABLE people DROP COLUMN phone;
ALTER TABLE people DROP COLUMN chat;
ALTER TABLE people DROP COLUMN location;
ALTER TABLE people RENAME COLUMN team TO role;
UPDATE search_index SET body=(SELECT role || char(10) || notes FROM people WHERE people.id=search_index.entity_id) WHERE type='person';
PRAGMA user_version=9;
COMMIT;
```

From version 9 to version 8, after the way back above when you start from version 10, a way back keeps everything but your resources: version 9 is version 8 plus the resources table. Export first to keep a readable copy of them (`records/resources.json`); their history, with each saved version of every page, stays in the activity records. With Worklog stopped and after a manual backup, run `DELETE FROM search_index WHERE type='resource'; DROP TABLE resources; PRAGMA user_version=8;` on the database, then start the version 8 checkout. From version 8 to version 7, there is also a way back that keeps every later change, after the ones above when you start from a later version: version 8 is version 7 plus one index, and its folded notes are valid version 7 data. With Worklog stopped and after a manual backup, run `DROP INDEX notes_item_scratchpad; PRAGMA user_version=7;` on the database, then start the version 7 checkout. Its items keep one note each until you add more.

Local backups protect against mistakes and some corruption, **not device loss**. Periodically copy completed backup files or exports to another protected device/location according to your retention needs. Treat archives as sensitive personal work data.

## Security and operating limits

The threat model is **one trusted OS account**. Browser sessions and agent bearer scopes protect application paths, and loopback, origin/CSRF checks, request limits, and attachment download behavior reduce exposure to other websites and imported content. Imported HTML is parsed into reviewable plain content; original files are downloaded as attachments, never executed as a page. Browser UI renders Markdown as text/content without enabling arbitrary HTML execution. Attachment names and copied local path references do not authorize filesystem execution.

The Chats page reads, and never changes, the transcripts Claude Code (`~/.claude/projects`, plus the Claude desktop app's session records) and Codex (`~/.codex/sessions`) keep for the chat IDs you track; set `CLAUDE_CONFIG_DIR` or `CODEX_HOME` if those tools keep them elsewhere. Agent credentials cannot read them through the API, since a transcript holds a whole conversation; as with the rest of Worklog, that is not a boundary against programs running as your OS user. A chat ID selects a transcript only in the UUID shape those tools use, never as a path. This is not adversarial isolation between programs running as your OS user: such a program can read/modify the database and files, inspect process environment, or alter the application. The OS account, filesystem ACLs, device security, Python environment, and local backups remain trusted. Do not expose port 8877 to a network, reverse proxy, tunnel, or shared account. The application has no multi-user authentication or end-to-end encryption. A stopped server is not accessible through the agent HTTP API.

## Development checks

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

### Documentation

`docs/GUIDE.md`, which is also the in-app Guide page, and `docs/AGENT-GUIDE.md`, which `worklog agent guide` prints, describe Worklog for people and for agents. A change to a page, a label, a command or flag, a proposal operation, an error code or a limit updates the matching guide in the same pull request. `backend/tests/test_guides.py` and `frontend/src/Guide.test.tsx` fail when an agent command, proposal operation, error code or sidebar page is missing from its guide, when a documented command no longer parses, or when a guide quotes interface text the frontend no longer has. In both guides, straight double quotes are reserved for exact interface text. The agent guide is ASCII, and the human guide links only to its own headings, because file links would not open in the app.
