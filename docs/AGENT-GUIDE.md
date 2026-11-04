# Worklog agent guide

> Worklog is a single-user workbench that runs on the person's own machine, by default at http://127.0.0.1:8877. It holds the person's daily plan, work items, roadmaps, people, repositories, tickets and resources (reference pages the person writes for you), and it coordinates the AI agents working for them. Agents use the `worklog agent` command line to read that context, report their own runs and repository activity, write tickets and discussion messages, receive the messages the person writes into their chats, and propose changes to the person's records for the person to approve.

This guide is for AI agents (Claude Code, Codex and others) acting for the person. Read sections 1 to 4 before your first Worklog command; sections 5 to 12 are reference. `worklog agent guide` prints this guide; it needs no credential and no running Worklog. The person's own guide is `docs/GUIDE.md`, which Worklog shows on its Guide page.

## Conventions

- MUST, MUST NOT and SHOULD are requirements. Everything else describes how Worklog behaves.
- `worklog` stands for the command line in the person's Worklog checkout: `<checkout>/.venv/Scripts/worklog.exe` on Windows, for example `C:/Users/NAME/Projects/worklog/.venv/Scripts/worklog.exe`. `scripts/Worklog.ps1` runs only the person's lifecycle commands, never agent commands.
- UPPERCASE words in commands are placeholders, such as NAME, CHAT_ID and ITEM_ID. Numbers such as `--revision 3` are examples: always use the current revision.
- Options go in this order. `--as` and `--url` belong to `agent` and MUST come before the group; every other option comes after the command:

```text
worklog [--port N] [--data-dir DIR] agent [--as NAME] [--url URL] GROUP COMMAND [ARGUMENTS] [OPTIONS]
```

- The instance is `http://127.0.0.1:PORT`, where PORT is `--port`, else the `WORKLOG_PORT` environment variable, else 8877. `--url` must be plain HTTP on 127.0.0.1 with no path. Use the same port and data directory for every command against one instance.
- The command line talks only to 127.0.0.1 over plain HTTP. It ignores proxy settings, refuses redirects and never prints a credential.
- Success prints ASCII JSON on stdout and exits 0. Failure prints one `Worklog: ...` line on stderr and exits 1; a usage error exits 2 (section 8). `worklog agent guide` is the one command that prints Markdown.
- IDs are UUIDs, except a ticket's key (`T-12`) and a chat ID (the provider's session or thread ID). Dates are `YYYY-MM-DD` in the person's timezone. Long text fields are Markdown.

## 1. Rules

1. **Credential.** The person issues a credential in Worklog's Settings and gives it to agents in the environment variable `WORKLOG_AGENT_TOKEN`. You MUST NOT print, echo, log or store its value, or put it in a command argument. You need it to `register`, and for the optional reads in section 10. If your process lacks it, load the person's user-level value into the same command by reference; each tool call starts a fresh shell:

```powershell
$env:WORKLOG_AGENT_TOKEN = [Environment]::GetEnvironmentVariable('WORKLOG_AGENT_TOKEN', 'User'); worklog agent register --chat CHAT_ID --label 'Parser fix (Claude)'
```

2. **Register once per chat, with your chat's ID**, before any other Worklog command (section 4). CHAT_ID is your Claude Code session ID, Claude desktop `local_...` ID or Codex thread ID. Worklog keeps it with your identity, so it can track your chat: your runs report it, and work you report on an item tracks your chat there. CHAT_ID also names you: NAME below is your chat ID, unless you give `--name NAME` (1-64 letters, digits, `-` or `_`, starting with a letter or digit, compared without case), which a chat ID that cannot be a name needs. Keep both for the whole chat: registering your name for another chat is refused (`chat_mismatch`). Worklog keeps your new token in its data directory; you MUST NOT open, copy or print that file.
3. **Act as yourself.** Every later command MUST give `--as NAME` before the group. Your runs, reports, messages, tickets and proposals then carry your own identity.
4. **One request ID per write.** Give each write a stable `--request-id`: 8-200 printable ASCII characters without spaces. After an uncertain result, rerun with the same ID and the identical payload; it cannot apply twice. A new operation MUST get a new ID. Proposal commands take none (section 7).
5. **Current revisions.** A revision-checked command MUST carry the revision from your latest read. On `409 conflict`, read the record again and decide afresh; never reapply blindly.
6. **Proposals for the person's records.** Work items, day entries, scratchpads and notes, checklist tasks, roadmaps and step progress, people, links, tracked chats, GitHub references, questions, decisions, repository edits and the files attached to the person's records change only through a proposal the person approves (section 6). You write tickets, discussions, your own runs, participation and updates, repository registrations and your inbox directly. Resources, the person's reference pages, are read-only to you: no command and no proposal changes one.
7. **Advisory, not authority.** Participation, ticket assignment and roles reserve nothing and lock nothing, and a fresh report does not prove an agent is running. An acknowledgement means "received and understood", never approval.
8. **Search first.** Before you investigate a failure, search past tickets, closed ones included: `tickets list --status all --q 'WORDS'`. File a ticket for an issue worth tracking, comment as you learn, and close it with a resolution that says what fixed it.
9. **Never work around a refusal.** `401` means your identity was revoked: by the person, by the retirement of the agent that registered you, or by a restore. Stop, tell the person, and register again only if they agree. `403 agent_scope` is outside agent authority: propose the change or ask. `429 agent_limit`: report it and do not retry. `403 repository_registration_off`: propose `repository.create`. Section 8 lists every code.
10. **Leave nothing open.** When the chat's work is done, offer or release the roles you own, release your participation, end your runs and `retire` (section 4, step 8). Retiring does not release roles: they would stay with a retired identity until the person reassigns them. The lifecycle commands `start`, `stop`, `backup`, `backups`, `restore`, `export` and `serve` are the person's: you MUST NOT run them unless the person asks. `worklog check` only reads, and is safe.

## 2. Records

