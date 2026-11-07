"""Shared, transaction-owned upgrades for startup and offline restoration.

Each step keeps the DDL its version shipped with, and that DDL never changes: an older database
holds what those statements made, and each later step builds on it. A new database starts from
schema.BASELINE instead, the current shape, so a schema change is a new step here and the same
change there.
"""

from __future__ import annotations

import json
import re
import sqlite3

from .common import now, setting, today, uid

SCHEMA_VERSION = 21
# Older versions that startup and offline restore upgrade in one transaction.
UPGRADABLE_VERSIONS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20})

# v1 grew in place before versions were counted: _upgrade_v1 adds whichever of these a v1 database
# lacks. People as v1 had them; v10 renames role to team, adds person details and lets links name
# repositories.
PEOPLE_SCHEMA = """
CREATE TABLE people(id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE person_links(id TEXT PRIMARY KEY,person_id TEXT NOT NULL REFERENCES people(id),target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap')),target_id TEXT NOT NULL,relationship TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(person_id,target_type,target_id));
CREATE INDEX person_links_target ON person_links(target_type,target_id);
CREATE INDEX person_links_person ON person_links(person_id);
"""

# Columns that tell who is working: they sit exactly where ALTER TABLE ADD COLUMN places
# them, so a v1 database whose agent chats gain them and one that gains agent chats agree.
AGENT_CHAT_COLUMNS = ", provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', repos TEXT NOT NULL DEFAULT '[]'"

LINKS_CHATS_GITHUB_SCHEMA = """
CREATE TABLE web_links(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL CHECK(owner_type IN('item','roadmap')),owner_id TEXT NOT NULL,url TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX web_links_owner ON web_links(owner_type,owner_id,archived);
CREATE TABLE agent_chats(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL CHECK(owner_type IN('item','roadmap')),owner_id TEXT NOT NULL,chat_id TEXT NOT NULL,title TEXT NOT NULL,created_date TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL""" + AGENT_CHAT_COLUMNS + """);
CREATE INDEX agent_chats_owner ON agent_chats(owner_type,owner_id,archived);
CREATE TABLE github_refs(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL CHECK(owner_type IN('item','roadmap')),owner_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN('commit','pr')),url TEXT NOT NULL,reference TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',chat_id TEXT REFERENCES agent_chats(id),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX github_refs_owner ON github_refs(owner_type,owner_id,archived);
"""

# A roadmap's agent chats name the steps they work on.
CHAT_STEPS_SCHEMA = """
CREATE TABLE chat_steps(chat_id TEXT NOT NULL REFERENCES agent_chats(id),step_id TEXT NOT NULL REFERENCES steps(id),created_at TEXT NOT NULL,PRIMARY KEY(chat_id,step_id));
CREATE INDEX chat_steps_step ON chat_steps(step_id);
"""

# A roadmap's questions with their answers, and the decisions made along the way.
QUESTIONS_DECISIONS_SCHEMA = """
CREATE TABLE roadmap_questions(id TEXT PRIMARY KEY,roadmap_id TEXT NOT NULL REFERENCES roadmaps(id),title TEXT NOT NULL,details TEXT NOT NULL DEFAULT '',answer TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','answered')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX roadmap_questions_roadmap ON roadmap_questions(roadmap_id,archived);
CREATE TABLE roadmap_decisions(id TEXT PRIMARY KEY,roadmap_id TEXT NOT NULL REFERENCES roadmaps(id),title TEXT NOT NULL,rationale TEXT NOT NULL DEFAULT '',decided_date TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX roadmap_decisions_roadmap ON roadmap_decisions(roadmap_id,archived);
"""

# v1's discussions and the agent identities they share, with v3's avatar column. The upgrade adds
# that column to identities that predate it before add_discussions compares their tables, and it
# sits exactly where ALTER TABLE ADD COLUMN places it.
AGENT_AVATAR_COLUMN = ", avatar TEXT"

DISCUSSION_SCHEMA = """
CREATE TABLE communication_agents(id TEXT PRIMARY KEY,label TEXT NOT NULL,created_at TEXT NOT NULL""" + AGENT_AVATAR_COLUMN + """);
CREATE TABLE communication_credentials(credential_id TEXT PRIMARY KEY REFERENCES credentials(id),agent_id TEXT NOT NULL REFERENCES communication_agents(id));
CREATE INDEX communication_credentials_agent ON communication_credentials(agent_id);
CREATE TABLE discussions(id TEXT PRIMARY KEY,title TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX discussions_updated ON discussions(archived,updated_at,id);
CREATE TABLE discussion_contexts(discussion_id TEXT NOT NULL REFERENCES discussions(id),context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step')),context_id TEXT NOT NULL,PRIMARY KEY(discussion_id,context_type,context_id));
CREATE INDEX discussion_contexts_context ON discussion_contexts(context_type,context_id,discussion_id);
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
CREATE TABLE discussion_change_contexts(seq INTEGER NOT NULL REFERENCES discussion_changes(seq),context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step')),context_id TEXT NOT NULL,PRIMARY KEY(seq,context_type,context_id));
CREATE INDEX discussion_change_contexts_context ON discussion_change_contexts(context_type,context_id,seq);
"""


