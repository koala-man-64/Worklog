"""The v36 push schema adds no rows and preserves every pre-existing v35 record."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import sealed
from test_sealed_schemas import contents
from worklog.db import Database
from worklog.migrations import SCHEMA_VERSION

V36_TABLES = ("push_enrollments", "push_subscriptions", "push_deliveries")


def test_v35_upgrade_preserves_existing_records_and_does_not_backfill_push_state(tmp_path):
    path = tmp_path / "v35.sqlite3"
    sealed.build(path, 35)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) "
            "VALUES ('item','Existing work',0,'2026-01-01','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO credentials(id,label,token_hash,created_at) VALUES ('credential','Agent','hash','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO communication_agents(id,label,created_at,avatar) "
            "VALUES ('agent','Agent','2026-01-01','red-visor-antenna')"
        )
        conn.execute(
            "INSERT INTO communication_credentials(credential_id,agent_id,registered_by_label,chat_id,directory) "
            "VALUES ('credential','agent','Agent','chat-1','C:/work')"
        )
        conn.execute(
            "INSERT INTO agent_chats(id,owner_type,owner_id,chat_id,title,created_date,"
            "created_at,updated_at,provider,model,repos) "
            "VALUES ('chat-record','item','item','chat-1','Existing chat',"
            "'2026-01-01','2026-01-01','2026-01-01','codex','model','[]')"
        )
        conn.execute(
            "INSERT INTO agent_runs(id,agent_id,credential_id,client_run_key,display_name,"
            "provider,external_chat_id,created_at,updated_at) "
            "VALUES ('run','agent','credential','run-key','Existing run','codex','chat-1','2026-01-01','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,"
            "created_at,local_date,before_json,after_json) "
            "VALUES ('event','item','work_item.created','agent','Agent','agent','2026-01-01','2026-01-01','{}','{}')"
        )
        conn.execute(
            "INSERT INTO discussions(id,title,created_at,updated_at) "
            "VALUES ('discussion','Existing discussion','2026-01-01','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO discussion_messages(id,discussion_id,text,author_id,author_label,author_kind,created_at) "
            "VALUES ('message','discussion','Existing message','agent','Agent','agent','2026-01-01')"
        )
        conn.commit()

    before_version, before_shape, before_rows = contents(path)
    assert before_version == 35
    assert before_shape == sealed.shape(35)

    Database(path).initialize()

    after_version, after_shape, after_rows = contents(path)
    assert SCHEMA_VERSION == after_version == 36
    assert after_shape == sealed.current()
    for table, rows in before_rows.items():
        if table == "discussion_messages":
            assert [row[:-1] for row in after_rows[table]] == rows
            assert [row[-1] for row in after_rows[table]] == [None] * len(rows)
        else:
            assert after_rows[table] == rows, table
    for table in V36_TABLES:
        assert after_rows[table] == []
