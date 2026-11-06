"""Agent avatars: drawn at registration, chosen by agents, recycled after revocation."""

import random
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import avatars, discussions, migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
STAMP = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        r = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrfToken"]
        yield c


def write(c, path, payload=None, agent=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.post("/api/v1" + path, headers=headers, json={} if payload is None else payload)


def get(c, path, agent=None):
    return c.get("/api/v1" + path, headers={"Authorization": "Bearer " + agent["token"]} if agent else {})


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def enroll(c, label, avatar=None):
    return ok(write(c, "/credentials", {"label": label, **({"avatar": avatar} if avatar else {})}))


def color(name):
    return name.split("-", 1)[0]


def free_avatar(c, agent=None):
    return next(entry["name"] for entry in ok(get(c, "/discussions/avatars", agent))["items"] if entry["available"])


def test_choice_spreads_colours_before_repeating_and_only_in_use_avatars_count():
    assert len(set(avatars.AVATARS)) == len(avatars.COLORS) * len(avatars.EYES) * len(avatars.TOPS)
    rng = random.Random(7)
    taken: list[str] = []
    for _ in avatars.COLORS:
        taken.append(avatars.choose(taken, rng))
    assert {color(name) for name in taken} == set(avatars.COLORS)
    assert avatars.choose(avatars.AVATARS[1:], rng) == avatars.AVATARS[0]
    assert avatars.choose([*avatars.AVATARS, *avatars.AVATARS[1:]], rng) == avatars.AVATARS[0]


def test_browser_draws_the_same_parts_in_the_same_order():
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "Avatar.tsx").read_text(encoding="utf-8")
    colors = re.search(r"AVATAR_COLORS = \{(.*?)\} as const", source, re.S)
    assert colors and tuple(re.findall(r"^\s*(\w+): \"#", colors.group(1), re.M)) == avatars.COLORS
    for name, parts in (("AVATAR_EYES", avatars.EYES), ("AVATAR_TOPS", avatars.TOPS)):
        listed = re.search(name + r" = \[(.*?)\] as const", source, re.S)
        assert listed and tuple(re.findall(r'"(\w+)"', listed.group(1))) == parts


def test_registration_draws_distinct_avatars_honours_a_choice_and_rotation_keeps_it(client):
    first, second = enroll(client, "First"), enroll(client, "Second")
    assert avatars.is_avatar(first["avatar"]) and avatars.is_avatar(second["avatar"])
    assert color(first["avatar"]) != color(second["avatar"])
    catalog = ok(get(client, "/discussions/avatars"))
    availability = {entry["name"]: entry["available"] for entry in catalog["items"]}
    assert len(availability) == len(avatars.AVATARS)
    assert not availability[first["avatar"]] and availability[catalog["suggested"]]
    assert catalog["current"] is None
    chosen = free_avatar(client)
    third = enroll(client, "Third", chosen)
    assert third["avatar"] == chosen
    duplicate = write(client, "/credentials", {"label": "Duplicate", "avatar": chosen})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "avatar_in_use"
    assert write(client, "/credentials", {"label": "Unknown", "avatar": "plaid-laser-cape"}).status_code == 422
    # A refused avatar issues no credential.
    assert len(ok(client.get("/api/v1/settings"))["credentials"]) == 3
    ok(write(client, f"/credentials/{first['id']}/rotate", {}))
    participants = {p["id"]: p["avatar"] for p in ok(get(client, "/discussions/participants"))["items"]}
    assert participants == {"local-user": None, first["agentId"]: first["avatar"],
                            second["agentId"]: second["avatar"], third["agentId"]: chosen}
    profiles = {agent["id"]: agent["avatar"] for agent in ok(client.get("/api/v1/settings"))["agents"]}
    assert profiles == {k: v for k, v in participants.items() if k != "local-user"}


