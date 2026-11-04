"""Shared, transaction-owned upgrades for startup and offline restoration."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 8
# Older versions that startup and offline restore upgrade in one transaction.
UPGRADABLE_VERSIONS = frozenset({1, 2, 3, 4, 5, 6, 7})

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


def execute_schema(conn: sqlite3.Connection, schema: str) -> None:
    """Unlike executescript, preserve the caller's transaction and rollback boundary."""
    for statement in filter(str.strip, schema.split(";")):
        conn.execute(statement)


def upgrade(conn: sqlite3.Connection) -> None:
    """Upgrade an accepted older version to the current shape atomically inside the caller's transaction.

    Credential IDs, proposal authors, receipts, and legacy chat repository labels
    are deliberately unchanged. No repository names are guessed or imported.
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
    from .tickets import SCHEMA as TICKET_SCHEMA

    if version < 4:
        execute_schema(conn, CHAT_MESSAGE_SCHEMA)
    if version < 5:
        execute_schema(conn, TICKET_SCHEMA)
    if version < 6:
        add_remote_identity(conn)
    if version < 7:
        add_repository_provenance(conn)
    # Every upgradable version predates item scratchpads (v8).
    add_item_scratchpads(conn)
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
