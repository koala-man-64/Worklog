# Upgrading Worklog

How to upgrade Worklog, what each version changes, and the way back. The
[README](../README.md) covers installing it, and [OPERATIONS.md](OPERATIONS.md) its backups and
restores.

## Upgrade

For an upgrade, stop Worklog, make a manual backup with the installed version, update the checkout, run `Setup.ps1`, then start and check the application. Use the same data directory and port throughout. The first startup snapshots a supported version 1 to 26 database and upgrades it to version 27 in one transaction.

`./scripts/Run.ps1` does the stop, backup, setup and start in one step after you update the checkout, passing the same `-DataDir` and `-Port` to each. Its backup is taken before the new version first starts, so it is the pre-upgrade backup; if the stop or backup fails, it ends before setup. It runs Setup's shared-folder check first, so a refusal leaves Worklog running.

## What each version changes

- **Version 27** removes Reminders. The upgrade drops the table that held reminders, every reminder with it, and their entries in search. The records a reminder named stay as they are, and no other record changes. The pre-migration backup keeps the table, but only the three newest pre-migration backups are kept: copy it aside if you still need a reminder.
- **Version 26** removes work item checklists. The upgrade drops the table that held each work item's tasks, every task with it. The work items stay as they are, and no other record changes. The pre-migration backup keeps the table, but only the three newest pre-migration backups are kept: copy it aside if you still need a task.
- **Version 25** removes the HTML plan import. The upgrade drops the table that held imported plans, with their original files and previews. A roadmap an import made stays as it is, and no other record changes. The pre-migration backup keeps the table, but only the three newest pre-migration backups are kept: copy it aside if you still need an original.
- **Version 24** links each tracked chat to the repository each of its labels names: the one repository, merged ones aside, whose name the label is exactly, as version 22 did for discussions. The labels stay, and a chat takes no new ones: link a registered repository instead. The upgrade adds only those links, and changes no record.
- **Version 23** has the database itself refuse what only Worklog's code refused before: a task, note, file reference or attachment archived other than 0 or 1, and a note or attachment owned by a kind of record it can't have. The upgrade builds those four tables again and changes no record. A row that already breaks one of those rules can only come from outside Worklog: the upgrade names it and changes nothing, and Worklog doesn't start until it is fixed. The message is in `server.log` (and `restore` prints it): set the row right with the `sqlite3` shell, with Worklog stopped and after a manual backup. Each refused start takes another pre-migration backup, of which the three newest are kept. Building the tables again leaves the space their old copies took, attachments' content included, free inside the file until `compact` gives it back, so the upgrade needs free disk of about twice the database: one copy for the pre-migration backup and one for the rebuilt tables.
- **Version 22** lets you tag a discussion with the records it is about (work items, roadmaps and their steps, repositories, tickets, releases, people, resources, questions and decisions) and pin it, and gives each repository a discussion of its own. It is the first upgrade that adds records: it opens a discussion, written as you, for each repository that is neither archived nor merged into another (an archived one gets its discussion when you recover it), each as of the upgrade, so they list first until other threads have newer activity. It also tags each discussion with the repository its free-text repository label names when exactly one repository not merged into another has that name. The labels themselves stay. Each tag a discussion already had counts as added by whoever wrote its first message.
- **Version 21** gives each day's reflection its own revision, so reordering a day or adding work to it no longer conflicts with a reflection you're writing; each reflection starts at its day's revision, and the upgrade changes no record.
- **Version 20** adds reminders, which start empty, and the upgrade changes no record.
- **Version 19** adds releases, which start empty, and changes no record.
- **Version 18** records the directory each agent registers from, which Review shows beside the agent's proposals. Agents that registered before it have none, and the upgrade changes no record.
- **Version 17** adds agent ties: which records each agent is tied to, with its role on each, and whether you or the agent set the tie. It starts with none and changes no record.
- **Version 16** records which work items and roadmaps each approved proposal was applied to, so their pages list those proposals in order. The upgrade fills that in for the proposals already approved, from their changes, and changes no record. A work item or roadmap an agent's proposal created is found from its history. One a proposal you wrote yourself created isn't, since you also create them directly.
- **Version 15** adds Waiting to a work item's statuses, for work that waits on someone else; tickets and roadmap steps keep Open, Doing, Blocked and Done, and the upgrade changes no record.
- **Version 14** lets you put each work item's tasks in your own order: every task keeps the place it was listed in, and no task counts as changed by the upgrade.
- **Version 13** binds chats to agent identities: what you write into a chat reaches an agent that registered with that chat, and any other agent whose run reports the chat's ID only once you confirm it on the chat page. A message still waiting for an agent bound to neither ends at the upgrade, as not confirmed.
- **Version 12** copies state that Worklog read from event history into columns: who registered each agent identity and the chat it registered with, when each proposal was last submitted and whether automatic approval applied it, and which stale-plan notices were sent. The events themselves stay as they are.
- **Version 11** adds only derived data: a key for each search entry, so a save no longer reads the whole search index; indexes for frequent lookups; and a date on each retry receipt, so receipts older than 14 days are pruned at startup and every hour.
- **Before version 11.** Existing work, roadmap questions and decisions, discussions, data identity, and agent credentials are preserved, every existing agent identity receives a generated avatar, chats start with no messages from you, and the ticket and resource lists start empty. Repositories are keyed by remote: when several records already name one remote, all are kept and every one but the oldest active record is marked as its duplicate. Each existing repository takes its registrant from its history and counts as reviewed. Each work item's active notes become one scratchpad: they fold into its oldest note, oldest first, each title as a `## heading`, and the others are archived whole, so a scratchpad that would pass the 1,000,000-byte note limit keeps what fits and says how many notes stay only in the archive. Existing chat repository labels remain labels; link them to catalog repositories explicitly. Each person's "Title or team" becomes their team, with the new title, description and contact details empty, and their ties to work and roadmaps stay as they were. An agent's proposal that is still open and sets that field is carried over to the team, under a new revision, so its agent reads it again before changing it.

## Roll back

Keep the previous version and its manual backup until verification succeeds. To roll back, stop Worklog, switch to the previous version and run its setup, then use that version’s `restore` command with the pre-upgrade backup. Start and check Worklog again. Restoring returns the data to the time of the snapshot, creates a new data identity, and revokes the restored credentials.

## The way back from version 27

From version 27 to version 26, a way back keeps every record. The reminders version 27's upgrade dropped don't come back with it, and neither do their search entries; the pre-upgrade backup has them. With Worklog stopped and after a manual backup, run this on the database, then start the version 26 checkout:

```sql
CREATE TABLE reminders(id TEXT PRIMARY KEY,title TEXT NOT NULL,due_date TEXT NOT NULL,due_time TEXT,state TEXT NOT NULL DEFAULT 'open' CHECK(state IN('open','done')),target_type TEXT CHECK(target_type IN('item','ticket','roadmap','person','repository','chat')),target_id TEXT,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,done_at TEXT,notes TEXT NOT NULL DEFAULT '',CHECK((target_type IS NULL)=(target_id IS NULL)));
CREATE INDEX reminders_due ON reminders(archived,state,due_date,due_time);
CREATE INDEX reminders_target ON reminders(target_type,target_id);
PRAGMA user_version=26;
```

Upgrading again drops the table again, with any reminder added at version 26 and its search entry.

Only the latest version's way back is kept here. Each older one was given while its version was
the latest: the history of this file in the repository has them, and before this file, the
history of `README.md`. Restoring the pre-upgrade
backup with the earlier version, as *Roll back* says, works from any version.