def test_agents_choose_their_own_avatar_and_a_revoked_agent_frees_its_avatar(client):
    agent, other = enroll(client, "Chooser"), enroll(client, "Other")
    assert ok(get(client, "/discussions/avatars", agent))["current"] == agent["avatar"]
    taken = write(client, "/discussions/avatar", {"avatar": other["avatar"]}, agent)
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "avatar_in_use"
    wanted = free_avatar(client, agent)
    chosen = ok(write(client, "/discussions/avatar", {"avatar": wanted}, agent, key="choose-avatar"))
    assert chosen == {"id": agent["agentId"], "label": "Chooser", "kind": "agent", "active": True, "avatar": wanted}
    assert ok(write(client, "/discussions/avatar", {"avatar": wanted}, agent, key="choose-avatar")) == chosen
    # A person picks an avatar when issuing a credential, not through the agent route.
    assert write(client, "/discussions/avatar", {"avatar": free_avatar(client)}).status_code == 403
    assert client.post(f"/api/v1/credentials/{other['id']}/revoke").status_code == 200
    assert {e["name"]: e["available"] for e in ok(get(client, "/discussions/avatars", agent))["items"]}[other["avatar"]]
    assert ok(write(client, "/discussions/avatar", {"avatar": other["avatar"]}, agent))["avatar"] == other["avatar"]
    assert write(client, "/discussions/avatar", {"avatar": wanted}, other).status_code == 401
    # The list of avatars is read-only: no route serves a POST to it, so an agent's is refused.
    refused = write(client, "/discussions/avatars", {"avatar": wanted}, agent)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "agent_scope"


def test_reissuing_a_revoked_agent_redraws_an_avatar_someone_took_meanwhile(client):
    returning = enroll(client, "Returning")
    assert client.post(f"/api/v1/credentials/{returning['id']}/revoke").status_code == 200
    successor = enroll(client, "Successor", returning["avatar"])
    ok(write(client, f"/credentials/{returning['id']}/rotate", {}))
    active = {p["id"]: p["avatar"] for p in ok(get(client, "/discussions/participants"))["items"] if p["kind"] == "agent" and p["active"]}
    assert active[successor["agentId"]] == returning["avatar"]
    assert avatars.is_avatar(active[returning["agentId"]]) and active[returning["agentId"]] != returning["avatar"]


def v2_database(path):
    """The shape main shipped as v2: coordination, but no avatars."""
    sealed.build(path, 2)
    with closing(sqlite3.connect(path)) as conn:
        for identity, label, revoked in (("live", "Live agent", 0), ("retired", "Retired agent", 1), ("second", "Second agent", 0)):
            conn.execute("INSERT INTO credentials VALUES(?,?,?,?,?)", (identity, label, "hash-" + identity, STAMP, revoked))
            conn.execute("INSERT INTO communication_agents(id,label,created_at) VALUES(?,?,?)", (identity, label, STAMP))
            conn.execute("INSERT INTO communication_credentials VALUES(?,?)", (identity, identity))
        conn.commit()
    recovery.validate_database(path)


def test_v2_upgrade_snapshots_then_gives_every_agent_a_distinct_avatar_and_restores(tmp_path):
    path = tmp_path / "live" / "worklog.sqlite3"
    path.parent.mkdir()
    v2_database(path)
    Database(path).initialize()
    with Database(path).read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assigned = dict(conn.execute("SELECT id,avatar FROM communication_agents").fetchall())
        assert tuple(conn.execute("SELECT token_hash,revoked FROM credentials WHERE id='live'").fetchone()) == ("hash-live", 0)
    assert all(avatars.is_avatar(name) for name in assigned.values())
    assert len({color(name) for name in assigned.values()}) == 3
    snapshots = list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert recovery._schema_signature(conn) == sealed.shape(2)
    Database(path).initialize()
    assert len(list((path.parent / "backups").glob("worklog-pre-migration-*.sqlite3"))) == 1
    restored = tmp_path / "restored" / "worklog.sqlite3"
    recovery.restore_database(restored, snapshots[0], tmp_path / "restore-backups")
    with Database(restored).read() as conn:
        assert recovery._schema_signature(conn) == sealed.current()
        assert all(avatars.is_avatar(row[0]) for row in conn.execute("SELECT avatar FROM communication_agents"))
        assert conn.execute("SELECT count(*) FROM credentials WHERE revoked=0").fetchone()[0] == 0


def test_failed_avatar_backfill_rolls_back_the_column_and_version(tmp_path, monkeypatch):
    path = tmp_path / "worklog.sqlite3"
    v2_database(path)

    def fail(conn):
        raise RuntimeError("Injected avatar backfill failure")

    monkeypatch.setattr(discussions, "assign_missing_avatars", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert recovery._schema_signature(conn) == sealed.shape(2)
    recovery.validate_database(path)


def test_a_v2_shape_cannot_claim_version_3(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    v2_database(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version=3")
    with pytest.raises(ValueError, match="schema definitions"):
        recovery.validate_database(path)
