"""History leaves the roadmap, work item and person reads (audit finding P1, roadmap step 3.1).

GET /roadmaps/{id} embedded the last 100 history events, each roadmap save holding the whole plan
before and after: 43.3 MB for the largest roadmap, 41.8 MB of it history, read again on every return
to the window and after every write. The work item and person reads embedded history the same way.
Now none of the three carries history, and GET /history lists who changed a record, how and when,
with each change's before and after only for one change asked for by its ID, or with payloads=true."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
SUMMARY = {"id", "entityId", "kind", "actorId", "actorLabel", "actorKind", "createdAt", "localDate"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, testing=True), base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def call(c, method, path, body=None, expect=200, headers=None):
    response = c.request(method, "/api/v1" + path, json=body,
                         headers={"Idempotency-Key": str(uuid.uuid4()), **(headers or {})})
    assert response.status_code == expect, response.text
    return response.json()


def plan(text):
    step = "5c1e8a4e-0000-4000-8000-000000000001"
    return [{"id": step, "title": "Step", "description": text}]


def test_the_roadmap_work_item_and_person_reads_carry_no_history(client):
    roadmap = call(client, "POST", "/roadmaps", {"title": "Plan", "steps": plan("a")})
    item = call(client, "POST", "/work-items", {"title": "Work"})
    call(client, "PATCH", f"/work-items/{item['id']}", {"revision": item["revision"], "title": "Work, renamed"})
    person = call(client, "POST", "/people", {"name": "Alice"})
    assert "history" not in call(client, "GET", f"/roadmaps/{roadmap['id']}")
    assert "history" not in call(client, "GET", f"/work-items/{item['id']}")
    assert "history" not in call(client, "GET", f"/people/{person['id']}")


def test_a_roadmaps_read_is_no_larger_after_twenty_saves_than_after_one(client):
    # Each save's history event held the whole plan twice: 20 saves of a 20 KB plan added 800 KB.
    text = "x" * 20_000
    roadmap = call(client, "POST", "/roadmaps", {"title": "Plan", "steps": plan(text)})
    roadmap = call(client, "POST", f"/roadmaps/{roadmap['id']}/revisions",
                   {"revision": roadmap["revision"], "title": "Plan", "steps": plan(text + "0")})

    def size():
        read = call(client, "GET", f"/roadmaps/{roadmap['id']}")
        # Each save lists one more version, by its number, time and note: a few dozen bytes.
        return len(json.dumps({key: value for key, value in read.items() if key != "versions"}))

    once = size()
    for n in range(1, 20):
        roadmap = call(client, "POST", f"/roadmaps/{roadmap['id']}/revisions",
                       {"revision": roadmap["revision"], "title": "Plan", "steps": plan(text + str(n % 10))})
    assert size() <= once + 100


def test_history_lists_changes_without_their_payloads_by_default(client):
    item = call(client, "POST", "/work-items", {"title": "Work"})
    call(client, "PATCH", f"/work-items/{item['id']}", {"revision": item["revision"], "title": "Work, renamed"})
    listed = call(client, "GET", f"/history?entityId={item['id']}")["items"]
    assert [entry["kind"] for entry in listed] == ["item.update", "item.create"]
    assert all(set(entry) == SUMMARY for entry in listed)


def test_history_gives_one_changes_payload_when_asked_for_it(client):
    item = call(client, "POST", "/work-items", {"title": "Work"})
    call(client, "PATCH", f"/work-items/{item['id']}", {"revision": item["revision"], "title": "Work, renamed"})
    update, create = call(client, "GET", f"/history?entityId={item['id']}")["items"]
    [one] = call(client, "GET", f"/history?entityId={item['id']}&eventId={update['id']}")["items"]
    assert set(one) == SUMMARY | {"before", "after"}
    assert (one["id"], one["before"]["title"], one["after"]["title"]) == (update["id"], "Work", "Work, renamed")
    # Every change whole, as before, when the caller asks for payloads.
    whole = call(client, "GET", f"/history?entityId={item['id']}&payloads=true")["items"]
    assert [(entry["id"], entry["after"]["title"]) for entry in whole] == [(update["id"], "Work, renamed"),
                                                                           (create["id"], "Work")]


def test_a_change_asked_for_by_an_id_it_does_not_have_is_not_found(client):
    item = call(client, "POST", "/work-items", {"title": "Work"})
    other = call(client, "POST", "/work-items", {"title": "Other"})
    [theirs] = call(client, "GET", f"/history?entityId={other['id']}")["items"]
    refused = call(client, "GET", f"/history?entityId={item['id']}&eventId={theirs['id']}", expect=404)
    assert refused["error"]["code"] == "not_found"
