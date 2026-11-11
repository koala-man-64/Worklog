"""Before an agent changes a repository, work item, step or ticket, it checks who else works there and
what they announced (the agent guide's rule 11): `GET /coordination-overlaps`. The check gathers what
others already recorded, leaves out what the caller did itself, and reserves nothing."""

import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit

import pytest
from test_discussion_pins import pin
from test_discussion_tags import credential, error, ok, person, read, start, write
from test_repository_discussions import repository
from worklog import cli

KEYS = {"repositories", "participations", "announcements", "discussions", "roles", "tickets"}


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
    assert (found["discussions"], found["roles"], found["tickets"]) == ([], [], [])

    # What a caller did itself is left out of its own check.
    report(client, reviewer, repositories=[repo], description="Reviewing the parser")
    reply(client, repo["discussionId"], "Reviewing the parser", reviewer)
    assert [w["agent"]["id"] for w in check(client, reviewer, repos=[repo["id"]])["participations"]] == [builder["agentId"]]
    mine = check(client, builder, repos=[repo["id"]])
    assert [(w["agent"]["label"], w["description"]) for w in mine["participations"]] == [("Reviewer", "Reviewing the parser")]
    assert [a["authorLabel"] for a in mine["announcements"]] == ["Reviewer"]
    # The person checks too, and sees every agent's.
    assert {a["authorLabel"] for a in check(client, repos=[repo["id"]])["announcements"]} == {"Builder", "Reviewer"}
    # An announcement shows its first 1,000 characters, counted as characters, not bytes.
    reply(client, repo["discussionId"], "x" * 1000, builder)
    reply(client, repo["discussionId"], "é" * 1001, builder)
    longer, exact = check(client, reviewer, repos=[repo["id"]])["announcements"][:2]
    assert (longer["text"], longer["truncated"]) == ("é" * 1000, True)
    assert (exact["text"], exact["truncated"]) == ("x" * 1000, False)


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
    # Stale work is still listed, and its author no longer counts as reporting there until it reports again.
    stale = check(client, reviewer, repos=[repo["id"]])
    assert stale["participations"][0]["stale"] and [a["authorReporting"] for a in stale["announcements"]] == [False, False]
    checked = ok(write(client, "POST", f"/participations/{reported['id']}/check-in", {"revision": reported["revision"]}, builder))
    assert [a["authorReporting"] for a in check(client, reviewer, repos=[repo["id"]])["announcements"]] == [False, True]
    ok(write(client, "POST", f"/participations/{reported['id']}/release", {"revision": checked["revision"]}, builder))
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
    # A thread only the caller wrote in is left out of its own check, and shown to the others.
    own = start(client, reviewer, title="Reviewing the API", contexts=[{"type": "step", "id": step["id"]}])
    assert own["id"] not in [d["id"] for d in check(client, reviewer, steps=[step["id"]])["discussions"]]
    assert [d["title"] for d in check(client, builder, steps=[step["id"]])["discussions"]] == ["Reviewing the API"]
    reply(client, own["id"], "The routes look right", builder)
    assert own["id"] in [d["id"] for d in check(client, reviewer, steps=[step["id"]])["discussions"]]


def test_roles_and_ticket_assignees_name_the_agents_on_the_records(client):
    repo = repository(client)
    work = item(client)
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    role = ok(write(client, "POST", "/discussions/roles", {"contextType": "item", "contextId": work["id"], "label": "Implementation"}, builder))
    ok(write(client, "POST", f"/discussions/roles/{role['id']}/claim", {"revision": role["revision"]}, builder))
    ticket = ok(write(client, "POST", "/tickets", {"title": "Parser drops comments", "assigneeId": builder["agentId"]}))
    theirs = ok(write(client, "POST", "/tickets", {"title": "Rename the CLI", "assigneeId": "local-user"}))

    found = check(client, reviewer, repos=[repo["id"]], items=[work["id"]], tickets=[f"T-{ticket['number']}", theirs["id"]])
    assert [(r["label"], r["contextType"], r["contextId"], r["owner"]["id"]) for r in found["roles"]] == [
        ("Implementation", "item", work["id"], builder["agentId"])]
    assert [(t["id"], t["number"], t["title"], t["status"], t["assignee"]["id"]) for t in found["tickets"]] == [
        (ticket["id"], ticket["number"], "Parser drops comments", "open", builder["agentId"]),
        (theirs["id"], theirs["number"], "Rename the CLI", "open", "local-user")]
    # The builder doesn't see its own role.
    assert check(client, builder, items=[work["id"]])["roles"] == []
    # A ticket named by its number, its key and its ID is listed once.
    named = check(client, reviewer, tickets=[str(ticket["number"]), f"t-{ticket['number']}", ticket["id"]])
    assert ([t["id"] for t in named["tickets"]], named["discussions"]) == ([ticket["id"]], [])
    # A thread tagged with a named ticket is found by it.
    thread = start(client, builder, title="Comments in the parser", contexts=[{"type": "ticket", "id": ticket["id"]}])
    assert [d["id"] for d in check(client, reviewer, tickets=[str(ticket["number"])])["discussions"]] == [thread["id"]]


