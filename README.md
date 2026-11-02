# Worklog

A personal workbench for daily work, a persistent backlog, Markdown notes, attachments, structured roadmaps, and agent proposals that a person reviews before applying. Python serves the built frontend and API together at **http://127.0.0.1:8877**. The app uses one local SQLite database; no hosted service is required.

## Install from source on Windows

Install Python **3.12 or newer** and a current Node.js LTS with npm. Node is needed only to install/build the frontend, not to run the installed application. From PowerShell in this checkout:

```powershell
./scripts/Setup.ps1
./scripts/Worklog.ps1 start
./scripts/Worklog.ps1 check
```

Setup creates `.venv`, installs the exact versions in `requirements.lock`, installs this project without resolving dependencies again, then runs `npm ci` using `frontend/package-lock.json` and builds `frontend/dist`. It needs internet access to fetch packages. If Python has a different executable name, use `./scripts/Setup.ps1 -PythonCommand python3.12`. Respect your organization's PowerShell script policy; do not disable it globally.

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

Choose an actual filename from `backups`; `EXAMPLE` is a placeholder. Backups use SQLite's online backup API, which takes a consistent snapshot while the database is in use. The database uses SQLite's rollback journal, not WAL. A temporary snapshot is validated before atomic publication. An automatic backup runs at startup and once each configured calendar day while the managed server remains running (checked every minute). Daily backups retain the most recent **14**, pre-migration backups retain **3**, and manual and pre-restore backups are not automatically pruned. A portable ZIP contains readable Markdown, table records as JSON, every binary attachment/import blob, and a manifest with SHA-256 hashes. Agent credential hashes are redacted from exports. Exported `imports/*.html` files are the original untrusted HTML: inspect them as text and never open them in a browser. Export is an interchange/archive format; restore takes a SQLite backup, not the ZIP. CLI export refuses to overwrite an existing file.

Restore is offline: it acquires the server's exclusive OS lock, reads the candidate without modifying it, checks SQLite integrity, foreign keys, supported schema version, all required tables, and the exact trusted schema definitions (including constraints and indexes), stages a separate replacement, revokes all restored agent credentials, and snapshots the current database before an atomic replacement. Extra triggers or altered definitions are rejected. A corrupt or incompatible candidate leaves the active database intact. An active managed server or busy SQLite writer blocks restore. After restore, issue new agent credentials in Settings. Never replace a running SQLite file by copying it or deleting its journal sidecar.

A damaged active database does not prevent recovery from a valid backup. Before probing or replacing it, offline restore preserves the original main file and any WAL, SHM, or journal sidecars byte-for-byte in a hashed `worklog-pre-restore-raw-*.zip` evidence archive. A healthy original also receives a normal SQLite pre-restore backup. Raw evidence is not itself a valid SQLite restore candidate; retain it for investigation. Sidecars are moved aside only during final replacement and restored if replacement fails. These evidence archives are not automatically pruned.

Every restore creates a new data generation identity while staging the replacement, in addition to revoking restored credentials. Browser drafts and queued edits are scoped to that identity, so an edit based on the previous database cannot silently target a restored record whose ID and revision happen to match. The candidate backup remains unchanged. The next start records the new identity and creates a fresh automatic snapshot for that generation.

Restore writes a durable `.restore-in-progress.json` marker before moving sidecars or replacing the main file. Startup refuses that directory if a power failure interrupts restoration. Keep the evidence archives and rerun the explicit offline `restore` command with a verified SQLite candidate; the retry preserves earlier marker/quarantine evidence before completing recovery. Do not delete the marker to force a startup. It clears only after verified replacement and cleanup, or a verified rollback to the original files.

For an upgrade, stop Worklog, make a manual backup with the installed version, update the checkout, run `Setup.ps1`, then start and check the application. Use the same data directory and port throughout. The first startup snapshots a supported version 1, 2 or 3 database and upgrades it to version 4 in one transaction. Existing work, roadmap questions and decisions, discussions, data identity, and agent credentials are preserved, every existing agent identity receives a generated avatar, and chats start with no messages from you. Existing chat repository labels remain labels; link them to catalog repositories explicitly.

Keep the previous version and its manual backup until verification succeeds. To roll back, stop Worklog, switch to the previous version and run its setup, then use that version’s `restore` command with the pre-upgrade backup. Start and check Worklog again. Restoring returns the data to the time of the snapshot, creates a new data identity, and revokes the restored credentials.

