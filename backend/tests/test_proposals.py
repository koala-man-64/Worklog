import json

import pytest
from worklog.common import AppError, event, require_revision
from worklog.db import Database
from worklog.plans import PlanService
from worklog.proposals import ProposalService
from worklog.services import WorkService

HUMAN = {"id": "human", "label": "Rudy", "kind": "human"}
AGENT = {"id": "agent", "label": "Engineer", "kind": "agent"}
OTHER = {"id": "other", "label": "Other", "kind": "agent"}


@pytest.fixture
def proposals(tmp_path):
    db = Database(tmp_path / "worklog.db")
    db.initialize()
    with db.transaction() as conn:
        conn.execute("CREATE TABLE test_targets(id TEXT PRIMARY KEY, revision INTEGER, title TEXT)")
        conn.execute("INSERT INTO test_targets VALUES('target',1,'Before')")

    def dispatch(conn, actor, action, payload):
        row = conn.execute("SELECT * FROM test_targets WHERE id=?", (payload["id"],)).fetchone()
        require_revision(row, payload["revision"])
        conn.execute("UPDATE test_targets SET title=?,revision=revision+1 WHERE id=?", (payload["title"], payload["id"]))
        event(conn, actor, action, payload["id"], dict(row), payload)
        if payload["title"] == "FAIL":
            raise RuntimeError("injected dispatch failure")
        return payload

    return ProposalService(db, dispatch)


def draft(proposals, operations=None):
    return proposals.create({"title": "Review", "rationale": "Reason", "operations": operations or [
        {"action": "item.update", "payload": {"id": "target", "revision": 1, "title": "After"}}]}, AGENT)


def submit(proposals, proposal):
    return proposals.transition(proposal["id"], "submit", {"revision": proposal["revision"]}, AGENT)


def store_approval_setting(proposals, value):
    with proposals.db.transaction() as conn:
        conn.execute("INSERT INTO settings VALUES('requireProposalApproval',?)", (value,))


def test_human_only_approval_attribution_and_no_implicit_writes(proposals):
    proposal = draft(proposals)
    with proposals.db.read() as conn:
        assert conn.execute("SELECT title FROM test_targets").fetchone()[0] == "Before"
    submitted = submit(proposals, proposal)
    with pytest.raises(AppError, match="Only a human"):
        proposals.transition(proposal["id"], "approve", {"revision": 2}, AGENT)
    result = proposals.transition(proposal["id"], "approve", {"revision": submitted["revision"]}, HUMAN)
    assert result["status"] == "approved" and result["autoApproved"] is False
    with proposals.db.read() as conn:
        assert conn.execute("SELECT title FROM test_targets").fetchone()[0] == "After"
        assert conn.execute("SELECT actor_kind FROM events WHERE kind='item.update'").fetchone()[0] == "agent"
        assert conn.execute("SELECT actor_kind FROM events WHERE kind='proposal.approve'").fetchone()[0] == "human"
    with pytest.raises(AppError):
        proposals.transition(proposal["id"], "approve", {"revision": result["revision"]}, HUMAN)


def test_stale_second_operation_rolls_back_everything(proposals):
    operations = [
        {"action": "item.update", "payload": {"id": "target", "revision": 1, "title": "First"}},
        {"action": "item.update", "payload": {"id": "target", "revision": 1, "title": "Stale"}},
    ]
    proposal = submit(proposals, draft(proposals, operations))
    with pytest.raises(AppError):
        proposals.transition(proposal["id"], "approve", {"revision": proposal["revision"]}, HUMAN)
    assert proposals.detail(proposal["id"], HUMAN)["status"] == "submitted"
    with proposals.db.read() as conn:
        assert conn.execute("SELECT title,revision FROM test_targets").fetchone()[:] == ("Before", 1)
        assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='item.update'").fetchone()[0] == 0


