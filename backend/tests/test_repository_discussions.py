"""Every repository has its own discussion (schema v21): Worklog opens it, as the person, however the
repository is registered, keeps it through a rename and a recover, and folds it into the survivor's
threads on a merge. Its repository's tag stays on it, and the repository's records name it."""

import json

import pytest
from test_discussion_tags import credential, error, ok, person, read, tags, untag, write
from worklog import migrations
from worklog.discussions import REPOSITORY_TEXT, REPOSITORY_TITLE


@pytest.fixture
def client(tmp_path):
    with person(tmp_path) as c:
        yield c


def repository(c, name="Worklog", url=None, agent=None):
    return ok(write(c, "POST", "/repositories", {"name": name, "url": url or f"https://example.com/acme/{name.lower()}"}, agent))


def own(c, record):
    """The repository's own discussion, as a person reads it."""
    return read(c, f"/discussions/{record['discussionId']}")


def opened(c):
    return [d for d in read(c, "/discussions?limit=200")["items"] if d["repository"]]


def test_the_person_registering_a_repository_opens_its_discussion_and_its_records_name_it(client):
    created = repository(client)
    assert created["created"] and created["discussionId"]
    thread = own(client, created)
    assert thread["discussion"]["title"] == REPOSITORY_TITLE.format(name="Worklog") == "Repository: Worklog"
    assert thread["discussion"]["repository"] == {"id": created["id"], "name": "Worklog"}
    [home] = thread["discussion"]["contexts"]
    assert (home["type"], home["id"], home["home"], home["addedBy"]["id"]) == ("repository", created["id"], True, "local-user")
    [message] = thread["messages"]
    assert (message["authorId"], message["authorKind"]) == ("local-user", "human")
    assert message["text"] == REPOSITORY_TEXT.format(name="Worklog", id=created["id"])
    # The upgrade finds a discussion it opened again by how its first message begins and the
    # repository's ID in backticks.
    assert message["text"].startswith(migrations.REPOSITORY_DISCUSSION_OPENING) and f"(`{created['id']}`)" in message["text"]
    assert migrations.REPOSITORY_DISCUSSION_TITLE == REPOSITORY_TITLE
    assert read(client, f"/repositories/{created['id']}")["discussionId"] == created["discussionId"]
    assert [r["discussionId"] for r in read(client, "/repositories")["items"]] == [created["discussionId"]]
    assert read(client, f"/context?repositoryId={created['id']}")["repositories"]["items"][0]["discussionId"] == created["discussionId"]
    assert [d["id"] for d in read(client, f"/discussions?contextType=repository&contextId={created['id']}")["items"]] == [created["discussionId"]]
    assert [d["id"] for d in read(client, "/discussions?q=Repository%20Worklog")["items"]] == [created["discussionId"]]
    # Registering the same remote again opens nothing more.
    again = repository(client, "Worklog again", url="git@example.com:acme/worklog.git")
    assert (again["id"], again["created"], again["discussionId"]) == (created["id"], False, created["discussionId"])
    assert len(opened(client)) == 1


def test_an_agent_registering_a_repository_or_reporting_a_new_remote_opens_one_too(client):
    builder = credential(client)
    ensured = ok(write(client, "POST", "/repositories/ensure", {"url": "https://example.com/acme/parser"}, builder))
    assert ensured["created"] and own(client, ensured)["discussion"]["title"] == "Repository: parser"
    # Written as the person, as Worklog's notices are, whoever registered the repository.
    assert own(client, ensured)["messages"][0]["authorId"] == "local-user"
    again = ok(write(client, "POST", "/repositories/ensure", {"url": "git@example.com:acme/parser.git"}, builder))
    assert (again["created"], again["discussionId"]) == (False, ensured["discussionId"])
    run = ok(write(client, "POST", "/agent-runs", {"clientRunKey": "run-1", "displayName": "Parser"}, builder))
    ok(write(client, "POST", "/participations", {"runId": run["id"], "contexts": [{"repositoryUrl": "https://example.com/acme/lexer"}]}, builder))
    resolved = read(client, "/repositories/resolve?url=https://example.com/acme/lexer")["repository"]
    assert resolved["discussionId"] and own(client, resolved)["discussion"]["title"] == "Repository: lexer"
    assert sorted(d["title"] for d in opened(client)) == ["Repository: lexer", "Repository: parser"]


def test_an_approved_proposal_that_registers_a_repository_opens_its_discussion(client):
    builder = credential(client)
    draft = ok(write(client, "POST", "/proposals", {"title": "Register", "rationale": "", "operations": [
        {"action": "repository.create", "payload": {"name": "Docs", "url": "https://example.com/acme/docs"}}]}, builder))
    submitted = ok(write(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, builder))
    assert opened(client) == []
    ok(write(client, "POST", f"/proposals/{draft['id']}/approve", {"revision": submitted["revision"]}))
    [thread] = opened(client)
    assert thread["title"] == "Repository: Docs"


