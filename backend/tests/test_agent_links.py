"""Schema v17 added agent ties, which v29 drops again (step 0.6 of docs/ROADMAPS-RETHINK.md). What stays:
the upgrade from v16, the way back to it, which older upgrade tests build their databases with, a v16
backup's restore, and the history a tie wrote, which an agent reads without learning of a draft
proposal it may not see."""

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
STAMP = "2026-11-07T00:00:00+00:00"
# From version 17 to version 16, with Worklog stopped and after a manual backup. The README gave this way back
# while version 17 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
BEGIN;
DROP TABLE agent_links;
PRAGMA user_version=16;
COMMIT;
"""


def to_v17(path):
    """Take a current database back to the v17 shape the documented way."""
    from test_registration_directory import to_v17 as from_v18

    from_v18(path)


def to_v16(path):
    """Take a current database back to the v16 shape the documented way: to version 17 first."""
    to_v17(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


@contextmanager
def person(directory):
    app = create_app(directory, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert response.status_code == 200, response.text
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


@pytest.fixture
def client(tmp_path):
    with person(tmp_path) as c:
        yield c


def write(c, method, path, payload=None, agent=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def read(c, path, agent=None):
    return ok(c.get("/api/v1" + path, headers={"Authorization": "Bearer " + agent["token"]} if agent else {}))


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text
    return response.json()["error"]


def credential(c, label="Builder"):
    return ok(write(c, "POST", "/credentials", {"label": label}))


def item(c, title="Fix the parser"):
    return ok(write(c, "POST", "/work-items", {"title": title}))


def tied(path, agent_id, target_type, target_id, relationship, at=STAMP, kind="agent.link"):
    """The history a tie wrote on its agent, as ties wrote it before v29 dropped them: a tie set in
    `after`, a tie removed in `before`."""
    state = json.dumps({"targetType": target_type, "targetId": target_id, "relationship": relationship, "setBy": "human"})
    before, after = ("null", state) if kind == "agent.link" else (state, "null")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO events(id,entity_id,kind,actor_id,actor_label,actor_kind,created_at,local_date,before_json,"
                     "after_json) VALUES(?,?,?,'human','You','human',?,?,?,?)",
                     (str(uuid.uuid4()), agent_id, kind, at, at[:10], before, after))
        conn.commit()


def test_an_agents_history_page_fills_up_past_entries_it_may_not_see(client, tmp_path):
    # A tie to a draft proposal wrote its history on the agent too: another agent reading that history
    # must not learn of the draft, from the tie or from its removal, and the page still fills up to its
    # limit.
    author, other = credential(client, "Author"), credential(client, "Other")
    operations = [{"action": "item.create", "payload": {"title": "Later"}}]
    draft = ok(write(client, "POST", "/proposals", {"title": "Secret plan", "rationale": "", "operations": operations}, author))
    work = item(client)
    # The latest entries, in the order written: after the agent's own.
    now = datetime.now(UTC)
    for minute, (kind, target_type, target_id, relationship) in enumerate((
            ("agent.link", "item", work["id"], "One"), ("agent.link", "proposal", draft["id"], "Hidden"),
            ("agent.link", "item", work["id"], "Three"), ("agent.unlink", "proposal", draft["id"], "Hidden")), start=1):
        tied(tmp_path / "worklog.sqlite3", author["agentId"], target_type, target_id, relationship,
             (now + timedelta(minutes=minute)).isoformat(), kind)

    def page(agent=None):
        entries = read(client, f"/history?entityId={author['agentId']}&limit=2", agent)["items"]
        return [(e["kind"], (e["after"] or e["before"])["relationship"]) for e in entries]

    assert page(other) == [("agent.link", "Three"), ("agent.link", "One")]
    assert page() == [("agent.unlink", "Hidden"), ("agent.link", "Three")]
    # Once submitted, the draft is no longer hidden.
    ok(write(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, author))
    assert page(other) == [("agent.unlink", "Hidden"), ("agent.link", "Three")]


def test_search_narrows_to_the_kinds_of_record_asked_for(client):
    item(client, "Parser release")
    ok(write(client, "POST", "/roadmaps", {"title": "Parser roadmap", "steps": [{"title": "Parser step"}]}))
    assert {r["type"] for r in read(client, "/search?q=parser")["items"]} == {"item", "roadmap", "step"}
    steps = read(client, "/search?q=parser&type=step")
    assert ([r["type"] for r in steps["items"]], steps["total"]) == (["step"], 1)
    assert {r["type"] for r in read(client, "/search?q=parser&type=step&type=item")["items"]} == {"step", "item"}
    error(client.get("/api/v1/search?q=parser&type=agent"), 422, "validation")


def test_the_upgrade_from_v16_snapshots_first_and_changes_no_record(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        item(c, "Kept as it was")
    to_v16(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        kept = conn.execute("SELECT * FROM work_items").fetchall()
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        # v17 adds the ties table and v29 drops it again.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='agent_links'").fetchone()[0] == 0
        assert conn.execute("SELECT * FROM work_items").fetchall() == kept
    snapshot, = (tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16


def test_the_way_back_to_v16_gives_its_exact_shape_and_upgrades_again(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        agent, work = credential(c)["agentId"], item(c)["id"]
    to_v17(path)
    # A tie as v17 kept it, and its history, which stays with the agent.
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO agent_links(id,agent_id,target_type,target_id,relationship,set_by,created_at) "
                     "VALUES(?,?,'item',?,'Implementer','human',?)", (str(uuid.uuid4()), agent, work, STAMP))
        conn.commit()
    tied(path, agent, "item", work, "Implementer")
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
        assert recovery._schema_signature(conn) == sealed.shape(16)
        assert conn.execute("SELECT count(*) FROM events WHERE kind='agent.link'").fetchone()[0] == 1
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()


def test_restore_takes_a_v16_backup(tmp_path):
    older, target = tmp_path / "older" / "worklog.sqlite3", tmp_path / "target" / "worklog.sqlite3"
    with person(older.parent) as c:
        item(c, "From before ties")
    to_v16(older)
    Database(target).initialize()
    recovery.restore_database(target, older, tmp_path / "restore-older")
    with closing(sqlite3.connect(target)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert [row[0] for row in conn.execute("SELECT title FROM work_items")] == ["From before ties"]