def test_a_check_names_records_that_exist_and_not_too_many_of_them(client):
    reviewer = credential(client, "Reviewer")
    error(overlaps(client, reviewer), 422, "validation")
    missing = str(uuid.uuid4())
    for named, fields in (({"repos": [missing]}, {"repositoryId": missing}), ({"items": [missing]}, {"itemId": missing}),
                          ({"steps": [missing]}, {"stepId": missing}), ({"tickets": ["T-99"]}, {"ticketId": "T-99"})):
        assert error(overlaps(client, reviewer, **named), 404, "not_found")["fields"] == fields
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
    # A revoked identity checks nothing.
    gone = credential(client, "Gone")
    ok(write(client, "POST", f"/credentials/{gone['id']}/revoke"))
    error(overlaps(client, gone, items=[work["id"]]), 401, "authentication")


def through(c, monkeypatch, agent):
    """Send the command line's requests to the test server, with the agent's credential."""
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", agent["token"])

    def forward(request, **options):
        url = urlsplit(request.full_url)
        response = c.request(request.get_method(), url.path + (f"?{url.query}" if url.query else ""),
                             headers=dict(request.header_items()), content=request.data)
        if response.status_code >= 400:
            raise HTTPError(request.full_url, response.status_code, "refused", response.headers, io.BytesIO(response.content))
        return io.BytesIO(response.content)

    monkeypatch.setattr(cli, "_open", forward)


def agent_command(capsys, *argv):
    code = cli.main(["agent", *argv])
    out, err = capsys.readouterr()
    return json.loads(out) if code == 0 else (code, err)


def test_rule_11_from_the_command_line_an_agent_asks_the_one_whose_work_overlaps_and_it_answers(client, monkeypatch, capsys):
    repo = repository(client)
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    report(client, builder, repositories=[repo])
    reply(client, repo["discussionId"], "Changing src/parser/lexer.py on claude/lexer", builder)

    # The reviewer checks, finds the builder's announcement, looks the builder up and asks it where it announced.
    through(client, monkeypatch, reviewer)
    [said] = agent_command(capsys, "overlaps", "--repo", repo["id"])["announcements"]
    profile = agent_command(capsys, "discussions", "participant", said["authorId"])
    assert (profile["label"], profile["active"], profile["working"][0]["contexts"][0]["repositoryId"]) == ("Builder", True, repo["id"])
    asked = agent_command(capsys, "discussions", "reply", said["discussionId"], "--text", "I change the lexer too; may I take the reader?",
                          "--ask", said["authorId"], "--request-id", "overlap-ask-1")
    assert [(t["kind"], t["recipientId"]) for t in asked["request"]["targets"]] == [("agent", builder["agentId"])]
    assert [d["id"] for d in read(client, "/discussions?view=awaiting", reviewer)["items"]] == [repo["discussionId"]]

    # The builder finds the request waiting on it, answers in the thread and acknowledges it.
    assert [d["id"] for d in read(client, "/discussions?view=awaiting", builder)["items"]] == [repo["discussionId"]]
    reply(client, repo["discussionId"], "Yes: I keep the lexer, you take the reader.", builder)
    slots = read(client, f"/discussions/messages/{asked['id']}", builder)["request"]["actionableTargetIds"]
    done = ok(write(client, "POST", f"/discussions/messages/{asked['id']}/acknowledge", {"targetIds": slots}, builder))
    assert done["request"]["state"] == "satisfied"
    assert read(client, "/discussions?view=awaiting", builder)["items"] == []

    # The person is asked by either name, and nobody can ask themselves.
    person_asked = agent_command(capsys, "discussions", "reply", repo["discussionId"], "--text", "Which of us takes the parser docs?",
                                 "--ask", "person", "--ask", "local-user", "--request-id", "overlap-ask-person")
    assert [(t["kind"], t["recipientId"]) for t in person_asked["request"]["targets"]] == [("human", "local-user")]
    assert [d["id"] for d in read(client, "/discussions?view=awaiting")["items"]] == [repo["discussionId"]]
    code, refused = agent_command(capsys, "discussions", "reply", repo["discussionId"], "--text", "Note to self",
                                  "--ask", reviewer["agentId"], "--request-id", "overlap-ask-self")
    assert code == 1 and "HTTP 422 validation: Nobody acknowledges their own message" in refused
    assert '"acknowledgement.targets.0.id":"self"' in refused
    mine = {"text": "Me?", "acknowledgement": {"mode": "any", "targets": [{"kind": "agent", "id": builder["agentId"]},
                                                                         {"kind": "human", "id": "local-user"}]}}
    assert error(write(client, "POST", f"/discussions/{repo['discussionId']}/messages", mine), 422, "validation")["fields"] == {
        "acknowledgement.targets.1.id": "self"}
