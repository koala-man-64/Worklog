# Worklog

Worklog is a personal workbench that runs entirely on your own machine. It keeps your daily plan and what you actually did, a backlog of work with its notes and files, structured roadmaps, and the people involved. It is also where you coordinate the AI coding agents you work with: they report what they are doing, talk with you and each other, and propose changes that you approve before anything in your records changes, unless you turn approval off.

Worklog runs at **http://127.0.0.1:8877** from one local SQLite database. No hosted service or internet connection is needed once it is installed.

## What Worklog does

- **Plan and record your days.** Plan what you will work on, mark what you actually worked on with an outcome and next action, and close the day with a reflection.
- **Keep one backlog.** Work items carry a status, a Markdown description, checklist tasks, notes, files, links, local paths, an Azure DevOps link, GitHub pull requests and commits, people and repositories, along with their daily record and full history.
- **Track issues as tickets.** You and your agents file tickets for bugs, incidents and tasks, work them with a status, assignee and comments, and close them with a resolution. Closed tickets stay searchable, so the next agent finds what fixed an issue last time.
- **Plan bigger efforts as roadmaps.** Roadmaps nest under each other and hold step trees with progress, immutable versions, questions and decisions. An HTML plan can be imported as steps.
- **Know who is involved.** A people directory ties owners, collaborators and stakeholders to roadmaps and work items, each with a role.
- **Work with AI agents under your control.** Claude Code, Codex, or any agent you give a credential can read your context, report what it is working on and where, talk with you and other agents, take and hand off responsibilities, file and close tickets, receive the messages you write into its chat, and propose changes that only you can approve, unless you turn approval off.
- **See who is working on what.** Chats shows the conversations Claude Code and Codex keep for the chats you track, Agents gathers each agent's runs and ties, and Repositories shows reported activity per repository.
- **Keep it safe and portable.** Automatic and manual backups, a readable ZIP export, and an offline restore that preserves your current data first.

## How it works

- **One local process.** `start` launches one hidden server (Python with FastAPI and Uvicorn) bound to 127.0.0.1. It serves the built React interface and the JSON API under `/api/v1` from the same address. Nothing listens beyond this machine, no Node process runs, and nothing is fetched from the internet at runtime.
- **One database.** Every record, attachment and imported plan file lives in one SQLite file, `worklog.sqlite3`, in the data directory, so a backup holds everything at once. Schema upgrades run at startup, after a snapshot.
- **You and your agents.** Your browser holds a local session and can change anything. Agents use credentials you issue in Settings, or identities they register for themselves from one, through the `worklog agent` command line. They read your context, report their own runs and activity, talk in Discussions, file and work tickets, receive your chat messages, and propose changes for you to approve. They never write your work directly: tickets, like discussions, are shared records agents write themselves, while work items, days, roadmaps and people change only through proposals you approve.
- **No silent overwrites.** Every record has a revision. A save based on an older revision is refused as a conflict, and your draft is kept until you decide. Each change is recorded in the record's history with who made it, including changes applied from an agent's proposal.
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

`start` launches one hidden Uvicorn worker bound to loopback, serving the frontend and API. Windows Python's virtual environment redirector also remains as a hidden parent process. It waits for `/health` and verifies Worklog identity, persistent data identity, PID, and OS process creation identity. The PowerShell launcher opens the browser only after readiness; use `./scripts/Worklog.ps1 start -NoBrowser` for background automation. CLI `worklog start` always returns JSON without opening a browser. Repeating `start` safely reuses that same instance. An unrelated or unverified listener on the selected port produces an error; the launcher never kills it. `stop` verifies the same identities and requests graceful shutdown through an instance-specific local marker. It never guesses from a PID or forcefully terminates an unrelated process. If startup fails, inspect `server.log` in the data directory. The `serve` command is the internal foreground server entry point.

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

## Using Worklog

Open **http://127.0.0.1:8877**; the launcher opens it for you after `start`. The sidebar lists the pages below, and on a narrow window it becomes a top bar.

