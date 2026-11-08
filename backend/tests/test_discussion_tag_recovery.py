"""Schema v22 widens discussion tags, adds pins and opens each repository's discussion: the upgrade,
the documented way back, restore and export keep every discussion whole, and an upgrade after the way
back finds each repository's discussion again rather than opening another."""

import io
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
import sealed
from test_sealed_schemas import contents
from worklog import coordination, migrations, recovery
from worklog.db import Database
from worklog.discussions import PERSON, REPOSITORY_TEXT, REPOSITORY_TITLE, DiscussionService

T1, T2 = "2026-10-01T10:00:00+00:00", "2026-10-02T10:00:00+00:00"
# README: from version 22 to version 21, with Worklog stopped and after a manual backup.
WAY_BACK = """\
BEGIN;
ALTER TABLE discussion_contexts RENAME TO discussion_contexts_v22;
CREATE TABLE discussion_contexts(discussion_id TEXT NOT NULL REFERENCES discussions(id),context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step')),context_id TEXT NOT NULL,PRIMARY KEY(discussion_id,context_type,context_id));
INSERT INTO discussion_contexts(discussion_id,context_type,context_id) SELECT discussion_id,context_type,context_id FROM discussion_contexts_v22 WHERE context_type IN ('item','roadmap','step');
DROP TABLE discussion_contexts_v22;
CREATE INDEX discussion_contexts_context ON discussion_contexts(context_type,context_id,discussion_id);
ALTER TABLE discussion_change_contexts RENAME TO discussion_change_contexts_v22;
CREATE TABLE discussion_change_contexts(seq INTEGER NOT NULL REFERENCES discussion_changes(seq),context_type TEXT NOT NULL CHECK(context_type IN('item','roadmap','step')),context_id TEXT NOT NULL,PRIMARY KEY(seq,context_type,context_id));
INSERT INTO discussion_change_contexts(seq,context_type,context_id) SELECT seq,context_type,context_id FROM discussion_change_contexts_v22 WHERE context_type IN ('item','roadmap','step');
DROP TABLE discussion_change_contexts_v22;
CREATE INDEX discussion_change_contexts_context ON discussion_change_contexts(context_type,context_id,seq);
ALTER TABLE discussions DROP COLUMN pinned_at;
PRAGMA user_version=21;
COMMIT;
"""
# The tables v22 adds to or reshapes; it leaves every other table as it was.
TOUCHED = {"discussions", "discussion_contexts", "discussion_messages", "discussion_changes", "discussion_change_contexts",
           "events", "sqlite_sequence"}
REPOSITORIES = [
    # id, name, archived, duplicate_of, merged_into
    ("repo-a", "worklog", 0, None, None),
    ("repo-b", "archived-repo", 1, None, None),
    ("repo-c", "old-worklog", 1, None, "repo-a"),
    ("repo-d", "worklog-fork", 0, "repo-a", None),
    ("repo-e1", "twin", 0, None, None),
    ("repo-e2", "twin", 0, None, None),
]
OPENED = ["repo-a", "repo-d", "repo-e1", "repo-e2"]  # active and not merged, in the order they were created


def to_v21(path):
    """Take a current database back to the v21 shape the documented way: to version 22 first."""
    from test_leaf_constraints import to_v22

    to_v22(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def v21_database(path):
    """A v21 database in its sealed shape, with repositories of every state, and discussions with work
    contexts, repository labels and a feed, written as v21 wrote them."""
    sealed.build(path, 21)
    with closing(sqlite3.connect(path)) as conn:
        for position, (identity, name, archived, duplicate, merged) in enumerate(REPOSITORIES):
            created = f"2026-09-0{position + 1}T00:00:00+00:00"
            conn.execute("INSERT INTO repositories(id,name,url,description,archived,revision,created_at,updated_at,duplicate_of,merged_into) "
                         "VALUES(?,?,'','',?,1,?,?,?,?)", (identity, name, archived, created, created, duplicate, merged))
        conn.execute("INSERT INTO discussions(id,title,archived,revision,created_at,updated_at) VALUES('talk-1','Parser',0,2,?,?)", (T1, T1))
        conn.execute("INSERT INTO discussions(id,title,archived,revision,created_at,updated_at) VALUES('talk-2','Old notes',1,1,?,?)", (T2, T2))
        for message, discussion, author, kind, stamp in (("m-1", "talk-1", "agent-1", "agent", T1), ("m-2", "talk-1", "local-user", "human", T2),
                                                          ("m-3", "talk-2", "local-user", "human", T2)):
            conn.execute("INSERT INTO discussion_messages(id,discussion_id,text,author_id,author_label,author_kind,created_at) VALUES(?,?,?,?,?,?,?)",
                         (message, discussion, f"Message {message}", author, "Builder" if kind == "agent" else "You", kind, stamp))
        conn.execute("INSERT INTO discussion_contexts(discussion_id,context_type,context_id) VALUES('talk-1','item','item-1')")
        for discussion, label in (("talk-1", "worklog"), ("talk-1", "twin"), ("talk-1", "old-worklog"), ("talk-1", "nowhere"),
                                  ("talk-2", "archived-repo")):
            conn.execute("INSERT INTO discussion_repos(discussion_id,repo) VALUES(?,?)", (discussion, label))
        conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,before_json,after_json) "
                     "VALUES('e-1','m-1','discussion.create','agent-1','Builder','agent',?,'2026-10-01','null','{}')", (T1,))
        conn.execute("INSERT INTO discussion_changes(event_id,kind,discussion_id,message_id,created_at) VALUES('e-1','discussion.create','talk-1','m-1',?)", (T1,))
        conn.execute("INSERT INTO discussion_change_contexts(seq,context_type,context_id) VALUES(1,'item','item-1')")
        conn.commit()


