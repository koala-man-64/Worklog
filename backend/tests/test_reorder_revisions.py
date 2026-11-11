"""Reordering changes only order (consolidation step 4.1, K4, REM-13). A backlog or day reorder, and
moving a work item up or down the backlog, leave the revisions of the items and entries they reorder
alone: the backlog's and the day's own revisions guard order. An entry change raises the day's
revision only when it adds or removes an entry, so saving an outcome leaves a reorder drafted
against the day able to save."""

import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
DAY = "2026-11-04"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def call(c, method, path, body=None):
    response = c.request(method, "/api/v1" + path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def items(client, *titles):
    return [call(client, "POST", "/work-items", {"title": title}) for title in titles]


def day(client, date=DAY):
    return call(client, "GET", f"/days/{date}")


def test_a_day_holds_no_reflection(client):
    # The end-of-day reflection is gone (step 0.2, decision R4): its two columns stay, unread.
    (item,) = items(client, "Write")
    call(client, "PUT", f"/days/{DAY}/entries/{item['id']}", {"revision": 0, "planned": True})
    assert not {"reflection", "reflectionRevision"} & set(day(client))
    saved = client.put(f"/api/v1/days/{DAY}/reflection", json={"revision": 0, "text": "A good day"},
                       headers={"Idempotency-Key": str(uuid.uuid4())})
    assert saved.status_code in (404, 405), saved.text


def test_a_day_reorder_leaves_each_entry_its_revision(client):
    first, second = items(client, "First", "Second")
    for item in (first, second):
        call(client, "PUT", f"/days/{DAY}/entries/{item['id']}", {"revision": 0, "planned": True})
    before = day(client)
    entries = {e["itemId"]: e["revision"] for e in before["entries"]}
    call(client, "POST", f"/days/{DAY}/reorder", {"revision": before["revision"], "ids": [e["id"] for e in reversed(before["entries"])]})
    after = day(client)
    assert {e["itemId"]: e["revision"] for e in after["entries"]} == entries
    assert [e["itemId"] for e in after["entries"]] == [second["id"], first["id"]]
    assert after["revision"] == before["revision"] + 1
    # So an outcome being written when the day was reordered still saves.
    call(client, "PUT", f"/days/{DAY}/entries/{first['id']}", {"revision": entries[first["id"]], "outcome": "Done first"})


def test_backlog_moves_leave_each_item_its_revision(client):
    work = items(client, "One", "Two", "Three")
    listed = call(client, "GET", "/work-items?view=backlog")
    revisions = {i["id"]: i["revision"] for i in work}
    call(client, "POST", "/work-items/reorder", {"revision": listed["collectionRevision"], "ids": [i["id"] for i in reversed(listed["items"])]})
    moved = call(client, "GET", "/work-items?view=backlog")
    call(client, "POST", f"/work-items/{work[0]['id']}/move", {"revision": moved["collectionRevision"], "direction": "up"})
    final = call(client, "GET", "/work-items?view=backlog")
    assert {i["id"]: i["revision"] for i in final["items"]} == revisions
    assert final["collectionRevision"] > listed["collectionRevision"]
    # So an edit open on a moved item when the backlog was reordered still saves.
    call(client, "PATCH", f"/work-items/{work[0]['id']}", {"revision": revisions[work[0]["id"]], "title": "One, renamed"})


def test_rescheduling_changes_a_day_only_where_it_adds_or_removes_an_entry(client):
    source, target = DAY, "2026-11-05"
    item, other = items(client, "Move me", "Stays")
    call(client, "PUT", f"/days/{target}/entries/{other['id']}", {"revision": 0, "planned": True})
    planned = call(client, "PUT", f"/days/{source}/entries/{item['id']}", {"revision": 0, "planned": True})
    worked = call(client, "PUT", f"/days/{source}/entries/{item['id']}", {"revision": planned["revision"], "worked": True})
    before_source, before_target = day(client, source), day(client, target)
    call(client, "POST", f"/days/{source}/reschedule",
         {"itemId": item["id"], "targetDate": target, "revision": worked["revision"], "targetRevision": 0})
    # The worked entry stays on its day, no longer planned; the target day gains an entry.
    assert day(client, source)["revision"] == before_source["revision"]
    after_target = day(client, target)
    assert after_target["revision"] == before_target["revision"] + 1
    # Back again: the source day keeps its entry, which plans the item once more, and the target
    # day loses the entry that only planned it.
    moved = next(e for e in after_target["entries"] if e["itemId"] == item["id"])
    kept = next(e for e in day(client, source)["entries"] if e["itemId"] == item["id"])
    call(client, "POST", f"/days/{target}/reschedule",
         {"itemId": item["id"], "targetDate": source, "revision": moved["revision"], "targetRevision": kept["revision"]})
    assert day(client, source)["revision"] == before_source["revision"]
    assert day(client, target)["revision"] == after_target["revision"] + 1


def test_an_outcome_leaves_the_day_revision(client):
    (item,) = items(client, "Write")
    entry = call(client, "PUT", f"/days/{DAY}/entries/{item['id']}", {"revision": 0, "planned": True})
    opened = day(client)
    call(client, "PUT", f"/days/{DAY}/entries/{item['id']}", {"revision": entry["revision"], "outcome": "Drafted it"})
    assert day(client)["revision"] == opened["revision"]
    # Adding and removing an entry still change the day.
    (other,) = items(client, "Read")
    call(client, "PUT", f"/days/{DAY}/entries/{other['id']}", {"revision": 0, "planned": True})
    added = day(client)
    assert added["revision"] == opened["revision"] + 1
    current = next(e for e in added["entries"] if e["itemId"] == other["id"])
    call(client, "POST", f"/days/{DAY}/entries/{other['id']}/remove", {"revision": current["revision"]})
    assert day(client)["revision"] == added["revision"] + 1