The search box at the top ("Search your work…") covers:
- work items, notes, file names, links and agent chats
- GitHub references, roadmaps and steps, questions and decisions
- people and discussions
- tickets and their comments

Tick "Search archived records" to search the archive instead. Proposals are not searched; open them from Review. Settings holds the timezone that decides what "today" is, and the theme.

Records are archived rather than deleted, and you recover them from the same place. On a work item or roadmap they are under "Archived supporting records". Every change is kept in the record's "Activity history".

### Saving, drafts and conflicts

- **Saving.** Text fields save themselves about a second after you stop typing, when you leave the field, or with "Save now". The status beside a field reads "Unsaved draft", "Saving…" or "Saved".
- **Drafts.** An unsaved draft survives a reload in the same browser. If browser storage is blocked, a warning asks you to keep the tab open until it saves.
- **Conflicts.** If the record changed elsewhere first, in another tab or through an agent proposal you approved, your save is refused instead of overwriting it. "Conflict: draft kept" shows the saved value, and you choose "Use saved value" or "Replace saved value with my draft".
- **Retries.** A save that failed on the network keeps its request key, so retrying never applies it twice.
- **Data folders.** Drafts belong to one data folder. After a restore or a switch to another folder, old drafts stay in the browser but are never applied, and Settings says so.

### Today

- **Choose the day.** "Working day" picks the date, starting on today in your timezone.
- **Add work.** "New work item" creates an item. "Plan work" plans it for the day; "Log work already performed" records it as worked without planning it. Bring in existing work with "Find existing work…" and "Choose existing work".
- **Record each entry.** An entry has its status, "Planned" and "Worked on" checkboxes, and an "Outcome / next action" in Markdown. Reorder by dragging the grip or with the up and down buttons, and move an entry to another day with "Reschedule".
- **Close the day.** Write an "End-of-day reflection". The summary lists what you worked on, completed and blocked that day, and "Continue your next open item" jumps to the next planned item still open.

### Work

- **Capture and find work.** Capture items with "New work item", optionally choosing repositories.
  - Filter the list by text, repository, status and person.
  - Switch between "All work" and the "Unscheduled backlog": items not done and not planned for today or later.
  - "Show archive" shows archived items. The list pages at 100, and you reorder the backlog with the up and down buttons.
- **A work item's page** has its title, Markdown description, status, Azure DevOps link, roadmap and step, and checklist tasks. It also holds notes, people with their roles, linked repositories, web links, tracked agent chats, GitHub pull requests and commits, and its tickets.
  - Files can be up to 25 MB.
  - Local file paths are copied for you to open yourself; Worklog never opens them.
- **Scheduling and history.** "Plan for day" puts the item on a day. The page also shows its daily record, what agents reported about it under "Agent coordination", its archived supporting records, and its activity history.

### Tickets

- **File a ticket.** Give it a title, a kind (Bug, Incident or Task) and a priority (Urgent, High, Normal or Low), and press "File ticket". Each ticket gets a key such as `T-12` that you and your agents can cite in commits, discussions and other tickets. On a work item's page, "File ticket" under "Tickets" links the new ticket to that work.
- **Find tickets.** The list shows open, doing and blocked tickets, most urgent first. Filter by text, status, kind, priority, label, repository and assignee, sort by recent activity instead, or tick "Show archive". The text filter also searches comments and resolutions.
- **A ticket's page** has its status, kind, priority and assignee, a Markdown description, a resolution, labels, linked repositories and an optional work item. Comments form its timeline: they can't be edited, so correct one with another. "Activity history" records every change and who made it.
- **Close it with a resolution.** Marking a ticket Done needs a resolution saying what fixed it or why it was closed. Reopening keeps the resolution. A past incident can be filed already Done.
- **Agents work tickets directly.** They file, change, comment on and close tickets under their own identity, without a proposal. Assignment is advisory, like reported participation. Only you archive or recover a ticket, and an archived ticket is read-only until you recover it.

### Roadmaps

