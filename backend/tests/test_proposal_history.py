"""The proposals applied to each work item and roadmap, and each agent's proposals (schema v16).

Approval records which work items and roadmaps each change of a proposal was made to, and the
v16 upgrade fills in the same for the proposals approved before it. A work item's or roadmap's
history lists what was applied to it in the order it was applied, and an agent's lists what it
proposed in the order it made them, the latest first and each numbered by its place. Upgrade,
the documented way back and restore are here too.
"""

import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
import sealed
from fastapi.testclient import TestClient
from worklog import migrations, recovery
from worklog.app import create_app
from worklog.common import today
from worklog.db import Database

ORIGIN = "http://127.0.0.1:8877"
# README: from version 16 to version 15, with Worklog stopped and after a manual backup.
WAY_BACK = """\
BEGIN;
DROP TABLE proposal_targets;
PRAGMA user_version=15;
COMMIT;
"""


def to_v15(path):
    """Take a current database back to the v15 shape the documented way."""
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


def write(c, method, path, payload=None, agent=None):
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def read(c, path, agent=None):
    return c.get("/api/v1" + path, headers={"Authorization": "Bearer " + agent["token"]} if agent else {})


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def credential(c, label):
    """An agent identity, with the credential it acts with."""
    return ok(write(c, "POST", "/credentials", {"label": label}))


def propose(c, agent, title, operations, decision="approve"):
    """An agent's proposal, submitted and then decided by the person: approve, reject, or None."""
    drafted = ok(write(c, "POST", "/proposals", {"title": title, "rationale": "", "operations": operations}, agent))
    submitted = ok(write(c, "POST", f"/proposals/{drafted['id']}/submit", {"revision": drafted["revision"]}, agent))
    if decision is None or submitted["status"] != "submitted":
        return submitted
    return ok(write(c, "POST", f"/proposals/{drafted['id']}/{decision}", {"revision": submitted["revision"]}))


def change(action, **payload):
    return {"action": action, "payload": payload}


def recorded(path):
    with closing(sqlite3.connect(path)) as conn:
        return sorted(conn.execute("SELECT * FROM proposal_targets").fetchall())


def titled(path, table, title):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(f"SELECT id FROM {table} WHERE title=?", (title,)).fetchone()[0]


def history(c, path, agent=None, **query):
    suffix = "&".join(f"{key}={value}" for key, value in query.items())
    return ok(read(c, path + "/proposals" + ("?" + suffix if suffix else ""), agent))


def numbered(listing):
    return [(entry["number"], entry["title"]) for entry in listing["items"]]


def plan(c):
    """What the person keeps before an agent proposes anything: work, a roadmap with steps under
    a parent, and records that belong to them, with a person and a repository beside them."""
    parent = ok(write(c, "POST", "/roadmaps", {"title": "Programme"}))
    roadmap = ok(write(c, "POST", "/roadmaps", {"title": "Release", "parentId": parent["id"],
                                                "steps": [{"title": "Build"}, {"title": "Ship"}]}))
    work = ok(write(c, "POST", "/work-items", {"title": "Fix the parser"}))
    other = ok(write(c, "POST", "/work-items", {"title": "Write the notes"}))
    return {
        "parent": parent, "roadmap": roadmap, "work": work, "other": other,
        "task": ok(write(c, "POST", "/tasks", {"itemId": work["id"], "text": "Reproduce it"})),
        "link": ok(write(c, "POST", "/links", {"ownerType": "roadmap", "ownerId": roadmap["id"], "url": "https://example.com/plan"})),
        "question": ok(write(c, "POST", "/questions", {"roadmapId": roadmap["id"], "title": "Which build?"})),
        "person": ok(write(c, "POST", "/people", {"name": "Ada"})),
        "repository": ok(write(c, "POST", "/repositories", {"name": "Worklog"})),
    }


