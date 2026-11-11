"""Discussion tags (schema v22): a thread names the records it is about, of nine kinds since v28 dropped
releases, and says who added each tag. Anyone adds a tag; an agent takes off only the tags it added,
and the person any but a repository's tag on its own discussion. Feeds and filters follow every kind,
free-text repository labels take no new writes (K8), and tags reserve and change nothing about the
records they name."""

import re
import uuid
from contextlib import contextmanager
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from worklog import cli, discussion_models, discussions, doctor, migrations
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
PERSON = {"id": "local-user", "label": "You", "kind": "human"}


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


def start(c, agent=None, **payload):
    return ok(write(c, "POST", "/discussions", {"text": "Who is on the parser?", **payload}, agent))["discussion"]


def tag(c, discussion, kind, identity, agent=None):
    return write(c, "POST", f"/discussions/{discussion['id']}/tags", {"type": kind, "id": identity}, agent)


def untag(c, discussion, kind, identity, agent=None):
    return write(c, "POST", f"/discussions/{discussion['id']}/tags/remove", {"type": kind, "id": identity}, agent)


def tags(summary):
    return [(ctx["type"], ctx["id"]) for ctx in summary["contexts"]]


def records(c):
    """One record of every kind a discussion can be tagged with, as {kind: record}."""
    roadmap = ok(write(c, "POST", "/roadmaps", {"title": "Release", "steps": [{"title": "Cut the branch"}]}))
    return {
        "item": ok(write(c, "POST", "/work-items", {"title": "Fix the parser"})),
        "roadmap": roadmap,
        "step": roadmap["steps"][0],
        "repository": ok(write(c, "POST", "/repositories", {"name": "Worklog", "url": "https://example.com/acme/worklog"})),
        "ticket": ok(write(c, "POST", "/tickets", {"title": "Flaky import"})),
        "person": ok(write(c, "POST", "/people", {"name": "Ada"})),
        "resource": ok(write(c, "POST", "/resources", {"title": "How we release", "summary": "The steps"})),
        "question": ok(write(c, "POST", "/questions", {"roadmapId": roadmap["id"], "title": "Which build?"})),
        "decision": ok(write(c, "POST", "/decisions", {"roadmapId": roadmap["id"], "title": "Ship on Friday"})),
    }


def test_every_list_of_tag_kinds_agrees():
    assert discussions.TAG_KINDS == get_args(discussion_models.ContextType) == cli.DISCUSSION_TAG_KINDS
    assert discussions.ROLE_KINDS == get_args(discussion_models.RoleContextType) == cli.ROLE_CONTEXT_KINDS
    # Both tag CHECKs allow every kind, and 'release', which v28 retired (decision R5).
    for table in (migrations.DISCUSSION_CONTEXTS_TABLE, migrations.DISCUSSION_CHANGE_CONTEXTS_TABLE):
        allowed = re.search(r"CHECK\(context_type IN\(([^)]*)\)\)", table).group(1)
        assert {kind.strip("'") for kind in allowed.split(",")} == {*discussions.TAG_KINDS,
                                                                     *doctor.RETIRED["discussion_contexts"]}


def test_a_thread_is_tagged_with_a_record_of_every_kind_and_each_tag_says_what_it_names(client):
    made = records(client)
    builder = credential(client)
    started = start(client, title="Release plan", contexts=[{"type": "item", "id": made["item"]["id"]}])
    assert [(ctx["type"], ctx["addedBy"]) for ctx in started["contexts"]] == [("item", PERSON)]
    for kind, record in made.items():
        summary = ok(tag(client, started, kind, record["id"], builder))
        assert (kind, record["id"]) in tags(summary)
    summary = read(client, f"/discussions/{started['id']}")["discussion"]
    # Listed in the order of the kinds, each with its record's title and who added it.
    assert [ctx["type"] for ctx in summary["contexts"]] == list(discussions.TAG_KINDS)
    shown = {ctx["type"]: ctx for ctx in summary["contexts"]}
    for kind, record in made.items():
        assert shown[kind]["title"] == (record.get("title") or record.get("name"))
        assert shown[kind]["archived"] is False and shown[kind]["home"] is False
        assert shown[kind]["addedBy"] == (PERSON if kind == "item" else {"id": builder["agentId"], "label": "Builder", "kind": "agent"})
    assert (shown["step"]["roadmapId"], shown["step"]["roadmapTitle"]) == (made["roadmap"]["id"], "Release")
    assert (shown["ticket"]["number"], shown["ticket"]["status"]) == (1, "open")
    assert shown["question"]["roadmapId"] == shown["decision"]["roadmapId"] == made["roadmap"]["id"]
    assert "mergedInto" in shown["repository"]
    # A tag names a record and changes nothing about it.
    assert read(client, f"/work-items/{made['item']['id']}")["revision"] == made["item"]["revision"]


