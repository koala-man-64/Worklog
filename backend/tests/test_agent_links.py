"""Agent ties (schema v17): the person ties any agent to any record, with its role there, and an agent
ties only itself and changes only the ties it set. Both sides read a tie, an agent finds its own in
its context, and a draft proposal stays hidden from other agents. Upgrade, the documented way back,
restore, export and the repository merge keep ties whole."""

import io
import json
import sqlite3
import uuid
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path
from typing import get_args

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import agent_links, cli, migrations, models, recovery
from worklog.app import create_app
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
# README: from version 17 to version 16, with Worklog stopped and after a manual backup.
WAY_BACK = """\
BEGIN;
DROP TABLE agent_links;
PRAGMA user_version=16;
COMMIT;
"""


def to_v16(path):
    """Take a current database back to the v16 shape the documented way: to version 17 first."""
    from test_release_recovery import to_v17

    to_v17(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


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


def tie(c, agent_id, target_type, target_id, relationship="", agent=None):
    """As the person, naming the agent; as an agent, for itself (agent_id None leaves it out)."""
    payload = {"targetType": target_type, "targetId": target_id, "relationship": relationship}
    return write(c, "POST", "/agent-links", payload if agent_id is None else {"agentId": agent_id, **payload}, agent)


def untie(c, agent_id, target_type, target_id, agent=None):
    payload = {"targetType": target_type, "targetId": target_id}
    return write(c, "POST", "/agent-links/unlink", payload if agent_id is None else {"agentId": agent_id, **payload}, agent)


def ties(c, query, agent=None):
    return read(c, "/agent-links?" + query, agent)


def item(c, title="Fix the parser"):
    return ok(write(c, "POST", "/work-items", {"title": title}))


def records(c, reviewer):
    """One record of every type an agent can be tied to, as {type: (id, title)}."""
    roadmap = ok(write(c, "POST", "/roadmaps", {"title": "Release", "steps": [{"title": "Cut the branch"}]}))
    work = item(c)
    discussion = ok(write(c, "POST", "/discussions", {"title": "Release review", "text": "Ready to cut?"}))["discussion"]
    made = {
        "item": work,
        "roadmap": roadmap,
        "step": roadmap["steps"][0],
        "repository": ok(write(c, "POST", "/repositories", {"name": "Worklog", "url": "https://example.com/acme/worklog"})),
        "person": ok(write(c, "POST", "/people", {"name": "Ada"})),
        "resource": ok(write(c, "POST", "/resources", {"title": "How we release", "summary": "The steps"})),
        "discussion": discussion,
        "proposal": ok(write(c, "POST", "/proposals", {"title": "Plan the retro", "rationale": "", "operations": []})),
        "ticket": ok(write(c, "POST", "/tickets", {"title": "Flaky import"})),
        "question": ok(write(c, "POST", "/questions", {"roadmapId": roadmap["id"], "title": "Which build?"})),
        "decision": ok(write(c, "POST", "/decisions", {"roadmapId": roadmap["id"], "title": "Ship on Friday"})),
    }
    found = {kind: (record["id"], record.get("title") or record.get("name")) for kind, record in made.items()}
    found["agent"] = (reviewer["agentId"], "Reviewer")
    return found


def test_every_list_of_tie_types_agrees():
    assert agent_links.TYPES == get_args(models.AgentTieTarget) == cli.TIE_TYPES
    assert "CHECK(target_type IN(" + ",".join(f"'{kind}'" for kind in agent_links.TYPES) + "))" in migrations.AGENT_LINKS_SCHEMA


def test_the_person_ties_an_agent_to_a_record_of_every_type_and_both_sides_read_it(client):
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    made = records(client, reviewer)
    assert set(made) == set(agent_links.TYPES)
    for kind, (identity, title) in made.items():
        tied = ok(tie(client, builder["agentId"], kind, identity, "Maintainer"))
        assert (tied["agentId"], tied["targetType"], tied["targetId"]) == (builder["agentId"], kind, identity)
        assert (tied["relationship"], tied["setBy"], tied["target"]["title"]) == ("Maintainer", "human", title)
        assert tied["agent"] == {"id": builder["agentId"], "label": "Builder", "avatar": builder["avatar"], "active": True}
        on = ties(client, f"targetType={kind}&targetId={identity}")
        assert [(t["agentId"], t["relationship"]) for t in on["items"]] == [(builder["agentId"], "Maintainer")]
    mine = ties(client, f"agentId={builder['agentId']}")
    # Grouped in the order the types are listed.
    assert [t["targetType"] for t in mine["items"]] == list(agent_links.TYPES)
    assert mine["total"] == len(agent_links.TYPES)
    target = {t["targetType"]: t["target"] for t in mine["items"]}
    roadmap_id = made["roadmap"][0]
    assert (target["step"]["roadmapId"], target["step"]["roadmapTitle"]) == (roadmap_id, "Release")
    assert target["question"]["roadmapId"] == target["decision"]["roadmapId"] == roadmap_id
    assert (target["ticket"]["number"], target["ticket"]["status"]) == (1, "open")
    assert target["proposal"] == {"id": made["proposal"][0], "title": "Plan the retro", "archived": False, "status": "draft"}
    assert target["agent"]["avatar"] == reviewer["avatar"] and target["agent"]["archived"] is False


def test_a_tie_changes_its_role_in_place_and_both_records_keep_its_history(client):
    builder = credential(client)
    work = item(client)
    first = ok(tie(client, builder["agentId"], "item", work["id"], "Implementer"))
    again = ok(tie(client, builder["agentId"], "item", work["id"], "  Reviewer  "))
    assert (again["id"], again["createdAt"], again["relationship"]) == (first["id"], first["createdAt"], "Reviewer")
    # The same role again changes nothing, so nothing new is recorded.
    assert ok(tie(client, builder["agentId"], "item", work["id"], "Reviewer")) == again
    assert ok(untie(client, builder["agentId"], "item", work["id"])) == {
        "ok": True, "id": first["id"], "agentId": builder["agentId"], "targetType": "item", "targetId": work["id"]}
    error(untie(client, builder["agentId"], "item", work["id"]), 404, "not_found")
    assert ties(client, f"agentId={builder['agentId']}") == {"items": [], "total": 0}
    for entity in (work["id"], builder["agentId"]):
        kept = [e for e in read(client, f"/history?entityId={entity}")["items"] if e["kind"].startswith("agent.")]
        assert [(e["kind"], (e["after"] or e["before"])["relationship"], e["actorKind"]) for e in kept] == [
            ("agent.unlink", "Reviewer", "human"), ("agent.link", "Reviewer", "human"), ("agent.link", "Implementer", "human")]
        assert kept[0]["before"]["targetTitle"] == "Fix the parser" and kept[0]["before"]["agentLabel"] == "Builder"


def test_a_new_tie_needs_an_agent_and_a_record_that_takes_one(client):
    builder = credential(client)
    work = item(client)
    error(tie(client, builder["agentId"], "item", str(uuid.uuid4())), 404, "not_found")
    error(tie(client, str(uuid.uuid4()), "item", work["id"]), 404, "not_found")
    error(tie(client, builder["agentId"], "day", "2026-01-05"), 422, "validation")
    # A chat's agents are those bound to it, by registration or the person's confirmation, never ties.
    error(tie(client, builder["agentId"], "chat", str(uuid.uuid4())), 422, "validation")
    error(tie(client, builder["agentId"], "agent", builder["agentId"]), 422, "validation")
    error(tie(client, builder["agentId"], "item", work["id"], "x" * 201), 422, "validation")
    error(write(client, "POST", "/agent-links", {"targetType": "item", "targetId": work["id"]}), 422, "validation")
    # A retired step keeps its ties but takes no new one.
    roadmap = ok(write(client, "POST", "/roadmaps", {"title": "Release", "steps": [{"title": "Cut"}, {"title": "Ship"}]}))
    cut, ship = (step["id"] for step in roadmap["steps"])
    ok(tie(client, builder["agentId"], "step", cut, "Owner"))
    ok(write(client, "POST", f"/roadmaps/{roadmap['id']}/revisions",
             {"revision": roadmap["revision"], "title": "Release", "steps": []}))
    error(tie(client, builder["agentId"], "step", ship), 409, "conflict")
    assert ok(tie(client, builder["agentId"], "step", cut, "Former owner"))["target"]["archived"] is True
    # So does an archived repository, until it is recovered.
    repository = ok(write(client, "POST", "/repositories", {"name": "Worklog", "url": "https://example.com/acme/worklog"}))
    other = ok(write(client, "POST", "/repositories", {"name": "Tools", "url": "https://example.com/acme/tools"}))
    ok(tie(client, builder["agentId"], "repository", repository["id"], "Maintainer"))
    ok(write(client, "POST", f"/repositories/{repository['id']}/archive", {"revision": repository["revision"]}))
    ok(write(client, "POST", f"/repositories/{other['id']}/archive", {"revision": other["revision"]}))
    error(tie(client, builder["agentId"], "repository", other["id"]), 409, "archived")
    assert ok(tie(client, builder["agentId"], "repository", repository["id"], "Former maintainer"))["relationship"] == "Former maintainer"
    ok(untie(client, builder["agentId"], "repository", repository["id"]))


def test_an_agent_ties_only_itself_and_changes_only_the_ties_it_set(client):
    builder, other = credential(client, "Builder"), credential(client, "Other")
    work = item(client)
    resource = ok(write(client, "POST", "/resources", {"title": "Release runbook"}))
    # A write without a request ID is refused, like any agent's write.
    bare = client.post("/api/v1/agent-links", headers={"Authorization": "Bearer " + builder["token"]},
                       json={"targetType": "resource", "targetId": resource["id"]})
    error(bare, 428, "idempotency")
    mine = ok(tie(client, None, "resource", resource["id"], "Follows", builder))
    assert (mine["agentId"], mine["setBy"], mine["relationship"]) == (builder["agentId"], "agent", "Follows")
    assert ok(tie(client, builder["agentId"], "resource", resource["id"], "Follows closely", builder))["setBy"] == "agent"
    error(tie(client, other["agentId"], "item", work["id"], "", builder), 403, "forbidden")
    error(untie(client, other["agentId"], "item", work["id"], builder), 403, "forbidden")
    # What the person set stays theirs.
    ok(tie(client, builder["agentId"], "item", work["id"], "Owner"))
    error(tie(client, None, "item", work["id"], "Helper", builder), 403, "forbidden")
    error(untie(client, None, "item", work["id"], builder), 403, "forbidden")
    # The person changes the agent's tie, and it becomes theirs.
    assert ok(tie(client, builder["agentId"], "resource", resource["id"], "Follows")) ["setBy"] == "human"
    error(untie(client, None, "resource", resource["id"], builder), 403, "forbidden")
    # An agent may tie itself to another agent, and removes what it set.
    assert ok(tie(client, None, "agent", other["agentId"], "Reviews for", builder))["target"]["title"] == "Other"
    ok(untie(client, None, "agent", other["agentId"], builder))
    # It reads its own ties without naming itself, and anyone's by naming them.
    assert [t["targetType"] for t in ties(client, "", builder)["items"]] == ["item", "resource"]
    assert ties(client, f"agentId={builder['agentId']}", other)["total"] == 2
    history = read(client, f"/history?entityId={work['id']}")["items"]
    assert history[0]["actorKind"] == "human"
    agent_history = [e for e in read(client, f"/history?entityId={builder['agentId']}")["items"] if e["kind"] == "agent.unlink"]
    assert agent_history[0]["actorId"] == builder["agentId"] and agent_history[0]["actorLabel"] == "Builder"


def test_an_agent_holds_a_bounded_number_of_ties_it_set_itself(client, monkeypatch):
    monkeypatch.setattr(agent_links, "SELF_SET_LIMIT", 2)
    builder = credential(client)
    first, second, third = (item(client, f"Item {n}") for n in range(3))
    ok(tie(client, None, "item", first["id"], "", builder))
    ok(tie(client, None, "item", second["id"], "", builder))
    error(tie(client, None, "item", third["id"], "", builder), 429, "agent_limit")
    # Changing a tie it holds is no new tie, and the person's ties don't count.
    ok(tie(client, None, "item", first["id"], "Lead", builder))
    ok(tie(client, builder["agentId"], "item", third["id"], "Owner"))
    ok(untie(client, None, "item", second["id"], builder))
    resource = ok(write(client, "POST", "/resources", {"title": "Runbook"}))
    ok(tie(client, None, "resource", resource["id"], "", builder))


def test_a_revoked_agent_ties_nothing_even_by_retrying(client):
    builder = credential(client)
    work = item(client)
    key = str(uuid.uuid4())
    payload = {"targetType": "item", "targetId": work["id"], "relationship": "Implementer"}
    ok(write(client, "POST", "/agent-links", payload, builder, key=key))
    ok(write(client, "POST", f"/credentials/{builder['id']}/revoke", {}))
    error(write(client, "POST", "/agent-links", payload, builder, key=key), 401, "authentication")
    # The tie stays, with its agent shown as no longer active.
    tied, = ties(client, f"targetType=item&targetId={work['id']}")["items"]
    assert tied["agent"]["active"] is False and tied["setBy"] == "agent"


def test_another_agents_draft_proposal_takes_no_tie_and_shows_in_no_read_until_submitted(client):
    author, other = credential(client, "Author"), credential(client, "Other")
    draft = ok(write(client, "POST", "/proposals", {
        "title": "Secret plan", "rationale": "Not yet", "operations": [{"action": "item.create", "payload": {"title": "Later"}}]}, author))
    error(tie(client, None, "proposal", draft["id"], "", other), 404, "not_found")
    # Its author ties itself to it. The person sees that; another agent sees nothing, not even in history.
    ok(tie(client, None, "proposal", draft["id"], "Author", author))
    assert ties(client, f"agentId={author['agentId']}")["total"] == 1
    assert ties(client, f"agentId={author['agentId']}", other) == {"items": [], "total": 0}
    assert ties(client, f"targetType=proposal&targetId={draft['id']}", other) == {"items": [], "total": 0}
    assert [e["kind"] for e in read(client, f"/history?entityId={author['agentId']}")["items"]].count("agent.link") == 1
    assert "agent.link" not in [e["kind"] for e in read(client, f"/history?entityId={author['agentId']}", other)["items"]]
    ok(write(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, author))
    assert ties(client, f"agentId={author['agentId']}", other)["items"][0]["target"]["status"] == "submitted"
    assert "agent.link" in [e["kind"] for e in read(client, f"/history?entityId={author['agentId']}", other)["items"]]


def test_a_tie_the_person_set_to_a_draft_an_agent_cannot_see_answers_as_if_there_were_none(client):
    author, other = credential(client, "Author"), credential(client, "Other")
    operations = [{"action": "item.create", "payload": {"title": "Later"}}]
    draft = ok(write(client, "POST", "/proposals", {"title": "Secret plan", "rationale": "", "operations": operations}, author))
    ok(tie(client, other["agentId"], "proposal", draft["id"], "Reviews it"))
    # Removing it would say the tie exists, so it answers as a missing tie does; so does setting it.
    for response in (untie(client, None, "proposal", draft["id"], other), tie(client, None, "proposal", draft["id"], "Mine", other)):
        assert error(response, 404, "not_found")["message"] in {"That agent is not tied to that record", "The record to tie does not exist"}
    assert ties(client, "", other) == {"items": [], "total": 0}
    # A tie the agent set itself it still removes after the proposal goes back to draft.
    submitted = ok(write(client, "POST", f"/proposals/{draft['id']}/submit", {"revision": draft["revision"]}, author))
    ok(untie(client, other["agentId"], "proposal", draft["id"]))
    ok(tie(client, None, "proposal", draft["id"], "Watching", other))
    ok(write(client, "POST", f"/proposals/{draft['id']}/reopen", {"revision": submitted["revision"]}, author))
    assert ok(untie(client, None, "proposal", draft["id"], other))["targetId"] == draft["id"]


def test_an_agents_history_page_fills_up_past_entries_it_may_not_see(client):
    author, other = credential(client, "Author"), credential(client, "Other")
    operations = [{"action": "item.create", "payload": {"title": "Later"}}]
    draft = ok(write(client, "POST", "/proposals", {"title": "Secret plan", "rationale": "", "operations": operations}, author))
    work = item(client)
    ok(tie(client, author["agentId"], "item", work["id"], "One"))
    ok(tie(client, author["agentId"], "proposal", draft["id"], "Hidden"))
    ok(tie(client, author["agentId"], "item", work["id"], "Three"))
    page = read(client, f"/history?entityId={author['agentId']}&limit=2", other)["items"]
    assert [e["after"]["relationship"] for e in page] == ["Three", "One"]
    assert [e["after"]["relationship"] for e in read(client, f"/history?entityId={author['agentId']}&limit=2")["items"]] == [
        "Three", "Hidden"]


def test_an_agent_finds_its_ties_in_its_context_and_reads_several_records_ties_at_once(client):
    builder = credential(client)
    ticket = ok(write(client, "POST", "/tickets", {"title": "Flaky import"}))
    # A ticket is also named by its key or number.
    ok(tie(client, None, "ticket", "T-1", "Fixer", builder))
    assert ok(tie(client, None, "ticket", "1", "Fixer", builder))["targetId"] == ticket["id"]
    context = read(client, "/context", builder)
    assert [(t["targetType"], t["targetId"], t["relationship"]) for t in context["ties"]["items"]] == [("ticket", ticket["id"], "Fixer")]
    assert read(client, "/context")["ties"] == {"items": [], "total": 0}
    roadmap = ok(write(client, "POST", "/roadmaps", {"title": "Release"}))
    questions = [ok(write(client, "POST", "/questions", {"roadmapId": roadmap["id"], "title": f"Question {n}"})) for n in range(3)]
    for question in questions[:2]:
        ok(tie(client, builder["agentId"], "question", question["id"], "Answers"))
    both = ties(client, "targetType=question&" + "&".join(f"targetId={q['id']}" for q in questions))
    assert {t["targetId"] for t in both["items"]} == {questions[0]["id"], questions[1]["id"]}
    for query in ("", "targetType=question", f"targetId={questions[0]['id']}",
                  f"agentId={builder['agentId']}&targetType=question&targetId={questions[0]['id']}"):
        error(client.get("/api/v1/agent-links?" + query), 422, "validation")
    error(client.get(f"/api/v1/agent-links?targetType=question&{'&'.join(['targetId=x'] * 201)}"), 422, "validation")
    error(client.get(f"/api/v1/agent-links?agentId={uuid.uuid4()}"), 404, "not_found")


def test_a_repository_merge_moves_its_agent_ties_to_the_survivor(client):
    builder, other, helper = credential(client, "Builder"), credential(client, "Other"), credential(client, "Helper")
    source = ok(write(client, "POST", "/repositories", {"name": "old", "url": "https://example.com/acme/old"}))
    survivor = ok(write(client, "POST", "/repositories", {"name": "new", "url": "https://example.com/acme/new"}))
    ok(tie(client, builder["agentId"], "repository", source["id"], "Maintainer"))
    ok(tie(client, builder["agentId"], "repository", survivor["id"], "Reviewer"))
    ok(tie(client, None, "repository", source["id"], "Owner", other))
    ok(tie(client, helper["agentId"], "repository", source["id"], "Lead"))
    ok(tie(client, None, "repository", survivor["id"], "Helps", helper))
    ok(write(client, "POST", f"/repositories/{source['id']}/merge",
             {"intoId": survivor["id"], "revision": source["revision"], "intoRevision": survivor["revision"]}))
    # One tied to both keeps the survivor's tie, with its role, unless only the merged one was the
    # person's: the person's tie stays theirs, in place of the one the agent set itself.
    moved = ties(client, f"targetType=repository&targetId={survivor['id']}")
    assert {(t["agentId"], t["relationship"], t["setBy"]) for t in moved["items"]} == {
        (builder["agentId"], "Reviewer", "human"), (other["agentId"], "Owner", "agent"), (helper["agentId"], "Lead", "human")}
    assert ties(client, f"targetType=repository&targetId={source['id']}")["total"] == 0
    merge = [e for e in read(client, f"/history?entityId={survivor['id']}")["items"] if e["kind"] == "repository.merge"]
    assert merge[0]["after"]["moved"]["agents"] == 3
    error(tie(client, builder["agentId"], "repository", source["id"]), 409, "repository_merged")


def test_search_narrows_to_the_kinds_of_record_asked_for(client):
    item(client, "Parser release")
    ok(write(client, "POST", "/roadmaps", {"title": "Parser roadmap", "steps": [{"title": "Parser step"}]}))
    assert {r["type"] for r in read(client, "/search?q=parser")["items"]} == {"item", "roadmap", "step"}
    steps = read(client, "/search?q=parser&type=step")
    assert ([r["type"] for r in steps["items"]], steps["total"]) == (["step"], 1)
    assert {r["type"] for r in read(client, "/search?q=parser&type=step&type=item")["items"]} == {"step", "item"}
    error(client.get("/api/v1/search?q=parser&type=agent"), 422, "validation")


def test_the_export_holds_every_tie(client):
    builder = credential(client)
    work = item(client)
    ok(tie(client, builder["agentId"], "item", work["id"], "Implementer"))
    archive = zipfile.ZipFile(io.BytesIO(client.get("/api/v1/export").content))
    rows = json.loads(archive.read("records/agent_links.json"))
    assert [(r["agent_id"], r["target_type"], r["target_id"], r["relationship"], r["set_by"]) for r in rows] == [
        (builder["agentId"], "item", work["id"], "Implementer", "human")]


def test_the_v17_upgrade_starts_with_no_ties_and_changes_no_record(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        item(c, "Kept as it was")
    to_v16(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        kept = conn.execute("SELECT * FROM work_items").fetchall()
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        # The upgrade goes on to v18, whose releases start empty too.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("SELECT count(*) FROM agent_links").fetchone()[0] == 0
        assert conn.execute("SELECT * FROM work_items").fetchall() == kept
    snapshot, = (tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16


def test_the_documented_way_back_to_v16_gives_its_exact_shape_and_upgrades_again(tmp_path):
    # The SQL tested here is the SQL the README gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        ok(tie(c, credential(c)["agentId"], "item", item(c)["id"], "Implementer"))
    to_v16(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
        assert recovery._schema_signature(conn) == sealed.shape(16)
        # The ties' history stays with the records.
        assert conn.execute("SELECT count(*) FROM events WHERE kind='agent.link'").fetchone()[0] == 2
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()


def test_restore_takes_a_v16_backup_and_a_v17_one_with_its_ties(tmp_path):
    older, current, target = (tmp_path / name / "worklog.sqlite3" for name in ("older", "current", "target"))
    with person(older.parent) as c:
        item(c, "From before ties")
    to_v16(older)
    with person(current.parent) as c:
        ok(tie(c, credential(c)["agentId"], "item", item(c)["id"], "Implementer"))
    Database(target).initialize()
    for source in (older, current):
        recovery.restore_database(target, source, tmp_path / f"restore-{source.parent.name}")
        with closing(sqlite3.connect(target)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
            assert recovery._schema_signature(conn) == sealed.current()
            assert conn.execute("SELECT count(*) FROM agent_links").fetchone()[0] == (source == current)