def everything(p):
    """One change of each kind a proposal applies, with the records each one counts toward."""
    work, other, roadmap, build = p["work"], p["other"], p["roadmap"], p["roadmap"]["steps"][0]
    return [
        change("item.create", title="Draft the release notes", roadmapId=roadmap["id"]),
        change("item.update", id=work["id"], revision=work["revision"], status="doing"),
        change("task.update", id=p["task"]["id"], revision=p["task"]["revision"], done=True),
        change("note.create", ownerType="item", ownerId=other["id"], title="Findings", text="It was the lexer."),
        change("note.create", ownerType="step", ownerId=build["id"], text="Use the nightly build."),
        change("progress.set", id=build["id"], revision=build["revision"], status="done"),
        change("link.update", id=p["link"]["id"], revision=p["link"]["revision"], title="The plan"),
        change("question.update", id=p["question"]["id"], revision=p["question"]["revision"], answer="Nightly", status="answered"),
        change("roadmap.create", title="Release follow-ups", parentId=p["parent"]["id"]),
        change("person.link", personId=p["person"]["id"], targetType="item", targetId=work["id"]),
        change("person.update", id=p["person"]["id"], revision=p["person"]["revision"], notes="Knows the lexer."),
        change("repository.update", id=p["repository"]["id"], revision=p["repository"]["revision"], description="This app"),
        change("entry.upsert", date=today(), itemId=other["id"], revision=0, planned=True),
    ]


def test_approval_records_the_work_items_and_roadmaps_each_change_was_made_to(tmp_path, client):
    p = plan(client)
    agent = credential(client, "Builder")
    approved = propose(client, agent, "Everything at once", everything(p))
    assert approved["status"] == "approved"
    work, other, roadmap, parent = p["work"]["id"], p["other"]["id"], p["roadmap"]["id"], p["parent"]["id"]
    created = titled(tmp_path / "worklog.sqlite3", "work_items", "Draft the release notes")
    child = titled(tmp_path / "worklog.sqlite3", "roadmaps", "Release follow-ups")
    # A person's or a repository's change alone (11, 12) counts toward no work item or roadmap.
    assert recorded(tmp_path / "worklog.sqlite3") == sorted((approved["id"], *row) for row in [
        (1, "item.create", "item", created), (1, "item.create", "roadmap", roadmap),
        (2, "item.update", "item", work), (3, "task.update", "item", work),
        (4, "note.create", "item", other), (5, "note.create", "roadmap", roadmap),
        (6, "progress.set", "roadmap", roadmap), (7, "link.update", "roadmap", roadmap),
        (8, "question.update", "roadmap", roadmap),
        (9, "roadmap.create", "roadmap", child), (9, "roadmap.create", "roadmap", parent),
        (10, "person.link", "item", work), (13, "entry.upsert", "item", other),
    ])
    listing = history(client, f"/work-items/{work}")
    assert listing["total"] == 1
    [entry] = listing["items"]
    assert entry == {
        "number": 1, "id": approved["id"], "title": "Everything at once", "status": "approved",
        "autoApproved": False, "actorLabel": "Builder", "agentId": agent["agentId"],
        "createdAt": approved["createdAt"], "submittedAt": approved["submittedAt"],
        "appliedAt": approved["updatedAt"], "updatedAt": approved["updatedAt"], "operationCount": 13,
        "changes": [{"number": 2, "action": "item.update"}, {"number": 3, "action": "task.update"},
                    {"number": 10, "action": "person.link"}]}
    assert [c["number"] for c in history(client, f"/roadmaps/{roadmap}")["items"][0]["changes"]] == [1, 5, 6, 7, 8]
    assert [c["action"] for c in history(client, f"/roadmaps/{child}")["items"][0]["changes"]] == ["roadmap.create"]
    assert [c["action"] for c in history(client, f"/work-items/{created}")["items"][0]["changes"]] == ["item.create"]


