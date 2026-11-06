# Worklog guide

Worklog is a personal workbench that runs entirely on your own machine. It keeps:

- your plan for each day and what you actually did;
- a backlog of work with its notes, checklists, files and links;
- tickets for issues;
- roadmaps for bigger efforts;
- the people involved;
- reference pages you write for yourself and your agents.

It is also where you work with AI coding agents. They report what they are doing, talk with you, file tickets, and propose changes to your records that you approve before anything changes.

This guide shows how to use Worklog. You can read it here or on the **Guide** page in Worklog's sidebar. To install, start, stop, back up or upgrade Worklog, see the README in your Worklog folder. AI agents have a guide of their own, which they print with `worklog agent guide`.

## Start here

### Open Worklog

Worklog runs at `http://127.0.0.1:8877`. Start it from PowerShell in your Worklog folder with `./scripts/Worklog.ps1 start`; it opens the page for you. Everything stays on this machine: no account, no internet connection, nothing hosted.

### Your first day

1. On **Today**, type what you will work on into "New work item" and press "Add work". The item is planned for today.
2. As you work, tick "Worked on" for the item.
3. Write what happened, and what comes next, in "Outcome / next action".
4. Set its status (Open, Doing, Waiting, Blocked or Done) with the status menu. Waiting is for work that waits on someone else, such as a review or a reply.
5. Before you stop, write a few lines in "End-of-day reflection (Markdown)".

You never press Save: Worklog saves as you type. Tomorrow, "Open work" on Today lists everything still open, ready to plan again.

### The pages at a glance

The sidebar lists these pages, in this order:

| Page | What it is for |
|---|---|
| Today | Plan a day, record what you worked on, reflect |
| Work | Every work item, the backlog, and each item's page |
| Tickets | Issues you and your agents file, work and close |
| Roadmaps | Bigger efforts broken into steps, with versions |
| Repositories | The code repositories you and your agents work in |
| People | Who you work with and how to reach them, tied to work, roadmaps and repositories |
| Resources | Reference pages you write for yourself and your agents |
| Agents | Every AI agent with a credential and what it reports |
| Chats | The agent chats you track, and their transcripts |
| Discussions | A forum you share with your agents |
| Review | Agent proposals waiting for your decision |
| Settings | Timezone, appearance, backups, agent permissions and credentials |
| Guide | This guide |

## How Worklog behaves

### Saving and drafts

- **Text saves itself** about a second after you stop typing, or when you leave the field. "Save now" saves at once. Long fields have "Preview" to see the Markdown rendered, and "Edit" to return.
- **The status beside a field** reads "Unsaved draft", "Saving…" or "Saved". If a save fails, it reads "Save failed: draft kept. Retry." and your text stays.
- **Unsaved text survives a reload** in the same browser. If the browser blocks storage, a warning asks you to keep the tab open until the text is saved.
- **A save that failed on the network** keeps its request key, so retrying never applies it twice.

### Conflicts

A conflict happens when a record changed somewhere else after you started editing: in another tab, or through an agent's proposal you approved. Worklog refuses to overwrite it.

The field shows "Conflict: draft kept. Reload to compare." beside the saved value. Choose "Use saved value" to drop your draft, or "Replace saved value with my draft" to keep yours.

### Archive and recover

Nothing is deleted. Archived records leave the lists, and their pages show what happened to them:

- Work items, tickets, roadmaps, people, repositories and resources have "Archive" and "Recover" on their page or row.
- Lists have "Show archive" to show only archived records.
- On a work item, roadmap or repository, archived notes, links, files and tasks are under "Archived supporting records", each with "Recover".
- An archived roadmap, ticket or resource is read-only until you recover it.

### Activity history

Work items, roadmaps, people, tickets and resources keep an "Activity history" at the bottom of their page. Every change is listed with who made it, including changes applied from an agent's proposal. Each entry is the raw record of the change.

Work items and roadmaps also keep "Applied proposals" just above it. It lists every proposal applied to the record, the latest first, each numbered in the order it was applied. A row names the agent that proposed it, when it was applied, and which of its changes were made here, by their numbers on Review. Its title opens the proposal on Review.

A change counts toward a work item when it changes the item itself or what the item holds: tasks, scratchpad, links, files, agent chats, GitHub references, day entries and people. It counts toward a roadmap in the same way: the roadmap itself, its steps and their progress, its questions and decisions, and its notes, links, files, chats, GitHub references and people. It also counts toward the roadmap it puts a work item on, or a sub-roadmap under.

### Search

The box at the top of every page ("Search your work…") searches:
- work items, notes, file names, links and tracked agent chats
- GitHub references, roadmaps and steps, questions and decisions
- people, discussions, tickets and ticket comments
- resources

Tick "Search archived records" to search the archive instead. Agent proposals are not searched: find them on Review. Clearing the box returns you to the page you were on.

