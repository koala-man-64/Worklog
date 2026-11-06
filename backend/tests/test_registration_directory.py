"""Agents register from a directory, and Review names it beside their proposals (schema v18).

`worklog agent register` sends the directory it runs in. Worklog keeps it on the registration's
row and in its credential.register event, and every proposal the identity makes, under any of its
credentials, carries it as actorDirectory. Upgrade, the documented way back and restore are here too.
"""

import json
import secrets
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
DIRECTORY = r"C:\Users\person\Projects\parser\.claude\worktrees\fix-lexer"
# README: from version 18 to version 17, with Worklog stopped and after a manual backup.
WAY_BACK = """\
BEGIN;
ALTER TABLE communication_credentials DROP COLUMN directory;
PRAGMA user_version=17;
COMMIT;
"""


def to_v17(path):
    """Take a current database back to the v17 shape the documented way: to version 18 first."""
    from test_release_recovery import to_v18

    to_v18(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


@contextmanager
def person(directory):
    """The person's session, and beside it what the command line sends: no origin, no cookie."""
    app = create_app(directory, testing=True)
    with TestClient(app, base_url=ORIGIN) as c, TestClient(app, base_url=ORIGIN) as agent:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert response.status_code == 200, response.text
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c, agent


@pytest.fixture
def clients(tmp_path):
    with person(tmp_path) as both:
        yield both


def write(c, method, path, payload=None, token=None):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **({"Authorization": "Bearer " + token} if token else {})}
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def issue(c, label="Shared agents"):
    return ok(write(c, "POST", "/credentials", {"label": label}))


def register(agent, sponsor_token, directory=DIRECTORY, label="Parser fix", token=None):
    """Register as the command line does; a directory of None sends none, as older ones did."""
    token = token or secrets.token_urlsafe(32)
    payload = {"label": label, "token": token, "chatId": str(uuid.uuid5(uuid.NAMESPACE_URL, token)),
               **({} if directory is None else {"directory": directory})}
    return write(agent, "POST", "/agent-registrations", payload, sponsor_token), token


def registered(agent, sponsor_token, **options):
    """A registered identity, with the token it acts with."""
    response, token = register(agent, sponsor_token, **options)
    return {**ok(response), "token": token}


def identities(c, agent):
    """A credential issued in Settings, an identity registered from DIRECTORY and one that sent none."""
    sponsor = issue(c)
    return sponsor, registered(agent, sponsor["token"]), registered(agent, sponsor["token"], directory=None, label="Older agent")


def propose(c, title, token=None):
    """A proposal for one new work item, submitted when an agent's token wrote it."""
    drafted = ok(write(c, "POST", "/proposals", {"title": title, "rationale": "", "operations": [
        {"action": "item.create", "payload": {"title": title}}]}, token))
    if token is None:
        return drafted
    return ok(write(c, "POST", f"/proposals/{drafted['id']}/submit", {"revision": drafted["revision"]}, token))


def directories(path):
    with closing(sqlite3.connect(path)) as conn:
        return dict(conn.execute("SELECT credential_id,directory FROM communication_credentials").fetchall())


def credentials(c):
    with c.app.state.db.read() as conn:
        return conn.execute("SELECT count(*) FROM credentials").fetchone()[0]


def contents(conn):
    """Every row of every table, with rowids where a table has them."""
    found = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        try:
            rows = conn.execute(f'SELECT rowid,* FROM "{name}"').fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
        found[name] = sorted(map(tuple, rows), key=repr)
    return found


