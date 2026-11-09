# Operating Worklog

How to back Worklog up, export it, restore it, compact it, check it and see what you use, and the
limits it runs within. The [README](../README.md) covers installing, starting and stopping it, and
[UPGRADING.md](UPGRADING.md) upgrading it.

## Backup, export and restore

Settings provides manual backup and portable export. CLI equivalents:

```powershell
./scripts/Worklog.ps1 backup
./scripts/Worklog.ps1 backups
./scripts/Worklog.ps1 backups -Verify
./scripts/Worklog.ps1 export 'D:/Archives/worklog-export.zip'
./scripts/Worklog.ps1 stop
./scripts/Worklog.ps1 restore 'D:/Archives/worklog-manual-EXAMPLE.sqlite3'
./scripts/Worklog.ps1 start
```

Choose an actual filename from `backups`; `EXAMPLE` is a placeholder. `backups` shows each file's SHA-256 as it was when the file was last hashed, which is kept beside the backups while the file keeps its size and modification time; `backups -Verify` hashes every file's bytes again. Backups use SQLite's online backup API, which takes a consistent snapshot while the database is in use. The database uses SQLite's rollback journal, not WAL. A temporary snapshot is validated before atomic publication. An automatic backup runs at startup and once each configured calendar day while the managed server remains running (checked every minute); set `WORKLOG_AUTO_BACKUP=0` to turn automatic backups off. Daily backups retain the most recent **14**, pre-migration backups retain **3**, pre-compact backups retain the **3** of the newest compactions that finished, and manual and pre-restore backups are not automatically pruned. A portable ZIP contains readable Markdown, table records as JSON, every binary attachment/import blob, and a manifest with SHA-256 hashes. Agent credential hashes are redacted from exports. Exported `imports/*.html` files are the original untrusted HTML: inspect them as text and never open them in a browser. Export is an interchange/archive format; restore takes a SQLite backup, not the ZIP. CLI export refuses to overwrite an existing file.

Restore is offline: it acquires the server's exclusive OS lock, reads the candidate without modifying it, checks SQLite integrity, foreign keys, supported schema version, all required tables, and the exact trusted schema definitions (including constraints and indexes), stages a separate replacement, revokes all restored agent credentials, and snapshots the current database before an atomic replacement. Extra triggers or altered definitions are rejected. A corrupt or incompatible candidate leaves the active database intact. An active managed server or busy SQLite writer blocks restore. After restore, issue new agent credentials in Settings. The backup's settings come back with it, including whether agent proposals need your approval, so check that choice there too. Never replace a running SQLite file by copying it or deleting its journal sidecar.

A damaged active database does not prevent recovery from a valid backup. Before probing or replacing it, offline restore preserves the original main file and any WAL, SHM, or journal sidecars byte-for-byte in a hashed `worklog-pre-restore-raw-*.zip` evidence archive. A healthy original also receives a normal SQLite pre-restore backup. Raw evidence is not itself a valid SQLite restore candidate; retain it for investigation. Sidecars are moved aside only during final replacement and restored if replacement fails. These evidence archives are not automatically pruned.

Every restore creates a new data generation identity while staging the replacement, in addition to revoking restored credentials. Browser drafts and queued edits are scoped to that identity, so an edit based on the previous database cannot silently target a restored record whose ID and revision happen to match. The candidate backup remains unchanged. The next start records the new identity and creates a fresh automatic snapshot for that generation.

Restore writes a durable `.restore-in-progress.json` marker before moving sidecars or replacing the main file. Startup refuses that directory if a power failure interrupts restoration. Keep the evidence archives and rerun the explicit offline `restore` command with a verified SQLite candidate; the retry preserves earlier marker/quarantine evidence before completing recovery. Do not delete the marker to force a startup. It clears only after verified replacement and cleanup, or a verified rollback to the original files.

Local backups protect against mistakes and some corruption, **not device loss**. Periodically copy completed backup files or exports to another protected device/location according to your retention needs. Treat archives as sensitive personal work data.

## Compaction

Compaction is offline too, and optional: it gives back the space old history takes. Run it with Worklog stopped, after the new version has started once if you upgraded, since it works only on a database at the current schema version:

```powershell
./scripts/Worklog.ps1 stop
./scripts/Worklog.ps1 compact
./scripts/Worklog.ps1 start
```

`compact` takes the server's exclusive OS lock and refuses a database that another process holds, or that has a journal, WAL or SHM file or an interrupted restore beside it. It validates the database as restore validates a candidate, takes a `worklog-pre-compact-*` backup (those of the three newest compactions that finished are kept), and then, on a copy beside the database:

- thins changes older than 90 days to work items, the day's plan and reflection, notes, tasks, links, GitHub and file references, questions, decisions, attachments, roadmaps, steps, progress and plan imports: each keeps only its record's `id`, `title`, `status` and `_statusChanged`, which Today's completed and blocked lists replay, so a record with none of them reads `{}`, and a payload that is not a record, such as the day's order or its reflection's text, becomes `null`. Changes of every other kind, registration, proposal, chat and discussion ones among them, keep everything, and no change is removed;
- keeps each roadmap's 20 newest versions;
- removes plan imports never committed to a roadmap 7 days after their preview, and idempotency receipts past their 14 days;
- runs `VACUUM`, then checks integrity, foreign keys and the exact schema again.

