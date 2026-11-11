import pytest
from worklog.common import AppError, read_events, uid
from worklog.db import Database
from worklog.plans import PlanService

HUMAN = {"id": "human", "label": "Rudy", "kind": "human"}


@pytest.fixture
def plans(tmp_path):
    db = Database(tmp_path / "worklog.db")
    db.initialize()
    return PlanService(db)


def apply(plans, action, payload):
    with plans.db.transaction() as conn:
        return plans.apply(conn, HUMAN, action, payload)


def test_version_retirement_restore_preserves_identity_and_live_progress(plans):
    parent, leaf = uid(), uid()
    steps = [{"id": parent, "title": "Parent"}, {"id": leaf, "title": "Leaf", "parentId": parent}]
    created = apply(plans, "roadmap.create", {"title": "Plan", "steps": steps})
    assert created["revision"] == 1
    original = plans.version(created["id"], 1)
    progress = apply(plans, "progress.set", {"id": leaf, "revision": 1, "status": "done"})
    current = plans.detail(created["id"])
    assert {s["status"] for s in current["steps"]} == {"done"}
    assert plans.version(created["id"], 1) == original
    with pytest.raises(AppError, match="Parent progress"):
        apply(plans, "progress.set", {"id": parent, "revision": 1, "status": "done"})
    retired = apply(plans, "roadmap.revise", {"id": created["id"], "revision": current["revision"], "title": "Empty", "steps": []})
    assert all(s["retired"] for s in retired["steps"])
    restored = apply(plans, "roadmap.restore", {"id": created["id"], "revision": retired["revision"], "number": 1})
    assert all(not s["retired"] for s in restored["steps"])
    assert next(s for s in restored["steps"] if s["id"] == leaf)["status"] == progress["status"]
    assert len(restored["versions"]) == 3
    assert {e["kind"] for e in plans.history(leaf)["items"]} >= {"step.retire", "step.restore", "progress.set"}


def test_tree_validation_is_atomic(plans):
    a, b = uid(), uid()
    for steps in ([{"id": a, "title": "A", "parentId": b}, {"id": b, "title": "B", "parentId": a}],
                  [{"id": a, "title": "A"}, {"id": a, "title": "Again"}],
                  [{"id": a, "title": "A", "parentId": b}]):
        with pytest.raises(AppError):
            apply(plans, "roadmap.create", {"title": "Invalid", "steps": steps})
    assert plans.list()["total"] == 0


def test_roadmaps_nest_move_and_reject_cycles_without_touching_versions(plans):
    top = apply(plans, "roadmap.create", {"title": "Top", "steps": [{"title": "Step"}]})
    middle = apply(plans, "roadmap.create", {"title": "Middle", "parentId": top["id"]})
    leaf = apply(plans, "roadmap.create", {"title": "Leaf", "parentId": middle["id"]})
    assert (top["parentId"], middle["parentId"], leaf["parentId"]) == (None, top["id"], middle["id"])
    assert [r["parentId"] for r in plans.list()["items"]] == [None, top["id"], middle["id"]]
    assert [a["title"] for a in plans.detail(leaf["id"])["ancestors"]] == ["Top", "Middle"]
    assert [c["id"] for c in plans.detail(top["id"])["children"]] == [middle["id"]]

    for target in (top["id"], middle["id"], leaf["id"]):
        with pytest.raises(AppError, match="itself or its sub-roadmaps") as error:
            apply(plans, "roadmap.move", {"id": top["id"], "revision": top["revision"], "parentId": target})
        assert error.value.status == 422
    with pytest.raises(AppError, match="does not exist"):
        apply(plans, "roadmap.move", {"id": leaf["id"], "revision": 1, "parentId": uid()})
    with pytest.raises(AppError, match="does not exist"):
        apply(plans, "roadmap.create", {"title": "Orphan", "parentId": uid()})
    with pytest.raises(AppError) as error:
        apply(plans, "roadmap.move", {"id": leaf["id"], "revision": 0, "parentId": None})
    assert error.value.status == 409

    moved = apply(plans, "roadmap.move", {"id": leaf["id"], "revision": 1, "parentId": top["id"]})
    assert moved["parentId"] == top["id"] and moved["revision"] == 2
    assert len(moved["versions"]) == 1
    assert [c["id"] for c in plans.detail(top["id"])["children"]] == [middle["id"], leaf["id"]]
    assert {e["kind"] for e in read_history(plans, leaf["id"])} >= {"roadmap.move"}

    # Revising or restoring the plan content keeps the roadmap where it is.
    revised = apply(plans, "roadmap.revise", {"id": leaf["id"], "revision": 2, "title": "Leaf v2", "steps": []})
    restored = apply(plans, "roadmap.restore", {"id": leaf["id"], "revision": revised["revision"], "number": 1})
    assert revised["parentId"] == restored["parentId"] == top["id"]
    assert restored["title"] == "Leaf"

    unnested = apply(plans, "roadmap.move", {"id": leaf["id"], "revision": restored["revision"], "parentId": None})
    assert unnested["parentId"] is None and plans.detail(leaf["id"])["ancestors"] == []


