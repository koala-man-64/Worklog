"""Tickets: agents file, change and comment directly; the person archives; history stays findable."""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog import tickets
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def request(c, method, path, payload=None, agent=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]


def get(c, path, agent=None):
    return ok(request(c, "GET", path, agent=agent))


def create(c, title="Backup locked the database", agent=None, **fields):
    return ok(request(c, "POST", "/tickets", {"title": title, **fields}, agent))


def patch(c, ticket, agent=None, **fields):
    return ok(request(c, "PATCH", f"/tickets/{ticket['id']}", {"revision": ticket["revision"], **fields}, agent))


def comment(c, ticket, text="Seen again during the nightly backup.", agent=None):
    return ok(request(c, "POST", f"/tickets/{ticket['key']}/comments", {"text": text}, agent))


def credential(c, label="Implementer"):
    return ok(request(c, "POST", "/credentials", {"label": label}))


def titles(page):
    return [t["title"] for t in page["items"]]


def test_tickets_take_the_next_key_and_answer_to_it(client):
    first = create(client)
    assert (first["number"], first["key"], first["status"], first["kind"], first["priority"]) == (1, "T-1", "open", "bug", "normal")
    assert first["author"] == {"id": "local-user", "label": "You", "kind": "human", "avatar": None}
    second = create(client, "Flaky import test", kind="task", priority="high")
    assert second["key"] == "T-2"
    for ref in ("T-2", "t-2", "2", second["id"]):
        assert get(client, f"/tickets/{ref}")["id"] == second["id"]
    error(request(client, "GET", "/tickets/T-99"), 404, "not_found")
    error(request(client, "GET", "/tickets/T-0"), 404, "not_found")


def test_a_retried_create_returns_the_same_ticket_and_a_reused_key_conflicts(client):
    key = str(uuid.uuid4())
    first = ok(request(client, "POST", "/tickets", {"title": "Once"}, key=key))
    assert ok(request(client, "POST", "/tickets", {"title": "Once"}, key=key))["id"] == first["id"]
    error(request(client, "POST", "/tickets", {"title": "Something else"}, key=key), 409, "idempotency_conflict")
    assert get(client, "/tickets?status=all")["total"] == 1


def test_an_agent_files_comments_and_closes_as_itself_across_a_token_rotation(client):
    agent = credential(client)
    ticket = create(client, "Parser drops trailing rows", agent=agent, labels=["parser"])
    assert ticket["author"]["id"] == agent["agentId"] and ticket["author"]["kind"] == "agent"
    rotated = ok(request(client, "POST", f"/credentials/{agent['id']}/rotate", {}))
    error(request(client, "POST", f"/tickets/{ticket['id']}/comments", {"text": "Old token"}, agent), 401, "authentication")
    note = comment(client, ticket, "The last row has no newline.", rotated)
    assert note["author"]["id"] == agent["agentId"]
    closed = patch(client, ticket, rotated, status="done", resolution="Read the final row before EOF. Fixed in #51.")
    assert closed["status"] == "done" and closed["closedAt"] and closed["revision"] == ticket["revision"] + 1
    history = get(client, f"/history?entityId={ticket['id']}", rotated)["items"]
    assert [e["kind"] for e in reversed(history)] == ["ticket.create", "ticket.comment", "ticket.close"]
    assert {e["actorId"] for e in history} == {agent["agentId"]}


