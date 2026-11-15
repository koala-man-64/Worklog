"""The registry reads the same pull requests as their owners, including upgraded references."""

import hashlib
import json
import sqlite3
from contextlib import closing
from uuid import uuid4

import sealed
from fastapi.testclient import TestClient
from test_api import ORIGIN, client, item, write  # noqa: F401
from test_step_update import ok, read, refused
from worklog.app import create_app
from worklog.common import now


def test_registry_tracks_submitters_across_work_and_roadmaps(client):  # noqa: F811
    work = item(client, title="Parser fixes")
    roadmap = ok(write(client, "POST", "/roadmaps", {"title": "Deliver the parser"}))
    chat = ok(write(client, "POST", "/chats", {"ownerType": "item", "ownerId": work["id"],
                                               "chatId": "parser-review", "title": "Review notes"}))
    github = ok(write(client, "POST", "/github", {"ownerType": "item", "ownerId": work["id"], "kind": "pr",
                                                   "url": "https://github.com/example/worklog/pull/1",
                                                   "chatId": chat["id"], "submittedBy": "  Åsa_Reviewer%  "}))
    ado = ok(write(client, "POST", "/github", {"ownerType": "roadmap", "ownerId": roadmap["id"], "kind": "pr",
                                                "url": "https://dev.azure.com/org/project/_git/worklog/pullrequest/2"}))
    ok(write(client, "POST", "/github", {"ownerType": "item", "ownerId": work["id"], "kind": "commit",
                                         "url": "https://github.com/example/worklog/commit/c14eab8"}))

    registry = ok(read(client, "/pull-requests?limit=1"))
    assert (registry["total"], registry["limit"], registry["offset"]) == (2, 1, 0)
    assert registry["items"] == [{**ado, "ownerTitle": "Deliver the parser"}]
    assert ok(read(client, "/pull-requests?limit=1&offset=1"))["items"] == [{**github, "ownerTitle": "Parser fixes"}]
    assert (github["submittedBy"], github["chatTitle"]) == ("Åsa_Reviewer%", "Review notes")
    assert ado["submittedBy"] == ""
    assert ok(read(client, "/pull-requests?submittedBy=%25"))["total"] == 1
    assert ok(read(client, "/pull-requests?submittedBy=%C3%A5SA_Reviewer%25"))["items"] == [
        {**github, "ownerTitle": "Parser fixes"}]

    path = f"/github/{ado['id']}"
    updated = ok(write(client, "PATCH", path, {"revision": ado["revision"], "submittedBy": "Morgan"}))
    refused(write(client, "PATCH", path, {"revision": ado["revision"], "submittedBy": "Old edit"}), 409, "conflict")
    assert ok(read(client, f"/roadmaps/{roadmap['id']}"))["github"][0]["submittedBy"] == "Morgan"
    assert ok(read(client, "/pull-requests?submittedBy=morg"))["items"] == [
        {**updated, "ownerTitle": "Deliver the parser"}]
    archived = ok(write(client, "POST", path + "/archive", {"revision": updated["revision"]}))
    assert ok(read(client, "/pull-requests?submittedBy=morg"))["total"] == 0
    ok(write(client, "POST", path + "/recover", {"revision": archived["revision"]}))
    assert ok(read(client, "/pull-requests?submittedBy=morg"))["total"] == 1


def test_v31_pull_requests_and_pending_retries_survive_upgrade(tmp_path):
    database = tmp_path / "worklog.sqlite3"
    sealed.build(database, 31)
    work_id, pr_id, request_id = (str(uuid4()) for _ in range(3))
    stamp = now()
    url = "https://github.com/example/worklog/pull/15"
    body = {"ownerType": "item", "ownerId": work_id, "kind": "pr", "url": url}
    legacy_payload = {**body, "reference": "", "title": "", "description": "", "chatId": None}
    digest = hashlib.sha256(("POST/api/v1/github?" + json.dumps(legacy_payload, sort_keys=True)).encode()).hexdigest()
    legacy_response = {**legacy_payload, "id": pr_id, "reference": "example/worklog#15", "chatTitle": None,
                       "archived": False, "revision": 1, "createdAt": stamp, "updatedAt": stamp}
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO work_items(id,title,backlog_order,created_at,updated_at) VALUES(?,?,1,?,?)",
                     (work_id, "Old work", stamp, stamp))
        conn.execute("INSERT INTO github_refs(id,owner_type,owner_id,kind,url,reference,created_at,updated_at) "
                     "VALUES(?,'item',?,'pr',?,?,?,?)", (pr_id, work_id, url, legacy_response["reference"], stamp, stamp))
        conn.execute("INSERT INTO receipts(actor_id,key,request_hash,response_json,created_at) VALUES('local-user',?,?,?,?)",
                     (request_id, digest, json.dumps(legacy_response), stamp))
        conn.commit()

    with TestClient(create_app(tmp_path, testing=True), base_url=ORIGIN, headers={"Origin": ORIGIN}) as person:
        boot = person.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        person.headers["X-CSRF-Token"] = ok(boot)["csrfToken"]
        retried = ok(write(person, "POST", "/github", body, headers={"Idempotency-Key": request_id}))
        assert retried == {**legacy_response, "submittedBy": ""}
        assert ok(read(person, "/pull-requests"))["items"] == [{**retried, "ownerTitle": "Old work"}]