def test_a_rename_retitles_the_discussion_and_archiving_leaves_it_alone(client):
    created = repository(client)
    before = own(client, created)["discussion"]
    renamed = ok(write(client, "PATCH", f"/repositories/{created['id']}", {"revision": created["revision"], "name": "Worklog core"}))
    after = own(client, created)["discussion"]
    assert (after["title"], after["revision"], after["updatedAt"]) == ("Repository: Worklog core", before["revision"] + 1, before["updatedAt"])
    assert after["repository"] == {"id": created["id"], "name": "Worklog core"}
    assert [d["id"] for d in read(client, "/discussions?q=core")["items"]] == [created["discussionId"]]
    assert read(client, "/discussions?q=Repository%20Worklog%20core")["total"] == 1
    feed = read(client, f"/discussions/changes?contextType=repository&contextId={created['id']}")
    assert [item["kind"] for item in feed["items"]] == ["discussion.create", "discussion.retitle"]
    described = ok(write(client, "PATCH", f"/repositories/{created['id']}", {"revision": renamed["revision"], "description": "Core"}))
    assert own(client, created)["discussion"]["revision"] == after["revision"]
    archived = ok(write(client, "POST", f"/repositories/{created['id']}/archive", {"revision": described["revision"]}))
    assert own(client, created)["discussion"]["archived"] is False
    recovered = ok(write(client, "POST", f"/repositories/{created['id']}/recover", {"revision": archived["revision"]}))
    assert recovered["discussionId"] == created["discussionId"] and len(opened(client)) == 1


def test_recovering_a_repository_that_has_no_discussion_opens_one(client):
    # As one archived when the upgrade opened the others' discussions would be.
    created = repository(client)
    archived = ok(write(client, "POST", f"/repositories/{created['id']}/archive", {"revision": created["revision"]}))
    with client.app.state.db.transaction() as c:
        c.execute("DELETE FROM discussion_contexts WHERE discussion_id=?", (created["discussionId"],))
    assert read(client, f"/repositories/{created['id']}")["discussionId"] is None
    recovered = ok(write(client, "POST", f"/repositories/{created['id']}/recover", {"revision": archived["revision"]}))
    assert recovered["discussionId"] not in (None, created["discussionId"])
    assert own(client, recovered)["discussion"]["title"] == "Repository: Worklog"


def test_a_repositorys_discussion_tagged_with_another_repository_stays_its_own(client):
    created, other = repository(client, "Worklog"), repository(client, "Docs")
    builder = credential(client)
    tagged = ok(write(client, "POST", f"/discussions/{created['discussionId']}/tags", {"type": "repository", "id": other["id"]}, builder))
    assert tagged["repository"] == {"id": created["id"], "name": "Worklog"}
    assert [(ctx["id"], ctx["home"]) for ctx in tagged["contexts"]] == sorted([(created["id"], True), (other["id"], False)])
    assert read(client, f"/repositories/{other['id']}")["discussionId"] == other["discussionId"]
    listed = read(client, f"/discussions?contextType=repository&contextId={other['id']}")
    assert {d["id"] for d in listed["items"]} == {other["discussionId"], created["discussionId"]}
    assert tags(ok(untag(client, {"id": created["discussionId"]}, "repository", other["id"], builder))) == [("repository", created["id"])]


def test_nobody_takes_a_repositorys_tag_off_its_own_discussion(client):
    created = repository(client)
    builder = credential(client)
    thread = {"id": created["discussionId"]}
    for agent in (None, builder):
        refused = error(untag(client, thread, "repository", created["id"], agent), 403, "forbidden")
        assert refused["fields"] == {"contextId": "home"}
    other = ok(write(client, "POST", "/work-items", {"title": "Parser"}))
    ok(write(client, "POST", f"/discussions/{created['discussionId']}/tags", {"type": "item", "id": other["id"]}, builder))
    assert tags(ok(untag(client, thread, "item", other["id"], builder))) == [("repository", created["id"])]


def test_a_merge_tags_the_merged_repositorys_threads_with_the_survivor_which_keeps_its_own_discussion(client):
    source, survivor = repository(client, "Old"), repository(client, "New")
    builder = credential(client)
    only_old = ok(write(client, "POST", "/discussions", {"text": "About old", "contexts": [{"type": "repository", "id": source["id"]}]}, builder))["discussion"]
    both = ok(write(client, "POST", "/discussions", {"text": "About both", "contexts": [
        {"type": "repository", "id": source["id"]}, {"type": "repository", "id": survivor["id"]}]}, builder))["discussion"]
    merged = ok(write(client, "POST", f"/repositories/{source['id']}/merge",
                      {"intoId": survivor["id"], "revision": source["revision"], "intoRevision": survivor["revision"]}))
    assert merged["discussionId"] is None
    assert read(client, f"/repositories/{survivor['id']}")["discussionId"] == survivor["discussionId"]
    old_home = own(client, source)["discussion"]
    assert (old_home["repository"], tags(old_home)) == (None, [("repository", survivor["id"])])
    assert tags(read(client, f"/discussions/{only_old['id']}")["discussion"]) == [("repository", survivor["id"])]
    assert tags(read(client, f"/discussions/{both['id']}")["discussion"]) == [("repository", survivor["id"])]
    # Moved without a new revision, as a merge moves every link.
    assert read(client, f"/discussions/{only_old['id']}")["discussion"]["revision"] == only_old["revision"]
    listed = read(client, f"/discussions?contextType=repository&contextId={survivor['id']}&limit=200")
    assert {d["id"] for d in listed["items"]} == {survivor["discussionId"], source["discussionId"], only_old["id"], both["id"]}
    assert read(client, f"/discussions?contextType=repository&contextId={source['id']}")["total"] == 0
    # The old discussion is no longer a repository's own, so the person can take the tag off it.
    assert tags(ok(untag(client, {"id": source["discussionId"]}, "repository", survivor["id"]))) == []
    with client.app.state.db.read() as c:
        history = c.execute("SELECT after_json FROM events WHERE kind='repository.merge' AND entity_id=?", (source["id"],)).fetchone()[0]
    assert json.loads(history)["moved"]["discussions"] == 3