def test_done_needs_a_resolution_and_reopening_keeps_it(client):
    ticket = create(client)
    failure = error(request(client, "PATCH", f"/tickets/{ticket['id']}", {"revision": ticket["revision"], "status": "done"}), 422, "validation")
    assert failure["fields"] == {"resolution": "required"}
    closed = patch(client, ticket, status="done", resolution="Retry with a fresh snapshot.")
    error(request(client, "PATCH", f"/tickets/{ticket['id']}", {"revision": ticket["revision"], "title": "Stale"}), 409, "conflict")
    reopened = patch(client, closed, status="blocked")
    assert (reopened["status"], reopened["closedAt"], reopened["resolution"]) == ("blocked", None, "Retry with a fresh snapshot.")
    kinds = [e["kind"] for e in get(client, f"/tickets/{ticket['id']}")["history"]]
    assert kinds[:2] == ["ticket.reopen", "ticket.close"]
    # A past incident can be recorded already resolved.
    past = create(client, "Disk filled during export", kind="incident", status="done", resolution="Freed space; added a warning.")
    assert past["closedAt"] == past["createdAt"]
    error(request(client, "POST", "/tickets", {"title": "No answer", "status": "done"}), 422, "validation")


def test_comments_leave_the_revision_alone_and_move_last_activity(client):
    ticket = create(client)
    note = comment(client, ticket)
    detail = get(client, f"/tickets/{ticket['id']}")
    assert detail["revision"] == ticket["revision"] and detail["lastActivityAt"] == note["createdAt"]
    assert detail["comments"] == {"items": [note], "total": 1} and detail["commentCount"] == 1
    # An edit based on the revision read before the comment still applies.
    assert patch(client, ticket, priority="urgent")["priority"] == "urgent"


def test_detail_stays_small_however_often_a_long_ticket_changes(client):
    ticket = create(client, description="x" * 99_000)
    for priority in ("high", "low") * 10:
        ticket = patch(client, ticket, priority=priority)
    response = request(client, "GET", f"/tickets/{ticket['id']}")
    # Each change repeats the whole ticket before and after; detail keeps only who, what and when.
    assert len(response.content) < 150_000
    history = response.json()["history"]
    assert len(history) == 21 and set(history[0]) == {"id", "kind", "actorId", "actorLabel", "actorKind", "createdAt"}
    full = get(client, f"/history?entityId={ticket['id']}&limit=1")["items"][0]
    assert full["after"]["priority"] == "low" and full["before"]["priority"] == "high"


def test_detail_keeps_the_latest_comments_oldest_first(client):
    ticket = create(client)
    for n in range(5):
        comment(client, ticket, f"Attempt {n}")
    detail = get(client, f"/tickets/{ticket['id']}?comments=3")
    assert [c["text"] for c in detail["comments"]["items"]] == ["Attempt 2", "Attempt 3", "Attempt 4"]
    assert detail["comments"]["total"] == 5


def test_only_the_person_archives_and_archived_tickets_are_read_only_and_hidden(client):
    agent = credential(client)
    ticket = create(client, "Quarantined flaky test", description="Quarantine reason zebracorn")
    error(request(client, "POST", f"/tickets/{ticket['id']}/archive", {"revision": ticket["revision"]}, agent), 403, "agent_scope")
    archived = ok(request(client, "POST", f"/tickets/{ticket['id']}/archive", {"revision": ticket["revision"]}))
    assert archived["archived"] and get(client, "/tickets?status=all")["total"] == 0
    assert titles(get(client, "/tickets?status=all&archived=true")) == ["Quarantined flaky test"]
    error(request(client, "PATCH", f"/tickets/{ticket['id']}", {"revision": archived["revision"], "title": "New"}, agent), 409, "ticket_archived")
    error(request(client, "POST", f"/tickets/{ticket['id']}/comments", {"text": "Still?"}, agent), 409, "ticket_archived")
    assert get(client, "/search?q=zebracorn")["total"] == 0
    assert get(client, "/search?q=zebracorn&archived=true")["total"] == 1
    recovered = ok(request(client, "POST", f"/tickets/{ticket['id']}/recover", {"revision": archived["revision"]}))
    assert patch(client, recovered, agent, title="Flaky test, fixed")["title"] == "Flaky test, fixed"
    kinds = [e["kind"] for e in get(client, f"/tickets/{ticket['id']}")["history"]]
    assert kinds[:3] == ["ticket.update", "ticket.recover", "ticket.archive"]
    error(request(client, "POST", f"/tickets/{ticket['id']}/delete", {"revision": 1}), 404, "not_found")