def test_a_tag_moves_the_revision_but_not_the_threads_place_and_a_repeat_changes_nothing(client):
    work = ok(write(client, "POST", "/work-items", {"title": "Fix the parser"}))
    older, newer = start(client, title="Older"), start(client, title="Newer")
    tagged = ok(tag(client, older, "item", work["id"]))
    assert tagged["revision"] == older["revision"] + 1 and tagged["updatedAt"] == older["updatedAt"]
    assert [d["id"] for d in read(client, "/discussions")["items"]] == [newer["id"], older["id"]]
    again = ok(tag(client, older, "item", work["id"]))
    assert again["revision"] == tagged["revision"]
    assert ok(untag(client, older, "roadmap", str(uuid.uuid4())))["revision"] == tagged["revision"]


def test_an_agent_takes_off_only_its_own_tags_and_the_person_takes_off_any(client):
    first, second = ok(write(client, "POST", "/work-items", {"title": "One"})), ok(write(client, "POST", "/work-items", {"title": "Two"}))
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    thread = start(client, builder, contexts=[{"type": "item", "id": first["id"]}])
    ok(tag(client, thread, "item", second["id"], reviewer))
    refused = error(untag(client, thread, "item", second["id"], builder), 403, "forbidden")
    assert refused["fields"] == {"contextId": "not_yours"}
    assert tags(ok(untag(client, thread, "item", second["id"], reviewer))) == [("item", first["id"])]
    ok(tag(client, thread, "item", second["id"], reviewer))
    # The person takes off a tag an agent added; the agent that added the first one may too.
    assert tags(ok(untag(client, thread, "item", second["id"]))) == [("item", first["id"])]
    assert tags(ok(untag(client, thread, "item", first["id"], builder))) == []


def test_a_ticket_is_named_by_its_key_or_number_and_kept_by_its_id(client):
    ticket = ok(write(client, "POST", "/tickets", {"title": "Flaky import"}))
    thread = start(client, contexts=[{"type": "ticket", "id": "T-1"}])
    assert tags(thread) == [("ticket", ticket["id"])]
    assert [d["id"] for d in read(client, "/discussions?contextType=ticket&contextId=1")["items"]] == [thread["id"]]
    untagged = ok(untag(client, thread, "ticket", "T-1"))
    assert tags(untagged) == []
    # A key that names no ticket is a record that doesn't exist, as an unknown id of any kind is.
    refused = error(tag(client, thread, "ticket", "T-9"), 422, "validation")
    assert refused["fields"] == {"contextId": "not_found"}
    assert read(client, "/discussions?contextType=ticket&contextId=T-9")["total"] == 0
    assert read(client, "/discussions/resume?contextType=ticket&contextId=T-9")["counts"]["discussions"] == 0
    assert ok(untag(client, thread, "ticket", "T-9"))["revision"] == untagged["revision"]


def test_a_tag_names_a_record_that_exists_and_a_merged_repository_answers_with_its_survivor(client):
    thread = start(client)
    error(tag(client, thread, "item", "not-a-record"), 422, "validation")
    error(write(client, "POST", f"/discussions/{thread['id']}/tags", {"type": "proposal", "id": str(uuid.uuid4())}), 422, "validation")
    source = ok(write(client, "POST", "/repositories", {"name": "Old", "url": "https://example.com/acme/old"}))
    survivor = ok(write(client, "POST", "/repositories", {"name": "New", "url": "https://example.com/acme/new"}))
    ok(write(client, "POST", f"/repositories/{source['id']}/merge",
             {"intoId": survivor["id"], "revision": source["revision"], "intoRevision": survivor["revision"]}))
    refused = error(tag(client, thread, "repository", source["id"]), 409, "repository_merged")
    assert refused["fields"] == {"repositoryId": survivor["id"]}
    # An archived record still takes a tag: a thread can be about it.
    archived = ok(write(client, "POST", "/work-items", {"title": "Done long ago"}))
    ok(write(client, "POST", f"/work-items/{archived['id']}/archive", {"revision": archived["revision"]}))
    assert ("item", archived["id"]) in tags(ok(tag(client, thread, "item", archived["id"])))


