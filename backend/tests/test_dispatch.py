"""Repo + Wave dispatch projection, WIP cap fallback, queue ordering, guardrails, and scale (schema v35)."""

from __future__ import annotations

import uuid

from test_step_update import change, client, ok, read, refused, submit, write

__all__ = ["client"]


def _credential(c, label="Bot"):
    return ok(write(c, "POST", "/credentials", {"label": label}))


def _repo(c, name, **extra):
    return ok(write(c, "POST", "/repositories", {"name": name, "url": f"https://github.com/org/{name}", **extra}))


def _roadmap(c, title="Asset Allocation", steps=None):
    return ok(write(c, "POST", "/roadmaps", {"title": title, "overview": "", "steps": steps or []}))


def test_dispatch_wip_caps_work_class_order_competing_wip_and_gate_exemption(client):
    # Repo 1: explicit wipCap=1 (Tier 1)
    r_cp = _repo(client, "control-plane", tier=1, wipCap=1)
    # Repo 2: no wipCap, but v34 slot policy configured with maxActiveLeases=2 (effectiveWipCap=2)
    r_jobs = _repo(client, "jobs", tier=2)
    ok(write(client, "PATCH", f"/repositories/{r_jobs['id']}/slot-policy", {"maxActiveLeases": 2}))
    # Repo 3: neither wipCap nor slot policy -> effectiveWipCap=None
    r_ui = _repo(client, "ui", tier=3)

    p1 = str(uuid.uuid4())
    s_stab, s_crit, s_comp, s_ref, s_multi, s_gate, s_unassigned = [str(uuid.uuid4()) for _ in range(7)]
    rm = _roadmap(client, steps=[
        {"id": p1, "title": "Phase 1: Foundation", "position": 0},
        {"id": s_stab, "title": "Fix crash loop", "parentId": p1, "position": 1,
         "workClass": "stabilization", "repositoryIds": [r_cp["id"]]},
        {"id": s_crit, "title": "Wave 1 DTO adoption", "parentId": p1, "position": 2,
         "workClass": "critical_path", "repositoryIds": [r_cp["id"]]},
        {"id": s_comp, "title": "Unrelated refactor in progress", "parentId": p1, "position": 3,
         "workClass": "refactor", "repositoryIds": [r_cp["id"]]},
        {"id": s_ref, "title": "Cleanup helpers", "parentId": p1, "position": 4,
         "workClass": "refactor", "repositoryIds": [r_cp["id"]]},
        {"id": s_multi, "title": "Cross-repo impl task", "parentId": p1, "position": 5,
         "workClass": "standard", "repositoryIds": [r_cp["id"], r_jobs["id"]]},
        {"id": s_gate, "title": "60-day golden path gate", "parentId": p1, "position": 6,
         "workClass": "gate", "repositoryIds": [r_cp["id"], r_jobs["id"], r_ui["id"]]},
        {"id": s_unassigned, "title": "[ui] Dashboard polish", "parentId": p1, "position": 7},
    ])

    # Mark s_stab, s_crit, s_comp as doing -> control-plane has 3 doing > wipCap 1 (overWipCap=True).
    for sid in (s_stab, s_crit, s_comp):
        st = ok(read(client, f"/steps/{sid}"))
        ok(write(client, "PATCH", f"/steps/{sid}/progress", {"revision": st["revision"], "status": "doing"}))

    # Activate Wave 1 containing s_crit only (s_stab is stabilization so NOT competing; s_comp IS competing!).
    w = ok(write(client, "POST", f"/roadmaps/{rm['id']}/waves", {
        "name": "Wave 1", "stepIds": [s_crit], "repositories": [{"repositoryId": r_cp["id"], "tier": 1}],
    }))
    ok(write(client, "POST", f"/waves/{w['id']}/status", {"revision": w["revision"], "status": "active"}))

    disp = ok(read(client, f"/roadmaps/{rm['id']}/dispatch"))
    assert disp["summary"]["overCapRepos"] == 1
    assert disp["summary"]["competingDoingCount"] == 1
    assert disp["summary"]["multiRepoLeafCount"] == 1
    assert disp["summary"]["unassignedLeafCount"] == 1

    # Lanes sorted: overWipCap first (control-plane), then tier (jobs=2, ui=3).
    assert [lane["repositoryId"] for lane in disp["repoLanes"]] == [r_cp["id"], r_jobs["id"], r_ui["id"]]
    cp_lane, jobs_lane, ui_lane = disp["repoLanes"]
    assert cp_lane["effectiveWipCap"] == 1
    assert cp_lane["overWipCap"] is True
    assert jobs_lane["effectiveWipCap"] == 2
    assert jobs_lane["overWipCap"] is False
    assert ui_lane["effectiveWipCap"] is None

    # Doing order inside control-plane: stabilization first, then critical_path; competing WIP separated.
    assert [c["id"] for c in cp_lane["doing"]] == [s_stab, s_crit]
    assert [c["id"] for c in cp_lane["competingWip"]] == [s_comp]
    # Queued order: standard (s_multi) -> refactor (s_ref) -> gate (s_gate).
    assert [c["id"] for c in cp_lane["queued"]] == [s_multi, s_ref, s_gate]

    # Gate is exempt from multiRepoLeaves and appears under gates.gateSteps.
    assert [c["id"] for c in disp["multiRepoLeaves"]] == [s_multi]
    assert [c["id"] for c in disp["gates"]["gateSteps"]] == [s_gate]

    # Unassigned leaf gets non-binding suggestion from "[ui]" title prefix.
    assert disp["unassignedLeaves"][0]["id"] == s_unassigned
    assert disp["unassignedLeaves"][0]["suggestedRepoIds"] == [r_ui["id"]]

    # Reorder repository queue explicitly and verify queue endpoint reflects it.
    rm_now = ok(read(client, f"/roadmaps/{rm['id']}"))
    c1 = ok(write(client, "POST", f"/roadmaps/{rm['id']}/steps", {
        "revision": rm_now["revision"], "parentId": p1, "title": "Std A", "workClass": "standard",
        "repositoryIds": [r_jobs["id"]],
    }))
    c2 = ok(write(client, "POST", f"/roadmaps/{rm['id']}/steps", {
        "revision": c1["roadmapRevision"], "parentId": p1, "title": "Std B", "workClass": "standard",
        "repositoryIds": [r_jobs["id"]],
    }))
    q_reordered = ok(write(client, "POST", f"/repositories/{r_jobs['id']}/queue/reorder", {
        "stepIds": [c2["id"], c1["id"]],
    }))
    std_ids = [x["id"] for x in q_reordered["queued"] if x["id"] in {c1["id"], c2["id"]}]
    assert std_ids == [c2["id"], c1["id"]]

    # Agents cannot reorder repository queue directly.
    agent = _credential(client, "Scout")
    refused(write(client, "POST", f"/repositories/{r_jobs['id']}/queue/reorder", {
        "stepIds": [c1["id"], c2["id"]],
    }, agent), 403, "agent_scope")


