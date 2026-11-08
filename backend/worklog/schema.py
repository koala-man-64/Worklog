"""The schema of a new database: every table and index of the current version, in one place.

Database.initialize creates a new database from BASELINE at migrations.SCHEMA_VERSION, and
migrations.upgrade brings an older one to the same shape. Each upgrade step keeps the DDL it shipped
with, so a change here never changes how an older database upgrades. test_sealed_schemas.py checks
that a new database, and every older shape once upgraded, have the shape the current version sealed
in recovery.SEALED_SCHEMAS.

A seal covers each statement's text with its whitespace collapsed to single spaces, so where a
space falls is part of the shape, though not whether it was a space or a line break. Each statement
here is spaced as an upgraded database records it: a column after ", " is one ALTER TABLE ADD
COLUMN appended after the table's last column, and the spaces inside the parentheses of roadmaps,
steps, roadmap_versions, plan_imports and proposals stand for the line breaks those tables were
first written with. Never add or remove a space in one. A schema change needs a new version: its
upgrade in migrations.py, the same change here, and a new seal (test_sealed_schemas.py says how).
"""

BASELINE = """
-- Settings, and the credentials agents call with.
CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE credentials(id TEXT PRIMARY KEY,label TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);

-- Work items, their days and what they hold.
CREATE TABLE work_items(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','doing','waiting','blocked','done')),ado_url TEXT,step_id TEXT REFERENCES steps(id),backlog_order INTEGER NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, roadmap_id TEXT REFERENCES roadmaps(id));
CREATE INDEX work_items_roadmap ON work_items(roadmap_id);
CREATE INDEX work_items_step ON work_items(step_id);
CREATE TABLE days(date TEXT PRIMARY KEY,reflection TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1, reflection_revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE daily_entries(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),date TEXT NOT NULL REFERENCES days(date),planned INTEGER NOT NULL DEFAULT 1 CHECK(planned IN(0,1)),worked INTEGER NOT NULL DEFAULT 0 CHECK(worked IN(0,1)),outcome TEXT NOT NULL DEFAULT '',position INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 1,UNIQUE(item_id,date));
CREATE INDEX daily_by_date ON daily_entries(date,position);
CREATE TABLE tasks(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),text TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0 CHECK(done IN(0,1)),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0 CHECK(position>=0));
CREATE INDEX tasks_item ON tasks(item_id,archived);
-- A work item's one active note is its scratchpad, and the index allows no second.
CREATE TABLE notes(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL CHECK(owner_type IN('item','roadmap','step')),owner_id TEXT NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX notes_item_scratchpad ON notes(owner_id) WHERE owner_type='item' AND archived=0;
CREATE INDEX notes_owner ON notes(owner_type,owner_id,archived);
CREATE TABLE file_references(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),path TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE INDEX file_references_item ON file_references(item_id,archived);
CREATE TABLE attachments(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL CHECK(owner_type IN('item','roadmap','step','proposal','repository')),owner_id TEXT NOT NULL,name TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,content BLOB NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE INDEX files_owner ON attachments(owner_type,owner_id,archived);
CREATE TABLE web_links(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL CHECK(owner_type IN('item','roadmap')),owner_id TEXT NOT NULL,url TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX web_links_owner ON web_links(owner_type,owner_id,archived);
CREATE TABLE agent_chats(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL CHECK(owner_type IN('item','roadmap')),owner_id TEXT NOT NULL,chat_id TEXT NOT NULL,title TEXT NOT NULL,created_date TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', repos TEXT NOT NULL DEFAULT '[]');
CREATE INDEX agent_chats_owner ON agent_chats(owner_type,owner_id,archived);
CREATE TABLE github_refs(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL CHECK(owner_type IN('item','roadmap')),owner_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN('commit','pr')),url TEXT NOT NULL,reference TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',chat_id TEXT REFERENCES agent_chats(id),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX github_refs_chat ON github_refs(chat_id);
CREATE INDEX github_refs_owner ON github_refs(owner_type,owner_id,archived);
CREATE TABLE chat_steps(chat_id TEXT NOT NULL REFERENCES agent_chats(id),step_id TEXT NOT NULL REFERENCES steps(id),created_at TEXT NOT NULL,PRIMARY KEY(chat_id,step_id));
CREATE INDEX chat_steps_step ON chat_steps(step_id);

-- Roadmaps: their steps, saved versions, HTML imports, questions and decisions.
CREATE TABLE roadmaps ( id TEXT PRIMARY KEY, title TEXT NOT NULL, overview TEXT NOT NULL, revision INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL , parent_id TEXT REFERENCES roadmaps(id));
CREATE TABLE steps ( id TEXT PRIMARY KEY, roadmap_id TEXT NOT NULL REFERENCES roadmaps(id), title TEXT NOT NULL, description TEXT NOT NULL, parent_id TEXT, position INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open', revision INTEGER NOT NULL, retired INTEGER NOT NULL DEFAULT 0 );
CREATE INDEX steps_parent ON steps(parent_id);
CREATE INDEX steps_roadmap ON steps(roadmap_id);
CREATE TABLE roadmap_versions ( roadmap_id TEXT NOT NULL REFERENCES roadmaps(id), number INTEGER NOT NULL, created_at TEXT NOT NULL, note TEXT NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(roadmap_id,number) );
CREATE TABLE plan_imports ( id TEXT PRIMARY KEY, name TEXT NOT NULL, original BLOB NOT NULL, sha256 TEXT NOT NULL, preview TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL, roadmap_id TEXT REFERENCES roadmaps(id) );
CREATE TABLE roadmap_questions(id TEXT PRIMARY KEY,roadmap_id TEXT NOT NULL REFERENCES roadmaps(id),title TEXT NOT NULL,details TEXT NOT NULL DEFAULT '',answer TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','answered')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX roadmap_questions_roadmap ON roadmap_questions(roadmap_id,archived);
CREATE TABLE roadmap_decisions(id TEXT PRIMARY KEY,roadmap_id TEXT NOT NULL REFERENCES roadmaps(id),title TEXT NOT NULL,rationale TEXT NOT NULL DEFAULT '',decided_date TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX roadmap_decisions_roadmap ON roadmap_decisions(roadmap_id,archived);

-- Proposals agents make for the person to review, the stale-plan notices each was sent, and the
-- work items and roadmaps each change of an applied proposal was made to.
CREATE TABLE proposals ( id TEXT PRIMARY KEY, title TEXT NOT NULL, rationale TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL, actor_id TEXT NOT NULL, actor_label TEXT NOT NULL, actor_kind TEXT NOT NULL, operations TEXT NOT NULL, supersedes_id TEXT REFERENCES proposals(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL , submitted_at TEXT, auto_approved INTEGER NOT NULL DEFAULT 0 CHECK(auto_approved IN(0,1)));
CREATE INDEX proposals_status ON proposals(status,created_at);
CREATE TABLE proposal_stale_notices(proposal_id TEXT NOT NULL REFERENCES proposals(id),proposal_revision INTEGER NOT NULL,conflicts TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(proposal_id,proposal_revision,conflicts));
CREATE TABLE proposal_targets(proposal_id TEXT NOT NULL REFERENCES proposals(id),operation INTEGER NOT NULL CHECK(operation>=1),action TEXT NOT NULL,target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap')),target_id TEXT NOT NULL,PRIMARY KEY(proposal_id,operation,target_type,target_id));
CREATE INDEX proposal_targets_target ON proposal_targets(target_type,target_id);

-- History, idempotency receipts and search.
CREATE TABLE events(id TEXT PRIMARY KEY,entity_id TEXT NOT NULL,kind TEXT NOT NULL,actor_id TEXT NOT NULL,actor_label TEXT NOT NULL,actor_kind TEXT NOT NULL,created_at TEXT NOT NULL,local_date TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL);
CREATE INDEX event_day ON events(local_date,kind);
CREATE INDEX event_entity ON events(entity_id,created_at);
CREATE TABLE receipts(actor_id TEXT NOT NULL,key TEXT NOT NULL,request_hash TEXT NOT NULL,response_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT '',PRIMARY KEY(actor_id,key));
CREATE INDEX receipts_created ON receipts(created_at);
CREATE VIRTUAL TABLE search_index USING fts5(entity_id UNINDEXED,type UNINDEXED,title,body,owner_id UNINDEXED,archived UNINDEXED,tokenize='unicode61');
-- search_index's own entity_id, type and owner_id are UNINDEXED: search_keys finds each record's row.
CREATE TABLE search_keys(entity_id TEXT NOT NULL PRIMARY KEY,doc INTEGER NOT NULL UNIQUE,type TEXT NOT NULL,owner_id TEXT NOT NULL);
CREATE INDEX search_keys_owner ON search_keys(owner_id,type);

-- People.
CREATE TABLE people(id TEXT PRIMARY KEY,name TEXT NOT NULL,team TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', chat TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '');
CREATE TABLE person_links(id TEXT PRIMARY KEY,person_id TEXT NOT NULL REFERENCES people(id),target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap','repository')),target_id TEXT NOT NULL,relationship TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(person_id,target_type,target_id));
CREATE INDEX person_links_person ON person_links(person_id);
CREATE INDEX person_links_target ON person_links(target_type,target_id);

-- Agent identities, and the discussions they hold.
CREATE TABLE communication_agents(id TEXT PRIMARY KEY,label TEXT NOT NULL,created_at TEXT NOT NULL, avatar TEXT);
CREATE TABLE communication_credentials(credential_id TEXT PRIMARY KEY REFERENCES credentials(id),agent_id TEXT NOT NULL REFERENCES communication_agents(id), registered_by TEXT, registered_by_label TEXT NOT NULL DEFAULT '', chat_id TEXT, directory TEXT);
CREATE INDEX communication_credentials_agent ON communication_credentials(agent_id);
CREATE TABLE discussions(id TEXT PRIMARY KEY,title TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, pinned_at TEXT);
CREATE INDEX discussions_updated ON discussions(archived,updated_at,id);
-- A discussion's tags: the records it is about, who added each and when. A repository's own
-- discussion is the one whose tag of it is home.
CREATE TABLE discussion_contexts(discussion_id TEXT NOT NULL REFERENCES discussions(id),context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step','repository','ticket','release','person','resource','question','decision')),context_id TEXT NOT NULL,home INTEGER NOT NULL DEFAULT 0 CHECK(home IN(0,1)),added_by TEXT NOT NULL,added_by_kind TEXT NOT NULL CHECK(added_by_kind IN('agent','human')),created_at TEXT NOT NULL,PRIMARY KEY(discussion_id,context_type,context_id),CHECK(home=0 OR context_type='repository'));
CREATE INDEX discussion_contexts_context ON discussion_contexts(context_type,context_id,discussion_id);
CREATE UNIQUE INDEX discussion_contexts_home ON discussion_contexts(context_type,context_id) WHERE home=1;
CREATE UNIQUE INDEX discussion_contexts_home_discussion ON discussion_contexts(discussion_id) WHERE home=1;
CREATE TABLE discussion_repos(discussion_id TEXT NOT NULL REFERENCES discussions(id),repo TEXT NOT NULL,PRIMARY KEY(discussion_id,repo));
CREATE INDEX discussion_repos_repo ON discussion_repos(repo,discussion_id);
CREATE TABLE discussion_roles(id TEXT PRIMARY KEY,context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step')),context_id TEXT NOT NULL,label TEXT NOT NULL,label_key TEXT NOT NULL,owner_id TEXT REFERENCES communication_agents(id),revision INTEGER NOT NULL DEFAULT 1,generation INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(context_type,context_id,label_key));
CREATE INDEX discussion_roles_owner ON discussion_roles(owner_id,context_type,context_id);
CREATE TABLE discussion_role_offers(id TEXT PRIMARY KEY,role_id TEXT NOT NULL REFERENCES discussion_roles(id),from_id TEXT NOT NULL REFERENCES communication_agents(id),to_id TEXT NOT NULL REFERENCES communication_agents(id),generation INTEGER NOT NULL,note TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN('pending','accepted','declined','withdrawn','invalidated')),created_at TEXT NOT NULL,ended_at TEXT);
CREATE UNIQUE INDEX discussion_role_pending_offer ON discussion_role_offers(role_id) WHERE state='pending';
CREATE TABLE discussion_messages(id TEXT PRIMARY KEY,discussion_id TEXT NOT NULL REFERENCES discussions(id),text TEXT NOT NULL,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),created_at TEXT NOT NULL,chat_id TEXT,on_behalf_of_role_id TEXT REFERENCES discussion_roles(id),ownership_generation INTEGER);
CREATE INDEX discussion_messages_discussion ON discussion_messages(discussion_id,created_at,id);
CREATE TABLE discussion_requests(message_id TEXT PRIMARY KEY REFERENCES discussion_messages(id),mode TEXT NOT NULL CHECK(mode IN('all','any','any_agent','anyone')),state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN('pending','satisfied','released_by_human','cancelled')),terminal_at TEXT,terminal_actor_id TEXT,terminal_actor_label TEXT,terminal_actor_kind TEXT,reason TEXT,authority_role_id TEXT REFERENCES discussion_roles(id),authority_generation INTEGER);
CREATE TABLE discussion_targets(id TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES discussion_requests(message_id),kind TEXT NOT NULL CHECK(kind IN('agent','human','role','open_agent','open_any')),recipient_id TEXT NOT NULL,UNIQUE(message_id,kind,recipient_id));
CREATE INDEX discussion_targets_recipient ON discussion_targets(kind,recipient_id,message_id);
CREATE TABLE discussion_acknowledgements(id TEXT PRIMARY KEY,target_id TEXT NOT NULL REFERENCES discussion_targets(id),actor_id TEXT NOT NULL,actor_label TEXT NOT NULL,actor_kind TEXT NOT NULL CHECK(actor_kind IN('agent','human')),role_id TEXT REFERENCES discussion_roles(id),ownership_generation INTEGER,created_at TEXT NOT NULL,late INTEGER NOT NULL CHECK(late IN(0,1)),UNIQUE(target_id,actor_id));
CREATE TABLE discussion_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE REFERENCES events(id),kind TEXT NOT NULL,discussion_id TEXT REFERENCES discussions(id),message_id TEXT REFERENCES discussion_messages(id),role_id TEXT REFERENCES discussion_roles(id),created_at TEXT NOT NULL);
CREATE TABLE discussion_change_contexts(seq INTEGER NOT NULL REFERENCES discussion_changes(seq),context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step','repository','ticket','release','person','resource','question','decision')),context_id TEXT NOT NULL,PRIMARY KEY(seq,context_type,context_id));
CREATE INDEX discussion_change_contexts_context ON discussion_change_contexts(context_type,context_id,seq);

-- The repository catalog, and the runs and participations agents report.
CREATE TABLE repositories(id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL, remote_identity TEXT NOT NULL DEFAULT '', duplicate_of TEXT REFERENCES repositories(id), merged_into TEXT REFERENCES repositories(id), registered_by_kind TEXT NOT NULL DEFAULT '', registered_by_label TEXT NOT NULL DEFAULT '', registered_by_agent TEXT REFERENCES communication_agents(id), reviewed INTEGER NOT NULL DEFAULT 1 CHECK(reviewed IN(0,1)));
CREATE UNIQUE INDEX repositories_remote_identity ON repositories(remote_identity) WHERE remote_identity<>'' AND duplicate_of IS NULL AND merged_into IS NULL;
CREATE TABLE item_repositories(item_id TEXT NOT NULL REFERENCES work_items(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(item_id,repository_id));
CREATE INDEX item_repositories_repository ON item_repositories(repository_id,item_id);
CREATE TABLE chat_repositories(chat_id TEXT NOT NULL REFERENCES agent_chats(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(chat_id,repository_id));
CREATE INDEX chat_repositories_repository ON chat_repositories(repository_id,chat_id);
CREATE TABLE agent_runs(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL REFERENCES communication_agents(id),credential_id TEXT NOT NULL REFERENCES credentials(id),client_run_key TEXT NOT NULL,display_name TEXT NOT NULL,provider TEXT NOT NULL DEFAULT '',external_chat_id TEXT,revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,ended_at TEXT,end_reason TEXT,UNIQUE(credential_id,client_run_key));
CREATE INDEX agent_runs_credential ON agent_runs(credential_id,ended_at);
CREATE TABLE participations(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES agent_runs(id),item_id TEXT REFERENCES work_items(id),description TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,released_at TEXT,release_reason TEXT);
CREATE INDEX participations_item ON participations(item_id,released_at);
CREATE INDEX participations_run ON participations(run_id,released_at);
CREATE TABLE participation_contexts(participation_id TEXT NOT NULL REFERENCES participations(id),position INTEGER NOT NULL CHECK(position>=0),repository_id TEXT NOT NULL REFERENCES repositories(id),checkout_path TEXT NOT NULL DEFAULT '',worktree_path TEXT NOT NULL DEFAULT '',branch TEXT NOT NULL DEFAULT '',PRIMARY KEY(participation_id,position));
CREATE INDEX participation_contexts_repository ON participation_contexts(repository_id,participation_id);
CREATE TABLE coordination_updates(id TEXT PRIMARY KEY,participation_id TEXT NOT NULL REFERENCES participations(id),run_id TEXT NOT NULL REFERENCES agent_runs(id),agent_id TEXT NOT NULL REFERENCES communication_agents(id),credential_id TEXT NOT NULL REFERENCES credentials(id),author_label TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN('update','blocker','handoff')),text TEXT NOT NULL,item_id TEXT REFERENCES work_items(id),contexts_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX coordination_updates_created ON coordination_updates(created_at,id);
CREATE INDEX coordination_updates_item ON coordination_updates(item_id,created_at,id);
CREATE INDEX coordination_updates_participation ON coordination_updates(participation_id,created_at,id);
CREATE TABLE coordination_update_repositories(update_id TEXT NOT NULL REFERENCES coordination_updates(id),repository_id TEXT NOT NULL REFERENCES repositories(id),PRIMARY KEY(update_id,repository_id));
CREATE INDEX coordination_update_repositories_repository ON coordination_update_repositories(repository_id,update_id);

-- Messages the person writes into tracked chats, their deliveries, and the identities the person confirmed in each chat.
CREATE TABLE chat_messages(id TEXT PRIMARY KEY,chat_id TEXT NOT NULL REFERENCES agent_chats(id),text TEXT NOT NULL,author_id TEXT NOT NULL,author_label TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX chat_messages_chat ON chat_messages(chat_id,created_at,id);
CREATE TABLE chat_message_deliveries(message_id TEXT NOT NULL REFERENCES chat_messages(id),run_id TEXT NOT NULL REFERENCES agent_runs(id),status TEXT NOT NULL CHECK(status IN('pending','delivered','expired')),expires_at TEXT NOT NULL,delivered_at TEXT,expired_at TEXT,expire_reason TEXT,PRIMARY KEY(message_id,run_id));
CREATE INDEX chat_message_deliveries_run ON chat_message_deliveries(run_id,status);
CREATE TABLE chat_confirmations(chat_id TEXT NOT NULL,agent_id TEXT NOT NULL REFERENCES communication_agents(id),confirmed_by TEXT NOT NULL,confirmed_by_label TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(chat_id,agent_id));

-- Tickets.
CREATE TABLE tickets(id TEXT PRIMARY KEY,number INTEGER NOT NULL UNIQUE CHECK(number>=1),title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL DEFAULT 'bug' CHECK(kind IN('bug','incident','task')),priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN('low','normal','high','urgent')),status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','doing','blocked','done')),resolution TEXT NOT NULL DEFAULT '',item_id TEXT REFERENCES work_items(id),assignee_id TEXT,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,closed_at TEXT,last_activity_at TEXT NOT NULL);
CREATE INDEX tickets_assignee ON tickets(assignee_id,archived,status);
CREATE INDEX tickets_item ON tickets(item_id,archived);
CREATE INDEX tickets_list ON tickets(archived,status,last_activity_at,id);
CREATE TABLE ticket_repositories(ticket_id TEXT NOT NULL REFERENCES tickets(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(ticket_id,repository_id));
CREATE INDEX ticket_repositories_repository ON ticket_repositories(repository_id,ticket_id);
CREATE TABLE ticket_labels(ticket_id TEXT NOT NULL REFERENCES tickets(id),label TEXT NOT NULL,PRIMARY KEY(ticket_id,label));
CREATE INDEX ticket_labels_label ON ticket_labels(label,ticket_id);
CREATE TABLE ticket_comments(id TEXT PRIMARY KEY,ticket_id TEXT NOT NULL REFERENCES tickets(id),text TEXT NOT NULL,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),created_at TEXT NOT NULL);
CREATE INDEX ticket_comments_ticket ON ticket_comments(ticket_id,created_at,id);

-- Resources. The page's text is the last column, so listing titles need not read through a long page's overflow pages.
CREATE TABLE resources(id TEXT PRIMARY KEY,title TEXT NOT NULL,summary TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,body TEXT NOT NULL DEFAULT '');

-- Agent ties: the records each agent identity is tied to, its role on each, and who set the tie.
CREATE TABLE agent_links(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL REFERENCES communication_agents(id),target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap','step','repository','person','resource','discussion','proposal','ticket','agent','question','decision')),target_id TEXT NOT NULL,relationship TEXT NOT NULL DEFAULT '',set_by TEXT NOT NULL CHECK(set_by IN('human','agent')),created_at TEXT NOT NULL,UNIQUE(agent_id,target_type,target_id));
CREATE INDEX agent_links_target ON agent_links(target_type,target_id);

-- Releases: what ships together and when, the work items, roadmaps, steps and GitHub references each holds,
-- and its checks: who needs to know, what to test and what to prepare first. A date is a YYYY-MM-DD day or null.
CREATE TABLE releases(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',release_date TEXT CHECK(release_date IS NULL OR date(release_date) IS release_date),status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN('planned','released','cancelled')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE release_contents(release_id TEXT NOT NULL REFERENCES releases(id),target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap','step','github')),target_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(release_id,target_type,target_id));
CREATE INDEX release_contents_target ON release_contents(target_type,target_id,release_id);
CREATE TABLE release_checks(id TEXT PRIMARY KEY,release_id TEXT NOT NULL REFERENCES releases(id),kind TEXT NOT NULL CHECK(kind IN('notify','test','prepare')),text TEXT NOT NULL,details TEXT NOT NULL DEFAULT '',person_id TEXT REFERENCES people(id),due_date TEXT CHECK(due_date IS NULL OR date(due_date) IS due_date),status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','doing','waiting','blocked','done')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX release_checks_release ON release_checks(release_id,archived,kind);

-- Reminders: each is due on a date, or at a time on it in the Worklog timezone, and names at most one
-- record. Notes are the last column, as a resource's text is.
CREATE TABLE reminders(id TEXT PRIMARY KEY,title TEXT NOT NULL,due_date TEXT NOT NULL,due_time TEXT,state TEXT NOT NULL DEFAULT 'open' CHECK(state IN('open','done')),target_type TEXT CHECK(target_type IN('item','ticket','roadmap','person','repository','chat')),target_id TEXT,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,done_at TEXT,notes TEXT NOT NULL DEFAULT '',CHECK((target_type IS NULL)=(target_id IS NULL)));
CREATE INDEX reminders_due ON reminders(archived,state,due_date,due_time);
CREATE INDEX reminders_target ON reminders(target_type,target_id);
"""
