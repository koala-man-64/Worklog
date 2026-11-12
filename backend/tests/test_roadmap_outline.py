"""Agent reads that fit in a context (step 1.1 of docs/ROADMAPS-RETHINK.md): a roadmap's outline gives
each step's ID, title, status, parent and position without its text, and a step's read adds the steps
above and beside it and the tracked chats on it."""

import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.plans import outline_order, step_place

ORIGIN = "http://127.0.0.1:8877"
TEXT = "Every detail of the step, which only its own read gives. " * 80
ROOT_B, ROOT_A, A1, B1, A2, A1A, B2, GONE = (str(uuid.uuid4()) for _ in range(8))


@contextmanager
def person(directory):
    app = create_app(directory, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


@pytest.fixture
def client(tmp_path):
    with person(tmp_path) as c:
        yield c


def send(c, method, path, payload=None, token=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if token:
        headers["Authorization"] = "Bearer " + token
    response = c.request(method, "/api/v1" + path, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def plan(c):
    """A roadmap whose steps are listed out of outline order: B, then A, with A1 and A2 under A, B1
    and B2 under B, A1a under A1, and a step that a revision then retires. A1a is done, B1 doing and
    B2 blocked. Returns the roadmap and an agent's token."""
    steps = [{"id": ROOT_B, "title": "Ship it"}, {"id": ROOT_A, "title": "Build it"},
             {"id": A1, "title": "Parser", "description": TEXT, "parentId": ROOT_A},
             {"id": B1, "title": "Release notes", "parentId": ROOT_B},
             {"id": A2, "title": "Writer", "parentId": ROOT_A},
             {"id": A1A, "title": "Tokenizer", "parentId": A1},
             {"id": B2, "title": "Announce", "parentId": ROOT_B},
             {"id": GONE, "title": "Old idea"}]
    roadmap = send(c, "POST", "/roadmaps", {"title": "Exports", "steps": steps})
    roadmap = send(c, "POST", f"/roadmaps/{roadmap['id']}/revisions",
                   {"revision": roadmap["revision"], "title": "Exports", "steps": steps[:-1]})
    for step, status in ((A1A, "done"), (B1, "doing"), (B2, "blocked")):
        current = send(c, "GET", f"/steps/{step}")
        send(c, "PATCH", f"/steps/{step}/progress", {"revision": current["revision"], "status": status})
    token = send(c, "POST", "/credentials", {"label": "Planner"})["token"]
    return send(c, "GET", f"/roadmaps/{roadmap['id']}"), token


def test_the_outline_gives_every_step_in_outline_order_without_its_text(client):
    roadmap, token = plan(client)
    outline = send(client, "GET", f"/roadmaps/{roadmap['id']}/outline", token=token)
    assert {key: outline[key] for key in ("id", "title", "archived", "revision")} == {
        "id": roadmap["id"], "title": "Exports", "archived": False, "revision": roadmap["revision"]}
    # Depth-first, each step before its children; the retired step is left out. A parent's status
    # comes from its leaves, as the roadmap's page shows it: a blocked leaf blocks it, and done work
    # beside open work makes it doing.
    assert [(s["id"], s["title"], s["status"], s["parentId"]) for s in outline["steps"]] == [
        (ROOT_B, "Ship it", "blocked", None), (B1, "Release notes", "doing", ROOT_B), (B2, "Announce", "blocked", ROOT_B),
        (ROOT_A, "Build it", "doing", None), (A1, "Parser", "done", ROOT_A), (A1A, "Tokenizer", "done", A1),
        (A2, "Writer", "open", ROOT_A)]
    assert all(set(step) == {"id", "title", "status", "parentId", "position"} for step in outline["steps"])
    positions = {step["id"]: step["position"] for step in roadmap["steps"]}
    assert all(step["position"] == positions[step["id"]] for step in outline["steps"])
    # The text stays with each step's own read.
    assert TEXT[:40] not in client.get(f"/api/v1/roadmaps/{roadmap['id']}/outline").text
    # An archived roadmap's outline says so, and still reads.
    send(client, "POST", f"/roadmaps/{roadmap['id']}/archive", {"revision": roadmap["revision"]})
    archived = send(client, "GET", f"/roadmaps/{roadmap['id']}/outline", token=token)
    assert (archived["archived"], [s["id"] for s in archived["steps"]]) == (True, [s["id"] for s in outline["steps"]])


def test_a_steps_read_names_the_steps_above_and_beside_it_and_its_chats(client):
    roadmap, token = plan(client)
    chat = send(client, "POST", "/chats", {"ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": "session-1",
                                           "title": "Parser work", "stepIds": [A1A]})
    earlier = send(client, "POST", "/chats", {"ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": "session-0",
                                              "title": "First try", "stepIds": [A1A]})
    send(client, "POST", f"/chats/{earlier['id']}/archive", {"revision": earlier["revision"]})
    send(client, "POST", "/chats", {"ownerType": "roadmap", "ownerId": roadmap["id"], "chatId": "session-2",
                                    "title": "Writer work", "stepIds": [A2]})
    step = send(client, "GET", f"/steps/{A1A}", token=token)
    assert (step["title"], step["roadmapId"]) == ("Tokenizer", roadmap["id"])
    assert [(s["id"], s["title"], s["status"]) for s in step["ancestors"]] == [(ROOT_A, "Build it", "doing"), (A1, "Parser", "done")]
    assert step["siblings"] == []
    # Its chats in the order they were put there, an archived one included and marked.
    assert [(c["id"], c["archived"], c["stepIds"]) for c in step["chats"]] == [(chat["id"], False, [A1A]), (earlier["id"], True, [A1A])]
    # A step's siblings are the others under its parent, in outline order, with their status.
    parser = send(client, "GET", f"/steps/{A1}", token=token)
    assert (parser["description"], parser["ancestors"]) == (TEXT, [{"id": ROOT_A, "title": "Build it", "status": "doing"}])
    assert [(s["id"], s["status"]) for s in parser["siblings"]] == [(A2, "open")]
    assert parser["chats"] == []
    # A top-level step has nothing above it; the retired step is no one's sibling.
    top = send(client, "GET", f"/steps/{ROOT_B}", token=token)
    assert (top["ancestors"], [s["id"] for s in top["siblings"]]) == ([], [ROOT_A])
    # A retired step still reads, beside the steps still in the plan.
    retired = send(client, "GET", f"/steps/{GONE}", token=token)
    assert (retired["retired"], retired["ancestors"], [s["id"] for s in retired["siblings"]]) == (True, [], [ROOT_B, ROOT_A])


def test_an_unknown_roadmap_has_no_outline(client):
    response = client.get(f"/api/v1/roadmaps/{uuid.uuid4()}/outline")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found", response.text


def step(identity, parent=None, position=0, status="open", retired=False):
    return {"id": identity, "title": identity.title(), "status": status, "parentId": parent, "position": position,
            "retired": retired}


def test_the_outline_order_keeps_every_step_once_whatever_its_parent_chain():
    # Tied positions go by ID, as the roadmap's page orders them. A step whose parent is not in the
    # plan starts a branch, with its children after it, and a broken chain that loops, or names the
    # step itself, comes last rather than not at all.
    steps = [step("b", position=1), step("a", position=1), step("a2", "a"), step("orphan", "gone"),
             step("orphan-child", "orphan"), step("x", "y", 5), step("y", "x", 6), step("self", "self", 7)]
    assert [s["id"] for s in outline_order(steps)] == ["orphan", "orphan-child", "a", "a2", "b", "x", "y", "self"]


def test_a_steps_place_leaves_out_retired_siblings_and_ends_a_looping_chain():
    steps = [step("top"), step("mid", "top", status="doing"), step("leaf", "mid"), step("peer", "mid", 1, "done"),
             step("old", "mid", 2, retired=True)]
    place = step_place(steps, "leaf")
    assert place["ancestors"] == [{"id": "top", "title": "Top", "status": "open"}, {"id": "mid", "title": "Mid", "status": "doing"}]
    assert place["siblings"] == [{"id": "peer", "title": "Peer", "status": "done", "position": 1}]
    assert [a["id"] for a in step_place([step("x", "y"), step("y", "x")], "x")["ancestors"]] == ["y"]