def test_overlaps_and_proposal_guardrails_surface_execution_warnings(client):
    r1 = _repo(client, "contracts", tier=1, wipCap=1)
    r2 = _repo(client, "service", tier=2, wipCap=1, upstreamIds=[r1["id"]])
    s_in, s_out, s_multi = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    rm = _roadmap(client, steps=[
        {"id": s_in, "title": "Wave work", "position": 0, "repositoryIds": [r1["id"]]},
        {"id": s_out, "title": "Off-wave work", "position": 1, "repositoryIds": [r1["id"]]},
        {"id": s_multi, "title": "Multi-repo leaf", "position": 2, "repositoryIds": [r1["id"], r2["id"]]},
    ])
    for sid in (s_in, s_out):
        st = ok(read(client, f"/steps/{sid}"))
        ok(write(client, "PATCH", f"/steps/{sid}/progress", {"revision": st["revision"], "status": "doing"}))

    w = ok(write(client, "POST", f"/roadmaps/{rm['id']}/waves", {
        "name": "Wave 1", "stepIds": [s_in],
        "repositories": [{"repositoryId": r1["id"], "tier": 1}, {"repositoryId": r2["id"], "tier": 2}],
    }))
    w = ok(write(client, "POST", f"/waves/{w['id']}/status", {"revision": w["revision"], "status": "active"}))
    ok(write(client, "POST", f"/waves/{w['id']}/freeze", {
        "revision": w["revision"], "repositoryId": r1["id"], "freezeStatus": "frozen", "frozenRef": "v1.0",
    }))

    agent = _credential(client, "Proposer")
    # coordination-overlaps includes dispatchAdvisories.
    ov = ok(read(client, f"/coordination-overlaps?repositoryId={r1['id']}&stepId={s_multi}&stepId={s_out}", agent))
    adv = ov["dispatchAdvisories"]
    r1_adv = next(r for r in adv["repositories"] if r["repositoryId"] == r1["id"])
    assert r1_adv["overWipCap"] is True
    assert r1_adv["frozenWaves"][0]["freezeStatus"] == "frozen"
    assert len(r1_adv["pendingRepins"]) == 1
    assert [s["stepId"] for s in adv["multiRepoSteps"]] == [s_multi]
    assert {s["stepId"] for s in adv["competingWaveSteps"]} == {s_out, s_multi}

    # Proposal touching s_out and s_multi surfaces identical guardrails at GET /proposals/{id}/guardrails.
    st_out = ok(read(client, f"/steps/{s_out}"))
    st_multi = ok(read(client, f"/steps/{s_multi}"))
    prop = submit(client, agent, "Touch frozen & multi-repo steps", [
        change("progress.set", id=s_out, revision=st_out["revision"], status="doing"),
        change("step.update", id=s_multi, revision=st_multi["revision"], title="Multi-repo leaf edited"),
    ])
    guard = ok(read(client, f"/proposals/{prop['id']}/guardrails"))
    assert any(r["repositoryId"] == r1["id"] and r["overWipCap"] for r in guard["repositories"])
    assert any(s["stepId"] == s_multi for s in guard["multiRepoSteps"])
    assert any(s["stepId"] == s_out for s in guard["competingWaveSteps"])


def test_dispatch_scale_120_steps_remains_lean_and_bounded_queries(client):
    repos = [_repo(client, f"repo-{i}", tier=(i % 3) + 1, wipCap=1) for i in range(4)]
    phase_id = str(uuid.uuid4())
    steps = [{"id": phase_id, "title": "Large Phase", "position": 0}]
    for i in range(120):
        steps.append({
            "id": str(uuid.uuid4()),
            "title": f"Task {i}",
            "description": "X" * 500,
            "outcome": "Y" * 200,
            "parentId": phase_id,
            "position": i + 1,
            "repositoryIds": [repos[i % 4]["id"]],
        })
    rm = _roadmap(client, title="Large Roadmap", steps=steps)
    disp = ok(read(client, f"/roadmaps/{rm['id']}/dispatch"))
    assert disp["summary"]["totalLeaves"] == 120
    # Lean projection omits prose description/outcome fields from step cards.
    sample_card = disp["repoLanes"][0]["queued"][0]
    assert "description" not in sample_card
    assert "outcome" not in sample_card
