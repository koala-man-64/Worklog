"""Before an agent changes a repository, work item, step or ticket, it checks who else works there and
what they announced (the agent guide's rule 11): `GET /coordination-overlaps`. The check gathers what
others already recorded, leaves out what the caller did itself, and reserves nothing."""

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from test_discussion_pins import pin
from test_discussion_tags import credential, error, ok, person, read, start, write
from test_repository_discussions import repository

KEYS = {"repositories", "participations", "announcements", "discussions", "roles", "ties", "tickets"}


@pytest.fixture
def client(tmp_path):
    with person(tmp_path) as c:
        yield c


def overlaps(c, agent=None, *, repos=(), items=(), steps=(), tickets=(), limit=None):
    query = [("repositoryId", r) for r in repos] + [("itemId", i) for i in items]
    query += [("stepId", s) for s in steps] + [("ticketId", t) for t in tickets]
    query += [("limit", limit)] if limit is not None else []
    return c.get("/api/v1/coordination-overlaps?" + urlencode(query),
                 headers={"Authorization": "Bearer " + agent["token"]} if agent else {})


def check(c, agent=None, **named):
    found = ok(overlaps(c, agent, **named))
    assert set(found) == KEYS
    return found


def report(c, agent, *, item=None, repositories=(), description="Changing the parser", branch="claude/parser"):
    running = ok(write(c, "POST", "/agent-runs", {"clientRunKey": str(uuid.uuid4()), "displayName": "Implementation"}, agent))
    return ok(write(c, "POST", "/participations", {
        "runId": running["id"], "description": description, "itemId": item and item["id"],
        "contexts": [{"repositoryId": r["id"], "branch": branch} for r in repositories]}, agent))


def reply(c, discussion_id, text, agent=None):
    return ok(write(c, "POST", f"/discussions/{discussion_id}/messages", {"text": text}, agent))


def item(c, title="Fix the parser", **fields):
    return ok(write(c, "POST", "/work-items", {"title": title, **fields}))


def who(credential, label):
    return {"id": credential["agentId"], "label": label, "kind": "agent", "avatar": credential["avatar"], "active": True}


def test_an_agent_sees_who_else_works_in_a_repository_and_what_they_announced(client):
    repo = repository(client)
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    builder["avatar"] = {p["id"]: p for p in read(client, "/discussions/participants")["items"]}[builder["agentId"]]["avatar"]
    reported = report(client, builder, repositories=[repo])
    announced = reply(client, repo["discussionId"], "Changing the parser on claude/parser", builder)

    found = check(client, reviewer, repos=[repo["id"]])
    assert found["repositories"] == [{"id": repo["id"], "name": "Worklog", "discussionId": repo["discussionId"]}]
    [working] = found["participations"]
    assert (working["participationId"], working["agent"], working["runName"]) == (reported["id"], who(builder, "Builder"), "Implementation")
    assert (working["description"], working["stale"], working["item"]) == ("Changing the parser", False, None)
    assert [(ctx["repositoryId"], ctx["branch"]) for ctx in working["contexts"]] == [(repo["id"], "claude/parser")]
    # Worklog's opening message is not an announcement.
    [said] = found["announcements"]
    assert said == {"repositoryId": repo["id"], "discussionId": repo["discussionId"], "messageId": announced["id"],
                    "author": who(builder, "Builder"), "authorId": builder["agentId"], "authorLabel": "Builder",
                    "authorReporting": True, "createdAt": announced["createdAt"],
                    "text": "Changing the parser on claude/parser", "truncated": False}
    # The repository's own discussion comes with it, not again among the threads.
    assert (found["discussions"], found["roles"], found["ties"], found["tickets"]) == ([], [], [], [])

    # What a caller did itself is left out of its own check.
    report(client, reviewer, repositories=[repo], description="Reviewing the parser")
    reply(client, repo["discussionId"], "Reviewing the parser", reviewer)
    assert [w["agent"]["id"] for w in check(client, reviewer, repos=[repo["id"]])["participations"]] == [builder["agentId"]]
    mine = check(client, builder, repos=[repo["id"]])
    assert [(w["agent"]["label"], w["description"]) for w in mine["participations"]] == [("Reviewer", "Reviewing the parser")]
    assert [a["authorLabel"] for a in mine["announcements"]] == ["Reviewer"]
    # The person checks too, and sees every agent's.
    assert {a["authorLabel"] for a in check(client, repos=[repo["id"]])["announcements"]} == {"Builder", "Reviewer"}