def test_lists_filter_sort_and_page(client):
    repository = ok(request(client, "POST", "/repositories", {"name": "worklog"}))
    item = ok(request(client, "POST", "/work-items", {"title": "Import format"}))
    low = create(client, "Typo in the README", priority="low", kind="task", labels=["Docs"])
    urgent = create(client, "Startup refuses the database", priority="urgent", kind="incident", repositoryIds=[repository["id"]])
    linked = create(client, "Importer drops rows", itemId=item["id"], assigneeId="local-user", labels=["parser", "import"])
    done = create(client, "Old crash on export", status="done", resolution="Fixed in the export rewrite.")
    comment(client, low, "Mentions the frobnicator twice.")
    assert titles(get(client, "/tickets")) == ["Startup refuses the database", "Importer drops rows", "Typo in the README"]
    assert titles(get(client, "/tickets?sort=recent")) == ["Typo in the README", "Importer drops rows", "Startup refuses the database"]
    assert titles(get(client, "/tickets?status=done")) == [done["title"]]
    assert get(client, "/tickets?status=all")["total"] == 4
    assert titles(get(client, "/tickets?kind=incident")) == [urgent["title"]]
    assert titles(get(client, "/tickets?priority=low")) == [low["title"]]
    assert titles(get(client, "/tickets?label=DOCS")) == [low["title"]]
    assert titles(get(client, f"/tickets?repositoryId={repository['id']}")) == [urgent["title"]]
    assert titles(get(client, f"/tickets?itemId={item['id']}")) == [linked["title"]]
    assert titles(get(client, "/tickets?assigneeId=local-user")) == [linked["title"]]
    assert titles(get(client, "/tickets?assigneeId=none")) == [urgent["title"], low["title"]]
    assert titles(get(client, "/tickets?q=frobnicator")) == [low["title"]]
    assert titles(get(client, "/tickets?q=export+rewrite&status=all")) == [done["title"]]
    page = get(client, "/tickets?limit=1&offset=1")
    assert (titles(page), page["total"], page["limit"], page["offset"]) == ([linked["title"]], 3, 1, 1)
    assert linked["labels"] == ["import", "parser"] and linked["item"] == {"id": item["id"], "title": "Import format", "status": "open", "archived": False}
    assert linked["assignee"]["label"] == "You"
    error(request(client, "GET", "/tickets?status=closed"), 422, "validation")
    error(request(client, "GET", "/tickets?sort=oldest"), 422, "validation")


def test_list_items_carry_a_snippet_and_detail_the_whole_text(client):
    ticket = create(client, description="x" * 1500, status="done", resolution="Short fix.")
    listed = get(client, "/tickets?status=all")["items"][0]
    assert len(listed["description"]) == tickets.SNIPPET and listed["truncated"]
    detail = get(client, f"/tickets/{ticket['id']}")
    assert len(detail["description"]) == 1500 and not detail["truncated"]