Only then does it rename the copy into the database's place, and it prints what went and the bytes reclaimed. A failure before that rename leaves the database as it was and removes the run's backup, which only repeated it; if another program has the database open, `compact` says so, so close that program and run it again. The way back from a compaction is to restore its pre-compaction backup, which revokes agent credentials as every restore does.

## Doctor

`doctor` checks what the schema cannot enforce. It reads a copy of the database, made as a backup is made, so it can run while Worklog does and changes nothing:

```powershell
./scripts/Worklog.ps1 doctor
```

It runs SQLite's integrity and foreign-key checks and the search index's own check. It also finds records that name a type their table never takes or a record that is gone, steps whose parent is gone or on another roadmap, a chat tracked twice on one record, and search keys that disagree with their rows. It prints each check's count of problems with up to five examples, and exits with 1 when it finds any.

## See what you use

Worklog counts which of its features get used, so you can see what earns its place and what nothing uses. Every API call counts once for its method and route, such as `POST /work-items` or `POST /proposals/{identity}/approve`, as yours or an agent's, and as failed when it is answered with an error. A call refused before it reaches a route, such as one without a session or an agent's call to a route that is yours alone, counts for nothing. Each visit to a page counts once, however you get there, and the Guide's views for you and for agents count apart; moving between a page's records or sections doesn't. A count holds the feature's name and the day, never a record, its text, a search or anything else you typed.

Worklog keeps the counts in memory and adds them to `usage.sqlite3` in the data directory every minute and when it stops, so a crash loses at most a minute of them. That file is not the database: backups, exports and restores leave it out, and deleting it while Worklog is stopped starts the counts over.

The **Usage** page, under System in the sidebar, shows them for the last 7, 30, 90 or 365 days: totals, each day's page views, changes and reads as charts, every page by its views, and the API calls by area, with those nothing used. It adds the counts Worklog holds for its next write, so it is current to the moment, and what nothing used goes by the version running. The command below prints the same report from the file alone.

```powershell
./scripts/Worklog.ps1 usage
./scripts/Worklog.ps1 usage -Days 7
# Equivalent installed CLI:
./.venv/Scripts/worklog.exe usage --days 7
```

`usage` reads that file, whether Worklog runs or not, changes nothing, and prints JSON for the 30 days to today, or the number of days you give. Under `pages` and `calls`, `used` lists each page with its views, and each call with how often you and your agents made it and how often it failed, the most used first. `unused` lists what the version that last ran serves that nothing used in those days. Each entry has the last day anything used it, `null` if nothing ever did, and each call its `scope` from the route policy: `human` for yours alone, `public` for the session start, or the agent scope that lets agents make some or all of those calls too, `null` for a call that version no longer serves. That version writes its list of features to the file when it starts; if it could not (`server.log` says why), the report goes by the list an earlier start wrote, and with none, `unused` is empty and every `scope` is `null`. `recordedSince` is the first day counted, so a window that starts before it covers fewer days. `daily` lists each day in the window that has counts, oldest first, with its page views, the reads (GET) and changes (any other method) you and your agents made, and the calls that failed. Pages read what they show on their own, and refresh it, such as the sidebar's count of due reminders on every page, so a read's count says less about what you use than page views and changes do.

## Security and operating limits

The threat model is **one trusted OS account**. Browser sessions and agent bearer scopes protect application paths, and loopback, origin/CSRF checks, request limits, and attachment download behavior reduce exposure to other websites and imported content. Imported HTML is parsed into reviewable plain content; original files are downloaded as attachments, never executed as a page. Browser UI renders Markdown as text/content without enabling arbitrary HTML execution. Attachment names and copied local path references do not authorize filesystem execution.

The Chats page reads, and never changes, the transcripts Claude Code (`~/.claude/projects`, plus the Claude desktop app's session records) and Codex (`~/.codex/sessions`) keep for the chat IDs you track; set `CLAUDE_CONFIG_DIR` or `CODEX_HOME` if those tools keep them elsewhere. To offer the Claude Code sessions running on this machine, it also reads the record Claude Code keeps of each running session (`~/.claude/sessions`), and asks the operating system whether that record's process still runs and started when the record says. It opens such a process only to ask that, and never signals or attaches to it. Agent credentials cannot read them through the API, since a transcript holds a whole conversation; as with the rest of Worklog, that is not a boundary against programs running as your OS user. A chat ID selects a transcript only in the UUID shape those tools use, never as a path. This is not adversarial isolation between programs running as your OS user: such a program can read/modify the database and files, inspect process environment, or alter the application. The OS account, filesystem ACLs, device security, Python environment, and local backups remain trusted. Do not expose port 8877 to a network, reverse proxy, tunnel, or shared account. The application has no multi-user authentication or end-to-end encryption. A stopped server is not accessible through the agent HTTP API.