def test_each_announcement_says_whether_its_author_is_still_at_it(client):
    repo = repository(client)
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    reported = report(client, builder, repositories=[repo])
    reply(client, repo["discussionId"], "Changing the parser", builder)
    reply(client, repo["discussionId"], "Keep the lexer as it is")
    found = check(client, reviewer, repos=[repo["id"]])
    # Newest first; the person reports no work anywhere.
    assert [(a["authorLabel"], a["author"]["kind"], a["authorReporting"]) for a in found["announcements"]] == [
        ("You", "human", False), ("Builder", "agent", True)]

    with client.app.state.db.transaction() as c:
        c.execute("UPDATE participations SET last_seen_at=?", ((datetime.now(UTC) - timedelta(hours=3)).isoformat(),))
    assert check(client, reviewer, repos=[repo["id"]])["participations"][0]["stale"]
    ok(write(client, "POST", f"/participations/{reported['id']}/release", {"revision": reported["revision"]}, builder))
    found = check(client, reviewer, repos=[repo["id"]])
    assert found["participations"] == []
    assert [a["authorReporting"] for a in found["announcements"]] == [False, False]
    # Work under a run that ended is finished too.
    again = report(client, builder, repositories=[repo])
    assert [w["participationId"] for w in check(client, reviewer, repos=[repo["id"]])["participations"]] == [again["id"]]
    ended = read(client, f"/agent-runs/{again['runId']}")
    ok(write(client, "POST", f"/agent-runs/{again['runId']}/end", {"revision": ended["revision"]}, builder))
    assert check(client, reviewer, repos=[repo["id"]])["participations"] == []
    # An author whose credential is revoked is no longer active.
    ok(write(client, "POST", f"/credentials/{builder['id']}/revoke"))
    assert check(client, reviewer, repos=[repo["id"]])["announcements"][1]["author"]["active"] is False


def test_work_on_named_items_or_on_a_steps_items_and_threads_tagged_with_them_are_found(client):
    roadmap = ok(write(client, "POST", "/roadmaps", {"title": "Platform", "steps": [{"title": "API"}]}))
    step = roadmap["steps"][0]
    on_step = item(client, "Build the API", roadmapId=roadmap["id"], stepId=step["id"])
    elsewhere = item(client, "Write the docs")
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    report(client, builder, item=on_step, description="Writing routes")

    by_step = check(client, reviewer, steps=[step["id"]])
    assert [(w["agent"]["label"], w["item"]) for w in by_step["participations"]] == [
        ("Builder", {"id": on_step["id"], "title": "Build the API", "status": on_step["status"]})]
    assert (by_step["repositories"], by_step["announcements"]) == ([], [])
    assert len(check(client, reviewer, items=[on_step["id"]])["participations"]) == 1
    assert check(client, reviewer, items=[elsewhere["id"]])["participations"] == []

    older = start(client, builder, title="Splitting the API", contexts=[{"type": "item", "id": on_step["id"]}])
    start(client, builder, title="API naming", contexts=[{"type": "step", "id": step["id"]}, {"type": "item", "id": elsewhere["id"]}])
    gone = start(client, builder, title="Old API plan", contexts=[{"type": "step", "id": step["id"]}])
    start(client, builder, title="Docs only", contexts=[{"type": "item", "id": elsewhere["id"]}])
    ok(write(client, "POST", f"/discussions/{gone['id']}/archive", {"revision": gone["revision"], "archived": True}))
    ok(pin(client, older))
    found = check(client, reviewer, items=[on_step["id"]], steps=[step["id"]])
    # Pinned first, then the latest; archived threads are left out, and each says which named record it carries.
    assert [(d["title"], d["pinned"], d["matched"]) for d in found["discussions"]] == [
        ("Splitting the API", True, [{"type": "item", "id": on_step["id"]}]),
        ("API naming", False, [{"type": "step", "id": step["id"]}])]
    assert [d["title"] for d in check(client, reviewer, items=[on_step["id"]], steps=[step["id"]], limit=1)["discussions"]] == [
        "Splitting the API"]