def test_a_thread_holds_at_most_thirty_tags(client):
    people = [ok(write(client, "POST", "/people", {"name": f"Person {n}"})) for n in range(31)]
    thread = start(client, contexts=[{"type": "person", "id": p["id"]} for p in people[:30]])
    assert len(thread["contexts"]) == 30
    refused = error(tag(client, thread, "person", people[30]["id"]), 422, "validation")
    assert refused["fields"] == {"contexts": "limit"}
    error(write(client, "POST", "/discussions", {"text": "Too many", "contexts": [{"type": "person", "id": p["id"]} for p in people]}), 422, "validation")


def test_filters_feeds_and_snapshots_follow_every_kind_and_an_untag_reaches_the_record_it_left(client):
    repository = ok(write(client, "POST", "/repositories", {"name": "Worklog", "url": "https://example.com/acme/worklog"}))
    resource = ok(write(client, "POST", "/resources", {"title": "Shipping", "summary": "How we ship"}))
    builder = credential(client)
    scope = f"contextType=resource&contextId={resource['id']}"
    cursor = read(client, "/discussions/changes?" + scope, builder)["cursor"]
    thread = start(client, builder, title="Ship notes", contexts=[{"type": "resource", "id": resource["id"]}])
    ok(tag(client, thread, "repository", repository["id"], builder))
    assert [d["id"] for d in read(client, "/discussions?" + scope, builder)["items"]] == [thread["id"]]
    resumed = read(client, "/discussions/resume?" + scope, builder)
    assert [d["id"] for d in resumed["discussions"]] == [thread["id"]] and resumed["roles"] == []
    ok(untag(client, thread, "resource", resource["id"], builder))
    assert read(client, "/discussions?" + scope, builder)["total"] == 0
    feed = read(client, f"/discussions/changes?{scope}&cursor={cursor}", builder)
    assert [item["kind"] for item in feed["items"]] == ["discussion.create", "discussion.tag", "discussion.untag"]
    repo_feed = read(client, f"/discussions/changes?contextType=repository&contextId={repository['id']}", builder)
    assert "discussion.tag" in [item["kind"] for item in repo_feed["items"]]
    # Roles stay on work items, roadmaps and steps.
    error(write(client, "POST", "/discussions/roles", {"contextType": "repository", "contextId": repository["id"], "label": "Review"}), 422, "validation")
    assert client.get(f"/api/v1/discussions/roles?contextType=repository&contextId={repository['id']}").status_code == 422


def test_free_text_repository_labels_take_no_new_writes_and_old_ones_stay_readable(client):
    refused = error(write(client, "POST", "/discussions", {"text": "Labelled", "repos": ["worklog"]}), 422, "validation")
    assert refused["fields"] == {"repos": "retired"}
    thread = start(client, title="Older thread")
    with client.app.state.db.transaction() as c:
        c.execute("INSERT INTO discussion_repos(discussion_id,repo) VALUES(?,?)", (thread["id"], "legacy-name"))
    listed = read(client, "/discussions?repo=legacy-name")
    assert [(d["id"], d["repos"]) for d in listed["items"]] == [(thread["id"], ["legacy-name"])]


def test_an_agents_message_in_a_repository_thread_ties_it_to_that_repository_and_other_kinds_tie_nothing(client):
    repository = ok(write(client, "POST", "/repositories", {"name": "Worklog", "url": "https://example.com/acme/worklog"}))
    ticket = ok(write(client, "POST", "/tickets", {"title": "Flaky import"}))
    builder = credential(client)
    start(client, builder, contexts=[{"type": "repository", "id": repository["id"]}, {"type": "ticket", "id": ticket["id"]}])
    agent = read(client, f"/agents/{builder['agentId']}")
    tied = {r["id"]: r["via"] for r in agent["repositories"]["items"]}
    assert tied == {repository["id"]: ["message"]}
