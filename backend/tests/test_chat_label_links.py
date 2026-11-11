"""Schema v24 links each tracked chat to the one repository, merged ones aside, whose name a label of
it is exactly (consolidation step 5.6, decision K8, findings A-08 and B-11), as v22 tagged
discussions. The labels stay, read-only: a chat takes no new ones, directly or by proposal. The
upgrade only adds links and changes no record, and the documented way back keeps the links, since
v23 holds them as well."""

import json
import sqlite3
import uuid
from contextlib import closing

import pytest
import sealed
from fastapi.testclient import TestClient
from test_sealed_schemas import contents
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
# From version 24 to version 23, with Worklog stopped and after a manual backup. docs/UPGRADING.md gave
# this way back while version 24 was the latest; older upgrade tests now build their databases with it.
WAY_BACK = """\
PRAGMA user_version=23;
"""


def to_v23(path):
    """Take a current database back to v23 the documented way."""
    from test_plan_import_removal import to_v24

    to_v24(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def session(data):
    client = TestClient(create_app(data, testing=True), base_url=ORIGIN)
    client.__enter__()
    client.headers["Origin"] = ORIGIN
    boot = client.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
    client.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
    return client


def call(client, method, path, payload=None, headers=None, status=200):
    response = client.request(method, "/api/v1" + path, json=payload,
                              headers={"Idempotency-Key": str(uuid.uuid4()), **(headers or {})})
    assert response.status_code == status, response.text
    return response.json()


def labeled(data):
    """A current database whose chats carry labels, as chats written before v24 do, and the IDs the
    upgrade should link them to. Each name the labels use is set up the way the rule must judge it."""
    client = session(data)
    try:
        repository = lambda name: call(client, "POST", "/repositories", {"name": name})  # noqa: E731
        worklog, parser, twin, _, kept, gone, replaced = (repository(name) for name in (
            "Worklog", "Parser", "Twin", "Twin", "Kept", "Gone", "Replaced"))
        archived = call(client, "POST", f"/repositories/{parser['id']}/archive", {"revision": parser["revision"]})
        assert archived["archived"]
        # Merged aside: "Gone" names only a merged repository, and "Replaced" one merged and one not.
        merge = lambda source, into: call(client, "POST", f"/repositories/{source['id']}/merge", {  # noqa: E731
            "revision": source["revision"], "intoId": into["id"], "intoRevision": into["revision"]})
        merge(gone, call(client, "GET", f"/repositories/{worklog['id']}"))
        merge(replaced, repository("Replaced again"))
        again = repository("Replaced")
        item = call(client, "POST", "/work-items", {"title": "Parser"})
        chat = lambda title, **fields: call(client, "POST", "/chats", {  # noqa: E731
            "ownerType": "item", "ownerId": item["id"], "chatId": str(uuid.uuid4()), "title": title, **fields})
        many, already, plain = chat("Many labels"), chat("Linked before", repositoryIds=[kept["id"]]), chat("No labels")
        archived_chat = chat("Archived")
        call(client, "POST", f"/chats/{archived_chat['id']}/archive", {"revision": archived_chat["revision"]})
    finally:
        client.__exit__(None, None, None)
    database = data / "worklog.sqlite3"
    labels = {many["id"]: ["Worklog", "Parser", "Twin", "Gone", "Replaced", "worklog", "Nowhere", "Worklog "],
              already["id"]: ["Kept", "Worklog"], archived_chat["id"]: ["Worklog"]}
    with closing(sqlite3.connect(database)) as conn:
        for identity, names in labels.items():
            conn.execute("UPDATE agent_chats SET repos=? WHERE id=?", (json.dumps(names), identity))
        conn.commit()
    expected = {many["id"]: {worklog["id"], parser["id"], again["id"]}, already["id"]: {kept["id"], worklog["id"]},
                archived_chat["id"]: {worklog["id"]}, plain["id"]: set()}
    return database, expected


def links(path):
    with closing(sqlite3.connect(path)) as conn:
        found: dict[str, set[str]] = {}
        for chat, repository in conn.execute("SELECT chat_id,repository_id FROM chat_repositories"):
            found.setdefault(chat, set()).add(repository)
        return found


def chats(path):
    """Each chat's row, labels, revision and times included."""
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT * FROM agent_chats ORDER BY id").fetchall()


def test_the_upgrade_links_each_label_that_names_one_repository_and_changes_no_record(tmp_path):
    database, expected = labeled(tmp_path / "data")
    to_v23(database)
    recovery.validate_database(database)
    before = chats(database)
    _, _, records = contents(database)
    earlier = set(records["chat_repositories"])
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    # Exactly one repository not merged aside: active, archived, or the one left beside a merged one.
    # Not a name two share, one only a merged repository has, another case, extra space, or nothing.
    assert links(database) == {chat: repositories for chat, repositories in expected.items() if repositories}
    # Only links are new: every chat, its labels, revision and times, and every other row stay. v25
    # to v28, which the upgrade also runs, drop the plan import, task, reminder and release tables,
    # empty here.
    assert chats(database) == before
    assert {table: rows for table, rows in after.items() if table != "chat_repositories"} == \
        {table: rows for table, rows in records.items()
         if table not in ("chat_repositories", "plan_imports", "tasks", "reminders", "releases", "release_contents",
                          "release_checks")}
    # The link a chat had before keeps its row, its time included.
    assert len(earlier) == 1 and earlier <= set(after["chat_repositories"])
    snapshot, = (tmp_path / "data" / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 23


@pytest.mark.parametrize("stored", [
    "not json", "", "null", "5", '"Worklog"', '{"Worklog": 1}', '[1, null, ["Worklog"], {"Worklog": 1}]',
    "﻿[\"Worklog\"]", "[" * 100_000], ids=lambda stored: repr(stored[:12]))
def test_labels_worklog_never_wrote_link_nothing_and_stop_no_upgrade(tmp_path, stored):
    # Worklog writes a chat's labels as a JSON list of names. Anything else, from outside Worklog,
    # names nothing to link, and the rest of the upgrade goes on.
    client = session(tmp_path / "data")
    try:
        worklog = call(client, "POST", "/repositories", {"name": "Worklog"})
        item = call(client, "POST", "/work-items", {"title": "Parser"})
        odd, good = (call(client, "POST", "/chats", {"ownerType": "item", "ownerId": item["id"],
                                                      "chatId": str(uuid.uuid4()), "title": title})
                     for title in ("Odd labels", "Good labels"))
    finally:
        client.__exit__(None, None, None)
    database = tmp_path / "data" / "worklog.sqlite3"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("UPDATE agent_chats SET repos=? WHERE id=?", (stored, odd["id"]))
        conn.execute("UPDATE agent_chats SET repos=? WHERE id=?", (json.dumps(["Worklog"]), good["id"]))
        conn.commit()
    to_v23(database)
    Database(database).initialize()
    assert links(database) == {good["id"]: {worklog["id"]}}
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT repos FROM agent_chats WHERE id=?", (odd["id"],)).fetchone()[0] == stored


def test_the_way_back_keeps_the_links_and_upgrading_again_adds_none(tmp_path):
    database, expected = labeled(tmp_path / "data")
    to_v23(database)
    Database(database).initialize()
    linked = links(database)
    to_v23(database)
    recovery.validate_database(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 23
        assert recovery._schema_signature(conn) == sealed.shape(23)
    assert links(database) == linked
    Database(database).initialize()
    assert links(database) == linked


def test_a_chat_takes_no_new_label_and_keeps_the_ones_it_has(tmp_path):
    client = session(tmp_path / "data")
    try:
        item = call(client, "POST", "/work-items", {"title": "Parser"})
        new = {"ownerType": "item", "ownerId": item["id"], "chatId": str(uuid.uuid4()), "title": "Run"}
        refused = call(client, "POST", "/chats", {**new, "repos": ["acme/api"]}, status=422)
        assert (refused["error"]["code"], refused["error"]["fields"]) == ("validation", {"repos": "retired"})
        assert "repositoryIds" in refused["error"]["message"]
        chat = call(client, "POST", "/chats", {**new, "repos": []})
        assert chat["repos"] == []
        with client.app.state.db.transaction() as c:
            c.execute("UPDATE agent_chats SET repos=? WHERE id=?", (json.dumps(["acme/api", "acme/web"]), chat["id"]))
        chat = call(client, "GET", f"/chats/{chat['id']}")
        for repos in (["acme/web"], ["acme/web", "acme/api"], ["acme/api", "acme/web", "acme/docs"], []):
            refused = call(client, "PATCH", f"/chats/{chat['id']}", {"revision": chat["revision"], "repos": repos}, status=422)
            assert refused["error"]["fields"] == {"repos": "retired"}, repos
        # Its labels sent back as they are change nothing, and leaving them out keeps them.
        same = call(client, "PATCH", f"/chats/{chat['id']}", {"revision": chat["revision"], "title": "Run again",
                                                              "repos": ["acme/api", "acme/web"]})
        renamed = call(client, "PATCH", f"/chats/{chat['id']}", {"revision": same["revision"], "model": "claude-opus-5-5"})
        assert (same["title"], renamed["repos"]) == ("Run again", ["acme/api", "acme/web"])
        # An agent's proposal is held to the same rule when it is approved.
        token = call(client, "POST", "/credentials", {"label": "Coding agent"})["token"]
        bearer = {"Authorization": "Bearer " + token}
        operations = [{"action": "chat.create", "payload": {**new, "chatId": "codex-7", "repos": ["acme/docs"]}}]
        proposal = call(client, "POST", "/proposals", {"title": "Track it", "rationale": "Started", "operations": operations},
                        headers=bearer)
        submitted = call(client, "POST", f"/proposals/{proposal['id']}/submit", {"revision": proposal["revision"]}, headers=bearer)
        refused = call(client, "POST", f"/proposals/{submitted['id']}/approve", {"revision": submitted["revision"]}, status=422)
        assert refused["error"]["fields"]["fields"] == {"repos": "retired"}
        with client.app.state.db.read() as c:
            assert c.execute("SELECT count(*) FROM agent_chats WHERE chat_id='codex-7'").fetchone()[0] == 0
    finally:
        client.__exit__(None, None, None)