def version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def test_registration_records_the_directory_the_agent_registers_from(clients):
    client, agent = clients
    sponsor = issue(client)
    placed = registered(agent, sponsor["token"])
    assert placed["directory"] == DIRECTORY
    assert directories(client.app.state.db.path)[placed["id"]] == DIRECTORY
    # The registration's history says where the agent registered from, as it says the chat.
    history = ok(client.get("/api/v1/history", params={"entityId": placed["id"]}))["items"]
    assert [(e["kind"], e["after"]["directory"]) for e in history] == [("credential.register", DIRECTORY)]
    # An agent that sends none, such as one calling the API from an older command line, registers
    # with its directory unknown.
    assert registered(agent, sponsor["token"], directory=None)["directory"] is None
    # Windows, UNC and POSIX paths are absolute; spaces of any kind and other scripts stay, as in
    # folder names, and the ends are trimmed.
    for directory in (r"\\server\share\parser", "/home/person/parser", "C:/Users/Jane Doe/Projekte/Übersetzer", " D:\\work ",
                      "C:\\Users\\Jane\u00a0Doe\\parser", "C:\\ユーザー\\作業\u3000一"):
        assert registered(agent, sponsor["token"], directory=directory)["directory"] == directory.strip()


@pytest.mark.parametrize("directory", [
    "", "   ", "parser", "./parser", "C:parser", "~/parser", "C:\\parser\nC:\\other", "C:\\par\tser", "C:\\par\x7fser",
    # A right-to-left override, a zero-width space or a line separator would show Review a path
    # other than the one recorded.
    "C:\\Users\\person\\\u202egnp.exe", "C:\\pars\u200ber", "C:\\parser\u2028C:\\other",
    "C:\\" + "x" * 2000, "C:\\" + "é" * 1000, 42, ["C:\\parser"],
])
def test_registration_refuses_a_directory_review_could_not_show_as_it_is(clients, directory):
    client, agent = clients
    sponsor = issue(client)
    count = credentials(client)
    response, token = register(agent, sponsor["token"], directory=directory)
    assert response.status_code == 422, response.text
    assert "directory" in json.dumps(response.json()["error"]["fields"])
    assert token not in response.text
    assert credentials(client) == count


def test_a_retry_returns_the_directory_first_recorded(clients):
    client, agent = clients
    sponsor = issue(client)
    first, token = register(agent, sponsor["token"])
    # Retrying an uncertain registration from elsewhere returns the identity as it registered.
    again = ok(register(agent, sponsor["token"], directory=r"D:\elsewhere", token=token)[0])
    assert again == ok(first) and again["directory"] == DIRECTORY


def test_each_proposal_names_the_directory_its_agent_registered_from(clients):
    client, agent = clients
    sponsor, placed, unplaced = identities(client, agent)
    proposals = {
        DIRECTORY: propose(agent, "From the worktree", placed["token"]),
        "issued": propose(agent, "From the shared credential", sponsor["token"]),
        "unknown": propose(agent, "From an unknown directory", unplaced["token"]),
        "person": propose(client, "The person's own"),
    }
    listed = {p["title"]: p["actorDirectory"] for p in ok(client.get("/api/v1/proposals"))["items"]}
    assert listed == {"From the worktree": DIRECTORY, "From the shared credential": None,
                      "From an unknown directory": None, "The person's own": None}
    for key, proposal in proposals.items():
        expected = DIRECTORY if key == DIRECTORY else None
        assert proposal["actorDirectory"] == expected
        assert ok(client.get(f"/api/v1/proposals/{proposal['id']}"))["actorDirectory"] == expected
    # Rotating the registered credential keeps the identity, and so where it works.
    rotated = ok(write(client, "POST", f"/credentials/{placed['id']}/rotate", {}))
    later = propose(agent, "After rotation", rotated["token"])
    assert ok(client.get(f"/api/v1/proposals/{later['id']}"))["actorDirectory"] == DIRECTORY
    # Agents read it on the proposals they may see, as they read the label.
    seen = ok(agent.get("/api/v1/proposals", headers={"Authorization": "Bearer " + sponsor["token"]}))["items"]
    assert {p["title"]: p["actorDirectory"] for p in seen}["From the worktree"] == DIRECTORY
    # The proposal's history records it as the proposal was.
    history = ok(client.get("/api/v1/history", params={"entityId": proposals[DIRECTORY]["id"]}))["items"]
    assert {e["after"]["actorDirectory"] for e in history} == {DIRECTORY}


