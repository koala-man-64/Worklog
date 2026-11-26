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
steps, roadmap_versions and proposals stand for the line breaks those tables were first written
with. Never add or remove a space in one. A schema change needs a new version: its
upgrade in migrations.py, the same change here, and a new seal (test_sealed_schemas.py says how).
"""

BASELINE = """
-- Settings, and the credentials agents call with.
CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE credentials(id TEXT PRIMARY KEY,label TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);

-- Work items, their days and what they hold. A day's reflection and reflection_revision are left
-- from the end-of-day reflection, removed without a schema change (decision R4 in
-- docs/ROADMAPS-RETHINK.md): nothing reads or writes them.
CREATE TABLE work_items(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','doing','waiting','blocked','done')),ado_url TEXT,step_id TEXT REFERENCES steps(id),backlog_order INTEGER NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, roadmap_id TEXT REFERENCES roadmaps(id));
CREATE INDEX work_items_roadmap ON work_items(roadmap_id);
CREATE INDEX work_items_step ON work_items(step_id);
CREATE TABLE days(date TEXT PRIMARY KEY,reflection TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1, reflection_revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE daily_entries(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),date TEXT NOT NULL REFERENCES days(date),planned INTEGER NOT NULL DEFAULT 1 CHECK(planned IN(0,1)),worked INTEGER NOT NULL DEFAULT 0 CHECK(worked IN(0,1)),outcome TEXT NOT NULL DEFAULT '',position INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 1,UNIQUE(item_id,date));
CREATE INDEX daily_by_date ON daily_entries(date,position);
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
CREATE TABLE github_refs(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL CHECK(owner_type IN('item','roadmap')),owner_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN('commit','pr')),url TEXT NOT NULL,reference TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',chat_id TEXT REFERENCES agent_chats(id),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL, submitted_by TEXT NOT NULL DEFAULT '');
CREATE INDEX github_refs_chat ON github_refs(chat_id);
CREATE INDEX github_refs_owner ON github_refs(owner_type,owner_id,archived);
CREATE TABLE chat_steps(chat_id TEXT NOT NULL REFERENCES agent_chats(id),step_id TEXT NOT NULL REFERENCES steps(id),created_at TEXT NOT NULL,PRIMARY KEY(chat_id,step_id));
CREATE INDEX chat_steps_step ON chat_steps(step_id);

-- Roadmaps: their steps, saved versions, questions and decisions.
CREATE TABLE roadmaps ( id TEXT PRIMARY KEY, title TEXT NOT NULL, overview TEXT NOT NULL, revision INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL , parent_id TEXT REFERENCES roadmaps(id));
CREATE TABLE steps ( id TEXT PRIMARY KEY, roadmap_id TEXT NOT NULL REFERENCES roadmaps(id), title TEXT NOT NULL, description TEXT NOT NULL, parent_id TEXT, position INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open', revision INTEGER NOT NULL, retired INTEGER NOT NULL DEFAULT 0 , outcome TEXT NOT NULL DEFAULT '', work_class TEXT NOT NULL DEFAULT '' CHECK(work_class IN('','stabilization','critical_path','standard','refactor','gate')));
CREATE INDEX steps_parent ON steps(parent_id);
CREATE INDEX steps_roadmap ON steps(roadmap_id);
CREATE TABLE roadmap_versions ( roadmap_id TEXT NOT NULL REFERENCES roadmaps(id), number INTEGER NOT NULL, created_at TEXT NOT NULL, note TEXT NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(roadmap_id,number) );
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
-- discussion is the one whose tag of it is home. 'release' stays in the CHECKs of this table and of
-- discussion_change_contexts, though no release tag can be written since v28 dropped releases
-- (decision R6 in docs/ROADMAPS-RETHINK.md).
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
CREATE TABLE discussion_messages(id TEXT PRIMARY KEY,discussion_id TEXT NOT NULL REFERENCES discussions(id),text TEXT NOT NULL,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),created_at TEXT NOT NULL,chat_id TEXT,on_behalf_of_role_id TEXT REFERENCES discussion_roles(id),ownership_generation INTEGER, reply_to_message_id TEXT REFERENCES discussion_messages(id));
CREATE INDEX discussion_messages_discussion ON discussion_messages(discussion_id,created_at,id);
CREATE TABLE discussion_requests(message_id TEXT PRIMARY KEY REFERENCES discussion_messages(id),mode TEXT NOT NULL CHECK(mode IN('all','any','any_agent','anyone')),state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN('pending','satisfied','released_by_human','cancelled')),terminal_at TEXT,terminal_actor_id TEXT,terminal_actor_label TEXT,terminal_actor_kind TEXT,reason TEXT,authority_role_id TEXT REFERENCES discussion_roles(id),authority_generation INTEGER, deadline_at TEXT, urgent INTEGER NOT NULL DEFAULT 0 CHECK(urgent IN(0,1)), escalated_at TEXT);
CREATE INDEX discussion_requests_deadline ON discussion_requests(deadline_at) WHERE state='pending' AND deadline_at IS NOT NULL AND escalated_at IS NULL;
CREATE TABLE discussion_targets(id TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES discussion_requests(message_id),kind TEXT NOT NULL CHECK(kind IN('agent','human','role','open_agent','open_any')),recipient_id TEXT NOT NULL,UNIQUE(message_id,kind,recipient_id));
CREATE INDEX discussion_targets_recipient ON discussion_targets(kind,recipient_id,message_id);
CREATE TABLE discussion_acknowledgements(id TEXT PRIMARY KEY,target_id TEXT NOT NULL REFERENCES discussion_targets(id),actor_id TEXT NOT NULL,actor_label TEXT NOT NULL,actor_kind TEXT NOT NULL CHECK(actor_kind IN('agent','human')),role_id TEXT REFERENCES discussion_roles(id),ownership_generation INTEGER,created_at TEXT NOT NULL,late INTEGER NOT NULL CHECK(late IN(0,1)),UNIQUE(target_id,actor_id));
CREATE TABLE discussion_seen(target_id TEXT NOT NULL REFERENCES discussion_targets(id),actor_id TEXT NOT NULL,seen_at TEXT NOT NULL,PRIMARY KEY(target_id,actor_id));
CREATE TABLE discussion_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE REFERENCES events(id),kind TEXT NOT NULL,discussion_id TEXT REFERENCES discussions(id),message_id TEXT REFERENCES discussion_messages(id),role_id TEXT REFERENCES discussion_roles(id),created_at TEXT NOT NULL);
CREATE TABLE discussion_change_contexts(seq INTEGER NOT NULL REFERENCES discussion_changes(seq),context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step','repository','ticket','release','person','resource','question','decision')),context_id TEXT NOT NULL,PRIMARY KEY(seq,context_type,context_id));
CREATE INDEX discussion_change_contexts_context ON discussion_change_contexts(context_type,context_id,seq);

-- The repository catalog, and the runs and participations agents report.
CREATE TABLE repositories(id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL, remote_identity TEXT NOT NULL DEFAULT '', duplicate_of TEXT REFERENCES repositories(id), merged_into TEXT REFERENCES repositories(id), registered_by_kind TEXT NOT NULL DEFAULT '', registered_by_label TEXT NOT NULL DEFAULT '', registered_by_agent TEXT REFERENCES communication_agents(id), reviewed INTEGER NOT NULL DEFAULT 1 CHECK(reviewed IN(0,1)), wip_cap INTEGER CHECK(wip_cap IS NULL OR wip_cap>=1), tier INTEGER CHECK(tier IS NULL OR tier BETWEEN 1 AND 9));
CREATE UNIQUE INDEX repositories_remote_identity ON repositories(remote_identity) WHERE remote_identity<>'' AND duplicate_of IS NULL AND merged_into IS NULL;
CREATE TABLE item_repositories(item_id TEXT NOT NULL REFERENCES work_items(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(item_id,repository_id));
CREATE INDEX item_repositories_repository ON item_repositories(repository_id,item_id);
CREATE TABLE chat_repositories(chat_id TEXT NOT NULL REFERENCES agent_chats(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(chat_id,repository_id));
CREATE INDEX chat_repositories_repository ON chat_repositories(repository_id,chat_id);
CREATE TABLE agent_runs(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL REFERENCES communication_agents(id),credential_id TEXT NOT NULL REFERENCES credentials(id),client_run_key TEXT NOT NULL,display_name TEXT NOT NULL,provider TEXT NOT NULL DEFAULT '',external_chat_id TEXT,revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,ended_at TEXT,end_reason TEXT,UNIQUE(credential_id,client_run_key));
CREATE INDEX agent_runs_credential ON agent_runs(credential_id,ended_at);
CREATE TABLE participations(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES agent_runs(id),item_id TEXT REFERENCES work_items(id),description TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,released_at TEXT,release_reason TEXT, step_id TEXT REFERENCES steps(id));
CREATE INDEX participations_item ON participations(item_id,released_at);
CREATE INDEX participations_run ON participations(run_id,released_at);
CREATE INDEX participations_step ON participations(step_id,released_at);
CREATE TABLE participation_contexts(participation_id TEXT NOT NULL REFERENCES participations(id),position INTEGER NOT NULL CHECK(position>=0),repository_id TEXT NOT NULL REFERENCES repositories(id),checkout_path TEXT NOT NULL DEFAULT '',worktree_path TEXT NOT NULL DEFAULT '',branch TEXT NOT NULL DEFAULT '',PRIMARY KEY(participation_id,position));
CREATE INDEX participation_contexts_repository ON participation_contexts(repository_id,participation_id);
CREATE TABLE participation_outputs(id TEXT PRIMARY KEY,participation_id TEXT NOT NULL REFERENCES participations(id),repository_id TEXT NOT NULL REFERENCES repositories(id),kind TEXT NOT NULL CHECK(kind IN('pr','commit')),reference TEXT NOT NULL,url TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(participation_id,repository_id,kind,reference));
CREATE INDEX participation_outputs_reference ON participation_outputs(kind,reference);
CREATE INDEX participation_outputs_repository ON participation_outputs(repository_id,participation_id);
CREATE TABLE coordination_updates(id TEXT PRIMARY KEY,participation_id TEXT NOT NULL REFERENCES participations(id),run_id TEXT NOT NULL REFERENCES agent_runs(id),agent_id TEXT NOT NULL REFERENCES communication_agents(id),credential_id TEXT NOT NULL REFERENCES credentials(id),author_label TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN('update','blocker','handoff')),text TEXT NOT NULL,item_id TEXT REFERENCES work_items(id),contexts_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX coordination_updates_created ON coordination_updates(created_at,id);
CREATE INDEX coordination_updates_item ON coordination_updates(item_id,created_at,id);
CREATE INDEX coordination_updates_participation ON coordination_updates(participation_id,created_at,id);
CREATE TABLE coordination_update_repositories(update_id TEXT NOT NULL REFERENCES coordination_updates(id),repository_id TEXT NOT NULL REFERENCES repositories(id),PRIMARY KEY(update_id,repository_id));
CREATE INDEX coordination_update_repositories_repository ON coordination_update_repositories(repository_id,update_id);

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

-- Repository slot policies, active/queued leases, and dispatch-time session queue (v34).
CREATE TABLE repository_slot_policies(repository_id TEXT PRIMARY KEY REFERENCES repositories(id),max_active_leases INTEGER NOT NULL DEFAULT 2 CHECK(max_active_leases>=0),default_ttl_seconds INTEGER NOT NULL DEFAULT 1800 CHECK(default_ttl_seconds BETWEEN 300 AND 14400),base_checkout_path TEXT NOT NULL DEFAULT '',slot_root_path TEXT NOT NULL DEFAULT '',default_branch TEXT NOT NULL DEFAULT 'main',base_readonly INTEGER NOT NULL DEFAULT 0 CHECK(base_readonly IN(0,1)),auto_rescue_dirty INTEGER NOT NULL DEFAULT 1 CHECK(auto_rescue_dirty IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),updated_at TEXT NOT NULL);
CREATE TABLE repository_leases(id TEXT PRIMARY KEY,repository_id TEXT NOT NULL REFERENCES repositories(id),agent_id TEXT REFERENCES communication_agents(id),credential_id TEXT REFERENCES credentials(id),run_id TEXT REFERENCES agent_runs(id),provider TEXT NOT NULL DEFAULT '',chat_id TEXT NOT NULL DEFAULT '',holder_label TEXT NOT NULL DEFAULT '',slot_index INTEGER CHECK(slot_index IS NULL OR slot_index>=1),worktree_path TEXT NOT NULL DEFAULT '',branch TEXT NOT NULL DEFAULT '',mode TEXT NOT NULL DEFAULT 'shared' CHECK(mode IN('shared','exclusive')),state TEXT NOT NULL CHECK(state IN('queued','offered','active','released','expired','expired_dirty','revoked','cancelled')),priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN('normal','pr_fixup','wave','incident')),queue_seq INTEGER NOT NULL,reason TEXT NOT NULL DEFAULT '',requested_at TEXT NOT NULL,offered_at TEXT,acquired_at TEXT,last_heartbeat_at TEXT NOT NULL,expires_at TEXT,released_at TEXT,release_reason TEXT,rescue_ref TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1));
CREATE INDEX repository_leases_repo_state ON repository_leases(repository_id,state,queue_seq);
CREATE INDEX repository_leases_agent_state ON repository_leases(agent_id,state);
CREATE INDEX repository_leases_run ON repository_leases(run_id,state);
CREATE UNIQUE INDEX repository_leases_active_slot ON repository_leases(repository_id,slot_index) WHERE state IN('offered','active','expired_dirty') AND slot_index IS NOT NULL;
CREATE UNIQUE INDEX repository_leases_active_branch ON repository_leases(repository_id,branch) WHERE state IN('offered','active') AND branch<>'';
CREATE UNIQUE INDEX repository_leases_active_worktree ON repository_leases(repository_id,worktree_path) WHERE state IN('offered','active','expired_dirty') AND worktree_path<>'';
CREATE TABLE dispatch_queue(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES work_items(id),repository_id TEXT NOT NULL REFERENCES repositories(id),lease_id TEXT NOT NULL UNIQUE REFERENCES repository_leases(id),provider TEXT NOT NULL CHECK(provider IN('codex','claude','antigravity')),requested_branch TEXT NOT NULL DEFAULT '',prompt TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN('queued','provisioning','preparing','ready','ready_manual','failed','cancelled')),session_id TEXT,chat_record_id TEXT REFERENCES agent_chats(id),model TEXT NOT NULL DEFAULT '',message TEXT NOT NULL DEFAULT '',process_token TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX dispatch_queue_repo_status ON dispatch_queue(repository_id,status,created_at);
CREATE INDEX dispatch_queue_item ON dispatch_queue(item_id,created_at);

-- Repo execution topology, step-repo links, and synchronized waves (v35).
CREATE TABLE repository_dependencies(upstream_id TEXT NOT NULL REFERENCES repositories(id),downstream_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(upstream_id,downstream_id),CHECK(upstream_id<>downstream_id));
CREATE INDEX repository_dependencies_downstream ON repository_dependencies(downstream_id,upstream_id);
CREATE TABLE step_repositories(step_id TEXT NOT NULL REFERENCES steps(id),repository_id TEXT NOT NULL REFERENCES repositories(id),position INTEGER,created_at TEXT NOT NULL,PRIMARY KEY(step_id,repository_id));
CREATE INDEX step_repositories_repository ON step_repositories(repository_id,position,step_id);
CREATE TABLE waves(id TEXT PRIMARY KEY,roadmap_id TEXT NOT NULL REFERENCES roadmaps(id),name TEXT NOT NULL,summary TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'planning' CHECK(status IN('planning','active','completed','cancelled')),position INTEGER NOT NULL DEFAULT 0,discussion_id TEXT REFERENCES discussions(id),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,activated_at TEXT,completed_at TEXT);
CREATE INDEX waves_roadmap ON waves(roadmap_id,archived,position);
CREATE TABLE wave_steps(wave_id TEXT NOT NULL REFERENCES waves(id),step_id TEXT NOT NULL REFERENCES steps(id),position INTEGER NOT NULL DEFAULT 0,added_at TEXT NOT NULL,PRIMARY KEY(wave_id,step_id));
CREATE INDEX wave_steps_step ON wave_steps(step_id);
CREATE TABLE wave_repositories(wave_id TEXT NOT NULL REFERENCES waves(id),repository_id TEXT NOT NULL REFERENCES repositories(id),tier INTEGER NOT NULL DEFAULT 2 CHECK(tier BETWEEN 1 AND 9),freeze_status TEXT NOT NULL DEFAULT 'open' CHECK(freeze_status IN('open','batching','frozen')),frozen_ref TEXT NOT NULL DEFAULT '',frozen_at TEXT,frozen_by_label TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),updated_at TEXT NOT NULL,PRIMARY KEY(wave_id,repository_id));
CREATE INDEX wave_repositories_repo ON wave_repositories(repository_id,wave_id);
CREATE TABLE wave_repins(wave_id TEXT NOT NULL REFERENCES waves(id),upstream_repo_id TEXT NOT NULL REFERENCES repositories(id),consumer_repo_id TEXT NOT NULL REFERENCES repositories(id),status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','in_progress','verified','waived')),pinned_ref TEXT NOT NULL DEFAULT '',pr_url TEXT NOT NULL DEFAULT '',commit_sha TEXT NOT NULL DEFAULT '',reported_by_label TEXT NOT NULL DEFAULT '',verified_at TEXT,revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),updated_at TEXT NOT NULL,PRIMARY KEY(wave_id,upstream_repo_id,consumer_repo_id),CHECK(upstream_repo_id<>consumer_repo_id));
CREATE INDEX wave_repins_consumer ON wave_repins(consumer_repo_id,wave_id);

-- Typed work dependencies and agent waits (v37).
CREATE TABLE dependencies(id TEXT PRIMARY KEY,dependent_type TEXT NOT NULL CHECK(dependent_type IN('repository','step','item','ticket','agent')),dependent_id TEXT NOT NULL,prerequisite_type TEXT NOT NULL CHECK(prerequisite_type IN('repository','step','item','ticket','agent')),prerequisite_id TEXT NOT NULL,context_type TEXT CHECK(context_type IS NULL OR context_type IN('repository','step','item','ticket')),context_id TEXT,reported_by_agent_id TEXT REFERENCES communication_agents(id),condition TEXT NOT NULL DEFAULT '',confirmed_at TEXT,confirmed_by_id TEXT,confirmed_by_label TEXT,confirmation_note TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,CHECK(dependent_type<>prerequisite_type OR dependent_id<>prerequisite_id),CHECK((context_type IS NULL)=(context_id IS NULL)),CHECK((dependent_type='agent')=(context_type IS NOT NULL)),CHECK((confirmed_at IS NULL AND confirmed_by_id IS NULL AND confirmed_by_label IS NULL) OR (confirmed_at IS NOT NULL AND confirmed_by_id IS NOT NULL AND confirmed_by_label IS NOT NULL)));
CREATE UNIQUE INDEX dependencies_shared_active ON dependencies(dependent_type,dependent_id,prerequisite_type,prerequisite_id) WHERE archived=0 AND context_type IS NULL;
CREATE UNIQUE INDEX dependencies_wait_pending ON dependencies(dependent_type,dependent_id,prerequisite_type,prerequisite_id,context_type,context_id,condition) WHERE archived=0 AND context_type IS NOT NULL AND confirmed_at IS NULL;
CREATE INDEX dependencies_dependent ON dependencies(dependent_type,dependent_id,archived,created_at,id);
CREATE INDEX dependencies_prerequisite ON dependencies(prerequisite_type,prerequisite_id,archived,created_at,id);
"""

PUSH_SCHEMA = """
CREATE TABLE push_enrollments(id TEXT PRIMARY KEY,credential_id TEXT NOT NULL REFERENCES communication_credentials(credential_id),provider TEXT NOT NULL CHECK(provider IN('codex','claude')),chat_id TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,disabled_at TEXT,UNIQUE(credential_id,provider,chat_id));
CREATE INDEX push_enrollments_credential ON push_enrollments(credential_id,disabled_at);
CREATE TABLE push_subscriptions(id TEXT PRIMARY KEY,enrollment_id TEXT NOT NULL REFERENCES push_enrollments(id),subject_type TEXT NOT NULL CHECK(subject_type IN('ticket','item','run')),subject_id TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(enrollment_id,subject_type,subject_id));
CREATE INDEX push_subscriptions_subject ON push_subscriptions(subject_type,subject_id,enabled,enrollment_id);
CREATE INDEX push_subscriptions_enrollment ON push_subscriptions(enrollment_id,enabled,subject_type,subject_id);
CREATE TABLE push_deliveries(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,enrollment_id TEXT NOT NULL REFERENCES push_enrollments(id),source_event_id TEXT NOT NULL REFERENCES events(id),kind TEXT NOT NULL CHECK(kind IN('discussion.ask','discussion.reply','ticket.status','item.status','run.end','run.update')),subject_type TEXT NOT NULL CHECK(subject_type IN('discussion','ticket','item','run')),subject_id TEXT NOT NULL,discussion_id TEXT REFERENCES discussions(id),message_id TEXT REFERENCES discussion_messages(id),state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN('pending','claimed','submitted','queued','unavailable','uncertain','cancelled')),attempt_id TEXT,claim_expires_at TEXT,attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,outcome_at TEXT,resume_warning INTEGER NOT NULL DEFAULT 0 CHECK(resume_warning IN(0,1) AND (resume_warning=0 OR state='queued')),UNIQUE(source_event_id,enrollment_id));
CREATE INDEX push_deliveries_enrollment_seq ON push_deliveries(enrollment_id,seq);
CREATE INDEX push_deliveries_pending ON push_deliveries(enrollment_id,seq) WHERE state IN('pending','unavailable');
"""

BASELINE += PUSH_SCHEMA
