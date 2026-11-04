"""Stack rank: one order of unfinished, unarchived work, read from backlog_order, and the
counts that Today's tabs show for each entry."""

import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert response.status_code == 200
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def write(c, method, path, payload=None, **kwargs):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **kwargs.pop("headers", {})}
    if payload is not None:
        kwargs["json"] = payload
    return c.request(method, "/api/v1" + path, headers=headers, **kwargs)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def items(c, *titles, **fields):
    return [ok(write(c, "POST", "/work-items", {"title": title, **fields})) for title in titles]


def work(c, query=""):
    return c.get("/api/v1/work-items?limit=200" + query).json()


def ranking(c):
    """Titles of ranked work, by rank."""
    page = work(c)
    ranked = [i for i in page["items"] if i["rank"] is not None]
    assert [i["rank"] for i in ranked] == list(range(1, len(ranked) + 1))
    assert page["rankedCount"] == len(ranked)
    return [i["title"] for i in ranked]


def rank(c, item_id, value, revision=None):
    if revision is None:
        revision = c.get(f"/api/v1/work-items/{item_id}").json()["revision"]
    return write(c, "POST", f"/work-items/{item_id}/rank", {"revision": revision, "rank": value})


def patch(c, item_id, **fields):
    current = c.get(f"/api/v1/work-items/{item_id}").json()
    return ok(write(c, "PATCH", f"/work-items/{item_id}", {"revision": current["revision"], **fields}))


def test_unfinished_unarchived_work_is_ranked_first_and_the_rest_unranked(client):
    a, b, c, d = items(client, "A", "B", "C", "D")
    patch(client, a["id"], status="done")
    archived = ok(write(client, "POST", f"/work-items/{d['id']}/archive", {"revision": d["revision"]}))
    page = work(client)
    assert [(i["title"], i["rank"]) for i in page["items"]] == [("B", 1), ("C", 2), ("A", None)]
    assert page["rankedCount"] == 2
    assert [i["rank"] for i in work(client, "&archived=true")["items"]] == [None]
    detail = client.get(f"/api/v1/work-items/{c['id']}").json()
    assert (detail["rank"], detail["rankedCount"]) == (2, 2)
    assert client.get(f"/api/v1/work-items/{archived['id']}").json()["rank"] is None
    # Filters narrow the list but never the rank, which counts all ranked work.
    assert [(i["title"], i["rank"]) for i in work(client, "&q=C")["items"]] == [("C", 2)]


def test_ranking_moves_work_up_down_and_past_the_last(client):
    a, b, c, d = items(client, "A", "B", "C", "D")
    moved = ok(rank(client, d["id"], 1))
    assert moved == {"id": d["id"], "rank": 1, "rankedCount": 4}
    assert ranking(client) == ["D", "A", "B", "C"]
    assert ok(rank(client, a["id"], 3))["rank"] == 3
    assert ranking(client) == ["D", "B", "A", "C"]
    assert ok(rank(client, d["id"], 99))["rank"] == 4
    assert ranking(client) == ["B", "A", "C", "D"]
    history = client.get(f"/api/v1/history?entityId={d['id']}").json()["items"]
    moves = sorted((e["before"]["rank"], e["after"]["rank"]) for e in history if e["kind"] == "item.rank")
    assert moves == [(1, 4), (4, 1)]


def test_ranking_to_the_same_rank_changes_nothing(client):
    a, b = items(client, "A", "B")
    before = work(client)["collectionRevision"]
    assert ok(rank(client, b["id"], 2)) == {"id": b["id"], "rank": 2, "rankedCount": 2}
    assert work(client)["collectionRevision"] == before
    assert not [e for e in client.get(f"/api/v1/history?entityId={b['id']}").json()["items"] if e["kind"] == "item.rank"]


def test_ranking_keeps_item_revisions_so_open_drafts_still_save(client):
    a, b, c = items(client, "A", "B", "C")
    ok(rank(client, c["id"], 1))
    assert [client.get(f"/api/v1/work-items/{i['id']}").json()["revision"] for i in (a, b, c)] == [1, 1, 1]
    # A draft based on the revision read before the move saves as it is.
    ok(write(client, "PATCH", f"/work-items/{a['id']}", {"revision": 1, "title": "A, renamed"}))