def test_injected_failure_rolls_back_and_optional_connection(proposals):
    proposal = submit(proposals, draft(proposals, [
        {"action": "item.update", "payload": {"id": "target", "revision": 1, "title": "FAIL"}}]))
    with pytest.raises(RuntimeError, match="injected"):
        with proposals.db.transaction() as conn:
            proposals.transition(proposal["id"], "approve", {"revision": proposal["revision"]}, HUMAN, conn=conn)
    with proposals.db.read() as conn:
        assert conn.execute("SELECT title FROM test_targets").fetchone()[0] == "Before"
    assert proposals.detail(proposal["id"], HUMAN)["status"] == "submitted"
    with pytest.raises(RuntimeError):
        with proposals.db.transaction() as conn:
            proposals.create({"title": "Rollback"}, AGENT, conn=conn)
            raise RuntimeError()
    assert proposals.list(HUMAN)["total"] == 1


def test_with_approval_off_submitting_applies_and_records_an_automatic_approval(proposals):
    store_approval_setting(proposals, "false")
    result = submit(proposals, draft(proposals))
    assert result["status"] == "approved" and result["autoApproved"] is True
    assert proposals.detail(result["id"], OTHER) == result
    assert proposals.list(OTHER, "approved")["items"] == [result]
    with proposals.db.read() as conn:
        assert conn.execute("SELECT title FROM test_targets").fetchone()[0] == "After"
        # The change keeps its proposer; the approval names the person's setting, not the person.
        assert [tuple(r) for r in conn.execute("SELECT kind,actor_id,actor_kind FROM events ORDER BY rowid")] == [
            ("proposal.create", "agent", "agent"), ("proposal.submit", "agent", "agent"),
            ("item.update", "agent", "agent"), ("proposal.approve", "automatic-approval", "human")]
        approval = conn.execute("SELECT after_json FROM events WHERE kind='proposal.approve'").fetchone()[0]
    assert json.loads(approval) == result


def test_with_approval_off_a_failed_apply_leaves_the_draft_unsubmitted(proposals):
    proposal = draft(proposals, [
        {"action": "item.update", "payload": {"id": "target", "revision": 1, "title": "First"}},
        {"action": "item.update", "payload": {"id": "target", "revision": 1, "title": "Stale"}},
    ])
    store_approval_setting(proposals, "false")
    with pytest.raises(AppError) as error:
        submit(proposals, proposal)
    assert error.value.status == 409 and error.value.message.startswith("Not submitted: with approval off")
    assert proposals.detail(proposal["id"], AGENT) == proposal
    with proposals.db.read() as conn:
        assert conn.execute("SELECT title,revision FROM test_targets").fetchone()[:] == ("Before", 1)
        assert [r[0] for r in conn.execute("SELECT kind FROM events ORDER BY rowid")] == ["proposal.create"]
    fixed = proposals.update(proposal["id"], {"revision": proposal["revision"], "operations": [
        {"action": "item.update", "payload": {"id": "target", "revision": 1, "title": "After"}}]}, AGENT)
    assert submit(proposals, fixed)["status"] == "approved"


@pytest.mark.parametrize("stored", ["true", "0", "null", '"false"'])
def test_only_an_explicit_false_turns_approval_off(proposals, stored):
    store_approval_setting(proposals, stored)
    assert submit(proposals, draft(proposals))["status"] == "submitted"


def test_owned_draft_visibility_editing_and_uploads(proposals):
    proposal = draft(proposals)
    assert proposals.list(OTHER)["total"] == 0
    with pytest.raises(AppError) as exc:
        proposals.detail(proposal["id"], OTHER)
    assert exc.value.status == 404
    with pytest.raises(AppError):
        proposals.update(proposal["id"], {"revision": 1, "title": "Takeover"}, HUMAN)
    with proposals.db.transaction() as conn:
        proposals.check_upload(conn, proposal["id"], AGENT)
    changed = proposals.update(proposal["id"], {"revision": 1, "title": "Edited"}, AGENT)
    submitted = submit(proposals, changed)
    assert proposals.detail(proposal["id"], OTHER)["status"] == "submitted"
    with proposals.db.transaction() as conn, pytest.raises(AppError):
        proposals.check_upload(conn, proposal["id"], AGENT)
    with pytest.raises(AppError):
        proposals.update(proposal["id"], {"revision": submitted["revision"], "title": "Changed after review"}, AGENT)