Local backups protect against mistakes and some corruption, **not device loss**. Periodically copy completed backup files or exports to another protected device/location according to your retention needs. Treat archives as sensitive personal work data.

## Agent CLI and approval boundary

Create a labeled agent credential in Settings and provide its one-time token to the agent process through **`WORKLOG_AGENT_TOKEN`**. Do not put the token in a command argument, source file, log, or shell history. Revoke it in Settings when the agent no longer needs access. The CLI uses a bearer credential only against literal loopback HTTP, ignores HTTP proxy settings, refuses redirects, and prints no credential.

```powershell
# Supply WORKLOG_AGENT_TOKEN through your local secret mechanism before these commands.
./.venv/Scripts/worklog.exe agent context
./.venv/Scripts/worklog.exe agent context --date 2026-10-28
./.venv/Scripts/worklog.exe agent search 'release checks'
./.venv/Scripts/worklog.exe agent proposals --status submitted
./.venv/Scripts/worklog.exe agent proposal draft --file proposal.json
./.venv/Scripts/worklog.exe agent proposal show PROPOSAL_UUID
./.venv/Scripts/worklog.exe agent proposal update PROPOSAL_UUID --revision 1 --file proposal.json
./.venv/Scripts/worklog.exe agent proposal evidence PROPOSAL_UUID --file evidence.txt
./.venv/Scripts/worklog.exe agent proposal submit PROPOSAL_UUID --revision 2
./.venv/Scripts/worklog.exe agent proposal withdraw PROPOSAL_UUID --revision 3
```

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

Existing-target operations carry the target's current `revision` in their payload. A stale revision returns a conflict rather than overwriting newer edits. Evidence can be uploaded only to the owning agent's draft proposal. Agents can read context/search and create, edit, submit, inspect, or withdraw proposals; they cannot directly change canonical work or approve proposals. Review submitted operations and evidence in the browser, then approve or reject there. Approval applies all operations atomically; a conflict applies none. See [docs/API.md](docs/API.md) for the operation contract.

## Repository coordination

Repositories are canonical catalog records. Work items and tracked chats can link to several repositories; legacy chat repository labels remain visible separately. Edit the catalog and canonical links in the browser, or submit reviewed proposals. Agents can directly report their own runs, repository participation, and immutable status updates without creating a work item first.

```powershell
./.venv/Scripts/worklog.exe agent repositories list
./.venv/Scripts/worklog.exe agent runs start --client-run-key 'my-chat-2026-11-01' --display-name 'Repository review' --request-id 'run-start-2026-11-01'
./.venv/Scripts/worklog.exe agent participation start --run RUN_ID --repo REPOSITORY_ID --request-id 'participation-start-2026-11-01'
./.venv/Scripts/worklog.exe agent updates post --participation PARTICIPATION_ID --kind update --text 'Reviewing the parser.' --request-id 'parser-update-2026-11-01'
./.venv/Scripts/worklog.exe agent context --repo REPOSITORY_ID
./.venv/Scripts/worklog.exe agent participation release PARTICIPATION_ID --revision CURRENT_REVISION --request-id 'participation-release-2026-11-01'
./.venv/Scripts/worklog.exe agent runs end RUN_ID --revision CURRENT_REVISION --request-id 'run-end-2026-11-01'
```

Use returned IDs and revisions. Choose one stable `--request-id` per write and reuse it with exactly the same payload after an uncertain result. A run's `--client-run-key` identifies that run within the issuing credential. Use a new key for a new run. Optional `--item ITEM_ID` links participation to work. Use `participation start --file context.json` for reported checkout/worktree paths and branches, and `participation update ID --revision N --file context.json --request-id KEY` to replace contexts or change the work link.