def test_done_and_archived_work_keeps_its_place_and_rejoins_the_ranking_there(client):
    a, b, c, d = items(client, "A", "B", "C", "D")
    patch(client, b["id"], status="done")
    ok(write(client, "POST", f"/work-items/{c['id']}/archive", {"revision": c["revision"]}))
    assert ranking(client) == ["A", "D"]
    ok(rank(client, d["id"], 1))
    assert ranking(client) == ["D", "A"]
    patch(client, b["id"], status="open")
    ok(write(client, "POST", f"/work-items/{c['id']}/recover", {"revision": c["revision"] + 1}))
    assert ranking(client) == ["D", "A", "B", "C"]
    # New work joins at the end.
    items(client, "E")
    assert ranking(client)[-1] == "E"


def test_ranking_refuses_done_archived_stale_and_invalid_requests(client):
    a, b, c = items(client, "A", "B", "C")
    patch(client, b["id"], status="done")
    archived = ok(write(client, "POST", f"/work-items/{c['id']}/archive", {"revision": c["revision"]}))
    done = rank(client, b["id"], 1)
    assert done.status_code == 409 and "reopen" in done.json()["error"]["message"]
    assert rank(client, archived["id"], 1, archived["revision"]).status_code == 404
    assert rank(client, "missing", 1, 1).status_code == 404
    stale = rank(client, a["id"], 1, a["revision"] - 1)
    assert stale.status_code == 409 and stale.json()["error"]["fields"]["currentRevision"] == a["revision"]
    for invalid in (0, -1, "1", True, 1.5, None):
        assert rank(client, a["id"], invalid).status_code == 422, invalid
    assert write(client, "POST", f"/work-items/{a['id']}/rank", {"revision": a["revision"]}).status_code == 422


def test_changes_to_other_work_never_refuse_a_move(client):
    a, b, c = items(client, "A", "B", "C")
    # What the person saw: C at rank 3, with its revision then.
    seen = client.get(f"/api/v1/work-items/{c['id']}").json()
    patch(client, a["id"], title="A, renamed elsewhere")
    ok(write(client, "PUT", f"/days/2026-10-28/entries/{b['id']}", {"revision": 0, "planned": True}))
    assert ok(rank(client, c["id"], 1, seen["revision"]))["rank"] == 1
    assert ranking(client) == ["C", "A, renamed elsewhere", "B"]
    # A change to the item itself still does.
    patch(client, c["id"], title="C, renamed elsewhere")
    assert rank(client, c["id"], 2, seen["revision"]).status_code == 409


def test_ranking_is_idempotent_under_its_key(client):
    a, b = items(client, "A", "B")
    key = {"Idempotency-Key": str(uuid.uuid4())}
    payload = {"revision": b["revision"], "rank": 1}
    first = ok(write(client, "POST", f"/work-items/{b['id']}/rank", payload, headers=key))
    assert ok(write(client, "POST", f"/work-items/{b['id']}/rank", payload, headers=key)) == first
    assert ranking(client) == ["B", "A"]


def test_backlog_reorder_and_moves_keep_planned_work_at_its_rank(client):
    a, b, c = items(client, "A", "B", "C")
    # Planned well ahead, B leaves the unscheduled backlog but keeps its stack rank.
    ok(write(client, "PUT", f"/days/2099-01-01/entries/{b['id']}", {"revision": 0, "planned": True}))
    backlog = work(client, "&view=backlog")
    assert [i["title"] for i in backlog["items"]] == ["A", "C"]
    ok(write(client, "POST", "/work-items/reorder", {"revision": backlog["collectionRevision"], "ids": [c["id"], a["id"]]}))
    assert ranking(client) == ["C", "B", "A"]
    ok(write(client, "POST", f"/work-items/{a['id']}/move", {"revision": work(client)["collectionRevision"], "direction": "up"}))
    assert ranking(client) == ["A", "B", "C"]


def test_backlog_move_over_tied_positions_leaves_other_work_in_place(client):
    a, b, c = items(client, "A", "B", "C")
    ok(write(client, "PUT", f"/days/2099-01-01/entries/{a['id']}", {"revision": 0, "planned": True}))
    with client.app.state.db.transaction() as conn:
        # Orderings before stack rank could leave backlog items tied with each other.
        conn.execute("UPDATE work_items SET backlog_order=5 WHERE id IN (?,?)", (b["id"], c["id"]))
    names = {b["id"]: "B", c["id"]: "C"}
    lower, higher = sorted(names)
    assert ranking(client) == ["A", names[lower], names[higher]]
    ok(write(client, "POST", f"/work-items/{higher}/move", {"revision": work(client)["collectionRevision"], "direction": "up"}))
    assert ranking(client) == ["A", names[higher], names[lower]]
    with client.app.state.db.read() as conn:
        positions = [r[0] for r in conn.execute("SELECT backlog_order FROM work_items")]
    assert len(set(positions)) == 3


