"""Global search: what it finds, where each result leads, and what an archived or retired parent
hides. Consolidation step 2.4 pins this before moving the query into search.py.

Every record carries the word "quillback", so one search returns them all, and a tag word of its
own, so each result names the record it came from.
"""

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"
WORD = "quillback"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        boot = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = boot.json()["csrfToken"]
        yield c


def write(c, method, path, body=None, **kwargs):
    headers = {"Idempotency-Key": str(uuid.uuid4()), **kwargs.pop("headers", {})}
    response = c.request(method, "/api/v1" + path, json=body, headers=headers, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def upload(c, owner_type, owner_id, tag, **headers):
    return write(c, "POST", f"/attachments?ownerType={owner_type}&ownerId={owner_id}&name={WORD}-{tag}.txt",
                 content=b"contents", headers={"Content-Type": "text/plain", **headers})


def seed(c):
    """One record of every kind search finds, under every owner each kind can have, by tag."""
    r = {"item": write(c, "POST", "/work-items", {"title": f"{WORD} itemtag", "description": "x" * 300})}
    step_id = str(uuid.uuid4())
    roadmap = write(c, "POST", "/roadmaps", {"title": f"{WORD} roadmaptag", "overview": "the overview", "steps": [
        {"id": step_id, "title": f"{WORD} steptag", "description": "the step", "parentId": None, "position": 0}]})
    r["roadmap"], r["step"] = roadmap, roadmap["steps"][0]
    # Work on a step of the roadmap, with a note of its own.
    r["linkeditem"] = write(c, "POST", "/work-items", {"title": f"{WORD} linkeditemtag", "stepId": step_id})
    r["person"] = write(c, "POST", "/people", {"name": f"{WORD} persontag", "team": "Platform"})
    r["repository"] = write(c, "POST", "/repositories", {"name": f"{WORD} repositoryhometag", "url": "https://github.com/example/quillback"})
    # A repository isn't in search itself, but the discussion Worklog opens for it (v22) is.
    r["repositoryhome"] = {"id": r["repository"]["discussionId"]}
    for owner in ("item", "roadmap", "step", "linkeditem"):
        owner_type = "item" if owner == "linkeditem" else owner
        r[f"{owner}note"] = write(c, "POST", "/notes", {"ownerType": owner_type, "ownerId": r[owner]["id"], "text": f"{WORD} {owner}notetag"})
    for owner in ("item", "roadmap", "step", "repository"):
        r[f"{owner}file"] = upload(c, owner, r[owner]["id"], f"{owner}filetag")
    for number, owner in enumerate(("item", "roadmap"), 1):
        r[f"{owner}link"] = write(c, "POST", "/links", {"ownerType": owner, "ownerId": r[owner]["id"],
                                                         "url": f"https://example.com/{owner}", "title": f"{WORD} {owner}linktag"})
        r[f"{owner}chat"] = write(c, "POST", "/chats", {"ownerType": owner, "ownerId": r[owner]["id"],
                                                         "chatId": f"chat-{owner}", "title": f"{WORD} {owner}chattag"})
        r[f"{owner}github"] = write(c, "POST", "/github", {"ownerType": owner, "ownerId": r[owner]["id"], "kind": "pr",
                                                            "url": f"https://github.com/example/quillback/pull/{number}",
                                                            "title": f"{WORD} {owner}githubtag"})
    r["question"] = write(c, "POST", "/questions", {"roadmapId": roadmap["id"], "title": f"{WORD} questiontag"})
    r["decision"] = write(c, "POST", "/decisions", {"roadmapId": roadmap["id"], "title": f"{WORD} decisiontag"})
    r["resource"] = write(c, "POST", "/resources", {"title": f"{WORD} resourcetag", "body": "The page"})
    opened = write(c, "POST", "/discussions", {"title": f"{WORD} discussiontag", "text": f"{WORD} openingtag"})
    r["discussion"] = opened["discussion"]
    write(c, "POST", f"/discussions/{r['discussion']['id']}/messages", {"text": f"{WORD} replytag"})
    r["ticket"] = write(c, "POST", "/tickets", {"title": f"{WORD} tickettag"})
    write(c, "POST", f"/tickets/{r['ticket']['id']}/comments", {"text": f"{WORD} commenttag"})
    # An agent's proposal evidence is never in canonical search.
    agent = write(c, "POST", "/credentials", {"label": "Planner"})
    bearer = {"Authorization": "Bearer " + agent["token"]}
    draft = write(c, "POST", "/proposals", {"title": "Evidence", "rationale": "", "operations": []}, headers=bearer)
    upload(c, "proposal", draft["id"], "evidencetag", **bearer)
    return r


def found(c, records, archived=False):
    """Each result as (tag, type, route type, route tag, owner tag)."""
    result = c.get("/api/v1/search", params={"q": WORD, "archived": archived, "limit": 200}).json()
    assert result["total"] == len(result["items"])
    names = {record["id"]: tag for tag, record in records.items()}
    rows = []
    for item in result["items"]:
        tags = re.findall(r"([a-z]+)tag\b", item["snippet"]) or re.findall(r"([a-z]+)tag\b", item["title"])
        rows.append((tags[0], item["type"], item["routeType"], names[item["routeId"]], names[item["ownerId"]]))
    return sorted(rows)


EVERYTHING = sorted([
    ("item", "item", "work", "item", "item"),
    ("itemnote", "note", "work", "item", "item"),
    ("itemfile", "attachment", "work", "item", "item"),
    ("itemlink", "link", "work", "item", "item"),
    ("itemchat", "chat", "work", "item", "item"),
    ("itemgithub", "github", "work", "item", "item"),
    ("linkeditem", "item", "work", "linkeditem", "linkeditem"),
    ("linkeditemnote", "note", "work", "linkeditem", "linkeditem"),
    ("roadmap", "roadmap", "roadmaps", "roadmap", "roadmap"),
    ("roadmapnote", "note", "roadmaps", "roadmap", "roadmap"),
    ("roadmapfile", "attachment", "roadmaps", "roadmap", "roadmap"),
    ("roadmaplink", "link", "roadmaps", "roadmap", "roadmap"),
    ("roadmapchat", "chat", "roadmaps", "roadmap", "roadmap"),
    ("roadmapgithub", "github", "roadmaps", "roadmap", "roadmap"),
    ("question", "question", "roadmaps", "roadmap", "roadmap"),
    ("decision", "decision", "roadmaps", "roadmap", "roadmap"),
    ("step", "step", "roadmaps", "roadmap", "roadmap"),
    ("stepnote", "note", "roadmaps", "roadmap", "step"),
    ("stepfile", "attachment", "roadmaps", "roadmap", "step"),
    ("person", "person", "people", "person", "person"),
    ("repositoryfile", "attachment", "repositories", "repository", "repository"),
    ("repositoryhome", "discussion", "discussions", "repositoryhome", "repositoryhome"),
    ("repositoryhome", "discussion_message", "discussions", "repositoryhome", "repositoryhome"),
    ("resource", "resource", "resources", "resource", "resource"),
    ("discussion", "discussion", "discussions", "discussion", "discussion"),
    ("opening", "discussion_message", "discussions", "discussion", "discussion"),
    ("reply", "discussion_message", "discussions", "discussion", "discussion"),
    ("ticket", "ticket", "tickets", "ticket", "ticket"),
    ("comment", "ticket_comment", "tickets", "ticket", "ticket"),
])


def test_search_finds_every_kind_and_routes_each_to_the_page_that_shows_it(client):
    records = seed(client)
    assert found(client, records) == EVERYTHING
    assert found(client, records, archived=True) == []


def test_a_result_shows_the_first_220_characters_and_pages_keep_the_full_total(client):
    seed(client)
    hit = client.get("/api/v1/search", params={"q": "itemtag"}).json()["items"]
    assert len(hit) == 1 and hit[0]["snippet"] == "x" * 220
    page = client.get("/api/v1/search", params={"q": WORD, "limit": 5, "offset": 5}).json()
    assert page["total"] == len(EVERYTHING) and len(page["items"]) == 5


def test_a_query_with_no_words_finds_nothing(client):
    seed(client)
    assert client.get("/api/v1/search", params={"q": "*:()"}).json() == {"items": [], "total": 0}


def hide(c, r, parent):
    """Archive or retire one record that others hang off."""
    if parent == "item":
        write(c, "POST", f"/work-items/{r['item']['id']}/archive", {"revision": r["item"]["revision"]})
    elif parent == "roadmap":
        current = c.get(f"/api/v1/roadmaps/{r['roadmap']['id']}").json()
        write(c, "POST", f"/roadmaps/{r['roadmap']['id']}/archive", {"revision": current["revision"]})
    elif parent == "step":
        current = c.get(f"/api/v1/roadmaps/{r['roadmap']['id']}").json()
        write(c, "POST", f"/roadmaps/{r['roadmap']['id']}/revisions",
              {"revision": current["revision"], "title": current["title"], "overview": current["overview"], "steps": []})
    elif parent == "person":
        write(c, "POST", f"/people/{r['person']['id']}/archive", {"revision": r["person"]["revision"]})
    elif parent == "repository":
        write(c, "POST", f"/repositories/{r['repository']['id']}/archive", {"revision": r["repository"]["revision"]})
    elif parent == "resource":
        write(c, "POST", f"/resources/{r['resource']['id']}/archive", {"revision": r["resource"]["revision"]})
    elif parent == "discussion":
        current = c.get(f"/api/v1/discussions/{r['discussion']['id']}").json()["discussion"]
        write(c, "POST", f"/discussions/{r['discussion']['id']}/archive", {"revision": current["revision"], "archived": True})
    elif parent == "ticket":
        write(c, "POST", f"/tickets/{r['ticket']['id']}/archive", {"revision": r["ticket"]["revision"]})
    elif parent == "itemnote":
        write(c, "POST", f"/notes/{r['itemnote']['id']}/archive", {"revision": r["itemnote"]["revision"]})


HIDDEN = {
    "item": {"item", "itemnote", "itemfile", "itemlink", "itemchat", "itemgithub"},
    # Work on one of its steps stays in search: an item is hidden only by its own archive flag.
    "roadmap": {"roadmap", "roadmapnote", "roadmapfile", "roadmaplink", "roadmapchat", "roadmapgithub",
                "question", "decision", "step", "stepnote", "stepfile"},
    "step": {"step", "stepnote", "stepfile"},
    "person": {"person"},
    "repository": {"repositoryfile"},
    "resource": {"resource"},
    "discussion": {"discussion", "opening", "reply"},
    "ticket": {"ticket", "comment"},
    "itemnote": {"itemnote"},
}


@pytest.mark.parametrize("parent", sorted(HIDDEN))
def test_an_archived_or_retired_parent_moves_what_hangs_off_it_to_archived_search(client, parent):
    records = seed(client)
    hide(client, records, parent)
    active = {row[0] for row in found(client, records)}
    archived = {row[0] for row in found(client, records, archived=True)}
    everything = {row[0] for row in EVERYTHING}
    assert archived == HIDDEN[parent]
    assert active == everything - HIDDEN[parent]