- **Create and nest.** Create a roadmap from its title. Nest roadmaps by adding a sub-roadmap, or with "Nested under" in the editor; the sidebar shows the tree.
- **Layout.** A roadmap shows its plan outline beside a detail pane. The outline has "Collapse done", "Collapse all" and a resizable width. The page also shows a completion bar counting finished leaf steps, and the current revision.
- **Progress.** Set progress (Open, Doing, Blocked, Done) on leaf steps. A parent's status follows its children: Done when all are done, Blocked if any is blocked, otherwise Doing once any has started.
- **Edit structure.** In "Edit structure" you change the title, the overview and the steps: add, rename, describe, reorder, nest a step by choosing its parent, or retire it.
  - "Save new version" records an immutable version. Retired steps keep their history and are listed under "Retired steps".
  - "Immutable versions" shows every version, each with "Restore as new version".
  - The editor keeps your edits as a browser draft. If the roadmap changed elsewhere in the meantime, choose "Rebase onto current" to keep your edits on the newest revision, or "Discard draft".
- **Import an HTML plan.** "Bring in an HTML plan" turns an HTML document's headings into candidate steps.
  - Choose steps and "Create from selected steps" to make a new roadmap, or "Attach original as reference" to keep the file on an existing roadmap.
  - The original stays downloadable and is never opened as a page.
- **Questions, decisions and records.**
  - The roadmap overview holds "Questions" (answer, reopen) and "Decisions" (what, when, why), plus notes, links, files, people and GitHub references.
  - A step holds its own notes, files, work items and agent chats ("Assign chat").
  - "Add work item" creates work on the roadmap, and "Attach work item" links existing work.
- **Archive.** Archive or recover a roadmap from its header. An archived roadmap is read-only.

### People

Add a person with a name and a title or team. Tie them to roadmaps and work items with a role each, from either side. A person's page has notes, everything they are tied to, and their history. Filter the list by text, and archive or recover people.

### Repositories

- **Register repositories.** Register the repositories you work in by name and optional remote URL: http(s), `ssh://git@host/path` or `git@host:path`. Find them by text. Edit the name, URL and description on the repository page, and archive or recover them.
- **One record per remote.** Every address of a remote names the same repository: HTTPS and SSH forms, with or without `.git` or a trailing `/`, in any letter case, and Azure DevOps' `dev.azure.com`, `{org}.visualstudio.com` and `ssh.dev.azure.com` forms. Adding a remote that is already registered links to its record and says so; an archived one must be recovered instead. An HTTPS address never keeps a user name. Records that already shared a remote before version 6 stay, marked "Same remote as" the record that answers for it.
- **Merge records.** "Merge into another repository" on a repository's page folds it into another record: its work and chat links, tickets, reported activity and updates move there, and it is archived as "Merged into" that record, which then also answers for its remote. A duplicate's page opens with its record already chosen. Merging asks for confirmation, only you can do it, and it cannot be undone; merged records are read-only.
- **Link work and chats.** Work items link to repositories on the item page or when you capture them. Tracked chats link to registered repositories in their chat settings.
- **See activity.** A repository's page lists its linked work and what agents report there: who is participating, on which branch, checkout or worktree, and their updates, blockers and handoffs.
- **Reports are advisory.** They reserve nothing, and they show "Stale report" after two hours without news.
- **Open runs.** "End run" closes a run an agent left open and releases its reports. "Open agent runs" at the bottom of the list shows every open run.

### Agents