@pytest.mark.parametrize("operation", [
    {"action": "shell.execute", "payload": {"command": "run"}},
    {"action": "item.update", "payload": {"id": "target", "title": "No revision"}},
    {"action": "item.update", "payload": {"id": "target", "revision": 1, "sql": "DELETE"}},
    {"action": "item.update", "payload": {"id": "target", "revision": True}},
    {"action": "item.update", "payload": []},
])
def test_operations_are_allowlisted_and_revision_bound(proposals, operation):
    with pytest.raises(AppError):
        draft(proposals, [operation])
    assert proposals.list(HUMAN)["total"] == 0


def test_real_dispatch_stale_approval_keeps_source_and_search_unchanged(tmp_path):
    db = Database(tmp_path / "integrated.db")
    db.initialize()
    plans = PlanService(db)
    work = WorkService(db, plans)
    service = ProposalService(db, work.apply)
    work.proposals = service
    with db.transaction() as conn:
        item = work.apply(conn, HUMAN, "item.create", {"title": "Original"})
    proposal = service.create({"title": "Review", "operations": [
        {"action": "roadmap.create", "payload": {"title": "Uncommitted roadmap"}},
        {"action": "item.update", "payload": {"id": item["id"], "revision": 1, "title": "Proposed"}},
    ]}, AGENT)
    proposal = service.transition(proposal["id"], "submit", {"revision": 1}, AGENT)
    with db.transaction() as conn:
        work.apply(conn, HUMAN, "item.update", {"id": item["id"], "revision": 1, "title": "Human edit"})
    with pytest.raises(AppError) as error:
        service.transition(proposal["id"], "approve", {"revision": proposal["revision"]}, HUMAN)
    assert error.value.status == 409
    assert work.detail(item["id"])["title"] == "Human edit"
    assert plans.list()["total"] == 0
    assert work.search("Uncommitted")["total"] == 0
    assert service.detail(proposal["id"], HUMAN)["status"] == "submitted"


def test_approved_item_completion_does_not_change_linked_step_progress(tmp_path):
    db = Database(tmp_path / "integrated.db")
    db.initialize()
    plans = PlanService(db)
    work = WorkService(db, plans)
    service = ProposalService(db, work.apply)
    work.proposals = service
    with db.transaction() as conn:
        plan = plans.apply(conn, HUMAN, "roadmap.create", {"title": "Plan", "steps": [{"title": "Explicit progress"}]})
        step = plan["steps"][0]
        item = work.apply(conn, HUMAN, "item.create", {"title": "Linked item", "stepId": step["id"]})
    proposal = service.create({"title": "Complete work", "operations": [
        {"action": "item.update", "payload": {"id": item["id"], "revision": 1, "status": "done"}},
    ]}, AGENT)
    proposal = service.transition(proposal["id"], "submit", {"revision": 1}, AGENT)
    service.transition(proposal["id"], "approve", {"revision": proposal["revision"]}, HUMAN)
    assert work.detail(item["id"])["status"] == "done"
    assert plans.detail(plan["id"])["steps"][0]["status"] == "open"