def test_roles_ties_and_ticket_assignees_name_the_agents_on_the_records(client):
    repo = repository(client)
    work = item(client)
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    role = ok(write(client, "POST", "/discussions/roles", {"contextType": "item", "contextId": work["id"], "label": "Implementation"}, builder))
    ok(write(client, "POST", f"/discussions/roles/{role['id']}/claim", {"revision": role["revision"]}, builder))
    ok(write(client, "POST", "/agent-links", {"targetType": "repository", "targetId": repo["id"], "relationship": "Maintainer"}, builder))
    ok(write(client, "POST", "/agent-links", {"targetType": "item", "targetId": work["id"], "relationship": "Reviewer"}, reviewer))
    ticket = ok(write(client, "POST", "/tickets", {"title": "Parser drops comments", "assigneeId": builder["agentId"]}))
    theirs = ok(write(client, "POST", "/tickets", {"title": "Rename the CLI", "assigneeId": "local-user"}))

    found = check(client, reviewer, repos=[repo["id"]], items=[work["id"]], tickets=[f"T-{ticket['number']}", theirs["id"]])
    assert [(r["label"], r["contextType"], r["contextId"], r["owner"]["id"]) for r in found["roles"]] == [
        ("Implementation", "item", work["id"], builder["agentId"])]
    # The reviewer's own tie is left out of its check.
    assert [(t["agent"]["id"], t["targetType"], t["targetId"], t["relationship"], t["setBy"]) for t in found["ties"]] == [
        (builder["agentId"], "repository", repo["id"], "Maintainer", "agent")]
    assert [(t["id"], t["number"], t["title"], t["status"], t["assignee"]["id"]) for t in found["tickets"]] == [
        (ticket["id"], ticket["number"], "Parser drops comments", "open", builder["agentId"]),
        (theirs["id"], theirs["number"], "Rename the CLI", "open", "local-user")]
    # The builder sees the reviewer's tie and not its own role.
    mine = check(client, builder, items=[work["id"]])
    assert (mine["roles"], [t["agent"]["id"] for t in mine["ties"]]) == ([], [reviewer["agentId"]])
    # A ticket named by its number, its key and its ID is listed once.
    named = check(client, reviewer, tickets=[str(ticket["number"]), f"t-{ticket['number']}", ticket["id"]])
    assert ([t["id"] for t in named["tickets"]], named["discussions"]) == ([ticket["id"]], [])
    # A thread tagged with a named ticket is found by it.
    thread = start(client, builder, title="Comments in the parser", contexts=[{"type": "ticket", "id": ticket["id"]}])
    assert [d["id"] for d in check(client, reviewer, tickets=[str(ticket["number"])])["discussions"]] == [thread["id"]]


def test_a_check_names_records_that_exist_and_not_too_many_of_them(client):
    reviewer = credential(client, "Reviewer")
    error(overlaps(client, reviewer), 422, "validation")
    for named in ({"repos": [str(uuid.uuid4())]}, {"items": [str(uuid.uuid4())]}, {"steps": [str(uuid.uuid4())]},
                  {"tickets": ["T-99"]}):
        error(overlaps(client, reviewer, **named), 404, "not_found")
    many = [str(uuid.uuid4()) for _ in range(21)]
    assert error(overlaps(client, reviewer, items=many), 422, "validation")["fields"] == {"itemId": "limit"}
    error(overlaps(client, reviewer, items=["   "]), 422, "validation")
    work = item(client)
    assert overlaps(client, reviewer, items=[work["id"]], limit=51).status_code == 422
    assert overlaps(client, reviewer, items=[work["id"]], limit=0).status_code == 422
    # A merged repository names the one to check instead.
    old, new = repository(client, "Old"), repository(client, "New")
    ok(write(client, "POST", f"/repositories/{old['id']}/merge",
             {"intoId": new["id"], "revision": old["revision"], "intoRevision": new["revision"]}))
    assert error(overlaps(client, reviewer, repos=[old["id"]]), 409, "repository_merged")["fields"] == {"repositoryId": new["id"]}
    # A check writes nothing: the discussion feed stands where it stood.
    before = read(client, "/discussions/changes", reviewer)
    check(client, reviewer, repos=[new["id"]], items=[work["id"]])
    assert read(client, "/discussions/changes", reviewer) == before
