"""Shared, transaction-owned upgrades for startup and offline restoration."""

from __future__ import annotations

import json
import sqlite3

SCHEMA_VERSION = 13
# Older versions that startup and offline restore upgrade in one transaction.
UPGRADABLE_VERSIONS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12})

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

# v6 keys repositories by remote rather than by address text. COORDINATION_SCHEMA keeps the
# v2-v5 shape and every database, new ones included, reaches v6 through add_remote_identity.
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
    """File notes in search as WorkService.index does, so search finds merged text and the archived rest.

    search_index cannot look up entity_id directly, so every delete reads the whole table:
    one delete per batch rather than per note keeps a large upgrade from stalling startup.
    """
    identities = [note[0] for note in notes]
    for start in range(0, len(identities), 500):
        batch = identities[start:start + 500]
        conn.execute(f"DELETE FROM search_index WHERE entity_id IN ({','.join('?' for _ in batch)})", batch)
    conn.executemany("INSERT INTO search_index(entity_id,type,title,body,owner_id,archived) VALUES(?,'note',?,?,?,?)", notes)


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
        conn.execute("INSERT OR IGNORE INTO proposal_stale_notices VALUES(?,?,?,?)",
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


def execute_schema(conn: sqlite3.Connection, schema: str) -> None:
    """Unlike executescript, preserve the caller's transaction and rollback boundary."""
    for statement in filter(str.strip, schema.split(";")):
        conn.execute(statement)


def upgrade(conn: sqlite3.Connection) -> None:
    """Upgrade an accepted older version to the current shape atomically inside the caller's transaction.

    Credential IDs, proposal authors, receipts (beyond the date v11 gives them), and legacy
    chat repository labels are deliberately unchanged. No repository names are guessed or imported.
    The one record v13 changes is a chat message still waiting for an identity that isn't bound to
    the chat: that delivery ends, with the reason 'unconfirmed'.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version not in UPGRADABLE_VERSIONS:
        raise ValueError(f"Unsupported database schema version {version}")
    from .discussions import AGENT_AVATAR_COLUMN, assign_missing_avatars

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # v2, and v1 databases that already hold discussions, predate avatars.
    if "communication_agents" in tables and "avatar" not in {row[1] for row in conn.execute("PRAGMA table_info(communication_agents)")}:
        conn.execute("ALTER TABLE communication_agents ADD COLUMN " + AGENT_AVATAR_COLUMN.removeprefix(", "))
    if version == 1:
        _upgrade_v1(conn, tables)
    assign_missing_avatars(conn)
    from .chat_messages import SCHEMA as CHAT_MESSAGE_SCHEMA
    from .resources import SCHEMA as RESOURCE_SCHEMA
    from .tickets import SCHEMA as TICKET_SCHEMA

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
    # Every upgradable version predates the chat bindings of v13.
    add_chat_confirmations(conn)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _upgrade_v1(conn: sqlite3.Connection, tables: set[str]) -> None:
    from .db import (
        AGENT_CHAT_COLUMNS,
        CHAT_STEPS_SCHEMA,
        LINKS_CHATS_GITHUB_SCHEMA,
        PEOPLE_SCHEMA,
        QUESTIONS_DECISIONS_SCHEMA,
    )
    from .discussions import migrate as migrate_discussions

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
    migrate_discussions(conn)
    execute_schema(conn, COORDINATION_SCHEMA)


def invalidate_restored_presence(conn: sqlite3.Connection, stamp: str) -> None:
    """Retain reports while making restored runs, participation and their deliveries visibly ended."""
    from .chat_messages import expire_pending

    conn.execute("UPDATE participations SET released_at=?,release_reason='restore',updated_at=?,revision=revision+1 WHERE released_at IS NULL", (stamp, stamp))
    conn.execute("UPDATE agent_runs SET ended_at=?,end_reason='restore',updated_at=?,revision=revision+1 WHERE ended_at IS NULL", (stamp, stamp))
    expire_pending(conn, "restore", stamp=stamp)