def test_a_ticket_change_counts_toward_the_work_item_the_ticket_is_linked_to_once_made(tmp_path, client):
    p = plan(client)
    agent = credential(client, "Builder")
    work, other = p["work"]["id"], p["other"]["id"]
    filed = propose(client, agent, "File it", [change("ticket.create", title="Lexer drops the last token", itemId=work),
                                               change("resource.create", title="Lexer notes")])
    ticket = ok(read(client, "/tickets/T-1"))
    moved = propose(client, agent, "Move it", [change("ticket.update", id="T-1", revision=1, itemId=other)])
    unlinked = propose(client, agent, "Unlink it", [change("ticket.update", id=ticket["id"], revision=2, itemId=None)])
    assert unlinked["status"] == "approved"
    # A resource, like a person or a repository, counts toward no work item or roadmap, and a ticket
    # linked to none after the change counts toward none.
    assert recorded(tmp_path / "worklog.sqlite3") == sorted([
        (filed["id"], 1, "ticket.create", "item", work), (moved["id"], 1, "ticket.update", "item", other)])
    assert [e["id"] for e in history(client, f"/work-items/{work}")["items"]] == [filed["id"]]
    assert [e["id"] for e in history(client, f"/work-items/{other}")["items"]] == [moved["id"]]


def test_a_records_history_holds_only_what_was_applied_to_it_the_latest_first(client):
    p = plan(client)
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    work = p["work"]
    first = propose(client, builder, "Start the fix", [change("item.update", id=work["id"], revision=1, status="doing")])
    propose(client, reviewer, "Rename it", [change("item.update", id=work["id"], revision=2, title="Nope")], "reject")
    propose(client, reviewer, "Still waiting", [change("item.update", id=work["id"], revision=2, title="Later")], None)
    propose(client, builder, "Elsewhere", [change("item.update", id=p["other"]["id"], revision=1, status="done")])
    second = propose(client, reviewer, "Finish the fix", [
        change("task.create", itemId=work["id"], text="Add a test"),
        change("item.update", id=work["id"], revision=2, status="done")])
    listing = history(client, f"/work-items/{work['id']}")
    assert numbered(listing) == [(2, "Finish the fix"), (1, "Start the fix")] and listing["total"] == 2
    assert [entry["id"] for entry in listing["items"]] == [second["id"], first["id"]]
    assert [entry["agentId"] for entry in listing["items"]] == [reviewer["agentId"], builder["agentId"]]
    assert listing["items"][0]["changes"] == [{"number": 1, "action": "task.create"}, {"number": 2, "action": "item.update"}]
    # Pages keep each proposal's number.
    assert numbered(history(client, f"/work-items/{work['id']}", limit=1, offset=1)) == [(1, "Start the fix")]
    assert history(client, f"/work-items/{work['id']}", limit=1, offset=5) == {"items": [], "total": 2}
    # Agents read the same history, every approved proposal being visible to them.
    assert history(client, f"/work-items/{work['id']}", builder) == listing
    assert history(client, f"/roadmaps/{p['roadmap']['id']}") == {"items": [], "total": 0}
    error(read(client, f"/work-items/{uuid.uuid4()}/proposals"), 404, "not_found")
    error(read(client, f"/roadmaps/{uuid.uuid4()}/proposals"), 404, "not_found")
    error(read(client, f"/work-items/{work['id']}/proposals?limit=201"), 422, "validation")