def rows(path, sql, args=()):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql, args).fetchall()


def homes(path):
    return dict(rows(path, "SELECT context_id,discussion_id FROM discussion_contexts WHERE home=1"))


def test_the_v22_upgrade_keeps_every_tag_links_exact_labels_and_opens_each_active_repositorys_discussion(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    v21_database(path)
    recovery.validate_database(path)
    _, _, before = contents(path)
    Database(path).initialize()
    version, signature, after = contents(path)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    # Every table v22 doesn't touch is as it was, the search index's aside, which gains the new threads.
    untouched = [t for t in before if t not in TOUCHED and not t.startswith("search_")]
    assert {t: after[t] for t in untouched} == {t: before[t] for t in untouched}
    # Earlier threads keep their rows, with no pin, and every message stays.
    assert rows(path, "SELECT id,title,archived,revision,pinned_at FROM discussions WHERE id LIKE 'talk-%' ORDER BY id") == [
        ("talk-1", "Parser", 0, 2, None), ("talk-2", "Old notes", 1, 1, None)]
    assert set(before["discussion_messages"]) <= set(after["discussion_messages"])
    assert set(before["events"]) <= set(after["events"]) and set(before["discussion_changes"]) <= set(after["discussion_changes"])
    # A work context keeps its row, added by whoever wrote the thread's first message; a label names a
    # repository only when one unmerged repository has exactly that name, an archived one included.
    assert rows(path, "SELECT * FROM discussion_contexts WHERE discussion_id LIKE 'talk-%' ORDER BY discussion_id,context_type,context_id") == [
        ("talk-1", "item", "item-1", 0, "agent-1", "agent", T1), ("talk-1", "repository", "repo-a", 0, "agent-1", "agent", T1),
        ("talk-2", "repository", "repo-b", 0, "local-user", "human", T2)]
    assert sorted(after["discussion_repos"]) == sorted(before["discussion_repos"])
    assert set(before["discussion_change_contexts"]) <= set(after["discussion_change_contexts"])
    # The active, unmerged repositories each get their own discussion, opened as the person.
    opened = homes(path)
    assert set(opened) == set(OPENED)
    names = {identity: name for identity, name, *_ in REPOSITORIES}
    for repository, discussion in opened.items():
        assert rows(path, "SELECT title,archived,pinned_at FROM discussions WHERE id=?", (discussion,)) == [
            (REPOSITORY_TITLE.format(name=names[repository]), 0, None)]
        assert rows(path, "SELECT text,author_id,author_label,author_kind FROM discussion_messages WHERE discussion_id=?", (discussion,)) == [
            (REPOSITORY_TEXT.format(name=names[repository], id=repository), "local-user", "You", "human")]
        assert rows(path, "SELECT c.kind,x.context_type,x.context_id FROM discussion_changes c JOIN discussion_change_contexts x ON x.seq=c.seq "
                          "WHERE c.discussion_id=?", (discussion,)) == [("discussion.create", "repository", repository)]
        assert sorted(r[0] for r in rows(path, "SELECT type FROM search_keys WHERE owner_id=?", (discussion,))) == ["discussion", "discussion_message"]
    assert rows(path, "SELECT count(*) FROM search_index WHERE search_index MATCH '\"Repository\" AND \"twin\"'") == [(4,)]
    snapshots = list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 21
    recovery.validate_database(path)


def test_the_documented_way_back_to_v21_gives_its_exact_shape_and_an_upgrade_finds_each_discussion_again(tmp_path):
    # The SQL tested here is the SQL the README gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    path = tmp_path / "worklog.sqlite3"
    v21_database(path)
    Database(path).initialize()
    opened = homes(path)
    with Database(path).transaction() as conn:
        DiscussionService(None).pin(conn, PERSON, "talk-1", {"revision": 2, "pinned": True})
    threads = rows(path, "SELECT id FROM discussions ORDER BY id")
    to_v21(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 21
        assert recovery._schema_signature(conn) == sealed.shape(21)
        # Every thread and message stays; only the work contexts stay tagged, and the pin goes.
        assert conn.execute("SELECT id FROM discussions ORDER BY id").fetchall() == threads
        assert conn.execute("SELECT * FROM discussion_contexts").fetchall() == [("talk-1", "item", "item-1")]
        assert conn.execute("SELECT DISTINCT context_type FROM discussion_change_contexts").fetchall() == [("item",)]
    Database(path).initialize()
    assert rows(path, "SELECT id FROM discussions ORDER BY id") == threads
    assert homes(path) == opened
    assert rows(path, "SELECT context_id FROM discussion_contexts WHERE discussion_id='talk-1' AND context_type='repository'") == [("repo-a",)]
    recovery.validate_database(path)


def test_after_the_way_back_a_repository_archived_since_gets_its_own_discussion_back(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    v21_database(path)
    Database(path).initialize()
    opened = homes(path)
    with Database(path).transaction() as conn:
        coordination.repository_apply(conn, PERSON, "repository.archive", {"id": "repo-a", "revision": 1})
    to_v21(path)
    Database(path).initialize()
    # An archived repository is found again too, so recovering it opens no second discussion.
    assert homes(path) == opened
    with Database(path).transaction() as conn:
        recovered = coordination.repository_apply(conn, PERSON, "repository.recover", {"id": "repo-a", "revision": 2})
    assert recovered["discussionId"] == opened["repo-a"]
    assert rows(path, "SELECT count(*) FROM discussions WHERE title='Repository: worklog'") == [(1,)]


def test_the_upgrade_takes_no_thread_it_did_not_open_and_none_already_a_repositorys_own(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    v21_database(path)
    with closing(sqlite3.connect(path)) as conn:
        # The person's own thread about repo-a, the older, and one that begins as v22 opens one but
        # names two repositories.
        for discussion, text, stamp in (("lookalike", "Notes on (`repo-a`): the build is flaky", T1),
                                        ("both", migrations.REPOSITORY_DISCUSSION_OPENING + "worklog** (`repo-a`) (`repo-d`).", T2)):
            conn.execute("INSERT INTO discussions(id,title,archived,revision,created_at,updated_at) VALUES(?,'Repository: worklog',0,1,?,?)",
                         (discussion, stamp, stamp))
            conn.execute("INSERT INTO discussion_messages(id,discussion_id,text,author_id,author_label,author_kind,created_at) "
                         "VALUES(?,?,?,'local-user','You','human',?)", ("m-" + discussion, discussion, text, stamp))
        conn.commit()
    Database(path).initialize()
    opened = homes(path)
    assert opened["repo-a"] == "both"
    assert opened["repo-d"] not in {"both", "lookalike"} and "lookalike" not in opened.values()
    recovery.validate_database(path)


def test_restore_and_export_keep_tags_pins_and_each_repositorys_discussion(tmp_path):
    source, target = tmp_path / "source" / "worklog.sqlite3", tmp_path / "target" / "worklog.sqlite3"
    Database(source).initialize()
    service = DiscussionService(None)
    with Database(source).transaction() as conn:
        conn.execute("INSERT INTO repositories(id,name,created_at,updated_at) VALUES('repo','worklog',?,?)", (T1, T1))
        home = service.open_repository_discussion(conn, "repo", "worklog")
        thread = service.create(conn, PERSON, {"title": "Plan", "text": "Who takes the parser?",
                                               "contexts": [{"type": "repository", "id": "repo"}]})["discussion"]
        service.pin(conn, PERSON, thread["id"], {"revision": thread["revision"], "pinned": True})
    Database(target).initialize()
    recovery.restore_database(target, source, tmp_path / "restore-backups")
    assert homes(target) == {"repo": home["id"]}
    assert rows(target, "SELECT pinned_at IS NOT NULL FROM discussions WHERE id=?", (thread["id"],)) == [(1,)]
    archive = zipfile.ZipFile(io.BytesIO(recovery.export_database(target)))
    exported = json.loads(archive.read("records/discussion_contexts.json"))
    assert sorted((r["discussion_id"], r["home"], r["added_by"]) for r in exported) == sorted(
        [(home["id"], 1, "local-user"), (thread["id"], 0, "local-user")])


def test_a_v21_shape_cannot_claim_version_22_and_a_v22_shape_cannot_claim_21(tmp_path):
    older, current = tmp_path / "older.sqlite3", tmp_path / "current.sqlite3"
    Database(older).initialize()
    to_v21(older)
    recovery.validate_database(older)
    Database(current).initialize()
    recovery.validate_database(current)
    for path, claimed in ((older, 22), (current, 21)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={claimed}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