def test_github_ref_links_to_chat_by_external_chat_id_even_when_listed_before_chat_create(tmp_path):
    db = Database(tmp_path / "integrated.db")
    db.initialize()
    plans = PlanService(db)
    work = WorkService(db, plans)
    service = ProposalService(db, work.apply)
    work.proposals = service
    with db.transaction() as conn:
        roadmap = plans.apply(conn, HUMAN, "roadmap.create", {"title": "Roadmap", "steps": [{"title": "Cutover"}]})
        step = roadmap["steps"][0]
        item = work.apply(conn, HUMAN, "item.create", {"title": "Contracts cutover", "roadmapId": roadmap["id"], "stepId": step["id"]})
        other_item = work.apply(conn, HUMAN, "item.create", {"title": "Unrelated item"})
        unrelated_chat = work.apply(conn, HUMAN, "chat.create", {
            "ownerType": "item",
            "ownerId": other_item["id"],
            "chatId": "unrelated-session",
            "title": "Unrelated chat",
        })
    proposal = service.create({
        "title": "Pre-publication evidence and chat provenance",
        "rationale": "Record PR and chat together.",
        "operations": [
            {
                "action": "github.create",
                "payload": {
                    "ownerType": "item",
                    "ownerId": item["id"],
                    "kind": "pr",
                    "url": "https://github.com/acme/contracts/pull/4556",
                    "title": "Replace versionMatrix with components",
                    "chatId": "7232a0ef-fe1b-4d86-8dfb-e9cd9a8e0ea2",
                },
            },
            {
                "action": "chat.create",
                "payload": {
                    "ownerType": "item",
                    "ownerId": item["id"],
                    "chatId": "7232a0ef-fe1b-4d86-8dfb-e9cd9a8e0ea2",
                    "title": "Publication & adoption coordination",
                },
            },
        ],
    }, AGENT)
    proposal = service.transition(proposal["id"], "submit", {"revision": 1}, AGENT)
    approved = service.transition(proposal["id"], "approve", {"revision": proposal["revision"]}, HUMAN)
    assert approved["status"] == "approved"
    detail = work.detail(item["id"])
    assert len(detail["chats"]) == 1
    assert len(detail["github"]) == 1
    assert detail["github"][0]["chatId"] == detail["chats"][0]["id"]
    assert detail["github"][0]["chatTitle"] == "Publication & adoption coordination"

    # A chat tracked on the item's roadmap (or a roadmap ref linking to an item chat) also resolves.
    with db.transaction() as conn:
        road_chat = work.apply(conn, HUMAN, "chat.create", {
            "ownerType": "roadmap",
            "ownerId": roadmap["id"],
            "chatId": "road-session-1",
            "title": "Roadmap planning chat",
            "stepIds": [step["id"]],
        })
        from_road = work.apply(conn, HUMAN, "github.create", {
            "ownerType": "item",
            "ownerId": item["id"],
            "kind": "commit",
            "url": "https://github.com/acme/contracts/commit/e52ae299c9dd6b6c050d4d6fe1afcda90d46e8a7",
            "chatId": "road-session-1",
        })
        from_item = work.apply(conn, HUMAN, "github.create", {
            "ownerType": "roadmap",
            "ownerId": roadmap["id"],
            "kind": "pr",
            "url": "https://github.com/acme/contracts/pull/4557",
            "chatId": "7232a0ef-fe1b-4d86-8dfb-e9cd9a8e0ea2",
        })
        assert from_road["chatId"] == road_chat["id"]
        assert from_road["chatTitle"] == "Roadmap planning chat"
        assert from_item["chatId"] == detail["chats"][0]["id"]
        assert from_item["chatTitle"] == "Publication & adoption coordination"
        with pytest.raises(AppError, match="Agent chat does not belong to this record"):
            work.apply(conn, HUMAN, "github.create", {
                "ownerType": "item",
                "ownerId": item["id"],
                "kind": "pr",
                "url": "https://github.com/acme/contracts/pull/9999",
                "chatId": unrelated_chat["id"],
            })
        with pytest.raises(AppError, match="Agent chat does not belong to this record"):
            work.apply(conn, HUMAN, "github.create", {
                "ownerType": "item",
                "ownerId": item["id"],
                "kind": "pr",
                "url": "https://github.com/acme/contracts/pull/9999",
                "chatId": "unrelated-session",
            })

