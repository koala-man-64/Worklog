"""Step create, split, and move (schema v35) — direct human writes, proposals, and Forecast checks."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing

from test_step_update import approve, change, client, ok, read, refused, submit, write

__all__ = ["client"]


def _credential(c, label="Splitter"):
    return ok(write(c, "POST", "/credentials", {"label": label}))


def _repo(c, name, **extra):
    return ok(write(c, "POST", "/repositories", {"name": name, "url": f"https://github.com/org/{name}", **extra}))


def _roadmap(c, title="Asset Allocation", steps=None):
    return ok(write(c, "POST", "/roadmaps", {"title": title, "overview": "", "steps": steps or []}))


def test_step_create_split_and_move_preserve_phase_and_cut_no_version(client):
    r_contracts = _repo(client, "contracts", tier=1, wipCap=1)
    r_cp = _repo(client, "control-plane", tier=2, wipCap=1)
    phase_id = str(uuid.uuid4())
    leaf_id = str(uuid.uuid4())
    rm = _roadmap(client, steps=[
        {"id": phase_id, "title": "Phase 2: Execution", "position": 0},
        {
            "id": leaf_id,
            "title": "Reconcile portfolio positions",
            "parentId": phase_id,
            "position": 1,
            "workClass": "critical_path",
            "repositoryIds": [r_contracts["id"], r_cp["id"]],
        },
    ])
    versions_before = len(rm["versions"])
    # Create an active wave containing the multi-repo leaf.
    wave = ok(write(client, "POST", f"/roadmaps/{rm['id']}/waves", {
        "name": "Wave 1",
        "stepIds": [leaf_id],
        "repositories": [{"repositoryId": r_contracts["id"], "tier": 1}],
    }))
    ok(write(client, "POST", f"/waves/{wave['id']}/status", {"revision": wave["revision"], "status": "active"}))

    # Attach a work item and active participation to the leaf to test rehomeToChildIndex.
    work_item = ok(write(client, "POST", "/work-items", {
        "title": "Implement contract DTO",
        "roadmapId": rm["id"],
        "stepId": leaf_id,
    }))
    agent = _credential(client, "Worker")
    run = ok(write(client, "POST", "/agent-runs", {"clientRunKey": "r1", "displayName": "Run"}, agent))
    part = ok(write(client, "POST", "/participations", {
        "runId": run["id"],
        "stepId": leaf_id,
        "description": "Working on split leaf",
        "contexts": [{"repositoryId": r_contracts["id"], "branch": "feat/dto"}],
    }, agent))

    step_before = ok(read(client, f"/steps/{leaf_id}"))
    split = ok(write(client, "POST", f"/steps/{leaf_id}/split", {
        "revision": step_before["revision"],
        "clearParentRepositories": True,
        "inheritWaves": True,
        "rehomeToChildIndex": 0,
        "children": [
            {
                "title": "contracts: Add position schema",
                "repositoryIds": [r_contracts["id"]],
                "workClass": "critical_path",
                "status": "doing",
            },
            {
                "title": "control-plane: Consume position schema",
                "repositoryIds": [r_cp["id"]],
                "status": "open",
            },
        ],
    }))
    assert split["parent"]["repositoryIds"] == []
    assert split["parent"]["status"] == "doing"  # derived from child 0 doing + child 1 open
    assert len(split["children"]) == 2
    c0, c1 = split["children"]
    assert c0["parentId"] == leaf_id
    assert c0["repositoryIds"] == [r_contracts["id"]]
    assert c1["repositoryIds"] == [r_cp["id"]]
    assert c1["workClass"] == "critical_path"  # inherited from parent when omitted

    # Wave inherited onto children and removed from parent; c1's repo auto-added to wave_repositories.
    wave_after = ok(read(client, f"/waves/{wave['id']}"))
    assert set(wave_after["stepIds"]) == {c0["id"], c1["id"]}
    assert {r["repositoryId"] for r in wave_after["repositories"]} == {r_contracts["id"], r_cp["id"]}

    # Work item & active participation rehomed to child 0.
    assert ok(read(client, f"/work-items/{work_item['id']}"))["stepId"] == c0["id"]
    assert ok(read(client, f"/participations?stepId={c0['id']}"))["items"][0]["id"] == part["id"]

    # Splitting a non-leaf step is refused.
    refused(write(client, "POST", f"/steps/{leaf_id}/split", {
        "revision": split["parent"]["revision"],
        "children": [{"title": "A"}, {"title": "B"}],
    }), 422, "validation")

    # step.create adds a sibling without cutting a roadmap version snapshot.
    rm_now = ok(read(client, f"/roadmaps/{rm['id']}"))
    assert len(rm_now["versions"]) == versions_before
    created = ok(write(client, "POST", f"/roadmaps/{rm['id']}/steps", {
        "revision": rm_now["revision"],
        "parentId": phase_id,
        "title": "Verify rollout gate",
        "workClass": "gate",
        "repositoryIds": [r_contracts["id"], r_cp["id"]],
    }))
    assert created["parentId"] == phase_id
    assert created["workClass"] == "gate"

    # step.move moves step under another parent and rejects self/descendant cycles.
    refused(write(client, "POST", f"/steps/{phase_id}/move", {
        "revision": created["roadmapRevision"],
        "parentId": leaf_id,
    }), 422, "validation")
    moved = ok(write(client, "POST", f"/steps/{created['id']}/move", {
        "revision": created["roadmapRevision"],
        "parentId": None,
        "position": 0,
    }))
    assert moved["parentId"] is None
    assert moved["position"] == 0
    assert len(ok(read(client, f"/roadmaps/{rm['id']}"))["versions"]) == versions_before

    # Direct agent calls are forbidden (agents must propose).
    refused(write(client, "POST", f"/roadmaps/{rm['id']}/steps", {
        "revision": moved["roadmapRevision"], "title": "Nope",
    }, agent), 403, "agent_scope")
    refused(write(client, "POST", f"/steps/{c0['id']}/split", {
        "revision": c0["revision"], "children": [{"title": "A"}, {"title": "B"}],
    }, agent), 403, "agent_scope")


def test_agent_proposes_step_create_split_and_move_with_forecast(client):
    agent = _credential(client, "Planner")
    r1 = _repo(client, "repo-a")
    r2 = _repo(client, "repo-b")
    phase_id = str(uuid.uuid4())
    leaf_id = str(uuid.uuid4())
    new_step_id = str(uuid.uuid4())
    child_a_id = str(uuid.uuid4())
    child_b_id = str(uuid.uuid4())
    rm = _roadmap(client, steps=[
        {"id": phase_id, "title": "Phase 1", "position": 0},
        {"id": leaf_id, "title": "Shared leaf", "parentId": phase_id, "position": 1,
         "repositoryIds": [r1["id"], r2["id"]]},
    ])
    leaf = ok(read(client, f"/steps/{leaf_id}"))

    # Multi-op proposal: create step, split existing leaf with explicit UUIDs, set progress on new child, move created step.
    proposal = submit(client, agent, "Split shared leaf and add follow-up", [
        change("step.create", roadmapId=rm["id"], revision=rm["revision"], id=new_step_id,
               parentId=phase_id, title="Follow-up", repositoryIds=[r1["id"]], workClass="standard"),
        change("step.split", id=leaf_id, revision=leaf["revision"], children=[
            {"id": child_a_id, "title": "repo-a part", "repositoryIds": [r1["id"]]},
            {"id": child_b_id, "title": "repo-b part", "repositoryIds": [r2["id"]]},
        ]),
        change("progress.set", id=child_a_id, revision=1, status="doing"),
        change("step.move", id=new_step_id, revision=rm["revision"] + 2, parentId=None, position=0),
    ])
    # Forecast predicts exact revisions so proposal is NOT reported stale before approval.
    stale_list = ok(read(client, f"/proposals/{proposal['id']}"))
    assert stale_list["status"] == "submitted"
    approved = ok(approve(client, proposal))
    assert approved["status"] == "approved"

    # Verify DB state matches every operation and zero extra roadmap_versions were cut.
    with closing(sqlite3.connect(client.db_path)) as conn:
        ver_count = conn.execute("SELECT COUNT(*) FROM roadmap_versions WHERE roadmap_id=?", (rm["id"],)).fetchone()[0]
    assert ver_count == 1
    assert ok(read(client, f"/steps/{child_a_id}"))["status"] == "doing"
    assert ok(read(client, f"/steps/{new_step_id}"))["parentId"] is None
    assert ok(read(client, f"/steps/{leaf_id}"))["repositoryIds"] == []
