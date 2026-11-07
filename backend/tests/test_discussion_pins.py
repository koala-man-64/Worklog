"""Pinned discussions (schema v21): only the person pins, a pinned thread lists first wherever it is
listed, the latest pinned first, and a pin changes the revision but not the thread's activity."""

import uuid

import pytest
from test_discussion_tags import credential, error, ok, person, read, start, write


@pytest.fixture
def client(tmp_path):
    with person(tmp_path) as c:
        yield c


def pin(c, discussion, pinned=True, agent=None, revision=None):
    payload = {"revision": discussion["revision"] if revision is None else revision, "pinned": pinned}
    return write(c, "POST", f"/discussions/{discussion['id']}/pin", payload, agent)


def ids(listing):
    return [d["id"] for d in listing["items"]]


def test_a_pinned_thread_lists_first_the_latest_pin_first_and_keeps_its_place_in_activity(client):
    first, second, third = start(client, title="First"), start(client, title="Second"), start(client, title="Third")
    assert ids(read(client, "/discussions")) == [third["id"], second["id"], first["id"]]
    pinned = ok(pin(client, first))
    assert pinned["pinned"] is True and pinned["pinnedAt"]
    assert pinned["revision"] == first["revision"] + 1 and pinned["updatedAt"] == first["updatedAt"]
    ok(pin(client, second))
    assert ids(read(client, "/discussions")) == [second["id"], first["id"], third["id"]]
    assert ids(read(client, "/discussions?pinned=true")) == [second["id"], first["id"]]
    assert ids(read(client, "/discussions?pinned=false")) == [third["id"]]
    builder = credential(client)
    # An agent reads the same order, and so does a snapshot of a context.
    assert ids(read(client, "/discussions", builder)) == [second["id"], first["id"], third["id"]]


def test_pins_order_resume_and_archived_threads_too(client):
    work = ok(write(client, "POST", "/work-items", {"title": "Fix the parser"}))
    older = start(client, title="Older", contexts=[{"type": "item", "id": work["id"]}])
    newer = start(client, title="Newer", contexts=[{"type": "item", "id": work["id"]}])
    ok(pin(client, older))
    resumed = read(client, f"/discussions/resume?contextType=item&contextId={work['id']}")
    assert [d["id"] for d in resumed["discussions"]] == [older["id"], newer["id"]]
    for thread in (older, newer):
        current = read(client, f"/discussions/{thread['id']}")["discussion"]
        ok(write(client, "POST", f"/discussions/{thread['id']}/archive", {"revision": current["revision"], "archived": True}))
    archived = read(client, "/discussions?view=archived")
    assert ids(archived) == [older["id"], newer["id"]] and archived["items"][0]["pinned"] is True


def test_only_the_person_pins_with_the_current_revision_and_a_repeat_changes_nothing(client):
    thread = start(client)
    builder = credential(client)
    error(pin(client, thread, agent=builder), 403, "agent_scope")
    error(pin(client, thread, revision=thread["revision"] + 1), 409, "conflict")
    pinned = ok(pin(client, thread))
    assert ok(pin(client, pinned))["revision"] == pinned["revision"]
    unpinned = ok(pin(client, pinned, pinned=False))
    assert (unpinned["pinned"], unpinned["pinnedAt"], unpinned["revision"]) == (False, None, pinned["revision"] + 1)
    error(write(client, "POST", f"/discussions/{uuid.uuid4()}/pin", {"revision": 1, "pinned": True}), 404, "not_found")


def test_pinning_and_unpinning_reach_the_feed(client):
    work = ok(write(client, "POST", "/work-items", {"title": "Fix the parser"}))
    builder = credential(client)
    scope = f"contextType=item&contextId={work['id']}"
    cursor = read(client, "/discussions/changes?" + scope, builder)["cursor"]
    thread = start(client, contexts=[{"type": "item", "id": work["id"]}])
    pinned = ok(pin(client, thread))
    ok(pin(client, pinned, pinned=False))
    feed = read(client, f"/discussions/changes?{scope}&cursor={cursor}", builder)
    assert [(item["kind"], item["actorId"]) for item in feed["items"]] == [
        ("discussion.create", "local-user"), ("discussion.pin", "local-user"), ("discussion.unpin", "local-user")]
