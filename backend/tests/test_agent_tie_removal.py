"""Schema v29 removes agent ties (decision R1 in docs/ROADMAPS-RETHINK.md, step 0.6): their routes,
the agent's `ties` commands, the context's and the overlap check's ties, and the tie sections are
gone. The upgrade drops the agent_links table, every tie with it, and changes no other record: the
history a tie wrote stays. The pre-migration backup keeps the table, and the documented way back
gives v28's exact shape."""

import io
import json
import sqlite3
import uuid
import zipfile
from contextlib import closing

import pytest
import sealed
from test_chat_label_links import call, session
from test_sealed_schemas import contents
from worklog import cli, migrations, recovery
from worklog.db import Database
from worklog.doctor import doctor_database

# From version 29 to version 28, with Worklog stopped and after a manual backup: the table and its
# index as version 28 had them. docs/UPGRADING.md gave this way back while version 29 was the latest;
# older upgrade tests now build their databases with it.
WAY_BACK = """\
CREATE TABLE agent_links(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL REFERENCES communication_agents(id),target_type TEXT NOT NULL CHECK(target_type IN('item','roadmap','step','repository','person','resource','discussion','proposal','ticket','agent','question','decision')),target_id TEXT NOT NULL,relationship TEXT NOT NULL DEFAULT '',set_by TEXT NOT NULL CHECK(set_by IN('human','agent')),created_at TEXT NOT NULL,UNIQUE(agent_id,target_type,target_id));
CREATE INDEX agent_links_target ON agent_links(target_type,target_id);
PRAGMA user_version=28;
"""
STAMP = "2026-11-07T00:00:00+00:00"


def to_v28(path):
    """Take a current database back to v28 the documented way: to version 29 first."""
    from test_chat_message_removal import to_v29

    to_v29(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def worked(data):
    """A current database with an agent, a work item and a roadmap with a step, and their IDs."""
    client = session(data)
    try:
        agent = call(client, "POST", "/credentials", {"label": "Builder"})["agentId"]
        item = call(client, "POST", "/work-items", {"title": "Fix the parser"})
        roadmap = call(client, "POST", "/roadmaps", {"title": "Parser", "steps": [{"title": "Cut the branch"}]})
    finally:
        client.__exit__(None, None, None)
    ids = {"agent": agent, "item": item["id"], "roadmap": roadmap["id"], "step": roadmap["steps"][0]["id"]}
    return data / "worklog.sqlite3", ids


def tie(conn, agent, target_type, target_id, relationship, set_by):
    """A tie as v28 kept it, and the history it wrote on its agent, which stays."""
    conn.execute("INSERT INTO agent_links(id,agent_id,target_type,target_id,relationship,set_by,created_at) "
                 "VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), agent, target_type, target_id, relationship, set_by, STAMP))
    conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,before_json,"
                 "after_json) VALUES(?,?,'agent.link','human','You','human',?,'2026-11-07','null',?)",
                 (str(uuid.uuid4()), agent, STAMP, json.dumps({"targetType": target_type, "targetId": target_id,
                                                                 "relationship": relationship, "setBy": set_by})))


def test_the_upgrade_drops_every_tie_and_changes_no_other_record(tmp_path):
    database, ids = worked(tmp_path / "data")
    to_v28(database)
    with closing(sqlite3.connect(database)) as conn:
        tie(conn, ids["agent"], "step", ids["step"], "Implementer", "agent")
        tie(conn, ids["agent"], "item", ids["item"], "Reviewer", "human")
        conn.commit()
    recovery.validate_database(database)
    _, _, before = contents(database)
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    assert "agent_links" not in after and len(before["agent_links"]) == 2
    # Every other table keeps every row: the agent, the records it was tied to, and the ties' history.
    # v30, which the upgrade also runs, drops the chat message and confirmation tables, empty here.
    dropped = ("agent_links", "chat_messages", "chat_message_deliveries", "chat_confirmations")
    assert [before[table] for table in dropped[1:]] == [[], [], []]
    # v31 gives each step an outcome, empty.
    assert after["steps"] == [(*row, "") for row in before["steps"]]
    after["steps"] = before["steps"]
    assert after =={table: rows for table, rows in before.items() if table not in dropped}
    assert sorted(row[2] for row in after["events"] if row[2] == "agent.link") == ["agent.link", "agent.link"]
    # Nothing is left for the doctor to find, and an export holds no ties.
    assert doctor_database(database)["ok"] is True
    assert "records/agent_links.json" not in zipfile.ZipFile(io.BytesIO(recovery.export_database(database))).namelist()
    snapshot, = (tmp_path / "data" / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 28
        assert conn.execute("SELECT count(*) FROM agent_links").fetchone()[0] == 2


def test_the_way_back_gives_v28s_exact_shape_and_upgrades_again(tmp_path):
    database, _ = worked(tmp_path / "data")
    to_v28(database)
    recovery.validate_database(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 28
        assert recovery._schema_signature(conn) == sealed.shape(28)
    Database(database).initialize()
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()


def test_the_routes_are_gone_and_no_read_lists_ties(tmp_path):
    _, ids = worked(tmp_path / "data")
    client = session(tmp_path / "data")
    try:
        agent = {"Authorization": "Bearer " + call(client, "POST", "/credentials", {"label": "Reader"})["token"]}
        tie = {"agentId": ids["agent"], "targetType": "item", "targetId": ids["item"]}
        for method, path, body in (("GET", f"/agent-links?agentId={ids['agent']}", None), ("POST", "/agent-links", tie),
                                   ("POST", "/agent-links/unlink", tie)):
            # No route serves them. An agent's read finds nothing either, and its write is outside what an
            # agent may call, as for any path no route serves.
            for headers, status in (({}, 404), (agent, 404 if method == "GET" else 403)):
                response = client.request(method, "/api/v1" + path, json=body,
                                          headers={**headers, "Idempotency-Key": str(uuid.uuid4())})
                assert response.status_code == status, (method, path, headers, response.text)
                if status == 403:
                    assert response.json()["error"]["code"] == "agent_scope"
        # Neither the context nor the overlap check lists ties.
        assert "ties" not in call(client, "GET", "/context", headers=agent)
        assert "ties" not in call(client, "GET", f"/coordination-overlaps?itemId={ids['item']}", headers=agent)
    finally:
        client.__exit__(None, None, None)


def test_the_agent_command_line_has_no_ties(capsys):
    with pytest.raises(SystemExit):
        cli.main(["agent", "ties", "list"])
    assert "invalid choice: 'ties'" in capsys.readouterr().err
    client = cli.AgentClient("http://127.0.0.1:8893", "token")
    for method, route in (("GET", "/agent-links"), ("POST", "/agent-links"), ("POST", "/agent-links/unlink")):
        with pytest.raises(ValueError, match="outside proposal/read scope"):
            client.request(method, route, {})