def add_discussions(conn: sqlite3.Connection) -> None:
    """Add the whole discussion schema atomically inside the caller's migration transaction, and
    give each credential that has none an agent identity."""
    from .common import AppError
    from .discussions import bind_credential

    statements = list(filter(str.strip, DISCUSSION_SCHEMA.split(";")))
    expected = {}
    for statement in statements:
        match = re.match(r"\s*CREATE (?:UNIQUE )?(?:TABLE|INDEX) (\w+)", statement)
        assert match is not None
        expected[match.group(1)] = " ".join(statement.split())
    existing = {row[0]: " ".join((row[1] or "").split()) for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'discussion%' OR name LIKE 'communication%'")}
    if existing and existing != expected:
        raise AppError(503, "database_invalid", "Communication schema is incomplete or changed; restore a verified backup")
    # Each statement is executed separately: executescript would commit the caller.
    if not existing:
        for statement in statements:
            conn.execute(statement)
    for row in conn.execute("SELECT id,label FROM credentials WHERE id NOT IN(SELECT credential_id FROM communication_credentials)").fetchall():
        bind_credential(conn, row[0], row[1])


# v2: the repository catalog, and the runs, participations and updates agents report.
COORDINATION_SCHEMA = """
CREATE TABLE repositories(id TEXT PRIMARY KEY,name TEXT NOT NULL,url TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX repositories_url ON repositories(url) WHERE url<>'';
CREATE TABLE item_repositories(item_id TEXT NOT NULL REFERENCES work_items(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(item_id,repository_id));
CREATE INDEX item_repositories_repository ON item_repositories(repository_id,item_id);
CREATE TABLE chat_repositories(chat_id TEXT NOT NULL REFERENCES agent_chats(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(chat_id,repository_id));
CREATE INDEX chat_repositories_repository ON chat_repositories(repository_id,chat_id);
CREATE TABLE agent_runs(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL REFERENCES communication_agents(id),credential_id TEXT NOT NULL REFERENCES credentials(id),client_run_key TEXT NOT NULL,display_name TEXT NOT NULL,provider TEXT NOT NULL DEFAULT '',external_chat_id TEXT,revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,ended_at TEXT,end_reason TEXT,UNIQUE(credential_id,client_run_key));
CREATE INDEX agent_runs_credential ON agent_runs(credential_id,ended_at);
CREATE TABLE participations(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES agent_runs(id),item_id TEXT REFERENCES work_items(id),description TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,released_at TEXT,release_reason TEXT);
CREATE INDEX participations_run ON participations(run_id,released_at);
CREATE INDEX participations_item ON participations(item_id,released_at);
CREATE TABLE participation_contexts(participation_id TEXT NOT NULL REFERENCES participations(id),position INTEGER NOT NULL CHECK(position>=0),repository_id TEXT NOT NULL REFERENCES repositories(id),checkout_path TEXT NOT NULL DEFAULT '',worktree_path TEXT NOT NULL DEFAULT '',branch TEXT NOT NULL DEFAULT '',PRIMARY KEY(participation_id,position));
CREATE INDEX participation_contexts_repository ON participation_contexts(repository_id,participation_id);
CREATE TABLE coordination_updates(id TEXT PRIMARY KEY,participation_id TEXT NOT NULL REFERENCES participations(id),run_id TEXT NOT NULL REFERENCES agent_runs(id),agent_id TEXT NOT NULL REFERENCES communication_agents(id),credential_id TEXT NOT NULL REFERENCES credentials(id),author_label TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN('update','blocker','handoff')),text TEXT NOT NULL,item_id TEXT REFERENCES work_items(id),contexts_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX coordination_updates_participation ON coordination_updates(participation_id,created_at,id);
CREATE INDEX coordination_updates_item ON coordination_updates(item_id,created_at,id);
CREATE INDEX coordination_updates_created ON coordination_updates(created_at,id);
CREATE TABLE coordination_update_repositories(update_id TEXT NOT NULL REFERENCES coordination_updates(id),repository_id TEXT NOT NULL REFERENCES repositories(id),PRIMARY KEY(update_id,repository_id));
CREATE INDEX coordination_update_repositories_repository ON coordination_update_repositories(repository_id,update_id);
"""

# v4: messages the person writes into a tracked chat, and their deliveries to agent runs.
CHAT_MESSAGE_SCHEMA = """
CREATE TABLE chat_messages(id TEXT PRIMARY KEY,chat_id TEXT NOT NULL REFERENCES agent_chats(id),text TEXT NOT NULL,author_id TEXT NOT NULL,author_label TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX chat_messages_chat ON chat_messages(chat_id,created_at,id);
CREATE TABLE chat_message_deliveries(message_id TEXT NOT NULL REFERENCES chat_messages(id),run_id TEXT NOT NULL REFERENCES agent_runs(id),status TEXT NOT NULL CHECK(status IN('pending','delivered','expired')),expires_at TEXT NOT NULL,delivered_at TEXT,expired_at TEXT,expire_reason TEXT,PRIMARY KEY(message_id,run_id));
CREATE INDEX chat_message_deliveries_run ON chat_message_deliveries(run_id,status);
"""

# v5: tickets. Every statement stands alone: execute_schema splits on semicolons.
TICKET_SCHEMA = """
CREATE TABLE tickets(id TEXT PRIMARY KEY,number INTEGER NOT NULL UNIQUE CHECK(number>=1),title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL DEFAULT 'bug' CHECK(kind IN('bug','incident','task')),priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN('low','normal','high','urgent')),status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','doing','blocked','done')),resolution TEXT NOT NULL DEFAULT '',item_id TEXT REFERENCES work_items(id),assignee_id TEXT,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,closed_at TEXT,last_activity_at TEXT NOT NULL);
CREATE INDEX tickets_list ON tickets(archived,status,last_activity_at,id);
CREATE INDEX tickets_item ON tickets(item_id,archived);
CREATE INDEX tickets_assignee ON tickets(assignee_id,archived,status);
CREATE TABLE ticket_repositories(ticket_id TEXT NOT NULL REFERENCES tickets(id),repository_id TEXT NOT NULL REFERENCES repositories(id),created_at TEXT NOT NULL,PRIMARY KEY(ticket_id,repository_id));
CREATE INDEX ticket_repositories_repository ON ticket_repositories(repository_id,ticket_id);
CREATE TABLE ticket_labels(ticket_id TEXT NOT NULL REFERENCES tickets(id),label TEXT NOT NULL,PRIMARY KEY(ticket_id,label));
CREATE INDEX ticket_labels_label ON ticket_labels(label,ticket_id);
CREATE TABLE ticket_comments(id TEXT PRIMARY KEY,ticket_id TEXT NOT NULL REFERENCES tickets(id),text TEXT NOT NULL,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),created_at TEXT NOT NULL);
CREATE INDEX ticket_comments_ticket ON ticket_comments(ticket_id,created_at,id);
"""

# v6 keys repositories by remote rather than by address text. COORDINATION_SCHEMA keeps the
# v2-v5 shape, and add_remote_identity brings every older database to v6's.
# duplicate_of flags a record that shares its remote with the canonical one; merged_into marks
# a record a person merged into another, whose remote then answers with that other record.
REPOSITORY_IDENTITY_COLUMNS = (", remote_identity TEXT NOT NULL DEFAULT '', duplicate_of TEXT REFERENCES repositories(id)"
                               ", merged_into TEXT REFERENCES repositories(id)")


def add_remote_identity(conn: sqlite3.Connection) -> None:
    """Key each repository by its remote, one canonical record per remote.

    Records that already share a remote stay as they are: all but one are flagged as
    duplicates for a person to merge, so this step never fails startup. The canonical
    record is the oldest active one, or the oldest when all are archived.
    """
    from .coordination import repository_identity

    for column in REPOSITORY_IDENTITY_COLUMNS.split(", ")[1:]:
        conn.execute("ALTER TABLE repositories ADD COLUMN " + column)
    conn.execute("DROP INDEX repositories_url")
    canonical: dict[str, str] = {}
    for record, url in conn.execute("SELECT id,url FROM repositories WHERE url<>'' ORDER BY archived,created_at,id").fetchall():
        key = repository_identity(url)
        holder = canonical.setdefault(key, record)
        conn.execute("UPDATE repositories SET remote_identity=?,duplicate_of=? WHERE id=?",
                     (key, None if holder == record else holder, record))
    conn.execute("CREATE UNIQUE INDEX repositories_remote_identity ON repositories(remote_identity) "
                 "WHERE remote_identity<>'' AND duplicate_of IS NULL AND merged_into IS NULL")


# v7 records who registered each repository. A repository an agent registered for itself
# stays unreviewed until the person confirms or edits it.
REPOSITORY_PROVENANCE_COLUMNS = (", registered_by_kind TEXT NOT NULL DEFAULT '', registered_by_label TEXT NOT NULL DEFAULT ''"
                                 ", registered_by_agent TEXT REFERENCES communication_agents(id)"
                                 ", reviewed INTEGER NOT NULL DEFAULT 1 CHECK(reviewed IN(0,1))")


def add_repository_provenance(conn: sqlite3.Connection) -> None:
    """Take each repository's registrant from its creation event; existing ones count as reviewed."""
    for column in REPOSITORY_PROVENANCE_COLUMNS.split(", ")[1:]:
        conn.execute("ALTER TABLE repositories ADD COLUMN " + column)
    created = "e.entity_id=repositories.id AND e.kind='repository.create' ORDER BY e.created_at,e.id LIMIT 1"
    # An agent is named by its identity, as new registrations are, rather than by the credential it used.
    identity = ("FROM events e JOIN communication_credentials cc ON cc.credential_id=e.actor_id JOIN communication_agents a"
                f" ON a.id=cc.agent_id WHERE e.actor_kind='agent' AND {created}")
    conn.execute(f"UPDATE repositories SET registered_by_kind=COALESCE((SELECT e.actor_kind FROM events e WHERE {created}),''),"
                 f"registered_by_label=COALESCE((SELECT a.label {identity}),(SELECT e.actor_label FROM events e WHERE {created}),''),"
                 f"registered_by_agent=(SELECT a.id {identity})")


# v8 gives each work item one scratchpad, its single active note, and the index allows no second.
SCRATCHPAD_INDEX = ("CREATE UNIQUE INDEX notes_item_scratchpad ON notes(owner_id) "
                    "WHERE owner_type='item' AND archived=0")


def add_item_scratchpads(conn: sqlite3.Connection) -> None:
    """Fold each work item's active notes into its oldest one, as sections, and archive the rest.

    Each note's title becomes its section's heading, so a scratchpad, which shows no title,
    hides none. The archived notes keep their text whole, so nothing is lost when a fold stops
    short of the note size limit (see scratchpad.fold). A lone untitled note, archived notes and
    other owners' notes stay as they are.
    """
    from .common import now
    from .scratchpad import TITLE, UNTITLED, fold

    items: dict[str, list[tuple[str, str, str]]] = {}
    for identity, owner, title, text in conn.execute(
            "SELECT id,owner_id,title,text FROM notes WHERE owner_type='item' AND archived=0 ORDER BY owner_id,created_at,id"):
        items.setdefault(owner, []).append((identity, title, text))
    stamp = now()
    refiled: list[tuple[str, str, str, str, int]] = []
    for owner, notes in items.items():
        (keeper, kept_title, _), rest = notes[0], notes[1:]
        if not rest and kept_title.strip() in UNTITLED:
            continue
        merged, titled = fold([(title, text) for _, title, text in notes])
        if not rest and not titled:
            continue  # a lone note whose heading would not fit stays as it was
        title = TITLE if titled else kept_title
        conn.execute("UPDATE notes SET title=?,text=?,revision=revision+1,updated_at=? WHERE id=?",
                     (title, merged, stamp, keeper))
        refiled.append((keeper, title, merged, owner, 0))
        # Archived as the archive command does: the text stays whole and readable.
        for identity, note_title, text in rest:
            conn.execute("UPDATE notes SET archived=1,revision=revision+1 WHERE id=?", (identity,))
            refiled.append((identity, note_title, text, owner, 1))
    _refile_notes(conn, refiled)
    conn.execute(SCRATCHPAD_INDEX)


def _refile_notes(conn: sqlite3.Connection, notes: list[tuple[str, str, str, str, int]]) -> None:
    """File notes in search as search.put does, so search finds merged text and the archived rest.

    search_index cannot look up entity_id directly, so every delete reads the whole table:
    one delete per batch rather than per note keeps a large upgrade from stalling startup.
    """
    identities = [note[0] for note in notes]
    for start in range(0, len(identities), 500):
        batch = identities[start:start + 500]
        conn.execute(f"DELETE FROM search_index WHERE entity_id IN ({','.join('?' for _ in batch)})", batch)
    conn.executemany("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,'note',?,?,?,?)", notes)


# v9: resources, which an upgrade leaves empty. Every statement stands alone: execute_schema
# splits on semicolons.
RESOURCE_SCHEMA = """
CREATE TABLE resources(id TEXT PRIMARY KEY,title TEXT NOT NULL,summary TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,body TEXT NOT NULL DEFAULT '');
"""


# v10 gives each person a title, a description and contact details. The field v9 kept as
# `role` ("Title or team") is their team now, and people can be tied to repositories too.
PERSON_DETAIL_COLUMNS = (", title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT ''"
                         ", email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT ''"
                         ", chat TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT ''")
PERSON_LINKS_TABLE = ("CREATE TABLE person_links(id TEXT PRIMARY KEY,person_id TEXT NOT NULL REFERENCES people(id),"
                      "target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap','repository')),target_id TEXT NOT NULL,"
                      "relationship TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,UNIQUE(person_id,target_type,target_id))")
PERSON_LINKS_INDEXES = """
CREATE INDEX person_links_target ON person_links(target_type,target_id);
CREATE INDEX person_links_person ON person_links(person_id);
"""
# Proposals that can still be submitted or approved; reopening brings a withdrawn one back.
OPEN_PROPOSALS = ("draft", "submitted", "withdrawn")


def add_person_details(conn: sqlite3.Connection) -> None:
    """Rename `role` to `team`, add the new person fields, and let links name repositories.

    SQLite cannot widen a CHECK constraint, so the links move to a new table that admits
    repositories. Nothing refers to person_links, so it is renamed aside, copied and dropped,
    which leaves the new table's definition exactly as PERSON_LINKS_TABLE spells it.
    """
    conn.execute("ALTER TABLE people RENAME COLUMN role TO team")
    for column in PERSON_DETAIL_COLUMNS.split(", ")[1:]:
        conn.execute("ALTER TABLE people ADD COLUMN " + column)
    conn.execute("ALTER TABLE person_links RENAME TO person_links_v9")
    conn.execute(PERSON_LINKS_TABLE)
    conn.execute("INSERT INTO person_links(id,person_id,target_type,target_id,relationship,created_at) "
                 "SELECT id,person_id,target_type,target_id,relationship,created_at FROM person_links_v9")
    conn.execute("DROP TABLE person_links_v9")  # and its indexes, whose names the new ones take
    execute_schema(conn, PERSON_LINKS_INDEXES)
    _propose_team(conn)


def _propose_team(conn: sqlite3.Connection) -> None:
    """An open proposal that sets a person's `role` sets their `team`: the same field, renamed.

    Without this, approving it would be refused for a field that no longer exists. As other
    upgrades do for the records they change, each rewritten proposal takes a new revision.
    Approved and rejected proposals stay as they were made, and anything malformed is left
    for review to refuse as it would have, so this step never fails startup.
    """
    from .common import now

    stamp = now()
    marks = ",".join("?" for _ in OPEN_PROPOSALS)
    for identity, stored in conn.execute(f"SELECT id,operations FROM proposals WHERE status IN ({marks})",
                                         OPEN_PROPOSALS).fetchall():
        try:
            operations = json.loads(stored)
        except (ValueError, RecursionError):
            continue
        changed = False
        for operation in operations if isinstance(operations, list) else []:
            payload = operation.get("payload") if isinstance(operation, dict) else None
            if (isinstance(payload, dict) and operation.get("action") in ("person.create", "person.update")
                    and "role" in payload and "team" not in payload):
                payload["team"] = payload.pop("role")
                changed = True
        if not changed:
            continue
        try:
            # Encoded as proposals keep them; a value they could not hold, such as NaN, stays as stored.
            rewritten = json.dumps(operations, allow_nan=False)
        except (ValueError, RecursionError):
            continue
        conn.execute("UPDATE proposals SET operations=?,revision=revision+1,updated_at=? WHERE id=?",
                     (rewritten, stamp, identity))


# v11 adds derived data only, so dropping it gives back v10 exactly (README):
# - search_keys maps each indexed record to its row in search_index. The index's own entity_id,
#   type and owner_id columns are FTS5 UNINDEXED, so finding a row by them read the whole index.
# - indexes for foreign keys that pages look records up by;
# - a date on each idempotency receipt, so receipts can be pruned (maintenance.prune_receipts).
SEARCH_KEYS_SCHEMA = """
CREATE TABLE search_keys(entity_id TEXT NOT NULL PRIMARY KEY,doc INTEGER NOT NULL UNIQUE,type TEXT NOT NULL,owner_id TEXT NOT NULL);
CREATE INDEX search_keys_owner ON search_keys(owner_id,type);
"""
LOOKUP_INDEXES = """
CREATE INDEX tasks_item ON tasks(item_id,archived);
CREATE INDEX file_references_item ON file_references(item_id,archived);
CREATE INDEX work_items_step ON work_items(step_id);
CREATE INDEX work_items_roadmap ON work_items(roadmap_id);
CREATE INDEX github_refs_chat ON github_refs(chat_id);
CREATE INDEX steps_parent ON steps(parent_id);
"""
RECEIPT_DATE_COLUMN = "created_at TEXT NOT NULL DEFAULT ''"


def add_search_keys_and_receipt_dates(conn: sqlite3.Connection) -> None:
    """Key every search row, add the lookup indexes, and date the receipts.

    Older writers could leave a record indexed twice; the newest row is the current one, so
    the others go, and a row that names no record goes too. Receipts written before this
    upgrade take its time, so a retry still in flight keeps replaying for the whole window.
    """
    from .common import now

    execute_schema(conn, SEARCH_KEYS_SCHEMA)
    conn.execute("DELETE FROM search_index WHERE entity_id IS NULL")
    conn.execute("DELETE FROM search_index WHERE rowid NOT IN (SELECT MAX(rowid) FROM search_index GROUP BY entity_id)")
    conn.execute("INSERT INTO search_keys(entity_id,doc,type,owner_id) "
                 "SELECT entity_id,rowid,COALESCE(type,''),COALESCE(owner_id,'') FROM search_index")
    execute_schema(conn, LOOKUP_INDEXES)
    conn.execute("ALTER TABLE receipts ADD COLUMN " + RECEIPT_DATE_COLUMN)
    conn.execute("UPDATE receipts SET created_at=?", (now(),))
    conn.execute("CREATE INDEX receipts_created ON receipts(created_at)")


# v12 moves state that features read from event history into columns, so history can be
# thinned later without changing what Worklog decides. Each value is copied from the events
# that held it, which stay as they are.
# - who registered each credential's identity, and the chat it registered with;
# - when each proposal was last submitted, and whether automatic approval applied it;
# - which stale-plan notices each proposal's agent was already sent.
# registered_by names the sponsor's identity without a foreign key, like every other actor id here,
# so the documented way back can drop it on any SQLite that has DROP COLUMN.
REGISTRATION_COLUMNS = (", registered_by TEXT"
                        ", registered_by_label TEXT NOT NULL DEFAULT ''"
                        ", chat_id TEXT")
PROPOSAL_STATE_COLUMNS = (", submitted_at TEXT"
                          ", auto_approved INTEGER NOT NULL DEFAULT 0 CHECK(auto_approved IN(0,1))")
STALE_NOTICES_SCHEMA = """
CREATE TABLE proposal_stale_notices(proposal_id TEXT NOT NULL REFERENCES proposals(id),proposal_revision INTEGER NOT NULL,conflicts TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(proposal_id,proposal_revision,conflicts));
"""
# The approval actor of the Settings choice that applies submitted proposals (proposals.AUTOMATIC).
AUTOMATIC_APPROVAL_ID = "automatic-approval"


def stale_signature(conflicts: list[dict]) -> str:
    """What makes two stale-plan notices the same: each conflicting operation and the revision it met."""
    return json.dumps([[conflict.get("operation"), conflict.get("currentRevision")] for conflict in conflicts],
                      separators=(",", ":"))


def add_decision_state(conn: sqlite3.Connection) -> None:
    """Copy registration, proposal and stale-notice state out of event JSON into columns."""
    for column in REGISTRATION_COLUMNS.split(", ")[1:]:
        conn.execute("ALTER TABLE communication_credentials ADD COLUMN " + column)
    for credential, after in conn.execute("SELECT entity_id,after_json FROM events WHERE kind='credential.register' "
                                          "ORDER BY created_at,id").fetchall():
        record = json.loads(after)
        sponsor = record.get("registeredBy") or {}
        conn.execute("UPDATE communication_credentials SET registered_by=?,registered_by_label=?,chat_id=? WHERE credential_id=?",
                     (sponsor.get("id"), sponsor.get("label") or "", record.get("chatId") or None, credential))
    for column in PROPOSAL_STATE_COLUMNS.split(", ")[1:]:
        conn.execute("ALTER TABLE proposals ADD COLUMN " + column)
    conn.execute("UPDATE proposals SET submitted_at=(SELECT MAX(e.created_at) FROM events e WHERE e.entity_id=proposals.id"
                 " AND e.kind='proposal.submit'),auto_approved=EXISTS(SELECT 1 FROM events e WHERE e.entity_id=proposals.id"
                 " AND e.kind='proposal.approve' AND e.actor_id=?)", (AUTOMATIC_APPROVAL_ID,))
    execute_schema(conn, STALE_NOTICES_SCHEMA)
    for proposal, after, at in conn.execute("SELECT entity_id,after_json,created_at FROM events WHERE kind='proposal.stale' "
                                            "ORDER BY created_at,id").fetchall():
        record = json.loads(after)
        conn.execute("INSERT OR IGNORE INTO proposal_stale_notices(proposal_id,proposal_revision,conflicts,created_at) VALUES(?,?,?,?)",
                     (proposal, record.get("proposalRevision"), stale_signature(record.get("conflicts", [])), at))


# v13 binds chats to identities (K3). An identity is bound to the chat it registered with
# (communication_credentials.chat_id, since v12) with no row here; this table holds the other
# bindings, each one the person confirmed on the chat page. chat_id is the chat's own ID, as runs
# report it and tracked chats hold it, so one confirmation covers every record that tracks the chat.
CHAT_CONFIRMATIONS_SCHEMA = """
CREATE TABLE chat_confirmations(chat_id TEXT NOT NULL,agent_id TEXT NOT NULL REFERENCES communication_agents(id),confirmed_by TEXT NOT NULL,confirmed_by_label TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(chat_id,agent_id));
"""


def add_chat_confirmations(conn: sqlite3.Connection) -> None:
    """Bind chats to identities. A delivery still waiting for an identity bound to no chat it was
    sent to ends now, as one would after the person removed its confirmation."""
    from .common import now

    execute_schema(conn, CHAT_CONFIRMATIONS_SCHEMA)
    # Nothing is confirmed yet, so an identity is bound only to the chat it registered with. A run
    # keeps the chat ID it started with, so its deliveries are all for that chat.
    stamp = now()
    conn.execute("UPDATE chat_message_deliveries SET status='expired',expired_at=?,expire_reason='unconfirmed' "
                 "WHERE status='pending' AND expires_at>? AND run_id IN (SELECT ar.id FROM agent_runs ar WHERE NOT EXISTS("
                 "SELECT 1 FROM communication_credentials cc WHERE cc.agent_id=ar.agent_id AND cc.chat_id=ar.external_chat_id))",
                 (stamp, stamp))


# v14 lets the person order each work item's tasks. position orders all of an item's tasks,
# archived ones included, so a recovered task comes back where it was; the item's active tasks
# are listed by it, and ties, which only a writer that skipped the column could leave, by the
# time each task was added.
TASK_POSITION_COLUMN = "position INTEGER NOT NULL DEFAULT 0 CHECK(position>=0)"


def add_task_positions(conn: sqlite3.Connection) -> None:
    """Number each work item's tasks from 1 in the order version 13 listed them.

    Version 13 read an item's tasks with the query below. Running it again keeps every list as
    the person last saw it, tasks added in the same instant included. Only the order is new:
    no task's revision or last-updated time changes.
    """
    conn.execute("ALTER TABLE tasks ADD COLUMN " + TASK_POSITION_COLUMN)
    for (item,) in conn.execute("SELECT DISTINCT item_id FROM tasks").fetchall():
        listed = conn.execute("SELECT * FROM tasks WHERE item_id=? ORDER BY created_at", (item,)).fetchall()
        conn.executemany("UPDATE tasks SET position=? WHERE id=?",
                         [(place, row[0]) for place, row in enumerate(listed, 1)])


# v15 lets a work item wait on someone else. The table is v14's with one more status in its
# CHECK, column for column, roadmap_id where ALTER TABLE ADD COLUMN placed it in v1.
WORK_ITEMS_TABLE = ("CREATE TABLE work_items(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',"
                    "status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','doing','waiting','blocked','done')),"
                    "ado_url TEXT,step_id TEXT REFERENCES steps(id),backlog_order INTEGER NOT NULL,"
                    "archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1,"
                    "created_at TEXT NOT NULL,updated_at TEXT NOT NULL, roadmap_id TEXT REFERENCES roadmaps(id))")
WORK_ITEM_COLUMNS = "id,title,description,status,ado_url,step_id,backlog_order,archived,revision,created_at,updated_at,roadmap_id"
WORK_ITEM_INDEXES = """
CREATE INDEX work_items_step ON work_items(step_id);
CREATE INDEX work_items_roadmap ON work_items(roadmap_id);
"""


def add_waiting_status(conn: sqlite3.Connection) -> None:
    """Admit 'waiting' as a work item's status. No record changes: every row, rowid included, and
    every reference to it is as it was.

    SQLite cannot widen a CHECK constraint, so work_items is built again. Unlike person_links in
    v10, the table is never renamed: a rename would rewrite the references other tables hold to it.
    Its rows go aside into a temporary copy, and the table is dropped and created again under its
    own name, so those references find it there. Dropping it deletes its rows first, which leaves
    every reference dangling until they come back; defer_foreign_keys holds those checks until the
    transaction commits, and the commit fails if any reference still dangles then. The setting
    lasts until the caller's transaction ends: turning it off sooner would forget the dangling
    references it is counting instead of checking them.
    """
    conn.execute("PRAGMA defer_foreign_keys=ON")
    conn.execute(f"CREATE TEMP TABLE work_items_v14 AS SELECT rowid AS kept_rowid,{WORK_ITEM_COLUMNS} FROM work_items")
    conn.execute("DROP TABLE work_items")  # and its indexes
    conn.execute(WORK_ITEMS_TABLE)
    conn.execute(f"INSERT INTO work_items(rowid,{WORK_ITEM_COLUMNS}) "
                 f"SELECT kept_rowid,{WORK_ITEM_COLUMNS} FROM temp.work_items_v14 ORDER BY kept_rowid")
    conn.execute("DROP TABLE temp.work_items_v14")
    execute_schema(conn, WORK_ITEM_INDEXES)


# v16 records which work items and roadmaps each change of an applied proposal was made to, by
# the rules in proposal_targets.py, so a record's page lists the proposals applied to it in order.
# operation is the change's number as Review shows it, and action its operation, so the list names
# the changes without reading the proposal's operations. Only approval writes rows, and an approved
# proposal is final, so a proposal's rows never change once written.
PROPOSAL_TARGETS_SCHEMA = """
CREATE TABLE proposal_targets(proposal_id TEXT NOT NULL REFERENCES proposals(id),operation INTEGER NOT NULL CHECK(operation>=1),action TEXT NOT NULL,target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap')),target_id TEXT NOT NULL,PRIMARY KEY(proposal_id,operation,target_type,target_id));
CREATE INDEX proposal_targets_target ON proposal_targets(target_type,target_id);
"""


def add_proposal_targets(conn: sqlite3.Connection) -> None:
    """Record what each proposal approved before v16 was applied to, as approval now records it.

    A change names the records it was made to, except the work item or roadmap it created, whose
    id Worklog chose while applying it. That creation is in the record's history, under the
    proposal's agent, written in the transaction that approved the proposal, before its
    proposal.approve event and after the approval before it: writes are serialized, an approval
    applies every change in its own transaction, and an agent creates work items and roadmaps only
    through proposals. History is read in the order it was written, by rowid, which no clock's
    resolution or adjustment can reorder; Worklog never deletes events or vacuums. Where a
    proposal's creations don't pair one for one with its creating changes, or the person wrote
    the proposal, it keeps the targets its changes name. Nothing here fails startup: a change
    that can't be read counts toward nothing, as approval refused it.
    """
    from .proposal_targets import record

    execute_schema(conn, PROPOSAL_TARGETS_SCHEMA)
    proposers = dict(conn.execute("SELECT id,actor_id FROM proposals WHERE status='approved'").fetchall())
    # Only an agent's creations: the person also creates work items and roadmaps directly.
    made: dict[str, dict[str, list[str]]] = {}
    since: list[tuple[str, str, str]] = []  # creations since the last approval, in the order made
    for kind, identity, actor in conn.execute(
            "SELECT kind,entity_id,actor_id FROM events WHERE kind='proposal.approve' "
            "OR (kind IN ('item.create','roadmap.create') AND actor_kind='agent') ORDER BY rowid").fetchall():
        if kind != "proposal.approve":
            since.append((kind, identity, actor))
            continue
        for created_kind, created_id, creator in since:
            if identity in proposers and creator == proposers[identity]:
                made.setdefault(identity, {}).setdefault(created_kind, []).append(created_id)
        since = []
    for proposal in proposers:
        # One at a time: a whole plan's revision runs to a megabyte.
        (stored,) = conn.execute("SELECT operations FROM proposals WHERE id=?", (proposal,)).fetchone()
        try:
            operations = json.loads(stored)
        except (ValueError, RecursionError):
            continue
        if not isinstance(operations, list):
            continue
        numbered = [(number, operation.get("action"), operation.get("payload"))
                    for number, operation in enumerate(operations, 1) if isinstance(operation, dict)]
        created: dict[int, str] = {}
        for kind, identities in made.get(proposal, {}).items():
            # Changes apply in their order, so the nth creation is the nth change that creates.
            creating = [number for number, action, _ in numbered if action == kind]
            if len(creating) == len(identities):
                created.update(zip(creating, identities, strict=True))
        for number, action, payload in numbered:
            record(conn, proposal, number, action, payload, created.get(number))


# v17 ties agent identities to records: any of these types, with the agent's role there, set by the
# person or by the agent itself (agent_links.py). A record's id is not a foreign key, since a tie names
# one of many tables; Worklog never deletes a record a tie can name, and a repository merge moves them.
AGENT_LINKS_SCHEMA = """
CREATE TABLE agent_links(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL REFERENCES communication_agents(id),target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap','step','repository','person','resource','discussion','proposal','ticket','agent','question','decision')),target_id TEXT NOT NULL,relationship TEXT NOT NULL DEFAULT '',set_by TEXT NOT NULL CHECK(set_by IN('human','agent')),created_at TEXT NOT NULL,UNIQUE(agent_id,target_type,target_id));
CREATE INDEX agent_links_target ON agent_links(target_type,target_id);
"""

# v18 records the directory each agent registered from, on its registration's row beside the chat
# it registered with, so Review can say where a proposal's agent works. Registration also names the
# directory in its credential.register event, as it names the chat.
REGISTRATION_DIRECTORY_COLUMN = "directory TEXT"


def add_registration_directories(conn: sqlite3.Connection) -> None:
    """Copy each registration's directory out of its event into the new column.

    No identity registered before v18 named one, so after an upgrade from v17 every directory is
    unknown. After the documented way back to v17 and an upgrade again, each identity registered
    under v18 gets its directory back from its event. An event that can't be read gives none.
    """
    conn.execute("ALTER TABLE communication_credentials ADD COLUMN " + REGISTRATION_DIRECTORY_COLUMN)
    for credential, after in conn.execute("SELECT entity_id,after_json FROM events WHERE kind='credential.register' "
                                          "ORDER BY created_at,id").fetchall():
        try:
            record = json.loads(after)
        except (TypeError, ValueError, RecursionError):
            continue
        directory = record.get("directory") if isinstance(record, dict) else None
        if isinstance(directory, str) and directory:
            conn.execute("UPDATE communication_credentials SET directory=? WHERE credential_id=?", (directory, credential))


# v19: releases, which an upgrade leaves empty: what ships together and when, the records each
# holds, and its checks of who needs to know, what to test and what to prepare first. A
# release_contents row names a work item, roadmap, step or GitHub reference by id, which the
# release service checks before it adds the row; Worklog never deletes those records. Both dates
# are a YYYY-MM-DD day or null, which the CHECKs hold, since past-due flags compare them as text.
# Every statement stands alone: execute_schema splits on semicolons.
RELEASE_SCHEMA = """
CREATE TABLE releases(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',release_date TEXT CHECK(release_date IS NULL OR date(release_date) IS release_date),status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN('planned','released','cancelled')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE release_contents(release_id TEXT NOT NULL REFERENCES releases(id),target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap','step','github')),target_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(release_id,target_type,target_id));
CREATE INDEX release_contents_target ON release_contents(target_type,target_id,release_id);
CREATE TABLE release_checks(id TEXT PRIMARY KEY,release_id TEXT NOT NULL REFERENCES releases(id),kind TEXT NOT NULL CHECK(kind IN('notify','test','prepare')),text TEXT NOT NULL,details TEXT NOT NULL DEFAULT '',person_id TEXT REFERENCES people(id),due_date TEXT CHECK(due_date IS NULL OR date(due_date) IS due_date),status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','doing','waiting','blocked','done')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX release_checks_release ON release_checks(release_id,archived,kind);
"""

# v20: reminders, which an upgrade leaves empty. Every statement stands alone: execute_schema
# splits on semicolons.
REMINDER_SCHEMA = """
CREATE TABLE reminders(id TEXT PRIMARY KEY,title TEXT NOT NULL,due_date TEXT NOT NULL,due_time TEXT,state TEXT NOT NULL DEFAULT 'open' CHECK(state IN('open','done')),target_type TEXT CHECK(target_type IN('item','ticket','roadmap','person','repository','chat')),target_id TEXT,author_id TEXT NOT NULL,author_label TEXT NOT NULL,author_kind TEXT NOT NULL CHECK(author_kind IN('agent','human')),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,done_at TEXT,notes TEXT NOT NULL DEFAULT '',CHECK((target_type IS NULL)=(target_id IS NULL)));
CREATE INDEX reminders_due ON reminders(archived,state,due_date,due_time);
CREATE INDEX reminders_target ON reminders(target_type,target_id);
"""

# v21 tags a discussion with the records it is about, of more kinds than v1's work contexts, and says
# who added each tag and when. The one tag marked home makes a discussion its repository's own: only a
# repository's tag can be home, and the partial unique indexes keep it to one home discussion for a
# repository and one home repository for a discussion. Feeds follow every kind a tag names. Nothing
# refers to either table, so each is renamed aside, built again, copied and dropped, as v10 did with
# person_links. A discussion can also be pinned, which only the person does.
DISCUSSION_CONTEXTS_TABLE = (
    "CREATE TABLE discussion_contexts(discussion_id TEXT NOT NULL REFERENCES discussions(id),"
    "context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step','repository','ticket','release','person','resource','question','decision')),"
    "context_id TEXT NOT NULL,home INTEGER NOT NULL DEFAULT 0 CHECK(home IN(0,1)),added_by TEXT NOT NULL,"
    "added_by_kind TEXT NOT NULL CHECK(added_by_kind IN('agent','human')),created_at TEXT NOT NULL,"
    "PRIMARY KEY(discussion_id,context_type,context_id),CHECK(home=0 OR context_type='repository'))")
DISCUSSION_CONTEXT_INDEXES = """
CREATE INDEX discussion_contexts_context ON discussion_contexts(context_type,context_id,discussion_id);
CREATE UNIQUE INDEX discussion_contexts_home ON discussion_contexts(context_type,context_id) WHERE home=1;
CREATE UNIQUE INDEX discussion_contexts_home_discussion ON discussion_contexts(discussion_id) WHERE home=1;
"""
DISCUSSION_CHANGE_CONTEXTS_TABLE = (
    "CREATE TABLE discussion_change_contexts(seq INTEGER NOT NULL REFERENCES discussion_changes(seq),"
    "context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step','repository','ticket','release','person','resource','question','decision')),"
    "context_id TEXT NOT NULL,PRIMARY KEY(seq,context_type,context_id))")
DISCUSSION_CHANGE_CONTEXT_INDEX = (
    "CREATE INDEX discussion_change_contexts_context ON discussion_change_contexts(context_type,context_id,seq)")
DISCUSSION_PIN_COLUMN = "pinned_at TEXT"
# A column of a discussion's first message: its author is who v21 counts as having added the tags
# the discussion had before.
_FIRST_MESSAGE = ("(SELECT m.{} FROM discussion_messages m WHERE m.discussion_id={} "
                  "ORDER BY m.created_at,m.id LIMIT 1)")
# What v21 writes as the person to open each repository's discussion. A first message that begins so
# and names the repository's ID in backticks marks the discussion as that repository's, so an upgrade
# after the documented way back finds it again.
REPOSITORY_DISCUSSION_TITLE = "Repository: {name}"
REPOSITORY_DISCUSSION_OPENING = "Worklog opened this discussion for the repository **"
REPOSITORY_DISCUSSION_TEXT = REPOSITORY_DISCUSSION_OPENING + "{name}** (`{id}`)."


def add_discussion_tags(conn: sqlite3.Connection) -> None:
    """Widen discussion tags and their feed to every kind v21 names, pin discussions, link exact
    repository labels, and open a discussion for each repository that takes changes.

    Each tag a discussion already had keeps its row, now added by whoever wrote the discussion's
    first message, when the discussion was created. A free-text repository label becomes a tag of
    the one repository, merged ones aside, whose name it is exactly; the labels themselves stay (K8).
    """
    conn.execute("ALTER TABLE discussions ADD COLUMN " + DISCUSSION_PIN_COLUMN)
    conn.execute("ALTER TABLE discussion_contexts RENAME TO discussion_contexts_v20")
    conn.execute(DISCUSSION_CONTEXTS_TABLE)
    conn.execute("INSERT INTO discussion_contexts(discussion_id,context_type,context_id,home,added_by,added_by_kind,created_at) "
                 "SELECT o.discussion_id,o.context_type,o.context_id,0,"
                 f"COALESCE({_FIRST_MESSAGE.format('author_id', 'o.discussion_id')},'local-user'),"
                 f"COALESCE({_FIRST_MESSAGE.format('author_kind', 'o.discussion_id')},'human'),COALESCE(d.created_at,'') "
                 "FROM discussion_contexts_v20 o LEFT JOIN discussions d ON d.id=o.discussion_id")
    conn.execute("DROP TABLE discussion_contexts_v20")  # and its index, whose name the new one takes
    execute_schema(conn, DISCUSSION_CONTEXT_INDEXES)
    conn.execute("ALTER TABLE discussion_change_contexts RENAME TO discussion_change_contexts_v20")
    conn.execute(DISCUSSION_CHANGE_CONTEXTS_TABLE)
    conn.execute("INSERT INTO discussion_change_contexts(seq,context_type,context_id) "
                 "SELECT seq,context_type,context_id FROM discussion_change_contexts_v20")
    conn.execute("DROP TABLE discussion_change_contexts_v20")
    conn.execute(DISCUSSION_CHANGE_CONTEXT_INDEX)
    conn.execute("INSERT OR IGNORE INTO discussion_contexts(discussion_id,context_type,context_id,home,added_by,added_by_kind,created_at) "
                 "SELECT dr.discussion_id,'repository',r.id,0,"
                 f"COALESCE({_FIRST_MESSAGE.format('author_id', 'dr.discussion_id')},'local-user'),"
                 f"COALESCE({_FIRST_MESSAGE.format('author_kind', 'dr.discussion_id')},'human'),COALESCE(d.created_at,'') "
                 "FROM discussion_repos dr JOIN repositories r ON r.name=dr.repo AND r.merged_into IS NULL "
                 "LEFT JOIN discussions d ON d.id=dr.discussion_id "
                 "WHERE (SELECT count(*) FROM repositories o WHERE o.name=dr.repo AND o.merged_into IS NULL)=1")
    _open_repository_discussions(conn)


def _open_repository_discussions(conn: sqlite3.Connection) -> None:
    """Give each repository that isn't merged its own discussion. One that had it before the
    documented way back to v20, archived ones included, has that one again, found by its first
    message: written by the person as v21 opens one and naming the repository's ID, in a thread not
    already some repository's own. Otherwise an active repository's is opened, as the person, with
    the history, feed entry and search rows a discussion opened today has; an archived one gets its
    discussion when it is recovered."""
    stamp = now()
    local_date = today(setting(conn, "timezone", "America/Chicago"))
    first_text = _FIRST_MESSAGE.format("text", "d.id")
    for repository, name, archived in conn.execute("SELECT id,name,archived FROM repositories WHERE merged_into IS NULL "
                                                   "ORDER BY created_at,id").fetchall():
        found = conn.execute(
            "SELECT d.id FROM discussions d WHERE d.title LIKE 'Repository: %' "
            f"AND {_FIRST_MESSAGE.format('author_id', 'd.id')}='local-user' "
            f"AND substr({first_text},1,?)=? AND instr({first_text},?)>0 "
            "AND NOT EXISTS(SELECT 1 FROM discussion_contexts h WHERE h.discussion_id=d.id AND h.home=1) "
            "ORDER BY d.created_at,d.id LIMIT 1",
            (len(REPOSITORY_DISCUSSION_OPENING), REPOSITORY_DISCUSSION_OPENING, "(`" + repository + "`)")).fetchone()
        if found is not None:
            conn.execute("INSERT INTO discussion_contexts(discussion_id,context_type,context_id,home,added_by,added_by_kind,created_at) "
                         "VALUES(?,'repository',?,1,'local-user','human',?) "
                         "ON CONFLICT(discussion_id,context_type,context_id) DO UPDATE SET home=1", (found[0], repository, stamp))
            continue
        if archived:
            continue
        discussion, message, recorded = uid(), uid(), uid()
        title = REPOSITORY_DISCUSSION_TITLE.format(name=name)
        body = REPOSITORY_DISCUSSION_TEXT.format(name=name, id=repository)
        conn.execute("INSERT INTO discussions(id,title,archived,revision,created_at,updated_at) VALUES(?,?,0,1,?,?)",
                     (discussion, title, stamp, stamp))
        conn.execute("INSERT INTO discussion_contexts(discussion_id,context_type,context_id,home,added_by,added_by_kind,created_at) "
                     "VALUES(?,'repository',?,1,'local-user','human',?)", (discussion, repository, stamp))
        conn.execute("INSERT INTO discussion_messages(id,discussion_id,text,author_id,author_label,author_kind,created_at) "
                     "VALUES(?,?,?,'local-user','You','human',?)", (message, discussion, body, stamp))
        for entity, kind, text in ((discussion, "discussion", ""), (message, "discussion_message", body)):
            doc = conn.execute("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,?,?,?,?,0)",
                               (entity, kind, title, text, discussion)).lastrowid
            conn.execute("INSERT INTO search_keys(entity_id,doc,type,owner_id) VALUES(?,?,?,?)", (entity, doc, kind, discussion))
        after = {"id": message, "discussionId": discussion, "text": body, "authorId": "local-user", "authorLabel": "You",
                 "authorKind": "human", "createdAt": stamp, "chatId": None, "onBehalfOfRoleId": None,
                 "ownershipGeneration": None, "request": None}
        conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,before_json,after_json) "
                     "VALUES(?,?,'discussion.create','local-user','You','human',?,?,'null',?)",
                     (recorded, message, stamp, local_date, json.dumps(after)))
        seq = conn.execute("INSERT INTO discussion_changes(event_id,kind,discussion_id,message_id,role_id,created_at) "
                           "VALUES(?,'discussion.create',?,?,NULL,?)", (recorded, discussion, message, stamp)).lastrowid
        conn.execute("INSERT INTO discussion_change_contexts(seq,context_type,context_id) VALUES(?,'repository',?)",
                     (seq, repository))


