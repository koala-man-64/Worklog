"""One proposal creates records and connects them: a create names its new record, such as @jane,
and later changes in the same proposal use the name where they would give the record's ID."""

import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.operations import CREATE_ID

ORIGIN = "http://127.0.0.1:8877"
REMOTE = "https://github.com/acme/payments-api"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def call(c, method, path, payload=None, agent=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def refused(response, status=422):
    assert response.status_code == status, response.text
    return response.json()["error"]


@pytest.fixture
def agent(client):
    return ok(call(client, "POST", "/credentials", {"label": "Onboarder"}))


def draft(c, agent, *operations):
    return call(c, "POST", "/proposals", {"title": "Bring in payments", "rationale": "", "operations": list(operations)}, agent)


def submitted(c, agent, *operations):
    proposal = ok(draft(c, agent, *operations))
    return ok(call(c, "POST", f"/proposals/{proposal['id']}/submit", {"revision": proposal["revision"]}, agent))


def approve(c, proposal):
    return call(c, "POST", f"/proposals/{proposal['id']}/approve", {"revision": proposal["revision"]})


def test_one_proposal_creates_people_repositories_work_tickets_and_pages_and_connects_them(client, agent, tmp_path):
    proposal = submitted(
        client, agent,
        {"action": "person.create", "payload": {"id": "@jane", "name": "Jane Doe", "team": "Payments"}},
        {"action": "repository.create", "payload": {"id": "@api", "name": "payments-api", "url": REMOTE}},
        {"action": "item.create", "payload": {"id": "@fix", "title": "Fix settlement rounding", "repositoryIds": ["@api"]}},
        {"action": "person.link", "payload": {"personId": "@jane", "targetType": "repository", "targetId": "@api",
                                              "relationship": "Owner"}},
        {"action": "person.link", "payload": {"personId": "@jane", "targetType": "item", "targetId": "@fix",
                                              "relationship": "Reviewer"}},
        {"action": "ticket.create", "payload": {"id": "@bug", "title": "Settlement rounds half-cents down",
                                                "itemId": "@fix", "repositoryIds": ["@api"], "labels": ["payments"]}},
        {"action": "resource.create", "payload": {"id": "@runbook", "title": "Settlement runbook",
                                                  "summary": "How to rerun a settlement", "body": "Rerun with `--day`."}},
        {"action": "note.create", "payload": {"ownerType": "item", "ownerId": "@fix", "title": "Repro", "text": "Pay 0.005."}},
    )
    assert ok(approve(client, proposal))["status"] == "approved"

    (person,) = ok(call(client, "GET", "/people"))["items"]
    (repository,) = ok(call(client, "GET", "/repositories"))["items"]
    (item,) = ok(call(client, "GET", "/work-items"))["items"]
    tied = ok(call(client, "GET", f"/people/{person['id']}"))
    assert [(r["repositoryId"], r["relationship"]) for r in tied["repositories"]] == [(repository["id"], "Owner")]
    assert [(w["itemId"], w["relationship"]) for w in tied["workItems"]] == [(item["id"], "Reviewer")]
    work = ok(call(client, "GET", f"/work-items/{item['id']}"))
    assert work["repositoryIds"] == [repository["id"]]
    (scratchpad,) = work["notes"]
    assert "## Repro" in scratchpad["text"] and "Pay 0.005." in scratchpad["text"]
    (ticket,) = ok(call(client, "GET", f"/tickets?itemId={item['id']}"))["items"]
    assert (ticket["repositoryIds"], ticket["labels"]) == ([repository["id"]], ["payments"])
    # A ticket names its author by the stable identity, as one the agent files itself does.
    assert ticket["author"]["id"] == agent["agentId"] and ticket["author"]["kind"] == "agent"
    (page,) = ok(call(client, "GET", "/resources"))["items"]
    history = ok(call(client, "GET", f"/resources/{page['id']}"))["history"]
    assert [(e["kind"], e["actorKind"], e["actorLabel"]) for e in history] == [("resource.create", "agent", "Onboarder")]

    # The proposal keeps its names, as it was proposed; what it applied to has the IDs.
    stored = ok(call(client, "GET", f"/proposals/{proposal['id']}"))
    assert stored["operations"][0]["payload"]["id"] == "@jane"
    applied = ok(call(client, "GET", f"/work-items/{item['id']}/proposals"))["items"]
    assert [c["action"] for c in applied[0]["changes"]] == ["item.create", "person.link", "ticket.create", "note.create"]
    with closing(sqlite3.connect(tmp_path / "worklog.sqlite3")) as conn:
        assert conn.execute("SELECT count(*) FROM proposal_targets WHERE target_id LIKE '@%'").fetchone()[0] == 0


ITEM = str(uuid.uuid4())
REFUSED = [
    ("dangling", [{"action": "person.link", "payload": {"personId": "@jane", "targetType": "item", "targetId": ITEM}}],
     "Change 1 of 1, person.link: personId names @jane, which no earlier change in this proposal creates",
     {"personId": "unknown_name"}),
    ("used before its create", [{"action": "item.update", "payload": {"id": "@fix", "revision": 1, "title": "Later"}},
                                {"action": "item.create", "payload": {"id": "@fix", "title": "First"}}],
     "Change 1 of 2, item.update: id names @fix, which no earlier change in this proposal creates", {"id": "unknown_name"}),
    ("its own name", [{"action": "roadmap.create", "payload": {"id": "@plan", "title": "Plan", "parentId": "@plan"}}],
     "Change 1 of 1, roadmap.create: parentId names @plan, which no earlier change in this proposal creates",
     {"parentId": "unknown_name"}),
    ("declared twice", [{"action": "person.create", "payload": {"id": "@jane", "name": "Jane Doe"}},
                        {"action": "person.create", "payload": {"id": "@jane", "name": "Jane Roe"}}],
     "Change 2 of 2, person.create: @jane already names the record change 1 creates; give each new record its own name",
     {"id": "duplicate_name"}),
    ("wrong kind", [{"action": "person.create", "payload": {"id": "@jane", "name": "Jane Doe"}},
                    {"action": "task.create", "payload": {"itemId": "@jane", "text": "Call her"}}],
     "Change 2 of 2, task.create: itemId names @jane, which change 1 creates as a person, not a work item",
     {"itemId": "wrong_kind"}),
    ("wrong kind in a list", [{"action": "person.create", "payload": {"id": "@jane", "name": "Jane Doe"}},
                              {"action": "ticket.create", "payload": {"title": "Flaky", "repositoryIds": ["@jane"]}}],
     "Change 2 of 2, ticket.create: repositoryIds names @jane, which change 1 creates as a person, not a repository",
     {"repositoryIds": "wrong_kind"}),
    ("wrong kind by type", [{"action": "roadmap.create", "payload": {"id": "@plan", "title": "Plan"}},
                            {"action": "link.create", "payload": {"ownerType": "item", "ownerId": "@plan", "url": "https://example.com"}}],
     "Change 2 of 2, link.create: ownerId names @plan, which change 1 creates as a roadmap, not a work item",
     {"ownerId": "wrong_kind"}),
    # The owner's or target's type is checked only when the change applies, so it may be any JSON value here.
    ("a list as the owner's type", [{"action": "person.create", "payload": {"id": "@jane", "name": "Jane Doe"}},
                                    {"action": "link.create", "payload": {"ownerType": ["item"], "ownerId": "@jane",
                                                                          "url": "https://example.com"}}],
     "Change 2 of 2, link.create: ownerId names @jane, but a record of that type can't be named", {"ownerId": "wrong_kind"}),
    ("an object as the target's type", [{"action": "person.create", "payload": {"id": "@jane", "name": "Jane Doe"}},
                                        {"action": "person.link", "payload": {"personId": ITEM, "targetType": {"kind": "item"},
                                                                              "targetId": "@jane"}}],
     "Change 2 of 2, person.link: targetId names @jane, but a record of that type can't be named", {"targetId": "wrong_kind"}),
    # It lands in the scratchpad, so a name would stand for the scratchpad, which other notes change too.
    ("a note on a work item", [{"action": "item.create", "payload": {"id": "@fix", "title": "Fix"}},
                               {"action": "note.create", "payload": {"id": "@note", "ownerType": "item", "ownerId": "@fix",
                                                                     "text": "Found it"}}],
     "Change 2 of 2, note.create: A note on a work item joins its one scratchpad rather than making a record of its own, "
     "so it can't be named", {"id": "not_nameable"}),
    ("a step", [{"action": "roadmap.create", "payload": {"id": "@plan", "title": "Plan"}},
                {"action": "note.create", "payload": {"ownerType": "step", "ownerId": "@plan", "text": "x"}}],
     "Change 2 of 2, note.create: ownerId names @plan, but a step can't be named: give the step's own ID, which "
     "roadmap.create and roadmap.revise take", {"ownerId": "wrong_kind"}),
    ("an ID on a create", [{"action": "item.create", "payload": {"id": ITEM, "title": "Mine"}}],
     f"Change 1 of 1, item.create: {CREATE_ID}", {"id": "not_a_name"}),
    ("a broken name on a create", [{"action": "person.create", "payload": {"id": "@jane doe", "name": "Jane Doe"}}],
     f"Change 1 of 1, person.create: {CREATE_ID}", {"id": "not_a_name"}),
    ("a broken name in use", [{"action": "person.create", "payload": {"id": "@jane", "name": "Jane Doe"}},
                              {"action": "person.update", "payload": {"id": "@-jane", "revision": 1}}],
     "Change 2 of 2, person.update: id '@-jane' is not a name: use @ and up to 100 letters, digits, '.', '_' or '-', "
     "starting with a letter or digit", {"id": "invalid_name"}),
    ("a file's copy", [{"action": "attachment.create", "payload": {"id": "@copy", "ownerType": "item", "ownerId": ITEM,
                                                                   "attachmentId": "a"}}],
     "Invalid fields for attachment.create: not accepted id", None),
    ("a person's tie", [{"action": "person.link", "payload": {"id": "@tie", "personId": ITEM, "targetType": "item",
                                                              "targetId": ITEM}}],
     "Invalid fields for person.link: not accepted id", None),
]


@pytest.mark.parametrize("operations,message,fields", [case[1:] for case in REFUSED], ids=[case[0] for case in REFUSED])
def test_a_name_is_declared_once_before_it_is_used_and_names_the_kind_the_field_takes(client, agent, operations, message, fields):
    error = refused(draft(client, agent, *operations))
    assert error["code"] == "validation" and error["message"] == message
    if fields is not None:
        assert error["fields"]["fields"] == fields
    # Editing a draft refuses the same, so no stored proposal holds one.
    proposal = ok(draft(client, agent, {"action": "item.create", "payload": {"title": "Fine"}}))
    error = refused(call(client, "PATCH", f"/proposals/{proposal['id']}", {"revision": proposal["revision"],
                                                                           "operations": operations}, agent))
    assert error["message"] == message


def test_later_changes_change_what_earlier_ones_created_and_github_references_still_apply_last(client, agent):
    proposal = submitted(
        client, agent,
        {"action": "item.create", "payload": {"id": "@fix", "title": "Fix settlement rounding"}},
        # Listed second, applied last, as every GitHub reference's change is; its name is known by then.
        {"action": "github.create", "payload": {"ownerType": "item", "ownerId": "@fix", "kind": "pr",
                                                "url": "https://github.com/acme/payments-api/pull/7"}},
        {"action": "ticket.create", "payload": {"id": "@bug", "title": "Rounding", "itemId": "@fix"}},
        {"action": "ticket.update", "payload": {"id": "@bug", "revision": 1, "status": "doing"}},
        {"action": "task.create", "payload": {"itemId": "@fix", "text": "Add a test"}},
    )
    ok(approve(client, proposal))
    (item,) = ok(call(client, "GET", "/work-items"))["items"]
    work = ok(call(client, "GET", f"/work-items/{item['id']}"))
    assert [g["url"] for g in work["github"]] == ["https://github.com/acme/payments-api/pull/7"]
    assert [t["text"] for t in work["tasks"]] == ["Add a test"]
    (ticket,) = ok(call(client, "GET", "/tickets"))["items"]
    assert (ticket["itemId"], ticket["status"], ticket["revision"]) == (item["id"], "doing", 2)


def test_the_person_s_own_proposal_files_tickets_and_writes_pages_as_the_person(client):
    proposal = submitted(
        client, None,
        {"action": "ticket.create", "payload": {"title": "Mine"}},
        {"action": "resource.create", "payload": {"title": "My page"}},
    )
    ok(approve(client, proposal))
    (ticket,) = ok(call(client, "GET", "/tickets"))["items"]
    assert (ticket["author"]["id"], ticket["author"]["kind"]) == ("local-user", "human")
    (page,) = ok(call(client, "GET", "/resources"))["items"]
    assert [e["actorKind"] for e in ok(call(client, "GET", f"/resources/{page['id']}"))["history"]] == ["human"]


def test_a_name_on_a_registered_remote_points_at_the_repository_already_there(client, agent):
    held = ok(call(client, "POST", "/repositories", {"name": "payments", "url": REMOTE}))
    proposal = submitted(
        client, agent,
        {"action": "repository.create", "payload": {"id": "@api", "name": "payments-api", "url": REMOTE + ".git"}},
        {"action": "item.create", "payload": {"title": "Fix settlement rounding", "repositoryIds": ["@api"]}},
    )
    ok(approve(client, proposal))
    assert [r["id"] for r in ok(call(client, "GET", "/repositories"))["items"]] == [held["id"]]
    (item,) = ok(call(client, "GET", "/work-items"))["items"]
    assert item["repositoryIds"] == [held["id"]]


def test_a_failing_change_applies_none_of_the_records_the_proposal_names(client, agent):
    first = ok(call(client, "POST", "/tickets", {"title": "Already filed"}))
    proposal = submitted(
        client, agent,
        {"action": "person.create", "payload": {"id": "@jane", "name": "Jane Doe"}},
        {"action": "repository.create", "payload": {"id": "@api", "name": "payments-api", "url": REMOTE}},
        {"action": "ticket.create", "payload": {"title": "Rounding", "repositoryIds": ["@api"]}},
        {"action": "resource.create", "payload": {"title": "Settlement runbook"}},
        # Done needs a resolution, which only applying the ticket checks.
        {"action": "ticket.create", "payload": {"title": "Closed already", "status": "done"}},
    )
    error = refused(approve(client, proposal))
    assert error["message"] == "Change 5 of 5, ticket.create: Write a resolution before marking the ticket done"
    assert ok(call(client, "GET", "/people"))["items"] == []
    assert ok(call(client, "GET", "/repositories"))["items"] == []
    assert ok(call(client, "GET", "/resources"))["items"] == []
    assert [t["id"] for t in ok(call(client, "GET", "/tickets"))["items"]] == [first["id"]]
    # The ticket number the failed approval took went back with it.
    assert ok(call(client, "POST", "/tickets", {"title": "Next"}))["key"] == "T-2"
    assert ok(call(client, "GET", f"/proposals/{proposal['id']}"))["status"] == "submitted"


def test_with_approval_off_names_and_resource_changes_apply_on_submit_and_stay_in_history(client, agent):
    page = ok(call(client, "POST", "/resources", {"title": "Release checklist", "body": "Run the tests."}))
    ok(call(client, "PATCH", "/settings", {"requireProposalApproval": False}))
    proposal = submitted(
        client, agent,
        {"action": "resource.update", "payload": {"id": page["id"], "revision": 1, "body": "Run the tests twice."}},
        {"action": "resource.create", "payload": {"id": "@new", "title": "Hotfix checklist"}},
        {"action": "resource.archive", "payload": {"id": "@new", "revision": 1}},
        {"action": "resource.recover", "payload": {"id": "@new", "revision": 2}},
    )
    assert (proposal["status"], proposal["autoApproved"]) == ("approved", True)
    changed = ok(call(client, "GET", f"/resources/{page['id']}"))
    assert (changed["body"], changed["revision"]) == ("Run the tests twice.", 2)
    assert [(e["kind"], e["actorKind"]) for e in changed["history"]] == [("resource.update", "agent"), ("resource.create", "human")]
    # The whole page before the agent's change stays in its history.
    (update, _) = ok(call(client, "GET", f"/history?entityId={page['id']}"))["items"]
    assert (update["before"]["body"], update["after"]["body"]) == ("Run the tests.", "Run the tests twice.")
    (created,) = [r for r in ok(call(client, "GET", "/resources"))["items"] if r["title"] == "Hotfix checklist"]
    assert (created["archived"], created["revision"]) == (False, 3)