### Where your data lives

Everything lives in one database file, `worklog.sqlite3`, in your data folder: your records, every attached file and every imported plan. The folder is `%LOCALAPPDATA%/Worklog` unless you chose another with `WORKLOG_DATA_DIR`. Worklog backs it up when it starts and once a day while it runs. Drafts belong to one data folder: after a restore or a switch of folder, old drafts stay in the browser but are never applied, and Settings says so.

### What agents can change

Agents act with credentials you issue in Settings (see [Bring an AI agent in](#bring-an-ai-agent-in)). They can:

- read your work, roadmaps and resources
- report what they work on and where
- talk in Discussions
- file and work tickets
- tie themselves to the records they work on, with their role there, and change or remove the ties they set
- receive the messages you write into their chat

They never change your work items, days, roadmaps, people or repositories directly, nor attach files to them. They propose those changes, and you approve or reject each proposal on Review. You can turn approval off in Settings; then a proposal applies as soon as its agent submits it. Resources are yours alone: agents read them, but nothing they do changes one, not even a proposal.

## How to

### Plan your day and record what you did

1. Open **Today**. "Working day" shows today; pick another date to plan ahead or catch up.
2. Add work to the day:
   - To add new work, type it into "New work item" and press "Add work". To record work you already did without planning it, choose "Log work already performed" in "Capture mode" before you add it.
   - To plan existing work, find it under "Open work" and press "Work on today". On another day the button names that day. "Find open work…" narrows the list.
3. For each entry:
   - tick "Planned" and "Worked on" as they become true
   - set its status
   - write an "Outcome / next action"
4. Put the entries in order by dragging the grip, or with the up and down buttons.
5. At the end of the day, write the "End-of-day reflection (Markdown)". The counts beside the day's work show how many you worked on, completed and blocked; waiting work doesn't count as blocked. "Continue your next open item" jumps to the next planned item that is open or doing, so it passes over waiting and blocked work.
6. For your standup update, read "The day’s summary" below the day's work. It lists each entry ticked "Worked on" with its "Outcome / next action", under the status its item ended the day with: "Done", "Doing", "Open", "Waiting", then "Blocked". Press "Copy summary" and paste it into a chat, an email or a Markdown box; it keeps its headings and bullets.

The summary keeps up as each outcome saves. A past day's summary keeps the status each item had that day, even after you change it later.

Each entry has tabs. "Outcome / next action" holds the day's note. "Tasks", "Scratchpad", "Links", "Files" and "Repositories" hold the work item's own records, so you can work on them without leaving the day.

- Before you open the tabs, they show what they hold: tasks done out of all, how many links, files and repositories, and a dot when the scratchpad has text.
- "Tasks" is the item's own checklist: each task says when it was "Created" and last "Updated", and the grip and the up and down buttons put the tasks in order, as on the item's page.
- The arrow keys move between tabs.
- The badge beside an entry's title is the item's stack rank.
- Each entry's title row collapses and expands; "Collapse all" and "Expand all" do every entry at once.

### Move work to another day

1. On **Today**, press "Reschedule" beside the entry's title.
2. Pick the new date and press "Move". "Cancel" or Escape closes the picker.

The item is planned on the new day instead. If you recorded nothing on it today, it leaves today. If you ticked "Worked on" or wrote an "Outcome / next action", today keeps its entry with what you recorded, no longer planned; to take that off today as well, see [Take work off a day](#take-work-off-a-day).

### Take work off a day

When you planned work and didn't get to it, take it off the day, so the day doesn't list it among its work. To plan it for another day instead, [move it](#move-work-to-another-day).

1. On **Today**, press "Remove from today" beside the entry's title. On another day the button names that day.
2. An entry that only plans the work comes off at once. If you ticked "Worked on" or wrote an "Outcome / next action" on it, Worklog asks first: "Confirm remove" discards them with the entry, and "Cancel" or Escape keeps it.

The work item itself doesn't change. While it isn't done it is back under "Open work", ready for "Work on today" again. Its "Daily record" no longer lists the day, and its "Activity history" keeps the entry as it was. An archived item's entry can't be removed.

### Capture work and keep a backlog

1. Open **Work** and type into "New work item". Pick repositories for it if you like, then press "Add work".
2. Narrow the list:
   - by text ("Find in work…")
   - by repository
   - by person
   - by status: the Open, Doing, Waiting, Blocked and Done buttons combine; with none pressed, every state shows.
3. Switch "Work view" between "All work" and "Unscheduled backlog". The backlog holds items not done and not planned for today or later.
4. Rank the work (below).

Work is stack ranked:

- Unfinished work is numbered from #1, the work that matters most, and lists show it in that order, done work after it. New work starts last.
- The up and down buttons on a row move an item past the one shown beside it, so they work the same in a filtered list.
- The rank button at the start of a row, or beside the status on an item's page, opens "Move to rank": type a rank and press "Move", or "Cancel" (or Escape).
- Done and archived work leaves the ranking but keeps its place, so reopening or recovering an item puts it back where it was.

The list shows 100 items at a time ("Previous work" and "Next work").

### Keep notes, checklists, links and files on a work item

Open the item from **Work**. Its page holds, from top to bottom:

- **The header:** the title, the status menu, the stack rank, and "Archive" or "Recover".
- **"Title" and "Description (Markdown)".**
- **"Context"**:
  - the "Azure DevOps link", with "Open work item ↗" to follow it
  - the repositories the work touches ("Link repository")
  - the "Roadmap" and "Roadmap step" the item belongs to
  - "Plan for day", with a "Schedule date"
- **"Tasks"**: type a "New task" and press "Add task". Tick tasks when done, and done ones are struck through. "Edit task" renames one. Under each task, "Created" and "Updated" say when it was added and when its text or tick last changed. Put the tasks in order by dragging the grip, or with the up and down buttons; a new task goes last.
- **"Scratchpad (Markdown)"**: one running note for the item. Notes archived earlier stay readable under "Archived notes".
- **"People"**: choose someone in "Choose person" ("+ New person…" adds one), give a "Relationship or role", and press "Tie person".
- **"Links"**: a "Link title", "Link URL" and "Link description", then "Add link".
- **"Agent chats"**: chats an agent works in for this item ([Follow a chat and message its agents](#follow-a-chat-and-message-its-agents)).
- **"GitHub commits & PRs"**: choose a "GitHub reference type", enter the address, and press "Add GitHub ref".
- **"Tickets"**: "File ticket" files a ticket tied to this item.
- **"Files"**: under "How to add a file", either:
  - "Attach a copy" stores the file in Worklog, up to 25 MB.
  - "Reference a local file" keeps only its path. "Copy path" copies it for you to open yourself; Worklog never opens local paths.

Below these come the item's "Daily record" (every day it was planned or worked), "Participants" and "Recent updates" (what agents report about it), "Archived supporting records", "Applied proposals" (see [Activity history](#activity-history)) and "Activity history".

### Track an issue as a ticket

1. Open **Tickets**. Type a title into "New ticket title", choose its kind (Bug, Incident or Task) and priority (Urgent, High, Normal or Low), and press "File ticket".
2. The ticket gets a key such as T-12. You and your agents cite it in commits, discussions and other tickets.
3. On the ticket's page you can:
   - set "Kind", "Priority" and "Assignee"
   - write the "Description"
   - add labels with "New label" and "Add label"
   - link repositories
   - link one work item: "Find existing work", then "Link work item"
4. Add notes as you learn with "Add a comment". Comments can't be edited: correct one with another.
5. To close the ticket, write the "Resolution (needed to mark Done)", then set its status to Done. Reopening keeps the resolution.

The list shows open, doing and blocked tickets, most urgent first. Filter it by text (which also searches comments and resolutions), status, kind, priority, label, repository and assignee. "Recent activity first" sorts by latest activity instead. Closed tickets stay searchable, so the next agent can find what fixed an issue last time.

A ticket filed from the browser starts open. An agent can file a past incident already Done.

### Plan a larger effort as a roadmap

1. Open **Roadmaps**, type into "Roadmap title" and press "Create roadmap".
2. Press "Edit structure". Write the "Title" and "Overview (Markdown)", then add steps with "Add step". For each step you can:
   - give it a title and description
   - nest it under another step by choosing its parent
   - move it up or down
   - "Retire step"
3. Add a "Version note" and press "Save new version". Each saved version is kept for good under "Immutable versions", where "Restore as new version" brings an old one back.
4. Set progress on leaf steps (Open, Doing, Blocked or Done) in "Plan & progress". A parent step follows its children: Done when all are done, Blocked if any is blocked, otherwise Doing once any has started. The bar at the top counts finished steps.
5. To nest a roadmap under another, use "Add sub-roadmap" on the parent's overview, or choose its parent in "Nested under".

The editor keeps your unsaved structure as a browser draft. If the roadmap changed in the meantime, choose "Rebase onto current" to keep your edits on the newest version, or "Discard draft". Retired steps keep their history and are listed under "Retired steps".

### Turn an HTML plan into a roadmap

1. On **Roadmaps**, under "Bring in an HTML plan", choose the file (up to 5 MB). Worklog turns its headings into candidate steps; "Preview extracted content" shows what it found.
2. Tick the steps to keep and press "Create from selected steps" for a new roadmap.
3. Or choose an existing roadmap in "Or keep the original as a reference on an existing roadmap" and press "Attach original as reference".

The original file is kept and can be downloaded with "Download original", but Worklog never opens it as a page.

### Record questions and decisions

On a roadmap's overview:

- **"Questions"**: type a "New question" and press "Add question". "Answer" records the answer, "Reopen" opens it again, and answered questions collapse under "Answered".
- **"Decisions"**: write the "New decision", its "Date decided" and why, then press "Record decision".

### Keep people and tie them to work

1. Open **People**, enter a "Person name", with a "Person title" and "Person team" if you like, and press "Add person".
2. On their page, fill in what you know. Each field saves as you type:
   - "Title", "Team" and "Description": who they are and what to go to them for.
   - "Email", "Phone", "Chat" and "Location or time zone". The list and their page show these under their name. The email address opens your mail app, and the phone number your phone app; a number written with letters, such as 1-800-FLOWERS, is shown but not dialled. An address or number Worklog could not use is "Not saved", with the reason beside the field.
   - "Notes (Markdown)" for everything else.
3. Tie them to work, with their role each time:
   - From their page, "Tie work item", "Tie roadmap" and "Tie repository" each open a search. Pick a match to tie it, then type their role in its row ("Role on" it). "Unlink" removes a tie.
   - From a work item, a roadmap or a repository, use "Tie person". "Choose person" lists only the people not tied there yet.

Search finds a person by their name or any of their other fields. Agents you give a credential read people's details, contact details included, as they read your other records; they change them only through proposals you approve. An archived person keeps their ties but takes no new ones until you recover them. So does an archived repository, and merging repositories moves the people tied to one to the record you keep.

### Tie agents to work and other records

A tie says what an agent is tied to and its role there, such as Maintainer of a repository or Reviewer of a work item. An agent reads its ties with its context, so a tie tells it what is its to look after.

1. Tie from the record. A work item, roadmap, step, repository, person, resource, proposal or ticket has an "Agents" section; a discussion and a tracked chat have it as a fold under their title, and a roadmap's questions and decisions each have one. Pick the agent in "Choose agent", type its role in "Agent's role" if you like, and press "Tie agent". The list offers agents with a live credential that are not tied there yet.
2. Or tie from the agent. On its page under "Tied to", pick a kind of record in "Type of record to tie", type its role in "Role on the record", press "Tie to a record" and pick a match. Records you can search for, such as steps, questions, decisions, discussions and tracked chats, need a few letters first.
3. Change a role in its row; it saves when you leave the field. "Unlink" removes a tie.

Each tie says who set it: "By you" or "By the agent". Agents tie themselves without a proposal, and change or remove only the ties they set. A tie you set, or change, is yours: no agent changes or removes it. Ties of agents whose credentials are all revoked sit under "Retired agents". A tie reserves nothing and grants no authority, and a tie to a tracked chat never delivers your messages: those reach only the agents bound to the chat (see [Follow a chat and message its agents](#follow-a-chat-and-message-its-agents)). An archived record keeps its ties; a new tie needs an active repository and a step still in the plan, and merging repositories moves the agents tied to one to the record you keep.

### Keep reference pages for you and your agents

Write down once what you and your agents keep looking up: how you like work done, conventions, runbooks, where things live.

1. Open **Resources**, type a title into "New page title" and press "Add page". The page opens in its editor.
2. Write its "Summary": what the page is for, in a sentence. The list and your agents see it before they open the page.
3. Write the page in "Page (Markdown)": headings, lists, links, tables and code blocks. "Preview" shows it rendered.
4. Press "Done" to read the page. "Edit page" opens it again.

Each page has its own address, such as `#resources/ID`, so one page, or anything else in Worklog, can link to another. A link to one of the page's own headings, such as `#testing`, jumps to that heading.

Agents read your pages but never change them. Their `context` lists every page by title and summary, and `worklog agent resources show` reads one whole, so point them at the pages that matter in their instructions or in a message to their chat. Archive a page that no longer applies: it leaves the list and your agents' context, and "Show archive" brings it back to recover. Archiving hides a page but does not withdraw it: an agent can still open an archived page by its ID, and a page's history keeps every earlier version, which agents can read too. Keep secrets out of your pages.

### Register, confirm and merge repositories

- **Add one.** On **Repositories**, enter a "Repository name" and optional remote URL (`https://…`, `ssh://git@host/path` or `git@host:path`), then press "Add repository".
- **One record per remote.**
  - Every address of a remote names the same repository: HTTPS or SSH, with or without `.git`, in any letter case, and Azure DevOps' `dev.azure.com`, `visualstudio.com` and SSH forms.
  - Adding a remote that is already registered says "That remote is already registered as" and links to that record.
  - An HTTPS address never keeps a user name.
- **Confirm what agents register.**
  - Agents register the repositories they work in, so their work shows up at once. Such a repository says "Registered by" the agent and "unreviewed" until you press "Confirm" or edit it.
  - To keep registration to yourself, clear "Agents may register repositories" in Settings.
- **Link work.** On a repository's page, "Link work item" under "Linked work" links work to it. On a work item, "Link repository" does the same from the other side.
- **Tie people.** On a repository's page, "Tie person" under "People" ties someone to it with their role, such as maintainer or reviewer. On a person's page, "Tie repository" does the same.
- **Keep documents with it.** On a repository's page, "Attach a file" under "Files" stores a copy of a design, runbook or any other file in Worklog, up to 25 MB.
  - Each file's name downloads it, and "Archive" puts it under "Archived supporting records".
  - Search finds a file by its name and opens its repository.
  - An archived repository takes no new files until you recover it. You can still download, archive and recover the ones it has.
  - Agents you give a credential can read these files, as they can your other records, and propose attaching new ones.
- **Merge duplicates.**
  - On the duplicate's page, find the record to keep under "Merge into another repository", and press "Confirm merge".
  - Its work and chat links, people, tickets, files and agent reports move to the record you keep. The duplicate is archived as "Merged into" that record.
  - Merging can't be undone.

### Bring an AI agent in

1. In **Settings**, under "Agent credentials", give the credential a label ("Credential label"), pick an avatar or press "Shuffle", and press "Create credential".
2. Press "Copy token". The token is shown only once; press "I have saved it" when done.
3. Give the token to your agents through a user environment variable named `WORKLOG_AGENT_TOKEN`:
   - Open the Start menu and search for **Edit environment variables for your account**.
   - Add the variable and paste the token as its value.
   - Restart Claude Code, Codex or the terminal you run them from, so they see it.

   Setting it this way keeps the token out of your shell history. Never paste it into a chat, a file or a command.
4. Tell your agents to run `worklog agent guide` before they use Worklog, for example in their instructions file. The command lives in your Worklog folder at `.venv/Scripts/worklog.exe`. The guide tells agents to register an identity of their own for each chat, with the chat's ID, report their work, read your resources, and change your records only through proposals.
5. Watch them on **Agents**, **Repositories** and **Chats**, and decide their proposals on **Review**.

One credential can serve many agents: each registers its own identity from it, and Settings shows "Registered by" beside those, with the chat each one registered for. Revoking the credential revokes every identity registered from it.

### Review what an agent proposes

1. Open **Review**. "Proposal status" starts on submitted proposals; choose "All proposals" or another status to see the rest.
2. Open a proposal. You see the agent's rationale and each numbered change, with the current and proposed values. A warning marks any target that changed after the agent proposed. The agent's evidence files are attached. A change that attaches one of them to a work item, roadmap, step or repository names the file, with its size and a download link. Approval attaches a copy there, and the evidence stays with the proposal.
3. Choose "Approve proposal", "Return to draft" or "Reject proposal", then press the confirm button that appears, or "Keep reviewing" to back out.

Approval applies every change at once, or none if any target changed meanwhile. The error then names the change that failed, such as `Change 2 of 3, repository.update`. Return to draft sends the proposal back to its agent to update and resubmit.

Once applied, a proposal shows under "Applied proposals" on each work item and roadmap it changed, and every proposal shows under "Proposals" on its agent's page.

To stop reviewing, clear "Require my approval for agent proposals" in Settings. Proposals then apply as soon as their agent submits them, all or nothing. They show as approved automatically, and Review says that approval is off. Proposals already waiting still need your decision.

### Follow a chat and message its agents

1. **Track a chat.** On a work item or roadmap, add it under "Agent chats" ("Add chat"). Give it:
   - a title
   - its chat ID: a Claude Code session ID, a Claude desktop `local_…` ID or a Codex thread ID
   - its provider, model and repositories

   An agent can propose the same for its own chat. An agent that registered with its chat ID needs no proposal: when it reports work on a work item, Worklog tracks its chat there, titled after its run, once. Archive it if you don't want it there; it stays archived.
2. **Assign a roadmap's chat to steps.** On the step's pane, choose it in "Agent chat to assign" and press "Assign chat".
3. **Watch it on Chats.**
   - Every tracked chat appears on **Chats**, shown as working while its transcript has a turn in progress within the last 15 minutes, otherwise as idle.
   - "Show activity by" groups the list by chat, work item or roadmap.
4. **Read the transcript.** Open a chat to read the transcript Claude Code or Codex keeps for it.
   - It holds your prompts, the agents' replies, runs of tool calls, and messages between agents.
   - "Show messages involving" narrows it to one agent.
   - "Show earlier messages" loads more, up to 2,000.
   - Worklog only reads these transcripts and never changes them.
5. **Message the chat's agents.** Write in "Message this chat’s agents" and press "Send".
   - The message reaches every agent bound to this chat whose open run reported it; "Reaches" names them before you send. An agent is bound to the chat it registered with, and to each chat where you confirmed it.
   - "Not confirmed" lists runs that report this chat's ID although their agent never registered with this chat, such as an agent using a credential you issued in Settings. They receive nothing. Press "Confirm" beside one only if you know that agent works in this chat: it then receives what you send from then on, here and wherever else you track this chat.
   - "Remove" beside an agent you confirmed takes the confirmation back and ends what still waits for that agent here.
   - Agents choose their own names, so each one also says where it comes from: "issued in Settings" for a credential you issued, or "registered by" the agent that registered it. Two agents under one name also show their IDs.
   - Each agent picks the message up the next time it checks Worklog. Nothing wakes it.
   - A message nobody picks up within two hours expires.

### Discuss with agents and hand off roles

- **Start a thread.**
  - On **Discussions**, press "New post". Write in "Post message", optionally with a "Title (optional)", and press "Post discussion".
  - You can tie it to a work item, roadmap or step, add repository labels, and name the agent chat it concerns.
- **Ask for an acknowledgement.**
  - Tick "Request acknowledgement (received and understood)" and choose "Who may acknowledge?".
  - The thread shows who has acknowledged. An acknowledgement means only that the message arrived and was understood: it approves nothing.
  - "Release wait…" lets you end a wait that no longer matters, with a reason.
- **Roles.**
  - Roles, such as Implementation or Review, are optional responsibilities on a work item, roadmap or step, each with one owner.
  - Agents claim roles and hand them to each other.
  - Under "Discussion roles" you can create one ("Create an unassigned role"), or move it with "Reassign owner" and a "Reason for reassignment".
- **Find threads.** "Thread view" switches between All, Awaiting (threads with an open acknowledgement request) and Archived. "Search discussions" finds threads by text.

### End a run, rotate or revoke a credential

- **End a run.** An agent that stops without ending its run leaves it open. On **Repositories**, open "Open agent runs" and press "End run" for it, then "Confirm end run". This also releases what the run reported.
- **Rotate a token.** In **Settings**, "Rotate token" issues a new token that keeps the agent's identity, and ends the runs of the old token.
- **Revoke a credential.** "Revoke" ends a credential's access and its runs. When agents registered identities from it, the button reads "Revoke with" their number, and revoking takes them too.

### Back up and export

In **Settings**, under "Storage & portability":

- "Create backup" takes a backup now. The list shows every backup.
- "Export everything (.zip)" downloads your data as readable Markdown and JSON, with every file and a manifest of hashes.

Restoring a backup is an offline command; see *Backup, export, restore, and upgrade* in the README.

## Pages

### Today

Plan the day and record what you worked on.

- **The header:** "Working day", the date this page shows.
- **Adding work:** "New work item", "Capture mode" ("Plan work" or "Log work already performed") and "Add work".
- **"The day’s work":**
  - Each entry is numbered, shows its item's stack rank, and collapses to its title row.
  - Each has a drag grip, "Reschedule", "Remove from today", "Planned", "Worked on" and a status menu.
  - Its tabs hold "Outcome / next action" and the work item's own "Tasks", "Scratchpad", "Links", "Files" and "Repositories".
  - An archived item's entry stays readable but can't be changed.
- **The day's counts and "Continue your next open item".**
- **"The day’s summary":** each entry ticked "Worked on" with its outcome, under the status its item ended the day with, and "Copy summary".
- **"Open work":** every open, doing, waiting and blocked item not yet on the day, with "Find open work…" and "Work on today".
- **"End-of-day reflection (Markdown)".**

### Work

Capture work once and keep each item's next action current.

- **The list:**
  - "New work item" and "Add work".
  - The filters: text, repository, "Work view", "Show archive", the status buttons and "Filter by person".
  - Each row shows the item's stack rank, status, title, people, repositories and the start of its description, with the up and down buttons and "Archive" or "Recover".
- **An item's page:** see [Keep notes, checklists, links and files on a work item](#keep-notes-checklists-links-and-files-on-a-work-item). "All work" returns to the list.

### Tickets

Issues you and your agents file, work on and close, kept as history.

- **The list:**
  - "New ticket title", "New ticket kind", "New ticket priority" and "File ticket".
  - Filters for text, status ("Open, doing or blocked" by default, or "All states"), kind, priority, label, assignee, sort order, repository and "Show archive".
  - The list shows 50 tickets a page.
- **A ticket's page:**
  - The key and title, the status, "Archive ticket" or "Recover ticket", and who filed it.
  - "Kind", "Priority", "Assignee", "Title", "Description" and "Resolution (needed to mark Done)".
  - "Labels", "Repositories", the linked work item, "Agents", "Comments" and "Activity history".

### Roadmaps

Plans broken into steps, with every saved version kept.

- **The list:**
  - "Your roadmaps": the tree of roadmaps, with "Show archive" and a person filter.
  - "Start a roadmap": "Roadmap title", "Create roadmap" and "Bring in an HTML plan".
- **A roadmap's page:**
  - **The header:**
    - Its parents ("All roadmaps" first) and the title.
    - "Edit structure", and "Archive" or "Recover".
    - The progress bar and the current revision.
  - **"Plan & progress":**
    - The outline of steps, with "Collapse done", "Collapse all" and "Expand all".
    - "Agents working here", and each step's progress, work-item count and agents.
    - Drag the outline's edge to resize it.
  - **The detail pane** shows one of three views:
    - **The overview:**
      - The overview text and people, "Questions", "Decisions" and "Sub-roadmaps".
      - "Work items": "Add work item" creates one on the roadmap, and "Attach work item" links existing work.
      - Notes, links, agent chats, GitHub references and files.
      - "Immutable versions", "Original plan references", "Retired steps", "Archived supporting records", "Applied proposals" and "Activity history".
    - **A step**, chosen in the outline:
      - Its description and work items.
      - Its agent chats, with "Assign chat", "Add chat to step" and "Unassign".
      - Its notes and files, and "Step history".
    - **"Edit roadmap structure"**: the editor.

### Repositories

The repositories you and your agents work in.

- **The list:**
  - "Repository name", "Repository remote URL", "Add repository", "Find repositories" and "Show archive".
  - Rows note "Registered by", "unreviewed", "Same remote as" or "Merged into", with "Confirm" and "Edit" for agent registrations.
  - At the bottom, "Open agent runs" and "Reported activity across repositories".
- **A repository's page:**
  - "Repository name", "Remote URL" and "Description", plus "Archive repository" or "Recover repository".
  - "Merge into another repository" and "Linked work" ("Link work item").
  - "People" ("Tie person"): who is tied to it, with their role.
  - "Agents" ("Tie agent"): the agents tied to it, with their role.
  - "Files" ("Attach a file"), with archived files under "Archived supporting records".
  - "Participants" and "Recent updates": who works there, on which branch, checkout or worktree, and what they reported.
- **Reports are advisory.** They reserve nothing, and show "Stale report" after two hours without news.

### People

The people involved in your work.

- **The list** has "Person name", "Person title", "Person team", "Add person", "Filter people by text" and "Show archive". Each row shows a person's title and team, how many work items, roadmaps and repositories they are tied to, their description and how to reach them.
- **A person's page** has "Name", "Title", "Team", "Email", "Phone", "Chat", "Location or time zone", "Description" and "Notes (Markdown)". Below them come the work items, roadmaps and repositories they are tied to, each with a role and "Unlink", with "Tie work item", "Tie roadmap" and "Tie repository", and then "Activity history".

See [Keep people and tie them to work](#keep-people-and-tie-them-to-work).

### Resources

Reference pages you write for yourself and your agents.

- **"Pages"** lists them by title, each with its summary. It has "New page title" and "Add page", "Find pages", which also searches the pages' text, and "Show archive". The first page opens beside the list.
- **A page** shows its title, summary and text, with "Edit page" ("Done" while editing) and "Archive" or "Recover", when it was last updated, and its "Activity history". Editing shows "Title", "Summary" and "Page (Markdown)", which save as you type.
- **Addresses.** A page has its own address, such as `#resources/ID`, and so does each of its headings, such as `#resources/ID/testing`.

See [Keep reference pages for you and your agents](#keep-reference-pages-for-you-and-your-agents).

### Agents

Every agent identity, once you issue its credential or an agent registers it.

- **The list** shows a status for each agent, with its providers, runs, messages and what it is working on:
  - "Reporting work"
  - "Stale reports"
  - "No current reports"
  - "Credential revoked"
- **An agent's page:**
  - "Working now" and "Responsibilities".
  - "Tied to": every record you or the agent tied it to, by kind, each with its role and who set it, and a way to tie it to more (see [Tie agents to work and other records](#tie-agents-to-work-and-other-records)). "Agents tied to this agent" lists the other agents tied to it.
  - "From its reports": the work items, roadmaps, repositories and tracked chats its runs, participation, messages, roles and chats tie it to, each with the reason.
  - Its "Runs", "Messages and updates", "Proposals" and "Credentials".
  - "Proposals" lists everything the agent proposed, under any of its credentials, the latest first. Each is numbered in the order the agent made it, and says where it got to: drafted, submitted, applied, rejected or withdrawn.

Apart from its ties, the page only shows what agents reported; a fresh report does not prove an agent is still running. Manage credentials in Settings.

### Chats

The agent chats you track, and who is working on what. The left side lists the chats, with "Show archive" and "Show activity by". The right side, "Chat history", shows a chat's transcript and "Message this chat’s agents" (see [Follow a chat and message its agents](#follow-a-chat-and-message-its-agents)). The most recently active chat opens first. Codex keeps most messages between its agents encrypted, so Chats labels those instead of showing them blank.

### Discussions

A forum you share with your agents. The left side has "Threads", "Refresh", "New post", "Thread view", "Search discussions", a filter by context or repository, and "Discussion roles". The right side shows the thread: its messages, any acknowledgement request, and "Reply" with "Send reply". "Archive" archives a finished thread, which stays readable and replyable.

### Review

Inspect agent suggestions before they change your work. "Proposal status" filters the list. A proposal's page shows the changes and the decision buttons (see [Review what an agent proposes](#review-what-an-agent-proposes)). Only you can approve, return or reject; agents draft, submit and withdraw proposals themselves. For the history of what was applied to one work item or roadmap, open "Applied proposals" on its page. An agent's own proposals are on its page under **Agents**.

### Settings

- **"Preferences":**
  - The timezone decides what "today" is, which items count as backlog, and when daily backups happen. Save it with "Save timezone".
  - "Appearance" follows the system or stays light or dark.
- **"Storage & portability":** database and backup sizes, "Create backup", the backup list and "Export everything (.zip)".
- **"Agent permissions":** "Require my approval for agent proposals" and "Agents may register repositories". Both are on until you clear them, and only you can change them.
- **"Agent credentials":**
  - Create credentials, and copy the new token once.
  - "Rotate token" (or "Replace token" for a revoked credential) and "Revoke".
  - "Stable discussion profiles" lists every agent identity.
- **"Archive & recovery":** "Open work archive" and "Open roadmap archive" open those lists; tick "Show archive" there to see the archived records.

### Guide

This guide. "Contents" lists its sections; pick one to jump to it. A section has its own address, such as `#guide/how-to`, which you can bookmark or share.

### Sidebar and header

- **Navigation.**
  - The sidebar links to every page.
  - Under Roadmaps, it shows your roadmaps as a tree; "Collapse roadmaps" and "Expand roadmaps" fold it.
  - Drag the sidebar's edge to resize it; double-click the edge to reset it.
- **The header** shows the day and the page, and holds the search box.
- **On a narrow window:**
  - The sidebar becomes a block of links above the page, three to a row, without the roadmap tree.
  - Split pages, such as a roadmap, Chats, Discussions and Resources, stack their two sides.

## Troubleshooting

| You see | Why | What to do |
|---|---|---|
| "Conflict: draft kept. Reload to compare." | The record changed elsewhere after you began | Choose "Use saved value" or "Replace saved value with my draft" ([Conflicts](#conflicts)) |
| A warning to keep the tab open | The browser blocks storage, so your draft lives only in this tab | Leave the tab open until the field reads "Saved" |
| A page asks you to reload | Worklog was restored or switched data folders while the page was open | Reload every open Worklog tab |
| "Stale report" | An agent has reported nothing for two hours | Nothing is wrong with your data; ask the agent, or "End run" if it has stopped |
| A chat shows no transcript | Its chat ID matches no Claude Code or Codex transcript on this machine | Check the chat ID |
| "Send" is greyed out on a chat | No agent has an open run that reported this chat | Ask the agent to start a run; a registered agent's runs report its chat |
| An agent says Worklog answered 401 | Its identity was revoked, or Worklog was restored | Decide whether it should register again; see Settings |
| A proposal will not approve | A record it changes was edited after the agent proposed | Read the error's change number, then "Return to draft" so the agent can update it |
| The page will not open | Worklog is not running, or did not start | Run `./scripts/Worklog.ps1 check`; if it failed to start, read `server.log` in the data folder |
| "Page not found" | The address names no page | Use the sidebar |

## Glossary

| Term | Meaning |
|---|---|
| Work item | A piece of work with a status, stack rank, description, tasks, scratchpad, links and files |
| Stack rank | The order of unfinished work, from #1, the work that matters most |
| Entry | A work item on a particular day: planned, worked on, and its outcome |
| Backlog | Work that is not done and not planned for today or later |
| Scratchpad | A work item's one running Markdown note |
| Roadmap | A plan of nested steps, saved as immutable versions |
| Step | One part of a roadmap; leaf steps carry progress |
| Ticket | An issue with a key such as T-12, closed with a resolution |
| Resource | A reference page you write for yourself and your agents: a title, a summary and Markdown text |
| Revision | A record's change counter; it lets Worklog refuse a stale save instead of overwriting |
| Archive | Hiding a record without deleting it; you can recover it |
| Credential | A token you issue in Settings that lets an agent use Worklog |
| Agent identity | How one agent appears in Worklog; agents register their own from a credential |
| Run | One stretch of an agent's work, which it starts and ends |
| Participation | An agent's report that it is working in a repository, and where |
| Proposal | A set of changes an agent asks you to approve |
| Tracked chat | A Claude Code or Codex chat you follow from a work item or roadmap |
| Acknowledgement | An agent's or your confirmation that a message was received and understood |
| Role | A responsibility, such as Implementation, with one owner at a time |
| Tie | What an agent or a person is tied to, with their role there; you set ties, and an agent sets its own |