def test_the_v18_upgrade_leaves_earlier_registrations_unknown_and_changes_no_record(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as (c, agent):
        sponsor, placed, unplaced = identities(c, agent)
        proposal = propose(agent, "Before the upgrade", placed["token"])
    to_v17(path)
    with closing(sqlite3.connect(path)) as conn:
        # Registrations before v18 named no directory in their events either.
        conn.execute("UPDATE events SET after_json=json_remove(after_json,'$.directory') WHERE kind='credential.register'")
        conn.commit()
        kept = contents(conn)
    recovery.validate_database(path)
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert version(conn) == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        upgraded = contents(conn)
    # Every row of every table is as it was; each registration gains a directory, unknown. The
    # upgrade goes on to v19, whose releases start empty, and to v20, whose reminders start empty too.
    for table in ("releases", "release_contents", "release_checks", "reminders"):
        assert upgraded.pop(table) == []
    assert {row[-1] for row in upgraded["communication_credentials"]} == {None}
    upgraded["communication_credentials"] = [row[:-1] for row in upgraded["communication_credentials"]]
    assert upgraded == kept
    assert set(directories(path)) == {sponsor["id"], placed["id"], unplaced["id"]}
    snapshots = list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert version(conn) == 17
    with person(tmp_path) as (c, _):
        assert ok(c.get(f"/api/v1/proposals/{proposal['id']}"))["actorDirectory"] is None


def test_after_the_way_back_an_upgrade_copies_each_directory_back_from_its_registration(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as (c, agent):
        sponsor, placed, unplaced = identities(c, agent)
        unreadable = registered(agent, sponsor["token"], label="Unreadable")
        proposal = propose(agent, "From the worktree", placed["token"])
    kept = directories(path)
    assert kept[placed["id"]] == kept[unreadable["id"]] == DIRECTORY and kept[unplaced["id"]] is None
    to_v17(path)
    with closing(sqlite3.connect(path)) as conn:
        # One registration's event can't be read; nothing else fails because of it.
        conn.execute("UPDATE events SET after_json='not json' WHERE kind='credential.register' AND entity_id=?", (unreadable["id"],))
        conn.commit()
    Database(path).initialize()
    assert directories(path) == {**kept, unreadable["id"]: None}
    with person(tmp_path) as (c, _):
        assert ok(c.get(f"/api/v1/proposals/{proposal['id']}"))["actorDirectory"] == DIRECTORY


def test_the_documented_way_back_to_v17_gives_its_exact_shape_and_upgrades_again(tmp_path):
    # The SQL tested here is the SQL the README gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as (c, agent):
        identities(c, agent)
    kept = directories(path)
    to_v17(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert version(conn) == 17
        assert recovery._schema_signature(conn) == sealed.shape(17)
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert version(conn) == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    assert directories(path) == kept


def test_restoring_a_v17_backup_upgrades_it_and_copies_its_directories_back(tmp_path):
    source, target = tmp_path / "source" / "worklog.sqlite3", tmp_path / "target" / "worklog.sqlite3"
    with person(source.parent) as (c, agent):
        identities(c, agent)
    kept = directories(source)
    to_v17(source)
    Database(target).initialize()
    recovery.restore_database(target, source, tmp_path / "restore-backups")
    with closing(sqlite3.connect(target)) as conn:
        assert version(conn) == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    assert directories(target) == kept


def test_a_v17_shape_cannot_claim_version_18_and_a_v18_shape_cannot_claim_17(tmp_path):
    older, current = tmp_path / "older.sqlite3", tmp_path / "current.sqlite3"
    Database(older).initialize()
    to_v17(older)
    recovery.validate_database(older)
    Database(current).initialize()
    recovery.validate_database(current)
    for path, claimed in ((older, 18), (current, 17)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={claimed}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