def execute_schema(conn: sqlite3.Connection, schema: str) -> None:
    """Unlike executescript, preserve the caller's transaction and rollback boundary."""
    for statement in filter(str.strip, schema.split(";")):
        conn.execute(statement)


def upgrade(conn: sqlite3.Connection) -> None:
    """Upgrade an accepted older version to the current shape atomically inside the caller's transaction.

    Credential IDs, proposal authors, receipts (beyond the date v11 gives them), and legacy
    chat repository labels are deliberately unchanged. No repository names are guessed or imported.
    The one record v13 changes is a chat message still waiting for an identity that isn't bound to
    the chat: that delivery ends, with the reason 'unconfirmed'. v14 gives every task its place in
    its work item's list, the one it was listed in, and changes nothing else about it. v15 lets a
    work item wait and changes no record. v16 records what each approved proposal was applied to
    and changes no record either. v17 adds agent ties, which start empty, and changes no record.
    v18 records the directory each agent registered from, unknown for those registered before it,
    and changes no record. v19 adds releases, which start empty, and changes no record.
    v20 adds reminders, which start empty too, and changes no record either. v21 lets a discussion
    be tagged with more kinds of record and pinned, tags each discussion with the one repository a
    repository label of it names exactly, and is the first to add records: it opens the discussion
    of each repository that takes changes.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version not in UPGRADABLE_VERSIONS:
        raise ValueError(f"Unsupported database schema version {version}")
    from .discussions import assign_missing_avatars

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # v2, and v1 databases that already hold discussions, predate avatars.
    if "communication_agents" in tables and "avatar" not in {row[1] for row in conn.execute("PRAGMA table_info(communication_agents)")}:
        conn.execute("ALTER TABLE communication_agents ADD COLUMN " + AGENT_AVATAR_COLUMN.removeprefix(", "))
    if version == 1:
        _upgrade_v1(conn, tables)
    assign_missing_avatars(conn)
    if version < 4:
        execute_schema(conn, CHAT_MESSAGE_SCHEMA)
    if version < 5:
        execute_schema(conn, TICKET_SCHEMA)
    if version < 6:
        add_remote_identity(conn)
    if version < 7:
        add_repository_provenance(conn)
    if version < 8:
        add_item_scratchpads(conn)
    if version < 9:
        execute_schema(conn, RESOURCE_SCHEMA)  # resources start empty
    if version < 10:
        add_person_details(conn)
    if version < 11:
        add_search_keys_and_receipt_dates(conn)
    if version < 12:
        add_decision_state(conn)
    if version < 13:
        add_chat_confirmations(conn)
    if version < 14:
        add_task_positions(conn)
    if version < 15:
        add_waiting_status(conn)
    if version < 16:
        add_proposal_targets(conn)
    if version < 17:
        execute_schema(conn, AGENT_LINKS_SCHEMA)
    if version < 18:
        add_registration_directories(conn)
    if version < 19:
        execute_schema(conn, RELEASE_SCHEMA)
    if version < 20:
        execute_schema(conn, REMINDER_SCHEMA)
    if version < 21:
        add_discussion_tags(conn)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _upgrade_v1(conn: sqlite3.Connection, tables: set[str]) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(work_items)")}
    if "roadmap_id" not in columns:
        conn.execute("ALTER TABLE work_items ADD COLUMN roadmap_id TEXT REFERENCES roadmaps(id)")
        conn.execute("UPDATE work_items SET roadmap_id=(SELECT roadmap_id FROM steps WHERE steps.id=work_items.step_id) WHERE step_id IS NOT NULL AND roadmap_id IS NULL")
    if not {"people", "person_links"} <= tables:
        execute_schema(conn, PEOPLE_SCHEMA)
    if not {"web_links", "agent_chats", "github_refs"} <= tables:
        execute_schema(conn, LINKS_CHATS_GITHUB_SCHEMA)
    chat_columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_chats)")}
    if "provider" not in chat_columns:
        for column in AGENT_CHAT_COLUMNS.split(", ")[1:]:
            conn.execute("ALTER TABLE agent_chats ADD COLUMN " + column)
    if "chat_steps" not in tables:
        execute_schema(conn, CHAT_STEPS_SCHEMA)
    if "parent_id" not in {row[1] for row in conn.execute("PRAGMA table_info(roadmaps)")}:
        conn.execute("ALTER TABLE roadmaps ADD COLUMN parent_id TEXT REFERENCES roadmaps(id)")
    if not {"roadmap_questions", "roadmap_decisions"} <= tables:
        execute_schema(conn, QUESTIONS_DECISIONS_SCHEMA)
    add_discussions(conn)
    execute_schema(conn, COORDINATION_SCHEMA)


def invalidate_restored_presence(conn: sqlite3.Connection, stamp: str) -> None:
    """Retain reports while making restored runs, participation and their deliveries visibly ended."""
    from .chat_messages import expire_pending

    conn.execute("UPDATE participations SET released_at=?,release_reason='restore',updated_at=?,revision=revision+1 WHERE released_at IS NULL", (stamp, stamp))
    conn.execute("UPDATE agent_runs SET ended_at=?,end_reason='restore',updated_at=?,revision=revision+1 WHERE ended_at IS NULL", (stamp, stamp))
    expire_pending(conn, "restore", stamp=stamp)
