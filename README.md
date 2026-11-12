# Worklog

Worklog is a personal workbench that runs entirely on your own machine. It keeps your daily plan and what you actually did, a backlog of work with its notes and files, structured roadmaps, and the people involved. It is also where you coordinate the AI coding agents you work with: they report what they are doing, talk with you and each other, and propose changes that you approve before anything in your records changes, unless you turn approval off.

Worklog runs at **http://127.0.0.1:8877** from one local SQLite database. No hosted service or internet connection is needed once it is installed.

## What Worklog does

- **Plan and record your days.** Plan what you will work on and mark what you actually worked on with an outcome and next action.
- **Keep one backlog, stack ranked.** Work items carry a status, a stack rank, a Markdown description, a scratchpad, files, links, local paths, an Azure DevOps link, GitHub pull requests and commits, people and repositories, along with their daily record and full history.
- **Track issues as tickets.** You and your agents file tickets for bugs, incidents and tasks, work them with a status, assignee and comments, and close them with a resolution. Closed tickets stay searchable, so the next agent finds what fixed an issue last time.
- **Plan bigger efforts as roadmaps.** Roadmaps nest under each other and hold step trees with progress, immutable versions, questions and decisions.
- **Know who is involved.** A people directory keeps each person's title, team, description, contact details and notes, and ties them to work items, roadmaps and repositories, with their role on each.
- **Keep reference pages.** Resources are Markdown pages you write once for yourself and your agents, such as how you like work done, conventions and runbooks. You read them on the Resources page, agents read them from the command line, and they change them only through proposals.
- **Work with AI agents under your control.** Claude Code, Codex, or any agent you give a credential can read your context, report what it is working on and where, talk with you and other agents, take and hand off responsibilities, file and close tickets, and propose changes that only you can approve, unless you turn approval off.
- **See who is working on what.** Chats shows the conversations Claude Code and Codex keep for the chats you track, and offers the Claude Code sessions running on this machine that nothing tracks yet, to tie to a work item or roadmap. A chat names its agents: those registered with it, then those whose runs report it. Agents is a rolodex with a card for each agent, naming the repositories and roadmaps its work took it to; each roadmap lists those agents in turn, and Repositories shows reported activity per repository.
- **Keep it safe and portable.** Automatic and manual backups, a readable ZIP export, and an offline restore that preserves your current data first.

## How it works

- **One local process.** `start` launches one hidden server (Python with FastAPI and Uvicorn) bound to 127.0.0.1. It serves the built React interface and the JSON API under `/api/v1` from the same address. Nothing listens beyond this machine, no Node process runs, and nothing is fetched from the internet at runtime.
- **One database.** Every record and attachment lives in one SQLite file, `worklog.sqlite3`, in the data directory, so a backup holds everything at once. Schema upgrades run at startup, after a snapshot.
- **You and your agents.** Your browser holds a local session and can change anything. Agents use credentials you issue in Settings, or identities they register for themselves from one, through the `worklog agent` command line. They read your context, report their own runs and activity, talk in Discussions, file and work tickets, and propose changes for you to approve. They never write your work directly: tickets, like discussions, are shared records agents write themselves, while work items, days, roadmaps, people, repositories and resources, your reference pages, change only through proposals you approve. One proposal can create several records and connect them, such as a person, the repository they own and a ticket against it, by naming each new record for the changes after it.
- **No silent overwrites.** Every record has a revision. A save based on an older revision is refused as a conflict, and your draft is kept until you decide. Each change is recorded in the record's history with who made it, including changes applied from an agent's proposal. Each work item and roadmap also lists the proposals applied to it, numbered in the order they were applied, and each agent's page the proposals it made.
- **Agent transcripts stay where they are.** Chats reads the transcripts Claude Code and Codex keep on this machine, and the record Claude Code keeps of each session while it runs, and never writes them. Agents cannot read them through Worklog.
- **Snapshots happen on their own.** A backup is taken at startup and once a day while Worklog runs, and before any upgrade or restore.

## Install from source on Windows

Install Python **3.12 or newer** and a current Node.js LTS with npm. Node is needed only to install/build the frontend, not to run the installed application. From PowerShell in this checkout:

```powershell
./scripts/Setup.ps1
./scripts/Worklog.ps1 start
./scripts/Worklog.ps1 check
```

Setup creates `.venv`, installs the exact versions in `requirements.lock`, what Worklog needs to run, checking each file against the hash the lock records, installs this project without resolving dependencies again, then runs `npm ci` using `frontend/package-lock.json` and builds `frontend/dist`. It needs internet access to fetch packages. `./scripts/Setup.ps1 -Dev` installs `requirements-dev.lock` instead, which adds the tools the checks run (pytest, ruff and mypy). pip removes nothing, so a `.venv` that has those tools keeps them: to have only what Worklog runs on, delete `.venv` and run Setup again. If Python has a different executable name, use `./scripts/Setup.ps1 -PythonCommand python3.12`. Respect your organization's PowerShell script policy; do not disable it globally.

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

Data defaults to **`%LOCALAPPDATA%/Worklog`**, outside the checkout. The SQLite database (`worklog.sqlite3`) includes attachment bytes. Backups, usage counts, log, lock, and launcher state files live alongside it. Do not put a live database in a network share or cloud-sync folder. Choose a data directory consistently for every command:

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
- **[docs/AGENT-GUIDE.md](docs/AGENT-GUIDE.md)** is the contract for AI agents: the rules they follow, every `worklog agent` command, the proposal operations, every error code with the response it needs, and the limits. Agents print it with `./.venv/Scripts/worklog.exe agent guide`, which needs no credential and no running Worklog. The **Guide** page shows it too, under "For agents".
- **[docs/API.md](docs/API.md)** is the HTTP contract, and `docs/openapi.json` its generated schema.

## Working with agents

Create a labeled credential in Settings and give its one-time token to your agents through the `WORKLOG_AGENT_TOKEN` environment variable, never in a command argument, file, log or shell history. Then tell each agent to run `worklog agent guide` before it uses Worklog. Agents register an identity of their own for each chat, report their runs and repository activity, put their own chat on the work they do, write tickets and discussions, and change your work, days, roadmaps, people, repositories and resources only through proposals you approve on Review, unless you turn approval off. When a chat ends before its work does, the agent's next chat continues the same identity, keeping its roles and taking over the old chat's place on the work and its steps. The guide's *Bring an AI agent in* walks through it. Revoke a credential in Settings when its agents no longer need access.

## Back up, upgrade and develop

Worklog backs itself up when it starts and once a day while it runs. Three more documents cover the rest:

- **[docs/OPERATIONS.md](docs/OPERATIONS.md)**: backing up, exporting, restoring, compacting and checking the database, seeing which features get used, and the security and operating limits. Worklog trusts one OS account and listens only on this machine.
- **[docs/UPGRADING.md](docs/UPGRADING.md)**: how to upgrade, what each version changes, and the way back.
- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**: the checks to run, how the dependencies are locked, and what to update in the guides when the app changes.