| Record | Identified by | States and key fields | Written by |
|---|---|---|---|
| Work item | UUID | `status` open, doing, blocked or done; `rank`, the person's stack rank (1 matters most; null when done or archived); `archived` | proposal (`item.*`); only the person ranks |
| Day entry | date and work item | `planned`, `worked`, `outcome` | proposal (`entry.upsert`) |
| Scratchpad | note UUID | the one Markdown note of a work item | proposal (`note.create` adds a section) |
| Note on a roadmap or step | UUID | `title`, `text` | proposal (`note.*`) |
| Checklist task | UUID | `text`, `done` | proposal (`task.*`) |
| Roadmap | UUID | `title`, `overview`, `parentId` for nesting, immutable versions | proposal (`roadmap.*`) |
| Step | UUID, stable across versions | progress open, doing, blocked or done on leaf steps (a parent's follows its children); `retired` | proposal (`roadmap.revise`, `progress.set`) |
| Question, decision | UUID | a question is open or answered | proposal |
| Person | UUID | `name`, `title`, `team`, `description`, contact details (`email`, `phone`, `chat`, `location`), `notes` (Markdown); tied to work items, roadmaps and repositories, each with a `relationship` (their role there) | proposal (`person.*`) |
| Link, GitHub reference | UUID | `url`; a GitHub reference's `kind` is commit or pr | proposal |
| File | UUID | `name`, `mime`, `size`, `sha256`, `archived`; `ownerType` item, roadmap, step, repository, or proposal for evidence | evidence: you, on your own draft; elsewhere: proposal (`attachment.create`) |
| Tracked chat | Worklog's UUID, and `chatId`, the provider's ID | on a work item or roadmap; `stepIds`, `provider`, `model` | proposal (`chat.*`) |
| Repository | UUID; one record per remote | `registeredBy`, `reviewed`, `duplicateOf`, `mergedInto` | you register; edits by proposal |
| Ticket | `T-12`, `12` or UUID | `kind` bug, incident or task; `priority` low, normal, high or urgent; `status` open, doing, blocked or done; `resolution` | you, directly |
| Resource | UUID | `title`, `summary` (what the page is for), `body` (Markdown); `archived` | the person only; you read |
| Proposal | UUID | draft, submitted, approved, rejected or withdrawn; `autoApproved` | you draft; the person decides |
| Identity, credential | `agentId`; a credential `id` | active or revoked | the person issues; you register and retire your own |
| Run | UUID; `--client-run-key` is unique per credential | open or ended | you, directly |
| Participation | UUID | active or released; stale after 2 hours without a report | you, directly |
| Update | UUID | `kind` update, blocker or handoff; immutable | you, directly |
| Discussion, message | UUID | messages are immutable; a request is pending, satisfied, released_by_human or cancelled | you, directly |
| Role | UUID | one owner; at most one pending offer | you, directly; the person reassigns |
| Inbox message | UUID | handed out once; expires after 2 hours | the person writes; you receive |

Records are archived, never deleted, and only the person archives or recovers them.

## 3. Authority

| Action | The person | You directly | You by proposal |
|---|---|---|---|
| Read work, days, roadmaps, steps, people, notes, tickets, resources, discussions, repositories, runs and proposals | yes | yes | - |
| Read chat transcripts, chat messages, the Agents page, Settings, credentials, backups or the export | yes | no | - |
| Change work items, day entries, notes, tasks, roadmaps, progress, people, links, tracked chats, GitHub references, questions or decisions | yes | no | yes |
| Register a repository | yes | yes, unless the person turned it off | yes |
| Edit, archive or recover a repository | yes | no | yes |
| Confirm or merge repositories | yes | no | no |
| File, change, comment on and close tickets | yes | yes | - |
| Archive or recover tickets | yes | no | no |
| Write, archive or recover resources | yes | no | no |
| Post, reply, acknowledge, cancel your own requests, act on roles | yes | yes | - |
| Archive a discussion, release a wait, reassign a role | yes | no | - |
| Start and end runs; report participation and updates | - | yes, your own | - |
| End another agent's run | yes | no | - |
| Approve or reject proposals; change Settings | yes | no | no |
| Write a message into a chat | yes | no: you receive with `inbox` | - |
| Upload evidence | - | to your own draft proposal | - |
| Attach files to work items, roadmaps, steps or repositories | yes | no | yes, a copy of your evidence |
| Archive or recover files, import plans, reorder, archive or restore roadmaps | yes | no | no |
| Issue, rotate or revoke credentials | yes | you register and retire identities you registered | - |

Approval is on by default. When the person turns it off in Settings, `proposal submit` applies your proposal at once (section 6).

## 4. A chat's lifecycle

**Step 1. Read this guide** with `worklog agent guide`.

**Step 2. Register your identity** (rules 1 and 2). The label says what you work on and which agent you are. After an uncertain result, run the same command again: it resolves to the same identity.

```powershell
worklog agent register --chat CHAT_ID --label 'Parser fix (Claude)'
```

**Step 3. Start a run.** It reports the chat you registered with; you need not give `--chat`. Keep the run's `id` and `revision`. The person sees your chat through it, and their messages reach the identity whose run reported the chat. Starting again with the same `--client-run-key` returns the same run; use a new key for a new run.

```powershell
worklog agent --as NAME runs start --client-run-key CHAT_ID --display-name 'Parser fix' --provider claude --request-id run-start-CHAT_ID
```

**Step 4. Read before you act:** the day's context, matching work, known issues, and the person's resources. `context` lists every resource by title and summary; read each one that bears on your task, and any the person names, with `resources show`: they hold what the person wants every agent to know.

```powershell
worklog agent --as NAME context
worklog agent --as NAME search 'parser'
worklog agent --as NAME tickets list --status all --q 'parser crash'
worklog agent --as NAME resources show RESOURCE_ID
```

**Step 5. Report each repository you change** by its checkout. This registers the checkout's `origin` remote when Worklog lacks it, and records your branch and checkout or worktree path. Repeat `--checkout` for each repository; add `--item` when the work has a work item, which also tracks your chat on that work item (once; the person can archive it). Many agents can report in one repository at once.

```powershell
worklog agent --as NAME participation start --run RUN_ID --checkout C:/Projects/repo --item ITEM_ID --description 'Fixing the parser' --request-id participation-CHAT_ID
```

**Step 6. Track your chat on the work (optional).** Propose `chat.create` (section 6) with `ownerType` `item` and the item's `ownerId`, or `roadmap` with the `stepIds` you work on; your `chatId`; a `title`; your `provider` and `model`. Once the person approves it, your chat shows on that work and on the Chats page (a roadmap's chat also beside its steps), and the person can write to you from it.

**Step 7. Work, and keep the record true.** Post updates, file and close tickets, propose changes and discuss. Nothing wakes you: at natural checkpoints, check your inbox and the discussions awaiting you.

```powershell
worklog agent --as NAME updates post --participation PARTICIPATION_ID --text 'Parser fixed; running the tests.' --request-id update-CHAT_ID-1
worklog agent --as NAME inbox
worklog agent --as NAME discussions list --view awaiting
```

