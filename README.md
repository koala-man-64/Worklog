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

Choose an actual filename from `backups`; `EXAMPLE` is a placeholder. Backups use SQLite's online backup API so live WAL writes are captured consistently. A temporary snapshot is validated before atomic publication. An automatic backup runs at startup and once each configured calendar day while the managed server remains running (checked every minute). Daily backups retain the most recent **14**, pre-migration backups retain **3**, and manual and pre-restore backups are not automatically pruned. A portable ZIP contains readable Markdown, table records as JSON, every binary attachment/import blob, and a manifest with SHA-256 hashes. Agent credential hashes are redacted from exports. Export is an interchange/archive format; restore takes a SQLite backup, not the ZIP. CLI export refuses to overwrite an existing file.

Restore is offline: it acquires the server's exclusive OS lock, reads the candidate without modifying it, checks SQLite integrity, foreign keys, supported schema version, all required tables, and the exact trusted schema definitions (including constraints and indexes), stages a separate replacement, revokes all restored agent credentials, and snapshots the current database before an atomic replacement. Extra triggers or altered definitions are rejected. A corrupt or incompatible candidate leaves the active database intact. An active managed server or busy SQLite writer blocks restore. After restore, issue new agent credentials in Settings. Never replace a running SQLite file by copying it or deleting WAL sidecars.

A damaged active database does not prevent recovery from a valid backup. Before probing or replacing it, offline restore preserves the original main file and any WAL, SHM, or journal sidecars byte-for-byte in a hashed `worklog-pre-restore-raw-*.zip` evidence archive. A healthy original also receives a normal SQLite pre-restore backup. Raw evidence is not itself a valid SQLite restore candidate; retain it for investigation. Sidecars are moved aside only during final replacement and restored if replacement fails. These evidence archives are not automatically pruned.

Every restore creates a new data generation identity while staging the replacement, in addition to revoking restored credentials. Browser drafts and queued edits are scoped to that identity, so an edit based on the previous database cannot silently target a restored record whose ID and revision happen to match. The candidate backup remains unchanged. The next start records the new identity and creates a fresh automatic snapshot for that generation.

Restore writes a durable `.restore-in-progress.json` marker before moving sidecars or replacing the main file. Startup refuses that directory if a power failure interrupts restoration. Keep the evidence archives and rerun the explicit offline `restore` command with a verified SQLite candidate; the retry preserves earlier marker/quarantine evidence before completing recovery. Do not delete the marker to force a startup. It clears only after verified replacement and cleanup, or a verified rollback to the original files.

For an upgrade, stop Worklog, make a manual backup, update the checkout, run `Setup.ps1`, then start and check the application. Keep the old checkout/version and backup until verification succeeds. Unsupported newer database schema versions are rejected; do not downgrade code against a newer database. Use a backup compatible with the code version for rollback. Current schema is version 1; no legacy migration is required.

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

## Security and operating limits

The threat model is **one trusted OS account**. Browser sessions and agent bearer scopes protect application paths, and loopback, origin/CSRF checks, request limits, and attachment download behavior reduce exposure to other websites and imported content. Imported HTML is parsed into reviewable plain content; original files are downloaded as attachments, never executed as a page. Browser UI renders Markdown as text/content without enabling arbitrary HTML execution. Attachment names and copied local path references do not authorize filesystem execution.

This is not adversarial isolation between programs running as your OS user: such a program can read/modify the database and files, inspect process environment, or alter the application. The OS account, filesystem ACLs, device security, Python environment, and local backups remain trusted. Do not expose port 8877 to a network, reverse proxy, tunnel, or shared account. The application has no multi-user authentication or end-to-end encryption. A stopped server is not accessible through the agent HTTP API.

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