def test_archived_roadmaps_keep_children_but_accept_no_new_placement(plans):
    parent = apply(plans, "roadmap.create", {"title": "Parent"})
    child = apply(plans, "roadmap.create", {"title": "Child", "parentId": parent["id"]})
    other = apply(plans, "roadmap.create", {"title": "Other"})
    archived = apply(plans, "roadmap.archive", {"id": parent["id"], "revision": parent["revision"]})
    assert plans.detail(child["id"])["ancestors"] == [{"id": parent["id"], "title": "Parent", "archived": True}]
    assert [c["id"] for c in plans.detail(parent["id"])["children"]] == [child["id"]]
    for action, payload in (("roadmap.create", {"title": "New", "parentId": parent["id"]}),
                            ("roadmap.move", {"id": other["id"], "revision": other["revision"], "parentId": parent["id"]})):
        with pytest.raises(AppError, match="Recover the parent") as error:
            apply(plans, action, payload)
        assert error.value.status == 409
    with pytest.raises(AppError, match="recovered before editing"):
        apply(plans, "roadmap.move", {"id": parent["id"], "revision": archived["revision"], "parentId": None})
    recovered = apply(plans, "roadmap.recover", {"id": parent["id"], "revision": archived["revision"]})
    assert apply(plans, "roadmap.move", {"id": other["id"], "revision": other["revision"], "parentId": recovered["id"]})["parentId"] == parent["id"]


def read_history(plans, identifier):
    with plans.db.read() as conn:
        return read_events(conn, identifier)


def test_cross_roadmap_step_identity_cannot_be_stolen(plans):
    sid = uid()
    apply(plans, "roadmap.create", {"title": "A", "steps": [{"id": sid, "title": "Owned"}]})
    with pytest.raises(AppError, match="another roadmap"):
        apply(plans, "roadmap.create", {"title": "B", "steps": [{"id": sid, "title": "Stolen"}]})
    assert plans.list()["total"] == 1


def test_new_steps_ignore_caller_progress_and_progress_leaves_the_plan_revision(plans):
    created = apply(plans, "roadmap.create", {"title": "Plan", "steps": [{"title": "New", "status": "done"}]})
    assert created["steps"][0]["status"] == "open"
    apply(plans, "progress.set", {"id": created["steps"][0]["id"], "revision": 1, "status": "doing"})
    # Progress is the step's: the roadmap's revision guards its structure, which progress leaves (K4).
    renamed = apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Renamed", "steps": created["steps"]})
    assert renamed["revision"] == 2 and renamed["steps"][0]["status"] == "doing"
    with pytest.raises(AppError) as error:
        apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Stale", "steps": []})
    assert error.value.status == 409