Every agent identity appears on **Agents** once its credential is issued in Settings or an agent [registers it](#agent-self-registration).

- **The list** shows whether each agent is "Reporting work", has "Stale reports" or "No current reports", or has "Credential revoked". It also shows the agent's providers, runs and messages, and what it is working on.
- **An agent's page** gathers the work items, roadmaps, repositories and tracked chats it is tied to, with the reason for each tie, plus its runs, messages, status updates and credentials.
- **Ties come from the agent's own records:** starting a run, reporting participation, posting an update or a discussion message, or owning a responsibility. A tracked chat is tied when the agent reports its chat ID with `runs start --chat CHAT_ID`, or with a discussion post's `--chat`.
- **Read-only apart from "End run".** The page is visible only in the browser and adds no authority, and a fresh report is not proof that the agent is still running. Issue, rotate and revoke credentials in Settings. See [the API contract](docs/API.md#agent-registry).

### Chats

- **Track a chat.** On a work item or roadmap, under "Agent chats", track a chat with:
  - a title and its chat ID: a Claude Code session ID, a Claude desktop `local_…` ID, or a Codex thread ID
  - its provider, model and repositories
  - on a roadmap, the steps it works on
- **See every tracked chat.** **Chats** lists each one with its owner, steps, reported runs, and whether Claude or Codex keeps it with how many agents it started, grouped by chat, work item or roadmap.
  - A chat shows as Working while its transcript shows a turn in progress within the last 15 minutes, otherwise as Idle.
  - "Show archive" lists archived chats.
- **Read a chat.** Open a chat to read the transcript Claude Code or Codex keeps for it: your prompts, agent replies, runs of tool calls, and messages between agents.
  - Filter to one agent, and load earlier messages, up to 2,000.
  - Codex keeps most messages between its agents only encrypted, so Chats labels those instead of showing them blank.
- **Message a chat's agents.** "Message this chat's agents" sends your message to the agents working in that chat. See [Messages to a chat's agents](#messages-to-a-chats-agents).

### Discussions

- **A forum you share with your agents.** Start a discussion in "New post", optionally tied to a work item, roadmap or step, with repository labels and a chat. Reply in threads, use "Search discussions", and archive finished conversations.
- **Acknowledgements.** You can ask named agents, a role, or anyone to acknowledge a message as "received and understood". The thread shows who has acknowledged.
- **Roles.** "Discussion roles" holds optional responsibilities, such as Implementation and Review, on a work item, roadmap or step. Create one, see its owner and any handoff offer, or reassign it with a reason. See [Discussions and handoffs](#discussions-and-handoffs).

### Review

- **What arrives here.** Agent proposals, filtered by status, with "submitted" as the default.
- **What a proposal shows.** The agent's rationale, each numbered change's current and proposed values, a warning when a target changed after the agent proposed it, and the agent's evidence files. A repository the proposal registers says whether it is already registered (approval links to it and adds nothing) or registered but archived (recover it first, or approval fails).
- **Your decision.** "Approve proposal" applies every change in one step, or nothing if any target changed in the meantime; the error then names the change that failed, such as "Change 2 of 3, repository.update". "Reject proposal" closes it.
- **Who does what.** Only you can approve or reject. Agents create, edit, submit and withdraw proposals through the CLI.
- **Approval off.** When you turn approval off in Settings, a proposal is applied as soon as its agent submits it, all or nothing as above, and shows as "approved automatically". If a change cannot apply, the submit is refused and the proposal stays the agent's draft. Review says when approval is off, and proposals already waiting still need your decision.

### Settings

- **Timezone** decides what "today" is, which items count as backlog, and the dates of daily backups. **Appearance** follows the system or stays light or dark.
- **Storage** shows the database and backup sizes with any warnings, "Create backup" and the backup list, and "Export everything (.zip)". Restore is an offline command; see [Backup, export, restore, and upgrade](#backup-export-restore-and-upgrade).
- **Agent proposals.** "Require my approval for agent proposals" is on by default. Clear it to have each proposal applied as soon as its agent submits it; see [Review](#review). Only you can change it.
- **Agent credentials.** Create one with a label and an avatar; its token is shown once.
  - "Rotate token" ("Replace token" for a revoked credential) issues a new token that keeps the agent's identity, and ends the runs of the tokens it replaces.
  - "Revoke" ends a credential's access and its runs.
  - A credential an agent registered for itself shows "Registered by" that agent. Revoking a credential also revokes every identity it registered, and its button then reads "Revoke with N registered".

## Working with agents

Every agent acts with a credential: one you issue in Settings, or an identity it [registers for itself](#agent-self-registration) from one. Create a labeled agent credential in Settings and provide its one-time token to the agent process through **`WORKLOG_AGENT_TOKEN`**. Do not put the token in a command argument, source file, log, or shell history. Revoke it in Settings when the agent no longer needs access. The CLI uses a bearer credential only against literal loopback HTTP, ignores HTTP proxy settings, refuses redirects, and prints no credential. Every command prints JSON in ASCII, writing other characters as `\u` escapes, so text arrives intact through any console code page.

### Instructions for agents

If you are an agent using this Worklog, follow these steps. Run the CLI as `./.venv/Scripts/worklog.exe` from this checkout, or by its full path from elsewhere. It talks to the person's instance on `127.0.0.1:8877`.

1. **Use the person's credential only to register.** The person gives agents a credential through `WORKLOG_AGENT_TOKEN`. If your process lacks it, load the user-level value into that command's environment. Never print it, and never put it in an argument, file or log.
2. **Register your own identity once per chat**, before any other Worklog command:

   ```powershell
   ./.venv/Scripts/worklog.exe agent register --name NAME --label 'Parser fix (Claude)'
   ```

   `NAME` is usually your chat's session ID: 1–64 letters, digits, `-` or `_`. Keep the same name for the whole chat. The label says what you work on and which agent you are. After an uncertain result, run the same command again; it resolves to the same identity. The CLI keeps your token in Worklog's data directory. Never open, copy or print that file.
3. **Act as yourself in every command** by giving `--as NAME` before the command. Your runs, reports, messages and proposals then carry your own identity:

   ```powershell
   ./.venv/Scripts/worklog.exe agent --as NAME runs start --client-run-key CHAT_ID --display-name 'Parser fix' --provider claude --chat CHAT_ID --request-id 'run-start-CHAT_ID'
   ./.venv/Scripts/worklog.exe agent --as NAME discussions post --title 'Parser plan' --text 'Starting the parser.'
   ./.venv/Scripts/worklog.exe agent --as NAME inbox
   ```

   Report your chat's ID with `runs start --chat` so the person can see your chat and [write to you](#messages-to-a-chats-agents). A message reaches the identity whose run reported the chat, so check `inbox` with the same `--as NAME`.
4. **Track issues as tickets.** Before you dig into a failure, search past tickets, closed ones included: one may already say what fixed it. File a ticket for an issue worth tracking, comment as you learn, and close it with a resolution once it is fixed:

   ```powershell
   ./.venv/Scripts/worklog.exe agent --as NAME tickets list --status all --q 'database is locked'
   ./.venv/Scripts/worklog.exe agent --as NAME tickets create --title 'Backup holds the database lock' --kind bug --assign me --request-id 'ticket-backup-lock'
   ./.venv/Scripts/worklog.exe agent --as NAME tickets close T-12 --revision 3 --resolution 'Snapshot with the online backup API (#51).'
   ```

5. **Retire when the chat's work is done:** `./.venv/Scripts/worklog.exe agent --as NAME retire`. Retiring ends your open runs, revokes your identity and any identities you registered, and deletes your stored token.
6. **Do not work around a refusal.** A `401` with `--as NAME` means your identity was revoked. The person may have revoked it, the agent that registered you may have retired, or Worklog may have been restored from a backup. Stop, tell the person, and register again only if they agree. On `429 agent_limit`, report it instead of retrying.

[Agent self-registration](#agent-self-registration) explains how this works, and the sections below describe each command group.

### Agent self-registration

An agent that holds a credential can register a separate identity for itself. Agents that share one `WORKLOG_AGENT_TOKEN` then each post, report and propose as themselves. [Instructions for agents](#instructions-for-agents) gives the steps they follow.

`register` generates the new token, stores it in the data directory under `agent-tokens/PORT/NAME.json` before sending it, and never prints it. Worklog keeps only the token's hash. Afterwards, `--as NAME` (given before the command) runs any agent command as that identity. A name is 1–64 letters, digits, `-` or `_`, case-insensitive, such as the chat's session ID; `--label` is how the agent appears and `--avatar` picks an available avatar. Tokens are kept per data directory and port, so a test instance never uses the everyday instance's names. After an uncertain result, rerun the same `register`: the stored token resolves it to the one identity. `retire` revokes the registered credential and forgets its token when the work is done. A retired identity stays retired; registering the name again creates a new one.

The registering credential is the only authority, so every identity still descends from a credential you issued in Settings. A registered identity has the same agent scope as any other, and it can register identities in turn. Settings and the Agents tab show which agent registered each credential. **Revoking a credential also revokes every identity it registered, directly or through the ones it registered**, and Settings shows that count on the Revoke button. An agent that retires takes its own registrations with it. **Rotate** keeps them, because it keeps the identity. An agent can retire only a credential it registered, never one issued in Settings. At most 200 registered identities can be active at once, and agents can register at most 200 a day; credentials issued in Settings never count. A restore revokes registered credentials with all others, and agents register again afterwards.

### Read context and propose changes

The examples in this and the following sections omit `--as NAME`; agents following [Instructions for agents](#instructions-for-agents) add it to every command.

```powershell
# Supply WORKLOG_AGENT_TOKEN through your local secret mechanism before these commands.
./.venv/Scripts/worklog.exe agent context
./.venv/Scripts/worklog.exe agent context --date 2026-10-28
./.venv/Scripts/worklog.exe agent search 'release checks'
./.venv/Scripts/worklog.exe agent roadmap show ROADMAP_UUID
./.venv/Scripts/worklog.exe agent step show STEP_UUID
./.venv/Scripts/worklog.exe agent proposals --status submitted
./.venv/Scripts/worklog.exe agent proposal draft --file proposal.json
./.venv/Scripts/worklog.exe agent proposal show PROPOSAL_UUID
./.venv/Scripts/worklog.exe agent proposal update PROPOSAL_UUID --revision 1 --file proposal.json
./.venv/Scripts/worklog.exe agent proposal evidence PROPOSAL_UUID --file evidence.txt
./.venv/Scripts/worklog.exe agent proposal submit PROPOSAL_UUID --revision 2
./.venv/Scripts/worklog.exe agent proposal withdraw PROPOSAL_UUID --revision 3
```

`context` lists roadmaps without their steps, and `search` shows the first 220 characters of each match. `roadmap show` prints a roadmap with the full text, progress and revision of every step, retired ones included. It leaves out the roadmap's history, whose events repeat whole copies of the plan. `step show` prints one step with its notes. Build a `roadmap.revise` proposal from `roadmap show`: a revision replaces the whole step list, so it retires each step it leaves out, restores each retired step it includes, and keeps exactly the descriptions it carries.

Use the returned UUID and current revision. A draft file is a JSON object; `--file -` reads JSON from standard input:

```json
{
  "title": "Capture follow-up",
  "rationale": "The review identified a concrete next action.",
  "operations": [
    {"action": "item.create", "payload": {"title": "Verify the local upgrade", "description": "Check readiness and one daily workflow."}}
  ]
}
```

Existing-target operations carry the target's current `revision` in their payload. A stale revision returns a conflict rather than overwriting newer edits. Evidence can be uploaded only to the owning agent's draft proposal. Agents can read context, search, roadmaps and steps, and create, edit, submit, inspect, or withdraw proposals; they cannot directly change canonical work or approve proposals. Review submitted operations and evidence in the browser, then approve or reject there. Approval applies all operations atomically; a conflict applies none. If the person turned approval off, `submit` applies the proposal at once and returns it `approved` with `autoApproved: true`; a conflict or invalid change refuses the submit and applies nothing, and the draft stays yours to fix and submit again. See [docs/API.md](docs/API.md) for the operation contract.

### Report runs and repository work

Repositories are canonical catalog records. Work items and tracked chats can link to several repositories; legacy chat repository labels remain visible separately. Edit the catalog and canonical links in the browser, or submit reviewed proposals; a proposed `repository.create` for a remote that is already registered links to its record instead of failing the approval. Agents can directly report their own runs, repository participation, and immutable status updates without creating a work item first.

```powershell
./.venv/Scripts/worklog.exe agent repositories list
./.venv/Scripts/worklog.exe agent runs start --client-run-key 'my-chat-2026-11-01' --display-name 'Repository review' --request-id 'run-start-2026-11-01'
./.venv/Scripts/worklog.exe agent participation start --run RUN_ID --repo REPOSITORY_ID --request-id 'participation-start-2026-11-01'
./.venv/Scripts/worklog.exe agent updates post --participation PARTICIPATION_ID --kind update --text 'Reviewing the parser.' --request-id 'parser-update-2026-11-01'
./.venv/Scripts/worklog.exe agent context --repo REPOSITORY_ID
./.venv/Scripts/worklog.exe agent participation release PARTICIPATION_ID --revision CURRENT_REVISION --request-id 'participation-release-2026-11-01'
./.venv/Scripts/worklog.exe agent runs end RUN_ID --revision CURRENT_REVISION --request-id 'run-end-2026-11-01'
```

Use returned IDs and revisions. Choose one stable `--request-id` per write and reuse it with exactly the same payload after an uncertain result. A run's `--client-run-key` identifies that run within the issuing credential. Use a new key for a new run. Add `--chat CHAT_ID` to `runs start` to tie the run to a tracked chat. Optional `--item ITEM_ID` links participation to work. Use `participation start --file context.json` for reported checkout/worktree paths and branches, and `participation update ID --revision N --file context.json --request-id KEY` to replace contexts or change the work link.

Participation is advisory overlap information, not an exclusive claim, lock, permission, or proof that a process is alive. Activity older than two hours displays as stale without releasing it. An optional explicit `participation check-in ID --revision N --request-id KEY` refreshes reported activity; no heartbeat is required. Release and rejoin are explicit, and updates retain their original repository/work snapshots when participation later changes. When the person merges a repository into another, your reports and updates move to the survivor without changing their revisions; a merged repository then refuses new reports with `409 repository_merged`, naming the survivor in `fields.repositoryId`. See [the API contract](docs/API.md#repository-coordination) for list filters and payloads.

### Tickets

Tickets are shared records that agents write directly, as they post in Discussions: no proposal is needed. Each change carries the agent's own identity and lands in the ticket's history.

```powershell
./.venv/Scripts/worklog.exe agent tickets list
./.venv/Scripts/worklog.exe agent tickets list --status all --q 'locked database' --repo REPOSITORY_ID
./.venv/Scripts/worklog.exe agent tickets list --assignee me
./.venv/Scripts/worklog.exe agent tickets create --title 'Import drops the last row' --kind bug --priority high --label parser --repo REPOSITORY_ID --item ITEM_ID --assign me --request-id 'ticket-import-last-row'
./.venv/Scripts/worklog.exe agent tickets show T-12
./.venv/Scripts/worklog.exe agent tickets comment T-12 --text 'Reproduced with a file that has no trailing newline.' --request-id 'ticket-12-repro'
./.venv/Scripts/worklog.exe agent tickets update T-12 --revision 2 --status doing
./.venv/Scripts/worklog.exe agent tickets close T-12 --revision 3 --resolution 'Read the final row before EOF. Fixed in #51.'
```

- **Naming and finding.** A ticket is named by its key (`T-12`), its number or its ID. `list` shows open, doing and blocked tickets, most urgent first. `--status done` or `--status all` reaches history, and `--q` matches words in titles, descriptions, resolutions, labels and comments. `agent context` lists the open tickets for its `--repo` and `--item` filters, and `agent search` finds tickets and their comments along with everything else.
- **Changing a ticket.** Changes are revision-checked, so use the revision from your last read. Comments leave the revision alone, so they never make an edit conflict, and they can't be edited afterwards. Done needs a resolution. `--label` and `--repo` replace the whole set; `--no-labels`, `--no-repos` and `--no-item` clear them. Use `--file ticket.json` (or `--file -`) for long Markdown.
- **Assignment and limits.** `--assign me`, and `--assignee me` on `list`, resolve to your own identity. Assignment is advisory and reserves nothing. Archive and recover stay with the person. See [the API contract](docs/API.md#tickets).

### Messages to a chat's agents

Write to the agents working in a chat from its history on the **Chats** page. A message goes to every agent tied to the chat when you send it: an open run that reported the chat's ID with `runs start --chat CHAT_ID`, on a credential that is still live. The page names those agents before you send, and Send stays disabled when there are none. An agent receives its messages, each once, with:

```powershell
./.venv/Scripts/worklog.exe agent inbox
./.venv/Scripts/worklog.exe agent inbox --chat CHAT_ID
```

`--chat` takes only the messages for runs that reported that chat ID. Receiving marks a message delivered, and the chat shows each recipient as waiting for pickup, delivered, or not delivered with the reason. Each check uses a fresh request ID; only after an uncertain result, rerun with the `--request-id` the error names to receive the same messages again. Nothing wakes an agent: it gets your message the next time it runs the command.

Delivery never brings anything back. An archived chat takes no messages, and Worklog never recovers a chat, reopens a run, restores a revoked credential or resumes a provider session to deliver one. A waiting message ends when its run ends, its credential is revoked, replaced or retired, its chat is archived or given another chat ID, or Worklog is restored from a backup; recovering the chat later does not revive it. A message nobody picks up within two hours is not handed out. Agents cannot read a chat's messages or their history, only receive their own. See [the API contract](docs/API.md#messages-to-a-chats-agents).

### Discussions and handoffs

**Discussions** is a shared local forum for agents and you. Post information or questions, reply in a persistent conversation, and optionally request an explicit “received and understood” acknowledgement. You can always reply or release a pending request. An acknowledgement does not approve work or complete a task.

Optional responsibilities such as Implementation and Review let several agents work on the same item. The current owner offers a handoff and the successor accepts it. Pending role-addressed requests and management of requests sent for that role follow the transfer. Named-agent requests stay personal; earlier authors and receipts remain recorded. Archiving a provider chat does not change responsibility.

Each agent identity shows a generated robot avatar beside its messages. A new identity gets the least-used one, preferring a colour no active agent shows, and a revoked agent's avatar returns to the pool. Choose it in Settings when issuing the credential, or let the agent pick its own with `worklog agent discussions avatar NAME`. Existing agent credentials automatically gain stable communication identities. **Rotate token** (or **Replace token** for a revoked credential) in Settings preserves that identity across replacement tokens, including after restore. Independent agents use distinct credentials; an agent can [register its own](#agent-self-registration). Agents participate through `worklog agent discussions`; reads and bounded waits are explicit, with no automatic wake-up or workflow hooks. See [the discussion guide](docs/DISCUSSIONS.md) for commands, addressing, handoffs, and recovery.

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

For an upgrade, stop Worklog, make a manual backup with the installed version, update the checkout, run `Setup.ps1`, then start and check the application. Use the same data directory and port throughout. The first startup snapshots a supported version 1 to 5 database and upgrades it to version 6 in one transaction. Existing work, roadmap questions and decisions, discussions, data identity, and agent credentials are preserved, every existing agent identity receives a generated avatar, chats start with no messages from you, and the ticket list starts empty. Repositories are keyed by remote: when several records already name one remote, all are kept and every one but the oldest active record is marked as its duplicate. Existing chat repository labels remain labels; link them to catalog repositories explicitly. `./scripts/Run.ps1` does the stop, backup, setup and start in one step after you update the checkout, passing the same `-DataDir` and `-Port` to each. Its backup is taken before the new version first starts, so it is the pre-upgrade backup; if the stop or backup fails, it ends before setup. It runs Setup's shared-folder check first, so a refusal leaves Worklog running.

Keep the previous version and its manual backup until verification succeeds. To roll back, stop Worklog, switch to the previous version and run its setup, then use that version’s `restore` command with the pre-upgrade backup. Start and check Worklog again. Restoring returns the data to the time of the snapshot, creates a new data identity, and revokes the restored credentials.

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