def test_an_agents_history_holds_everything_it_proposed_under_any_of_its_credentials(client):
    p = plan(client)
    builder, reviewer = credential(client, "Builder"), credential(client, "Reviewer")
    work = p["work"]
    propose(client, builder, "Start the fix", [change("item.update", id=work["id"], revision=1, status="doing")])
    propose(client, reviewer, "Not mine", [change("item.update", id=p["other"]["id"], revision=1, status="done")])
    propose(client, builder, "Rename it", [change("item.update", id=work["id"], revision=2, title="Nope")], "reject")
    rotated = ok(write(client, "POST", f"/credentials/{builder['id']}/rotate", {}))
    assert rotated["agentId"] == builder["agentId"]
    waiting = propose(client, rotated, "Finish the fix", [change("item.update", id=work["id"], revision=2, status="done")], None)
    drafted = ok(write(client, "POST", "/proposals", {"title": "Thinking", "rationale": "", "operations": []}, rotated))
    listing = history(client, f"/agents/{builder['agentId']}")
    assert numbered(listing) == [(4, "Thinking"), (3, "Finish the fix"), (2, "Rename it"), (1, "Start the fix")]
    assert [(e["status"], e["appliedAt"] is not None, e["operationCount"]) for e in listing["items"]] == [
        ("draft", False, 0), ("submitted", False, 1), ("rejected", False, 1), ("approved", True, 1)]
    assert listing["items"][0]["submittedAt"] is None and listing["items"][1]["submittedAt"] == waiting["submittedAt"]
    assert listing["items"][0]["id"] == drafted["id"] and "changes" not in listing["items"][0]
    assert numbered(history(client, f"/agents/{reviewer['agentId']}")) == [(1, "Not mine")]
    # Agents never read the registry, this part of it included.
    error(read(client, f"/agents/{builder['agentId']}/proposals", reviewer), 403, "agent_scope")
    error(read(client, f"/agents/{uuid.uuid4()}/proposals"), 404, "not_found")


def test_automatic_approval_records_what_it_applied(client):
    p = plan(client)
    agent = credential(client, "Builder")
    ok(write(client, "PATCH", "/settings", {"requireProposalApproval": False}))
    applied = propose(client, agent, "Applied at once", [change("item.update", id=p["work"]["id"], revision=1, status="done")])
    assert applied["status"] == "approved" and applied["autoApproved"] is True
    [entry] = history(client, f"/work-items/{p['work']['id']}")["items"]
    assert entry["autoApproved"] is True and entry["appliedAt"] == applied["updatedAt"]


def test_an_approval_that_fails_records_nothing(tmp_path, client):
    p = plan(client)
    agent = credential(client, "Builder")
    work = p["work"]
    submitted = propose(client, agent, "Twice", [change("item.update", id=work["id"], revision=1, status="doing"),
                                                 change("item.update", id=work["id"], revision=1, title="Stale")], None)
    error(write(client, "POST", f"/proposals/{submitted['id']}/approve", {"revision": submitted["revision"]}), 409, "conflict")
    assert recorded(tmp_path / "worklog.sqlite3") == []
    assert history(client, f"/work-items/{work['id']}") == {"items": [], "total": 0}


def approvals(c):
    """Proposals approved over time from two agents, one of them rotated, with the person's own
    work in between and approval turned off for a while."""
    p = plan(c)
    builder, reviewer = credential(c, "Builder"), credential(c, "Reviewer")
    first = propose(c, builder, "Set up the release", [
        change("item.create", title="Draft the release notes"),
        change("item.create", title="Tag the build", roadmapId=p["roadmap"]["id"]),
        change("roadmap.create", title="Release follow-ups", parentId=p["parent"]["id"]),
        change("task.update", id=p["task"]["id"], revision=p["task"]["revision"], done=True)])
    ok(write(c, "POST", "/work-items", {"title": "The person's own"}))
    ok(write(c, "POST", "/roadmaps", {"title": "The person's own plan"}))
    propose(c, reviewer, "Rename it", [change("item.update", id=p["work"]["id"], revision=1, title="Nope")], "reject")
    ok(write(c, "PATCH", "/settings", {"requireProposalApproval": False}))
    propose(c, reviewer, "Applied at once", [change("item.create", title="Check the tag", roadmapId=p["roadmap"]["id"])])
    ok(write(c, "PATCH", "/settings", {"requireProposalApproval": True}))
    rotated = ok(write(c, "POST", f"/credentials/{builder['id']}/rotate", {}))
    propose(c, rotated, "Plan the follow-ups", [change("roadmap.create", title="Release retro"),
                                                change("item.create", title="Book the retro", roadmapId=p["roadmap"]["id"])])
    return p, first