def test_links_must_be_active_and_assignees_able_to_act(client):
    item = ok(request(client, "POST", "/work-items", {"title": "Release"}))
    agent = credential(client)
    error(request(client, "POST", "/tickets", {"title": "x", "repositoryIds": [str(uuid.uuid4())]}), 422, "repository_unavailable")
    error(request(client, "POST", "/tickets", {"title": "x", "itemId": str(uuid.uuid4())}), 404, "not_found")
    error(request(client, "POST", "/tickets", {"title": "x", "assigneeId": "nobody"}), 422, "assignee_unavailable")
    ticket = create(client, itemId=item["id"], assigneeId=agent["agentId"])
    assert ticket["assignee"]["id"] == agent["agentId"] and ticket["assignee"]["active"]
    ok(request(client, "POST", f"/work-items/{item['id']}/archive", {"revision": item["revision"]}))
    # The existing link stays; only a new link must point at active work.
    renamed = patch(client, ticket, title="Release blocked")
    assert renamed["item"]["archived"] and renamed["itemId"] == item["id"]
    other = create(client)
    error(request(client, "PATCH", f"/tickets/{other['id']}", {"revision": other["revision"], "itemId": item["id"]}), 409, "archived")
    unlinked = patch(client, renamed, itemId=None, assigneeId=None)
    assert unlinked["itemId"] is None and unlinked["assignee"] is None
    assert ok(request(client, "POST", f"/credentials/{agent['id']}/revoke", {}))["ok"]
    error(request(client, "PATCH", f"/tickets/{other['id']}", {"revision": other["revision"], "assigneeId": agent["agentId"]}), 422, "assignee_unavailable")


def test_labels_are_normalized_and_bounded(client):
    ticket = create(client, labels=[" SQLite", "sqlite", "CI"])
    assert ticket["labels"] == ["ci", "sqlite"]
    assert patch(client, ticket, labels=[])["labels"] == []
    error(request(client, "POST", "/tickets", {"title": "x", "labels": ["has space"]}), 422, "validation")
    error(request(client, "POST", "/tickets", {"title": "x", "labels": [f"l{n}" for n in range(21)]}), 422, "validation")
    error(request(client, "POST", "/tickets", {"title": " "}), 422, "validation")


def test_search_finds_tickets_and_comments_and_routes_to_the_ticket(client):
    ticket = create(client, "Locked database", description="The quokkas symptom appears at midnight.")
    comment(client, ticket, "Wombats reproduce it on demand.")
    results = get(client, "/search?q=quokkas")["items"] + get(client, "/search?q=wombats")["items"]
    assert [(r["type"], r["routeType"], r["routeId"], r["title"]) for r in results] == [
        ("ticket", "tickets", ticket["id"], "T-1 Locked database"),
        ("ticket_comment", "tickets", ticket["id"], "T-1 Locked database")]
    patch(client, ticket, title="Backup holds a lock")
    assert get(client, "/search?q=wombats")["items"][0]["title"] == "T-1 Backup holds a lock"


def test_context_lists_the_open_tickets_of_the_filtered_work(client):
    item = ok(request(client, "POST", "/work-items", {"title": "Import format"}))
    linked = create(client, "Importer drops rows", itemId=item["id"])
    create(client, "Elsewhere")
    create(client, "Fixed before", itemId=item["id"], status="done", resolution="Done.")
    agent = credential(client)
    context = get(client, f"/context?itemId={item['id']}", agent)
    assert [t["id"] for t in context["tickets"]["items"]] == [linked["id"]]
    assert get(client, "/context", agent)["tickets"]["total"] == 2


def test_agents_read_tickets_but_only_file_change_and_comment(client):
    agent = credential(client)
    ticket = create(client)
    assert get(client, "/tickets", agent)["total"] == 1
    assert get(client, f"/tickets/{ticket['key']}", agent)["id"] == ticket["id"]
    error(request(client, "POST", f"/tickets/{ticket['id']}/recover", {"revision": 1}, agent), 403, "agent_scope")
    assert ok(request(client, "POST", f"/credentials/{agent['id']}/revoke", {}))["ok"]
    error(request(client, "POST", "/tickets", {"title": "After revocation"}, agent), 401, "authentication")


def test_a_failed_history_write_leaves_no_ticket_label_or_search_row(client, monkeypatch):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected storage failure")

    monkeypatch.setattr(tickets, "event", fail)
    error(request(client, "POST", "/tickets", {"title": "Never stored", "labels": ["ghost"]}), 503, "database_error")
    with client.app.state.db.read() as conn:
        for table in ("tickets", "ticket_labels", "ticket_repositories"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM search_index WHERE type='ticket'").fetchone()[0] == 0