**Step 8. Finish.** Offer or release any roles you own (section 5.7). Release participation and end the run, each with the revision from your latest read (posting an update advances a participation's revision), then retire. `retire` alone also ends your open runs and releases their participation; it revokes your identity and every identity you registered, and deletes your stored token.

```powershell
worklog agent --as NAME participation release PARTICIPATION_ID --revision 2 --request-id release-CHAT_ID
worklog agent --as NAME runs end RUN_ID --revision 1 --request-id run-end-CHAT_ID
worklog agent --as NAME retire
```

## 5. Command reference

Every command below except `guide` and `register` takes `--as NAME` before its group; the tables leave it out. In the tables, the Rev column marks commands that need `--revision`, and the Request ID column says whether `--request-id` is required, optional or absent (-). Where `--file` takes JSON, `--file -` reads it from standard input.

### 5.1 Guide and identity

| Command | Arguments and options | Rev | Request ID | Prints |
|---|---|---|---|---|
| `guide` | - | - | - | this guide, as Markdown |
| `register` | `--chat CHAT_ID --label LABEL [--name NAME] [--avatar AVATAR]` | - | - (rerun the same command) | `{name,id,agentId,label,avatar,chatId,registeredBy}`, never the token |
| `retire` | needs `--as NAME` | - | - | `{name,id,agentId,retired,registeredRevoked}` |

- **Your chat.** An identity works in the one chat it registered with. Its runs report that chat unless `runs start --chat` names another, and its participation with `--item` tracks that chat on the work item. A new chat registers a new identity; registering a name again for another chat is refused (`chat_mismatch`). The name is the chat ID unless `--name` gives another.
- **Authority.** `register` acts with `--as NAME` (an identity you registered) or else with `WORKLOG_AGENT_TOKEN`. An identity you register can register others; retiring or revoking it takes them with it.
- **Tokens.** Tokens are kept per data directory and port, so a test instance never uses the everyday instance's names.
- **Retiring.** A retired identity stays retired: registering the name again creates a new identity. You can retire only an identity you registered, never a credential the person issued.
- **Avatars.** `--avatar` takes a name that `discussions avatars` marks available.

```powershell
worklog agent guide
worklog agent register --chat CHAT_ID --label 'Parser fix (Claude)' --avatar teal-visor-antenna
worklog agent --as NAME retire
```

### 5.2 Reading

| Command | Arguments and options | Prints |
|---|---|---|
| `context` | `[--date DATE] [--repo REPOSITORY_ID] [--item ITEM_ID]` | `{day,workItems,roadmaps,people,proposals,repositories,runs,participations,updates,tickets,resources}`: the day's entries, roadmaps without their steps, up to 100 of each list, open tickets, and resources by title and summary without their text |
| `search QUERY` | `[--limit 50] [--archived]` | `{items:[{id,type,title,snippet,ownerId,routeType,routeId}],total}`; 220-character snippets; the first 30 words of QUERY count |
| `roadmap show ID` | - | the roadmap with every step's full text, progress, revision and `retired` flag, its versions, work items, notes, links, tracked chats, GitHub references, questions and decisions; without its history |
| `step show ID` | - | the step with its `roadmapId`, notes and attachments |
| `resources list` | `[--q WORDS] [--archived] [--limit 50] [--offset 0]` | `{items:[{id,title,summary,archived,revision,createdAt,updatedAt}],total,limit,offset}` by title, without the pages' text; `--q` searches titles, summaries and text; `--archived` lists archived pages only |
| `resources show ID` | - | one page with its whole Markdown `body`, and its latest 100 changes without their records |
| `proposals` | `[--status draft\|submitted\|approved\|rejected\|withdrawn]` | `{items,total}`: yours, and other agents' once submitted |
| `proposal show ID` | - | one proposal with its operations and evidence |

`search` covers work items, notes, file names, links, tracked chats, GitHub references, roadmaps and steps, questions and decisions, people, discussions, tickets and ticket comments, and resources, but not proposals.

```powershell
worklog agent --as NAME context --date 2026-11-04 --repo REPOSITORY_ID --item ITEM_ID
worklog agent --as NAME search 'release checks' --limit 20 --archived
worklog agent --as NAME roadmap show ROADMAP_ID
worklog agent --as NAME step show STEP_ID
worklog agent --as NAME resources list --q 'release checks' --limit 20 --archived
worklog agent --as NAME resources show RESOURCE_ID
worklog agent --as NAME proposals --status submitted
worklog agent --as NAME proposal show PROPOSAL_ID
```

### 5.3 Proposals

| Command | Arguments and options | Rev | Prints |
|---|---|---|---|
| `proposal draft` | `--file FILE` | - | the new draft |
| `proposal update ID` | `--revision N --file FILE` | yes | the draft, with the title, rationale and operations the file carries |
| `proposal evidence ID` | `--file PATH [--name NAME]`: a file path, not standard input; at most 25,000,000 bytes | - | the uploaded file's record, whose `id` an `attachment.create` names (section 6.2) |
| `proposal submit ID` | `--revision N` | yes | the proposal, submitted, or approved when approval is off |
| `proposal reopen ID` | `--revision N` | yes | the proposal, back in draft, with its evidence |
| `proposal withdraw ID` | `--revision N` | yes | the proposal, withdrawn |

None of these take `--request-id` (section 7). Section 6 describes the file and the operations.

```powershell
worklog agent --as NAME proposal draft --file proposal.json
worklog agent --as NAME proposal update PROPOSAL_ID --revision 1 --file proposal.json
worklog agent --as NAME proposal evidence PROPOSAL_ID --file test-output.txt --name 'Test run.txt'
worklog agent --as NAME proposal submit PROPOSAL_ID --revision 2
worklog agent --as NAME proposal reopen PROPOSAL_ID --revision 3
worklog agent --as NAME proposal withdraw PROPOSAL_ID --revision 4
```

### 5.4 Repositories, runs, participation and updates

| Command | Arguments and options | Rev | Request ID | Notes |
|---|---|---|---|---|
| `repositories list` | `[--q TEXT] [--archived] [--limit 50] [--offset 0]` | - | - | the catalog; `--archived` lists archived records only |
| `repositories show ID` | - | - | - | one record |
| `repositories ensure` | `--remote URL` or `--checkout PATH`; `[--name NAME] [--description TEXT]` | - | required | registers the remote, or returns the record that holds it, with `created`; reports no work |
| `runs list` | `[--include-ended] [--limit 50] [--offset 0]` | - | - | open runs unless `--include-ended` |
| `runs show ID` | - | - | - | one run |
| `runs start` | `--client-run-key KEY --display-name NAME [--provider PROVIDER] [--chat CHAT_ID]` | - | required | a key already used by this credential returns that run; without `--chat`, the run reports the chat you registered with |
| `runs end ID` | `--revision N` | yes | required | ends the run and releases its participation |
| `participation list` | `[--repo ID] [--item ID] [--run ID] [--include-released] [--limit 50] [--offset 0]` | - | - | active participation unless `--include-released` |
| `participation show ID` | - | - | - | one participation |
| `participation start` | `--run RUN_ID`, contexts from `--checkout PATH` or `--repo REPOSITORY_ID` (each repeatable); `[--item ITEM_ID] [--description TEXT] [--file FILE]` | - | required | flags override the file's keys |
| `participation update ID` | `--revision N` and either `--description TEXT` or `--file FILE` | yes | required | the file holds `{itemId,description,contexts}`; omitted fields stay; `itemId: null` unlinks the work |
| `participation check-in ID` | `--revision N` | yes | required | refreshes reported activity; no heartbeat is required |
| `participation release ID` | `--revision N` | yes | required | stops reporting |
| `participation rejoin ID` | `--revision N` | yes | required | reports again after a release |
| `updates list` | `[--repo ID] [--item ID] [--participation ID] [--limit 50] [--offset 0]` | - | - | updates as posted |
| `updates post` | `--participation ID` and either `--text TEXT [--kind update\|blocker\|handoff]` or `--file FILE` | - | required | immutable; advances the participation's revision |

- **Remotes.** A remote is an `https://` address, `ssh://git@host/path` or `git@host:path`. Every form of one remote names the same record: HTTPS or SSH, with or without `.git` or a trailing `/`, in any case, and Azure DevOps' `dev.azure.com`, `{org}.visualstudio.com` and `ssh.dev.azure.com` forms.
- **Unreviewed registrations.** A repository you register shows as unreviewed until the person confirms or edits it.
- **Checkouts.** `--checkout PATH` reads the checkout's `origin` remote, branch and checkout or worktree path with git; a folder without an `origin` fails before any request.
- **The participation file.** `participation start --file` takes `{runId,itemId,description,contexts:[{repositoryId or repositoryUrl,checkoutPath,worktreePath,branch}]}`.
- **Staleness.** Participation is stale after 2 hours without a report. Stale is a label: the participation is not released.
- **Snapshots.** An update keeps the repositories and work item it was posted with.
- **Merged repositories.** When the person merges a repository into another, your reports and updates move to the survivor, keeping their revisions.

```powershell
worklog agent --as NAME repositories list --q parser --limit 20
worklog agent --as NAME repositories show REPOSITORY_ID
worklog agent --as NAME repositories ensure --remote https://github.com/team/repo --request-id repository-team-repo
worklog agent --as NAME repositories ensure --checkout C:/Projects/repo --name repo --request-id repository-local-repo
worklog agent --as NAME runs list --include-ended
worklog agent --as NAME runs show RUN_ID
worklog agent --as NAME runs start --client-run-key CHAT_ID --display-name 'Repository review' --request-id run-start-CHAT_ID
worklog agent --as NAME runs end RUN_ID --revision 1 --request-id run-end-CHAT_ID
worklog agent --as NAME participation list --run RUN_ID --include-released
worklog agent --as NAME participation show PARTICIPATION_ID
worklog agent --as NAME participation start --run RUN_ID --repo REPOSITORY_ID --checkout C:/Projects/repo --request-id participation-CHAT_ID
worklog agent --as NAME participation start --file context.json --request-id participation-file-CHAT_ID
worklog agent --as NAME participation update PARTICIPATION_ID --revision 2 --description 'Reviewing the tests' --request-id participation-update-1
worklog agent --as NAME participation check-in PARTICIPATION_ID --revision 3 --request-id participation-check-in-1
worklog agent --as NAME participation release PARTICIPATION_ID --revision 4 --request-id participation-release-1
worklog agent --as NAME participation rejoin PARTICIPATION_ID --revision 5 --request-id participation-rejoin-1
worklog agent --as NAME updates list --participation PARTICIPATION_ID
worklog agent --as NAME updates post --participation PARTICIPATION_ID --kind blocker --text 'Waiting for the schema review.' --request-id update-blocker-1
```

### 5.5 Tickets

| Command | Arguments and options | Rev | Request ID | Notes |
|---|---|---|---|---|
| `tickets list` | `[--status active\|all\|open\|doing\|blocked\|done] [--q WORDS] [--kind KIND] [--priority PRIORITY] [--label LABEL] [--repo ID] [--item ID] [--assignee me\|none\|local-user\|AGENT_ID] [--sort priority\|recent] [--archived] [--limit 50] [--offset 0]` | - | - | `active` (the default) is open, doing and blocked; `priority` sorts urgent first; `--q` searches titles, descriptions, resolutions, labels and comments |
| `tickets show REF` | `[--comments N]` | - | - | full text, the latest N comments (default 200, 1-1000) and the latest 100 changes |
| `tickets create` | `--title TITLE` and any of `--description`, `--kind`, `--priority`, `--status`, `--resolution`, `--label` (repeat), `--repo` (repeat), `--item`, `--assign me\|none\|local-user\|AGENT_ID`, `--file FILE` | - | optional | kind defaults to bug, priority to normal, status to open |
| `tickets update REF` | `--revision N` and the create options, plus `--no-item`, `--no-labels`, `--no-repos` | yes | optional | `--label` and `--repo` replace the whole set |
| `tickets close REF` | `--revision N --resolution TEXT` | yes | optional | sets done |
| `tickets comment REF` | `--text TEXT` or `--file FILE` | - | optional | immutable; leaves the ticket's revision alone |

- **References.** REF is the key (`T-12`), the number (`12`) or the ID. Cite keys in commits, discussions and other tickets.
- **Resolutions.** Done needs a resolution, whether you give one with the status or the ticket already has one. Reopening keeps the resolution. A past incident can be filed already done, with `--status done --resolution TEXT`.
- **Limits.** Labels: at most 20, lower-cased, each matching `[a-z0-9][a-z0-9._-]{0,49}`. Titles: up to 1,000 bytes. Descriptions, resolutions and comments: up to 100,000 bytes.
- **Assignment** is advisory. `me` resolves to your identity.
- **Archiving.** The person archives and recovers tickets. An archived ticket refuses changes and comments.

```powershell
worklog agent --as NAME tickets list --status all --q 'database is locked' --kind bug --priority high --label parser --repo REPOSITORY_ID --item ITEM_ID --assignee me --sort recent --limit 20
worklog agent --as NAME tickets show T-12 --comments 20
worklog agent --as NAME tickets create --title 'Import drops the last row' --kind bug --priority high --label parser --repo REPOSITORY_ID --item ITEM_ID --assign me --request-id ticket-import-last-row
worklog agent --as NAME tickets update T-12 --revision 2 --status doing --no-labels
worklog agent --as NAME tickets close T-12 --revision 3 --resolution 'Read the final row before EOF. Fixed in PR 51.'
worklog agent --as NAME tickets comment T-12 --text 'Reproduced with a file that has no trailing newline.' --request-id ticket-12-repro
```

### 5.6 Inbox

| Command | Arguments and options | Request ID | Prints |
|---|---|---|---|
| `inbox` | `[--chat CHAT_ID]` | optional | `{items,total}`; each item is `{id,text,authorKind,authorId,authorLabel,sentAt,chat:{id,chatId,title},runIds}` |

The person writes to a chat's agents from the Chats page. A message reaches every identity with an open run that reported the chat's ID when it was sent; your runs report the chat you registered with. `inbox` hands you your waiting messages oldest first and marks them delivered, so each arrives once. `--chat` takes only the messages for runs that reported that chat ID. Check with a fresh request ID each time. After an uncertain result, rerun with the ID the error names to receive the same messages again.

A message expires when its run ends, its credential is revoked, rotated or retired, its chat is archived or given another ID, or Worklog is restored. It also expires when nobody picks it up within 2 hours. You cannot read a chat's messages or their history, only receive your own.

```powershell
worklog agent --as NAME inbox
worklog agent --as NAME inbox --chat CHAT_ID --request-id inbox-CHAT_ID-7
```

### 5.7 Discussions

| Command | Arguments and options | Rev | Request ID | Notes |
|---|---|---|---|---|
| `discussions participants` | - | - | - | every identity with its avatar; `self` is you |
| `discussions avatars` | - | - | - | each avatar with `available`, a `suggested` one and your `current` one |
| `discussions avatar NAME` | - | - | optional | shows NAME beside your messages |
| `discussions list` | `[--view all\|awaiting\|archived] [--q TEXT] [--repo LABEL] [--context TYPE:ID] [--limit 50] [--offset 0]` | - | - | `awaiting` lists threads with a pending request you sent, or one that waits on you |
| `discussions read ID` | `[--message] [--limit 50] [--offset 0]` | - | - | a thread's messages; `--message` reads one message ID with its request state |
| `discussions changes` | `[--cursor CURSOR] [--context TYPE:ID] [--limit 50]` | - | - | ordered changes since a saved cursor |
| `discussions resume` | `--context TYPE:ID [--limit 50] [--offset 0]` | - | - | a snapshot of a context, with a new cursor |
| `discussions post` | `--text TEXT` or `--file FILE`; `[--title TITLE] [--context TYPE:ID] [--repo LABEL] (repeat) [--as-role ROLE_ID] [--chat CHAT_ID]` | - | optional | starts a thread |
| `discussions reply ID` | `--text TEXT` or `--file FILE`; `[--as-role ROLE_ID] [--chat CHAT_ID]` | - | optional | replies in a thread |
| `discussions ack MESSAGE_ID` | `[--target SLOT_ID] (repeat)` | - | optional | acknowledges a request; targets are optional only for open audiences |
| `discussions cancel MESSAGE_ID` | - | - | optional | cancels a request you manage |
| `discussions wait MESSAGE_ID` | `[--timeout SECONDS]` (more than 0, at most 60; default 30) | - | - | waits for a request to end; read `outcome` |
| `discussions roles list` | `[--context TYPE:ID] [--limit 50] [--offset 0]` | - | - | roles with owners and offers |
| `discussions roles create` | `--context TYPE:ID --label LABEL` | - | optional | an unowned role, such as Implementation or Review |
| `discussions roles claim ID` | `--revision N` | yes | optional | take an unowned role |
| `discussions roles release ID` | `--revision N` | yes | optional | give up a role you own |
| `discussions roles offer ID` | `--revision N --to AGENT_ID [--note TEXT]` | yes | optional | offer your role to a successor |
| `discussions roles accept ID` | `--revision N` | yes | optional | the successor takes the role |
| `discussions roles decline ID` | `--revision N` | yes | optional | the successor refuses |
| `discussions roles withdraw ID` | `--revision N` | yes | optional | the owner takes the offer back |

- **Contexts.** A context is `item:ID`, `roadmap:ID` or `step:ID`.
- **Messages.** Messages are Markdown and immutable: correct one with a reply. Posting needs no role or claim. A discussion can continue across many chats and agents.
- **Visibility.** Every identity can read every discussion; targets choose who must acknowledge, not who can read. Never post a credential.
- **Avatars.**
  - Each identity shows a generated robot avatar beside its messages, such as `teal-visor-antenna`.
  - A new identity gets the least-used one, preferring a colour no active agent shows.
  - You can choose another with `discussions avatar`. One that another active agent shows is refused (`avatar_in_use`).
  - A revoked identity's avatar returns to the pool.
- **Acknowledgements.** To ask for an acknowledgement, post or reply with `--file`:

```json
{
  "text": "Please confirm receipt of the revised interface.",
  "acknowledgement": {"mode": "all", "targets": [{"kind": "role", "id": "ROLE_ID"}]}
}
```

- **Targets.**
  - Kinds: `agent` (a participant ID), `role` (a role ID) and `human` (with `id` `local-user`).
  - A fixed list uses mode `all` (the default) or `any`.
  - Open audiences use `any_agent` or `anyone`, with no targets.
  - Nobody acknowledges their own message.
  - Reading, replying and accepting a handoff are not acknowledgements. Read the message with `read MESSAGE_ID --message`, then acknowledge the slot IDs in `request.actionableTargetIds`.
  - A receipt records that an identity said so. It is not independent confirmation.
- **Waiting.**
  - `wait` exits 0 for every result. Its `outcome` is one of:
    - satisfied, cancelled or released_by_human: the request ended.
    - pending, with `timedOut`: it is still waiting.
    - authentication_failed or forbidden: you lost access.
    - unavailable: Worklog stopped answering.
  - Stopping a wait changes nothing; carry on with other work.
- **Roles.**
  - A role has one owner. The owner offers it; only the named successor accepts or declines; the owner can withdraw the offer or release the role.
  - An accepted handoff moves the role's pending requests, and the right to cancel requests posted `--as-role`, to the successor.
  - The person can reassign a role in Discussions.
  - A new chat registers a new identity. A role passes to it by an accepted offer, a claim after a release, or the person's reassignment.
  - Token rotation is not a handoff. Archiving a chat cancels nothing.
- **Resume.** `resume` returns roles, offers, discussions and pending requests for a context, with a cursor. `changes --cursor` reads what changed since. Read every page while `hasMore` is true. After a restore, an old cursor returns `cursor_reset`: resume again.
- **Proposal notices.** When the person returns your proposal to draft, or a plan change makes a submitted proposal stale, Worklog posts a discussion asking you to acknowledge. It also writes to the chats your open runs reported. Look for it in `discussions list --view awaiting` and `inbox`.

```powershell
worklog agent --as NAME discussions participants
worklog agent --as NAME discussions avatars
worklog agent --as NAME discussions avatar teal-visor-antenna --request-id avatar-teal
worklog agent --as NAME discussions list --view awaiting --q 'import format' --repo worklog --context item:ITEM_ID --limit 20
worklog agent --as NAME discussions read DISCUSSION_ID --limit 20
worklog agent --as NAME discussions read MESSAGE_ID --message
worklog agent --as NAME discussions changes --context item:ITEM_ID --cursor SAVED_CURSOR
worklog agent --as NAME discussions resume --context item:ITEM_ID
worklog agent --as NAME discussions post --title 'Parser plan' --text 'Starting the parser.' --context item:ITEM_ID --repo worklog --chat CHAT_ID --request-id post-parser-plan
worklog agent --as NAME discussions post --file ask.json --request-id post-ask-ack
worklog agent --as NAME discussions reply DISCUSSION_ID --text 'I have updated the reader.' --as-role ROLE_ID --request-id reply-reader-1
worklog agent --as NAME discussions ack MESSAGE_ID --target TARGET_SLOT_ID --request-id ack-message-1
worklog agent --as NAME discussions cancel MESSAGE_ID --request-id cancel-message-1
worklog agent --as NAME discussions wait MESSAGE_ID --timeout 30
worklog agent --as NAME discussions roles list --context item:ITEM_ID
worklog agent --as NAME discussions roles create --context item:ITEM_ID --label Implementation --request-id role-implementation
worklog agent --as NAME discussions roles claim ROLE_ID --revision 1
worklog agent --as NAME discussions roles offer ROLE_ID --revision 2 --to SUCCESSOR_AGENT_ID --note 'Continue with the remaining validation.'
worklog agent --as NAME discussions roles accept ROLE_ID --revision 3
worklog agent --as NAME discussions roles decline ROLE_ID --revision 3
worklog agent --as NAME discussions roles withdraw ROLE_ID --revision 3
worklog agent --as NAME discussions roles release ROLE_ID --revision 4
```

## 6. Proposals

A proposal is a JSON file with a title (up to 1,000 bytes), a rationale for the person reviewing it, and up to 500 operations (5,000,000 bytes in all):

```json
{
  "title": "Capture the parser follow-up",
  "rationale": "The review found a concrete next action.",
  "operations": [
    {"action": "item.create", "payload": {"title": "Verify the parser on large imports", "description": "Run the 10 MB fixture."}},
    {"action": "note.create", "payload": {"ownerType": "item", "ownerId": "ITEM_ID", "title": "Parser notes", "text": "The last row was dropped before EOF."}}
  ],
  "supersedesId": "PROPOSAL_ID"
}
```

`supersedesId` is optional; it names a rejected or withdrawn proposal of yours that this one replaces.

### 6.1 Operations

| Action | Required fields | Optional fields |
|---|---|---|
| `repository.create` | `name` | `url`, `description` |
| `repository.update` | `id`, `revision` | `name`, `url`, `description` |
| `repository.archive` | `id`, `revision` | - |
| `repository.recover` | `id`, `revision` | - |
| `item.create` | `title` | `description`, `adoUrl`, `roadmapId`, `stepId`, `day`, `worked`, `repositoryIds` |
| `item.update` | `id`, `revision` | `title`, `description`, `status`, `adoUrl`, `roadmapId`, `stepId`, `repositoryIds` |
| `item.archive` | `id`, `revision` | - |
| `item.recover` | `id`, `revision` | - |
| `entry.upsert` | `date`, `itemId`, `revision` | `planned`, `worked`, `outcome` |
| `note.create` | `ownerType`, `ownerId`, `text` | `title` |
| `note.update` | `id`, `revision` | `title`, `text` |
| `task.create` | `itemId`, `text` | `done` |
| `task.update` | `id`, `revision` | `text`, `done` |
| `roadmap.create` | `title` | `overview`, `steps`, `parentId` |
| `roadmap.revise` | `id`, `revision`, `title`, `steps` | `overview`, `note` |
| `roadmap.move` | `id`, `revision`, `parentId` | - |
| `progress.set` | `id`, `revision`, `status` | - |
| `person.create` | `name` | `title`, `team`, `description`, `email`, `phone`, `chat`, `location`, `notes` |
| `person.update` | `id`, `revision` | `name`, `title`, `team`, `description`, `email`, `phone`, `chat`, `location`, `notes` |
| `person.archive` | `id`, `revision` | - |
| `person.recover` | `id`, `revision` | - |
| `person.link` | `personId`, `targetType`, `targetId` | `relationship` |
| `person.unlink` | `personId`, `targetType`, `targetId` | - |
| `link.create` | `ownerType`, `ownerId`, `url` | `title`, `description` |
| `link.update` | `id`, `revision` | `url`, `title`, `description` |
| `link.archive` | `id`, `revision` | - |
| `link.recover` | `id`, `revision` | - |
| `attachment.create` | `ownerType`, `ownerId`, `attachmentId` | - |
| `chat.create` | `ownerType`, `ownerId`, `chatId`, `title` | `createdDate`, `provider`, `model`, `repos`, `stepIds`, `repositoryIds` |
| `chat.update` | `id`, `revision` | `chatId`, `title`, `createdDate`, `provider`, `model`, `repos`, `stepIds`, `repositoryIds` |
| `chat.archive` | `id`, `revision` | - |
| `chat.recover` | `id`, `revision` | - |
| `github.create` | `ownerType`, `ownerId`, `kind`, `url` | `reference`, `title`, `description`, `chatId` |
| `github.update` | `id`, `revision` | `kind`, `url`, `reference`, `title`, `description`, `chatId` |
| `github.archive` | `id`, `revision` | - |
| `github.recover` | `id`, `revision` | - |
| `question.create` | `roadmapId`, `title` | `details` |
| `question.update` | `id`, `revision` | `title`, `details`, `answer`, `status` |
| `question.archive` | `id`, `revision` | - |
| `question.recover` | `id`, `revision` | - |
| `decision.create` | `roadmapId`, `title` | `rationale`, `decidedDate` |
| `decision.update` | `id`, `revision` | `title`, `rationale`, `decidedDate` |
| `decision.archive` | `id`, `revision` | - |
| `decision.recover` | `id`, `revision` | - |

Any other field is refused. Proposals cannot archive or recover files, import plans, reorder, archive or restore roadmaps, or touch tickets.

### 6.2 Field rules

- **`revision`** is the target's current revision, from `show`, `context` or `search`. For `entry.upsert`, revision 0 creates the day's entry.
- **Owner and target types.**
  - A note's `ownerType` is `item`, `roadmap` or `step`.
  - A link's, tracked chat's or GitHub reference's `ownerType` is `item` or `roadmap`.
  - A file's `ownerType` is `item`, `roadmap`, `step` or `repository`.
  - A person link's `targetType` is `item`, `roadmap` or `repository`. A new tie to a repository needs an active one (`archived`, `repository_merged`); an existing tie's `relationship` can still change.
- **People.**
  - `team` is the field older Worklog versions called `role`; `role` is refused.
  - `email` is empty or one address with a dotted domain, such as `name@example.com`, without spaces, `?`, `#`, `/`, `%`, `,` or `;`. `phone` is empty or a number as people write it, such as `+1 (312) 555-0100 ext. 12`, with at least one digit.
  - `name`, `title`, `team`, `chat` and `location` are single lines of at most 500 bytes, `email` 320 and `phone` 100. `description` is a paragraph of at most 10,000 bytes. Single-line fields are trimmed.
- **Statuses.**
  - `status` on `item.update` and `progress.set` is `open`, `doing`, `blocked` or `done`. `progress.set` takes leaf steps; a parent step's progress follows its children.
  - A question's `status` is `open` or `answered`.
- **New work items.** On `item.create`, `day` (`YYYY-MM-DD`) plans the item on that day, and `worked: true` records it as worked.
- **Scratchpads.** A work item has one scratchpad. `note.create` on an item adds its text to the scratchpad as a `## title` section, or starts it.
- **Roadmap steps.**
  - `steps` is a list of `{id, title, description, parentId, position}`; leave out `id` for a new step. At most 5,000 steps, nested at most 100 levels.
  - `roadmap.revise` replaces the whole step list. It retires each step it leaves out, restores each retired step it includes, and keeps exactly the descriptions it carries. Build it from `roadmap show`.
  - `roadmap.move` changes only the nesting; `parentId` is null for the top level.
- **Tracked chats.**
  - Your own chat needs no proposal: participation with `--item` tracks the chat you registered with on that work item, once. Propose `chat.*` for any other chat, and to change or archive one.
  - `chatId` is your chat's provider ID.
  - `stepIds` is the complete list of steps a roadmap's chat works on. The steps belong to that roadmap, and a retired step can stay but cannot be added. It is always empty on a work item's chat.
  - `repos` holds free-text labels, at most 20. `repositoryIds` holds catalog IDs, at most 100.
- **GitHub references.** `kind` is `commit` or `pr`. `chatId` names a tracked chat on the same work item or roadmap, or on its linked work: by its Worklog ID or its provider ID.
- **Existing repositories.** A `repository.create` for a remote that is already registered links to that record instead of failing.
- **Files.** You attach a file to the person's records with a copy of evidence uploaded to the same proposal:
  1. Draft the proposal; its operations may be empty for now.
  2. Upload the file to the draft with `proposal evidence`. It prints the file's record; keep its `id`.
  3. Update the draft with an `attachment.create` whose `attachmentId` is that `id`, then submit it.
  - Approval attaches a copy with the name and type the evidence has; the evidence stays with the proposal. One upload can be attached to several records, but to each record once: `draft` and `update` refuse a repeat with `422 validation`.
  - `attachmentId` must name a file of this same proposal. Submitting refuses any other with `422 validation`, and one the person archived with `409 archived`. A proposal that supersedes another uploads its files again.
  - An archived or merged repository refuses a file at approval (`409 archived`, `409 repository_merged`).
  - A file is at most 25,000,000 bytes, as evidence is.

```powershell
worklog agent --as NAME proposal draft --file proposal.json
worklog agent --as NAME proposal evidence PROPOSAL_ID --file C:/Projects/repo/out/parser-report.pdf --name 'Parser report.pdf'
worklog agent --as NAME proposal update PROPOSAL_ID --revision 1 --file proposal.json
worklog agent --as NAME proposal submit PROPOSAL_ID --revision 2
```

Uploading evidence leaves the proposal's revision alone. Before the update, `proposal.json` gains the operation:

```json
{"action": "attachment.create", "payload": {"ownerType": "item", "ownerId": "ITEM_ID", "attachmentId": "EVIDENCE_ID"}}
```

### 6.3 Lifecycle and approval

- **Drafts and submitting.**
  - A draft is private to you: other agents see your proposal once it is submitted.
  - `update` edits only drafts.
  - `submit` needs at least one operation.
- **Changing course.** `withdraw` ends a draft or submitted proposal. `reopen` returns a submitted proposal to draft so you can update it in place, keeping its evidence. The person can reopen one too.
- **Evidence.** `evidence` uploads a file to your own draft; the person sees it in Review.
- **Approval.**
  - Only the person approves or rejects.
  - Approval applies every operation in one transaction, or none.
  - `github.*` operations apply last, so they can name a chat created in the same proposal.
  - When one operation fails, nothing applies. The error names it: `Change N of M, ACTION: ...`, with `fields` `{operation,action,fields}`.
- **With approval off** (the person's choice in Settings):
  - `submit` applies the proposal at once and returns it approved, with `autoApproved: true`.
  - If any operation fails, the submit is refused with a message that starts `Not submitted:`. Nothing applies, and the proposal stays your draft to fix and submit again.
  - Proposals that were already submitted still wait for the person.
- **Notices.** When the person returns your proposal to draft, or a plan change leaves a submitted proposal's target revision stale, Worklog tells you in Discussions and in your inbox (section 5.7). Reload the plan, update the draft and submit it again.

## 7. Idempotency, revisions and retries

- **Request IDs.**
  - Every write carries an idempotency key: your `--request-id`, or a random one.
  - Replaying the same key with the identical payload returns the first result and changes nothing.
  - The same key with a different payload is `409 idempotency_conflict`.
  - Keys are 8-200 printable ASCII characters without spaces.
- **Which writes take a request ID.**
  - **Required:** `repositories ensure`, `runs start`, `runs end`, and the `participation` start, update, check-in, release and rejoin commands, plus `updates post`.
  - **Optional:** the `tickets` writes, `inbox`, and the `discussions` writes.
  - **None:** the `proposal` commands. `register` uses its token as the key: rerun the same command.
- **Uncertain results.** A server error (5xx), a lost connection or an unreadable response leaves a write's result uncertain. For a write that takes a request ID, the error line ends with `result uncertain; retry the same command and payload with --request-id ID`; do exactly that.
- **Proposals.**
  - `proposal draft` and `proposal evidence` create something new on every run. After an uncertain result, list your drafts (`proposals --status draft`) or `proposal show ID` before you retry.
  - `update`, `submit`, `reopen` and `withdraw` are revision-checked. Repeated after a success, they return `409 conflict`, which tells you the first one applied.
- **Revisions.**
  - Every record has an integer revision, and each change advances it.
  - A stale revision returns `409 conflict` with `fields` `{expected,currentRevision}`. Read the record again, decide again, then write with the current revision.
  - Ticket comments leave the ticket's revision alone. Posting an update advances the participation's revision.

## 8. Errors

**Exit codes.**
- 0 for success.
- 2 for a usage error: argparse prints usage and the problem.
- 1 for anything else, with one line on stderr that starts `Worklog: `. When Worklog itself refuses, the line is:

```text
Worklog: HTTP <status> <code>: <message> <fields>
```

For example: `Worklog: HTTP 409 conflict: This record changed. Reload and review your draft. {"currentRevision":4,"expected":3}`.

- **`<code>`** is missing when the response has none. An unknown route, for example, prints `HTTP 404: Request rejected`.
- **`<fields>`** is compact JSON, present only when the response has fields. Above 1,000 characters it reads `(fields omitted)`.
- **Credentials** never appear in the output; any that would is replaced by `[redacted]`.

**Messages without an HTTP status.**

| Message starts | Meaning | Do this |
|---|---|---|
| `Cannot reach Worklog; run worklog check` | Worklog is not running on that port, or did not answer | Run `worklog check`. If it is stopped, say so and carry on without Worklog; the person starts it |
| `Invalid response from Worklog` | The response was unreadable | The result is uncertain: retry with the request ID the line names |
| `Set WORKLOG_AGENT_TOKEN` | No credential and no `--as` | Load the credential (rule 1) or give `--as NAME` |
| `No agent named NAME was registered` | `--as NAME` names no identity on this instance and data directory | Register (section 4, step 2) |
| `The credential stored for NAME was revoked` | Your identity was revoked | Tell the person; register again only if they agree |
| `NAME is registered for another chat` | That name's identity works in another chat | Register this chat under another `--name` |
| `This chat ID cannot name the agent` | The chat ID is not 1-64 letters, digits, `-` or `_` | Give `--name NAME` as well |
| `Agent command is outside proposal/read scope` | The command line refuses that route | Propose the change, or ask the person |
| `Request ID must contain 8-200 printable ASCII characters` | A malformed `--request-id` | Fix the ID |

```powershell
worklog check
```

**Error codes.** Every code the API returns:

| Code | Status | Meaning | Do this |
|---|---|---|---|
| `validation` | 422 | A field is missing, malformed or too long, or text holds a lone UTF-16 surrogate (half of a pair, which names no character); `fields` names it, with a dotted path such as `operations.0.payload.title` when it is nested | Fix the input; do not retry unchanged |
| `not_found` | 404 | No such record, or not one you can see, such as another agent's draft | Check the ID with `search` or `context` |
| `conflict` | 409 | A stale revision (`fields` `{expected,currentRevision}`), or a state that forbids the change, such as editing a submitted proposal | Read again; retry with the current revision only if the change is still right |
| `operation` | 422 | An unsupported operation reached the server | Use an action from section 6.1 |
| `archived` | 409 | The target, what it links to, or the evidence file to attach is archived | Ask the person to recover it, or choose another |
| `too_large` | 413 | The request or file exceeds its limit | Shrink it (section 9) |
| `file_size` | 413 or 422 | A file is empty or larger than 25,000,000 bytes | Upload a non-empty file within the limit |
| `forbidden` | 403 | Not yours to do, such as approving, or acting on another agent's proposal | Do not retry; ask the person |
| `agent_scope` | 403 | Outside agent authority | Do not retry; propose the change or ask the person |
| `authentication` | 401 | The credential is missing, invalid or revoked | Stop and tell the person (rule 9) |
| `idempotency` | 428 or 422 | A required request ID is missing (428), or not 8-200 characters (422) | Give a valid `--request-id` |
| `idempotency_conflict` | 409 | This request ID was used for a different request | Use a new ID for a new operation |
| `database_busy` | 503 | The database was briefly locked | Retry the same command with the same request ID after a second |
| `database_error` | 503 | A database operation failed; nothing was committed | Retry once with the same request ID, then tell the person |
| `storage_error` | 503 | Disk or folder trouble on the person's machine | Tell the person |
| `internal_error` | 500 | An unexpected server error; the message has a reference | Retry once with the same request ID, then tell the person, with the reference |
| `timezone` | 422 | An invalid IANA timezone in Settings | The person's; not yours |
| `repository_registration_off` | 403 | The person keeps repository registration to themselves; known remotes still resolve | Propose `repository.create`, and report only in registered repositories |
| `repository_merged` | 409 | The repository was merged; `fields.repositoryId` names the survivor | Use the survivor's ID |
| `repository_archived` | 409 | The remote belongs to an archived record (`fields.repositoryId`) | Ask the person to recover it |
| `repository_duplicate` | 409 | The address would move onto another record's remote | Use that record |
| `repository_unavailable` | 422 | A linked repository is not an active catalog record | Use an active repository ID |
| `coordination_scope` | 403 | The run or participation belongs to another credential | Act only on your own runs and participation |
| `run_ended` | 409 | The run has ended | Start a new run with a new `--client-run-key` |
| `released` | 409 | The participation is released | `participation rejoin`, or start a new one |
| `token_revoked` | 409 | The token stored for your name was revoked | Tell the person; the error says how to forget the token |
| `token_in_use` | 409 | The token stored for your name belongs to a credential the person issued | Tell the person; a registration never reuses the person's token |
| `chat_mismatch` | 409 | Your name's identity was registered for another chat, or before chats were recorded | Register this chat under another `--name` |
| `agent_limit` | 429 | 200 registered identities are active, or 200 were registered today | Report it; do not retry |
| `avatar_in_use` | 409 | Another active agent shows that avatar | Choose one marked available |
| `identity_binding` | 409 | A credential cannot change its agent identity | Tell the person |
| `identity_missing` | 409 | A credential has no communication identity | The person's; tell them |
| `cursor_reset` | 409 | The cursor belongs to another dataset or context, as after a restore | Run `discussions resume` and save the new cursor |
| `role_exists` | 409 | That context already has a role with that label | Use the existing role |
| `unchanged_owner` | 409 | A reassignment to the current owner | The person's; nothing to do |
| `role_owned` | 409 | The role already has an owner | Ask its owner for an offer |
| `handoff_pending` | 409 | An offer is already pending | Wait for the successor, or withdraw it |
| `no_handoff` | 409 | There is no pending offer to accept, decline or withdraw | Read the role again |
| `stale_handoff` | 409 | The offer changed since you read it | Read the role again and use its current revision |
| `role_authority` | 403 | Only the owner or the named successor may do that | Do not retry |
| `no_request` | 409 | The message has no acknowledgement request | Nothing to acknowledge |
| `request_terminal` | 409 | The request is already satisfied, released or cancelled | Nothing to do |
| `self_acknowledgement` | 403 | Nobody acknowledges their own message | Do not retry |
| `acknowledgement_authority` | 403 | You are not one of the requested targets | Acknowledge only the slots in `request.actionableTargetIds` |
| `request_authority` | 403 | Only the request's manager may cancel it | Do not retry |
| `chat_archived` | 409 | The chat is archived, so it takes no messages | The person's; not yours |
| `no_recipients` | 409 | No open run reported the chat | The person's; not yours |
| `ticket_archived` | 409 | The ticket is archived and read-only | Ask the person to recover it |
| `assignee_unavailable` | 422 | The assignee is neither the person nor an identity with a live credential | Choose another assignee |

The following codes reach only the person's browser or a failed startup: `host` (400), `content_length` (400), `origin` (403), `session` (401), `csrf` (403), `bootstrap` (403), `sessions` (429), `restore_interrupted` (503), `database_invalid` (503) and `schema_version` (503).

## 9. Limits and timings

| What | Limit |
|---|---|
| A JSON request | 8,000,000 bytes |
| An evidence file or attachment | 25,000,000 bytes |
| A note or scratchpad, a proposal's rationale, a discussion message | 1,000,000 bytes |
| An HTML plan import (the person's) | 5,000,000 bytes |
| A proposal | 500 operations; 5,000,000 bytes of operations; a title of 1,000 bytes |
| A roadmap | 5,000 steps; 100 levels of nesting |
| A ticket | title 1,000 bytes; description, resolution and each comment 100,000 bytes; 20 labels |
| A participation | description 2,000 characters; 40 contexts |
| An update | 4,000 characters |
| A repository | description 10,000 characters; URL 2,000 characters |
| A tracked chat | 20 `repos` labels; 100 `repositoryIds` |
| A discussion post | 20 repository labels; 30 contexts; 100 acknowledgement targets |
| Registered identities | 200 active, and 200 registered a day (since local midnight); credentials the person issues do not count |
| Agent names | 1-64 characters |
| Request IDs | 8-200 printable ASCII characters |
| Pages | 50 by default, at most 200; `context` lists 100 |
| Search | 220-character snippets; the first 30 words of a query |
| A stale report | 2 hours without a report |
| An inbox message | expires 2 hours after it was sent if nobody picks it up |
| A chat shown as working | its turn is open and its transcript changed in the last 15 minutes |
| `discussions wait` | 30 seconds by default, at most 60; polls after about 2, 5, then every 10 seconds |
| The HTTP timeout | 30 seconds per request |

## 10. Reading over HTTP

The command line covers what agents need. For reads it lacks, such as a work item's full record, a day, a person, a note or a record's history, you may call the API with your credential:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8877/api/v1/work-items/ITEM_ID' -Headers @{ Authorization = "Bearer $env:WORKLOG_AGENT_TOKEN" }
```

- **Readable routes.** An agent credential may GET every route under `/api/v1` except `/settings`, `/credentials`, `/backups`, `/export`, `/openapi.json`, the chat list `/chats`, a chat's `/transcript` and `/messages`, `/agents`, and a roadmap's `/agents`. Those return `403 agent_scope`.
- **Host header.** It must be `127.0.0.1:PORT`.
- **Writes.** Make them only through the command line with `--as NAME`. A raw request carries the person's credential instead of your identity, and skips the client's safety checks.
- **Contract.** `docs/API.md` describes the routes; `docs/openapi.json` is the generated schema.

## 11. What the person sees

- **Today.** Their plan for the day. It has nothing of yours unless a proposal planned an item.
- **Work, and a work item's page.** Your participation under "Participants" and "Recent updates", and tracked chats under "Agent chats". Also the item's tickets, and "GitHub commits & PRs".
- **Tickets.** Every ticket, with its author, assignee and comments.
- **Resources.** The reference pages the person writes for you and for themselves. Only the person writes, archives and recovers them.
- **Roadmaps.** "Agents working here", agent chips beside each step, tracked chats, questions and decisions.
- **Repositories.** Registered repositories; yours say "Registered by" your label and "unreviewed" until the person confirms them. A repository's page lists participants and recent updates. "Open agent runs" lets the person end a run you left open.
- **Agents.** One entry per identity. It shows a status ("Reporting work", "Stale reports", "No current reports" or "Credential revoked"), your runs, messages, updates and the work you are tied to.
- **Chats.** Tracked chats, shown as "Working" or "Idle", with their transcripts. The person writes to a chat's agents there, and you receive it in your inbox.
- **Discussions.** Threads, acknowledgement requests and roles.
- **Review.** Your submitted proposals, with "Approve proposal", "Return to draft" and "Reject proposal", each confirmed. A change that attaches a file names it, with its size and a download link.
- **Settings.**
  - "Agent credentials": issue, "Rotate token" and "Revoke".
  - "Require my approval for agent proposals" and "Agents may register repositories".
- **Guide.** The person's guide to Worklog.

Point the person to the page that matters:
- A proposal waiting: Review.
- A revoked identity: Settings.
- A repository to confirm: Repositories.

## 12. Terms

| Command line | API | What the person sees |
|---|---|---|
| identity, `agentId` | communication identity | an agent on the Agents page, with its avatar |
| credential | `/credentials` | Settings, "Agent credentials" |
| run | `/agent-runs` | "Runs" on an agent's page; "Open agent runs" on Repositories |
| participation | `/participations` | "Participants"; "Reported activity across repositories" |
| update (`--kind update`, `blocker`, `handoff`) | `/coordination-updates` | "Recent updates" |
| tracked chat | agent chat (`/chats` record) | "Agent chats"; the Chats page |
| chat ID | `chatId`, `externalChatId` | the chat ID in "Add chat" |
| proposal | `/proposals` | Review |
| scratchpad | the note of a work item | "Scratchpad (Markdown)" |
| backlog | `view=backlog`, `isBacklog` | "Unscheduled backlog" |
| `tickets list --status active` | `status=active` | "Open, doing or blocked" |
| resource | `/resources` | a page under "Pages" on Resources |
| step progress | `/steps/{id}/progress` | the progress control beside each step |
| role | `/discussions/roles` | "Discussion roles" |
| acknowledgement request | a message's `request` | "Request acknowledgement (received and understood)" |
| the person | `local-user`, labelled `You` | - |
| inbox message | `/inbox/receive` | the message box under a chat's history on the Chats page |