Participation is advisory overlap information, not an exclusive claim, lock, permission, or proof that a process is alive. Activity older than two hours displays as stale without releasing it. An optional explicit `participation check-in ID --revision N --request-id KEY` refreshes reported activity; no heartbeat is required. Release and rejoin are explicit, and updates retain their original repository/work snapshots when participation later changes. See [the API contract](docs/API.md#repository-coordination) for list filters and payloads.

## Agent registry

**Agents** shows who works on what. Every agent credential's identity appears there once it is issued; there is no separate registration step. Starting a run, reporting participation, posting a status update, posting a discussion message or owning a responsibility role is what registers that work, and those records are kept as they are written. An agent's page gathers the work items, roadmaps, repositories and tracked chats it is tied to, with the reason for each tie, plus its runs, messages, status updates and credentials. A tracked chat is tied when an agent reports its chat ID with `runs start --chat CHAT_ID` or a discussion post's `--chat`. The registry is read-only and visible only in the browser; it adds no authority, and a fresh report is not proof that the agent is still running. See [the API contract](docs/API.md#agent-registry).

## Messages to a chat's agents

Write to the agents working in a chat from its history on the **Chats** page. A message goes to every agent tied to the chat when you send it: an open run that reported the chat's ID with `runs start --chat CHAT_ID`, on a credential that is still live. The page names those agents before you send, and Send stays disabled when there are none. An agent receives its messages, each once, with:

```powershell
./.venv/Scripts/worklog.exe agent inbox
./.venv/Scripts/worklog.exe agent inbox --chat CHAT_ID
```

`--chat` takes only the messages for runs that reported that chat ID. Receiving marks a message delivered, and the chat shows each recipient as waiting for pickup, delivered, or not delivered with the reason. Each check uses a fresh request ID; only after an uncertain result, rerun with the `--request-id` the error names to receive the same messages again. Nothing wakes an agent: it gets your message the next time it runs the command.

Delivery never brings anything back. An archived chat takes no messages, and Worklog never recovers a chat, reopens a run, restores a revoked credential or resumes a provider session to deliver one. A waiting message ends when its run ends, its credential is revoked or replaced, its chat is archived or given another chat ID, or Worklog is restored from a backup; recovering the chat later does not revive it. A message nobody picks up within two hours is not handed out. Agents cannot read a chat's messages or their history, only receive their own. See [the API contract](docs/API.md#messages-to-a-chats-agents).

## Agent discussions and handoffs

**Discussions** is a shared local forum for agents and you. Post information or questions, reply in a persistent conversation, and optionally request an explicit “received and understood” acknowledgement. You can always reply or release a pending request. An acknowledgement does not approve work or complete a task.

Optional responsibilities such as Implementation and Review let several agents work on the same item. The current owner offers a handoff and the successor accepts it. Pending role-addressed requests and management of requests sent for that role follow the transfer. Named-agent requests stay personal; earlier authors and receipts remain recorded. Archiving a provider chat does not change responsibility.

Each agent identity shows a generated robot avatar beside its messages. A new identity gets the least-used one, preferring a colour no active agent shows, and a revoked agent's avatar returns to the pool. Choose it in Settings when issuing the credential, or let the agent pick its own with `worklog agent discussions avatar NAME`. Existing agent credentials automatically gain stable communication identities. **Rotate / reissue** in Settings preserves that identity across replacement tokens, including after restore. Independent agents use distinct credentials. Agents participate through `worklog agent discussions`; reads and bounded waits are explicit, with no automatic wake-up or workflow hooks. See [the discussion guide](docs/DISCUSSIONS.md) for commands, addressing, handoffs, and recovery.

## Security and operating limits

The threat model is **one trusted OS account**. Browser sessions and agent bearer scopes protect application paths, and loopback, origin/CSRF checks, request limits, and attachment download behavior reduce exposure to other websites and imported content. Imported HTML is parsed into reviewable plain content; original files are downloaded as attachments, never executed as a page. Browser UI renders Markdown as text/content without enabling arbitrary HTML execution. Attachment names and copied local path references do not authorize filesystem execution.

The Chats page reads, and never changes, the transcripts Claude Code (`~/.claude/projects`, plus the Claude desktop app's session records) and Codex (`~/.codex/sessions`) keep for the chat IDs you track; set `CLAUDE_CONFIG_DIR` or `CODEX_HOME` if those tools keep them elsewhere. Agent credentials cannot read them through the API, since a transcript holds a whole conversation; as with the rest of Worklog, that is not a boundary against programs running as your OS user. A chat ID selects a transcript only in the UUID shape those tools use, never as a path. This is not adversarial isolation between programs running as your OS user: such a program can read/modify the database and files, inspect process environment, or alter the application. The OS account, filesystem ACLs, device security, Python environment, and local backups remain trusted. Do not expose port 8877 to a network, reverse proxy, tunnel, or shared account. The application has no multi-user authentication or end-to-end encryption. A stopped server is not accessible through the agent HTTP API.

## Development checks

```powershell
./.venv/Scripts/python.exe -m pytest
./.venv/Scripts/python.exe -m ruff check backend
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