def test_backlog_move_keeps_planned_work_tied_with_its_neighbour_at_its_rank(client):
    made = items(client, "X", "Y", "Z")
    name = {i["id"]: i["title"] for i in made}
    low, middle, high = sorted(name)
    # An ordering from before stack rank tied planned work with the backlog item after it.
    with client.app.state.db.transaction() as conn:
        conn.execute("UPDATE work_items SET backlog_order=5 WHERE id=?", (low,))
        conn.execute("UPDATE work_items SET backlog_order=6 WHERE id IN (?,?)", (middle, high))
    ok(write(client, "PUT", f"/days/2099-01-01/entries/{middle}", {"revision": 0, "planned": True}))
    assert ranking(client) == [name[low], name[middle], name[high]]
    ok(write(client, "POST", f"/work-items/{low}/move", {"revision": work(client)["collectionRevision"], "direction": "down"}))
    # The two backlog items trade places; the planned item between them keeps its rank.
    assert ranking(client) == [name[high], name[middle], name[low]]


def test_only_the_person_ranks_work(client):
    a, b = items(client, "A", "B")
    cred = ok(write(client, "POST", "/credentials", {"label": "Ranking agent"}))
    agent = {"Authorization": "Bearer " + cred["token"]}
    refused = write(client, "POST", f"/work-items/{b['id']}/rank", {"revision": b["revision"], "rank": 1}, headers=agent)
    assert refused.status_code == 403
    assert ranking(client) == ["A", "B"]


def test_scratchpad_of_whitespace_alone_counts_as_empty(client):
    day = "2026-10-28"
    (blank,) = items(client, "Blank", day=day)
    ok(write(client, "POST", "/notes", {"ownerType": "item", "ownerId": blank["id"], "title": "Scratchpad", "text": "  \t 　"}))
    assert client.get(f"/api/v1/days/{day}").json()["entries"][0]["counts"]["scratchpad"] is False


def test_today_entries_carry_rank_and_what_their_tabs_hold(client):
    day = "2026-10-28"
    first, second = items(client, "First", "Second", day=day)
    ok(rank(client, second["id"], 1))
    for task in ("Draft", "Review", "Old"):
        made = ok(write(client, "POST", "/tasks", {"itemId": first["id"], "text": task}))
        if task == "Review":
            ok(write(client, "PATCH", f"/tasks/{made['id']}", {"revision": made["revision"], "done": True}))
        if task == "Old":
            ok(write(client, "POST", f"/tasks/{made['id']}/archive", {"revision": made["revision"]}))
    ok(write(client, "POST", "/links", {"ownerType": "item", "ownerId": first["id"], "url": "https://example.com/a"}))
    gone = ok(write(client, "POST", "/links", {"ownerType": "item", "ownerId": first["id"], "url": "https://example.com/b"}))
    ok(write(client, "POST", f"/links/{gone['id']}/archive", {"revision": gone["revision"]}))
    ok(write(client, "POST", f"/attachments?ownerType=item&ownerId={first['id']}&name=a.txt", content=b"evidence", headers={"Content-Type": "text/plain"}))
    ok(write(client, "POST", "/references", {"itemId": first["id"], "path": "C:\\work\\a.txt"}))
    ok(write(client, "POST", "/notes", {"ownerType": "item", "ownerId": first["id"], "title": "Scratchpad", "text": "Context"}))
    ok(write(client, "POST", "/notes", {"ownerType": "item", "ownerId": second["id"], "title": "Scratchpad", "text": "  \n"}))
    entries = {e["item"]["title"]: e for e in client.get(f"/api/v1/days/{day}").json()["entries"]}
    assert entries["First"]["counts"] == {"tasks": 2, "tasksDone": 1, "links": 1, "files": 2, "scratchpad": True}
    assert entries["Second"]["counts"] == {"tasks": 0, "tasksDone": 0, "links": 0, "files": 0, "scratchpad": False}
    assert (entries["Second"]["item"]["rank"], entries["First"]["item"]["rank"]) == (1, 2)
    patch(client, first["id"], status="done")
    assert {e["item"]["title"]: e["item"]["rank"] for e in client.get(f"/api/v1/days/{day}").json()["entries"]} == {"First": None, "Second": 1}