def test_the_v16_upgrade_fills_in_what_each_approved_proposal_was_applied_to(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        p, _ = approvals(c)
        before = {key: history(c, route) for key, route in (
            ("work", f"/work-items/{p['work']['id']}"), ("roadmap", f"/roadmaps/{p['roadmap']['id']}"),
            ("parent", f"/roadmaps/{p['parent']['id']}"))}
    kept = recorded(path)
    assert len({row[0] for row in kept}) == 3  # each approved proposal, the rejected one not
    to_v15(path)
    recovery.validate_database(path)
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # The upgrade finds what approval recorded, the work items and roadmaps the changes created
    # included, and attributes none of the person's own to a proposal.
    assert recorded(path) == kept
    snapshots = list((tmp_path / "backups").glob("worklog-pre-migration-*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    with person(tmp_path) as c:
        assert history(c, f"/work-items/{p['work']['id']}") == before["work"]
        assert history(c, f"/roadmaps/{p['roadmap']['id']}") == before["roadmap"]
        assert history(c, f"/roadmaps/{p['parent']['id']}") == before["parent"]
        assert numbered(before["roadmap"]) == [(3, "Plan the follow-ups"), (2, "Applied at once"), (1, "Set up the release")]


def test_an_upgrade_that_cannot_pair_creations_keeps_what_the_changes_name(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        p, first = approvals(c)
    kept = recorded(path)
    to_v15(path)
    with closing(sqlite3.connect(path)) as conn:
        # One of the first proposal's two new work items lost its creation from history.
        conn.execute("DELETE FROM events WHERE kind='item.create' AND entity_id=(SELECT id FROM work_items WHERE title='Tag the build')")
        conn.commit()
    Database(path).initialize()
    # Neither work item it created is named then, but the roadmap the second went on still is.
    dropped = {titled(path, "work_items", title) for title in ("Draft the release notes", "Tag the build")}
    assert recorded(path) == [row for row in kept if not (row[0] == first["id"] and row[4] in dropped)]
    assert (first["id"], 2, "item.create", "roadmap", p["roadmap"]["id"]) in recorded(path)


def test_after_the_upgrade_a_proposal_the_person_wrote_counts_only_toward_what_it_names(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        roadmap = ok(write(c, "POST", "/roadmaps", {"title": "Release"}))
        drafted = ok(write(c, "POST", "/proposals", {"title": "My own", "rationale": "", "operations": [
            change("item.create", title="Mine", roadmapId=roadmap["id"])]}))
        submitted = ok(write(c, "POST", f"/proposals/{drafted['id']}/submit", {"revision": drafted["revision"]}))
        ok(write(c, "POST", f"/proposals/{drafted['id']}/approve", {"revision": submitted["revision"]}))
    mine = titled(path, "work_items", "Mine")
    assert recorded(path) == sorted([(drafted["id"], 1, "item.create", "item", mine),
                                     (drafted["id"], 1, "item.create", "roadmap", roadmap["id"])])
    to_v15(path)
    Database(path).initialize()
    # The person creates work directly too, so history can't tell which creation was this one.
    assert recorded(path) == [(drafted["id"], 1, "item.create", "roadmap", roadmap["id"])]


def test_the_upgrade_pairs_creations_in_the_order_they_were_written_whatever_the_clock_said(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        p = plan(c)
        agent = credential(c, "Builder")
        earlier = propose(c, agent, "Start", [change("item.update", id=p["work"]["id"], revision=1, status="doing")])
        later = propose(c, agent, "Two at once", [change("item.create", title="First"), change("item.create", title="Second")])
    kept = recorded(path)
    first, second = titled(path, "work_items", "First"), titled(path, "work_items", "Second")
    assert (later["id"], 1, "item.create", "item", first) in kept and (later["id"], 2, "item.create", "item", second) in kept
    to_v15(path)
    with closing(sqlite3.connect(path)) as conn:
        # A coarse clock: both creations and both approvals in one tick, the second creation's
        # event id sorting before the first's.
        conn.execute("UPDATE events SET created_at=? WHERE (kind='item.create' AND entity_id IN (?,?)) OR kind='proposal.approve'",
                     (earlier["updatedAt"], first, second))
        conn.execute("UPDATE proposals SET updated_at=? WHERE status='approved'", (earlier["updatedAt"],))
        conn.execute("UPDATE events SET id='ffffffff-ffff-4fff-bfff-ffffffffffff' WHERE kind='item.create' AND entity_id=?", (first,))
        conn.execute("UPDATE events SET id='00000000-0000-4000-8000-000000000000' WHERE kind='item.create' AND entity_id=?", (second,))
        conn.commit()
    Database(path).initialize()
    assert recorded(path) == kept


def test_the_upgrade_passes_over_approved_changes_it_cannot_read(tmp_path):
    path = tmp_path / "worklog.sqlite3"
    # Stored rows no approval would have taken: not a list, not JSON, an unknown action, owners
    # and targets that aren't strings, and ids or actions holding a lone surrogate.
    unreadable = [
        '{"not": "a list"}', "not json", '[1, {"action": "item.retitle", "payload": {}}]',
        '[{"action": "note.create", "payload": {"ownerType": ["item"], "ownerId": "x", "text": "t"}}]',
        '[{"action": "person.link", "payload": {"personId": "p", "targetType": {"kind": "item"}, "targetId": "x"}}]',
        r'[{"action": "item.update", "payload": {"id": "\ud800", "stepId": "\udc00"}}]',
        r'[{"action": "item.upd\ud800ate", "payload": {"id": "x"}}]',
    ]
    with person(tmp_path) as c:
        p = plan(c)
        agent = credential(c, "Builder")
        good = propose(c, agent, "Readable", [change("item.update", id=p["work"]["id"], revision=1, status="doing")])
        odd = [propose(c, agent, f"Odd {n}", [change("item.update", id=p["other"]["id"], revision=1 + n, status="doing")])
               for n in range(len(unreadable))]
    to_v15(path)
    with closing(sqlite3.connect(path)) as conn:
        for proposal, stored in zip(odd, unreadable, strict=True):
            conn.execute("UPDATE proposals SET operations=? WHERE id=?", (stored, proposal["id"]))
        conn.commit()
    Database(path).initialize()
    assert recorded(path) == [(good["id"], 1, "item.update", "item", p["work"]["id"])]
    with person(tmp_path) as c:
        assert history(c, f"/agents/{agent['agentId']}")["total"] == 1 + len(unreadable)


def test_the_documented_way_back_to_v15_gives_its_exact_shape_and_upgrades_again(tmp_path):
    # The SQL tested here is the SQL the README gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    path = tmp_path / "worklog.sqlite3"
    with person(tmp_path) as c:
        approvals(c)
    kept = recorded(path)
    to_v15(path)
    recovery.validate_database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
        assert recovery._schema_signature(conn) == sealed.shape(15)
    Database(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    assert recorded(path) == kept


def test_restoring_a_v15_backup_upgrades_it_and_fills_in_its_history(tmp_path):
    source, target = tmp_path / "source" / "worklog.sqlite3", tmp_path / "target" / "worklog.sqlite3"
    with person(source.parent) as c:
        approvals(c)
    kept = recorded(source)
    to_v15(source)
    Database(target).initialize()
    recovery.restore_database(target, source, tmp_path / "restore-backups")
    with closing(sqlite3.connect(target)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    assert recorded(target) == kept


def test_a_v15_shape_cannot_claim_version_16_and_a_v16_shape_cannot_claim_15(tmp_path):
    older, current = tmp_path / "older.sqlite3", tmp_path / "current.sqlite3"
    Database(older).initialize()
    to_v15(older)
    recovery.validate_database(older)
    Database(current).initialize()
    recovery.validate_database(current)
    for path, claimed in ((older, 16), (current, 15)):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(f"PRAGMA user_version={claimed}")
        with pytest.raises(ValueError, match="schema definitions"):
            recovery.validate_database(path)
