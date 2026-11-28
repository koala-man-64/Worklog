"""Pre-completion step gates: CRUD, hybrid auto-evaluation, leaf blocking, parent roll-up, and cycles."""

import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from worklog.app import create_app
from worklog.doctor import doctor_database

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        c.data_dir = tmp_path  # type: ignore[attr-defined]
        yield c


def req(c, method, path, payload=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())} if method != "GET" else {}
    if payload is not None:
        return c.request(method, "/api/v1" + path, headers=headers, json=payload)
    return c.request(method, "/api/v1" + path, headers=headers)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def err(response, status, code):
    assert response.status_code == status, response.text
    result = response.json()["error"]
    assert result["code"] == code
    return result


def make_plan(c, title="Release Plan"):
    phase = str(uuid.uuid4())
    leaf_a = str(uuid.uuid4())
    leaf_b = str(uuid.uuid4())
    roadmap = ok(req(c, "POST", "/roadmaps", {
        "title": title,
        "steps": [
            {"id": phase, "title": "Phase 1", "parentId": None, "position": 0},
            {"id": leaf_a, "title": "Build service", "parentId": phase, "position": 0},
            {"id": leaf_b, "title": "Run rollout", "parentId": phase, "position": 1},
        ],
    }))
    return roadmap, phase, leaf_a, leaf_b


def step_by_id(roadmap_detail, step_id):
    return next(s for s in roadmap_detail["steps"] if s["id"] == step_id)


def test_manual_gate_blocks_leaf_completion_until_passed_or_waived(client):
    roadmap, phase, leaf_a, leaf_b = make_plan(client)
    gate = ok(req(client, "POST", f"/steps/{leaf_a}/gates", {
        "title": "Security review signed off",
        "criteria": "Confirm auth boundary before shipping.",
        "kind": "approval",
        "humanOnly": True,
    }))
    assert gate["state"] == "open"
    assert gate["satisfied"] is False
    assert gate["humanOnly"] is True
    assert gate["autoEvaluated"] is False

    # Marking leaf_a done is hard-blocked with 409 open_gates naming the blocking gate.
    blocked = err(
        req(client, "PATCH", f"/steps/{leaf_a}/progress", {"revision": 1, "status": "done"}),
        409,
        "open_gates",
    )
    assert blocked["fields"]["stepId"] == leaf_a
    assert blocked["fields"]["gates"][0]["id"] == gate["id"]
    assert blocked["fields"]["gates"][0]["humanOnly"] is True

    # Non-done statuses (doing, blocked, open) remain allowed while a gate is open.
    doing = ok(req(client, "PATCH", f"/steps/{leaf_a}/progress", {"revision": 1, "status": "doing"}))
    assert doing["status"] == "doing"
    assert doing["gateSummary"]["blocking"] == 1
    assert doing["gateSummary"]["humanOnlyBlocking"] == 1

    # Passing the manual gate unlocks completion.
    passed = ok(req(client, "POST", f"/gates/{gate['id']}/pass", {
        "revision": gate["revision"],
        "note": "Auth boundary verified",
    }))
    assert passed["state"] == "passed"
    assert passed["satisfied"] is True
    assert passed["resolution"]["note"] == "Auth boundary verified"
    assert passed["resolution"]["actorKind"] == "human"

    done_a = ok(req(client, "PATCH", f"/steps/{leaf_a}/progress", {
        "revision": doing["revision"],
        "status": "done",
    }))
    assert done_a["status"] == "done"
    assert done_a["gateSummary"]["blocking"] == 0
    assert done_a["gateSummary"]["passed"] == 1

    # Waiving requires a non-blank reason and unlocks leaf_b too.
    waive_gate = ok(req(client, "POST", f"/steps/{leaf_b}/gates", {"title": "24h soak"}))
    err(req(client, "POST", f"/gates/{waive_gate['id']}/waive", {
        "revision": waive_gate["revision"], "reason": "   ",
    }), 422, "validation")
    waived = ok(req(client, "POST", f"/gates/{waive_gate['id']}/waive", {
        "revision": waive_gate["revision"], "reason": "Hotfix approved by lead",
    }))
    assert waived["state"] == "waived"
    assert waived["satisfied"] is True
    assert waived["resolution"]["note"] == "Hotfix approved by lead"

    ok(req(client, "PATCH", f"/steps/{leaf_b}/progress", {"revision": 1, "status": "done"}))
    detail = ok(req(client, "GET", f"/roadmaps/{roadmap['id']}"))
    assert step_by_id(detail, phase)["status"] == "done"

    # History captures create + pass events.
    hist = ok(req(client, "GET", f"/gates/{gate['id']}/history"))
    assert [e["kind"] for e in hist["items"]] == ["gate.pass", "gate.create"]


def test_parent_gate_holds_phase_rollup_until_satisfied_and_reopen_reholds(client):
    roadmap, phase, leaf_a, leaf_b = make_plan(client)
    phase_gate = ok(req(client, "POST", f"/steps/{phase}/gates", {
        "title": "Phase 1 release sign-off",
        "kind": "approval",
        "humanOnly": True,
    }))
    ok(req(client, "PATCH", f"/steps/{leaf_a}/progress", {"revision": 1, "status": "done"}))
    ok(req(client, "PATCH", f"/steps/{leaf_b}/progress", {"revision": 1, "status": "done"}))

    # All leaves are done, but the parent's open gate holds the phase in 'doing'.
    detail = ok(req(client, "GET", f"/roadmaps/{roadmap['id']}"))
    assert step_by_id(detail, phase)["status"] == "doing"
    assert step_by_id(detail, phase)["gateSummary"]["blocking"] == 1
    outline = ok(req(client, "GET", f"/roadmaps/{roadmap['id']}/outline"))
    assert next(s for s in outline["steps"] if s["id"] == phase)["status"] == "doing"
    dispatch = ok(req(client, "GET", f"/roadmaps/{roadmap['id']}/dispatch"))
    assert dispatch["gates"]["phases"][0]["status"] == "doing"

    # Passing the parent gate immediately rolls the phase up to 'done'.
    passed = ok(req(client, "POST", f"/gates/{phase_gate['id']}/pass", {
        "revision": phase_gate["revision"],
        "note": "Signed off",
    }))
    detail = ok(req(client, "GET", f"/roadmaps/{roadmap['id']}"))
    assert step_by_id(detail, phase)["status"] == "done"

    # Reopening the parent gate drops the phase back to 'doing'.
    reopened = ok(req(client, "POST", f"/gates/{phase_gate['id']}/reopen", {
        "revision": passed["revision"],
        "reason": "Regression found in staging",
    }))
    assert reopened["state"] == "open"
    assert reopened["resolution"] is None
    detail = ok(req(client, "GET", f"/roadmaps/{roadmap['id']}"))
    assert step_by_id(detail, phase)["status"] == "doing"


def test_hybrid_linked_gates_auto_evaluate_across_all_four_target_types(client):
    roadmap, phase, leaf_a, leaf_b = make_plan(client)

    # Create linked targets: deliverable, ticket, question, and prerequisite step (leaf_a).
    deliverable = ok(req(client, "POST", f"/roadmaps/{roadmap['id']}/deliverables", {
        "title": "API spec",
        "requirements": [{"text": "Schema frozen", "checked": True, "verificationNote": "Checked"}],
    }))
    ticket = ok(req(client, "POST", "/tickets", {"title": "Fix race condition"}))
    question = ok(req(client, "POST", "/questions", {
        "roadmapId": roadmap["id"], "title": "Which region deploys first?",
    }))

    g_step = ok(req(client, "POST", f"/steps/{leaf_b}/gates", {
        "title": "Service built", "target": {"type": "step", "id": leaf_a},
    }))
    g_del = ok(req(client, "POST", f"/steps/{leaf_b}/gates", {
        "title": "API spec accepted", "target": {"type": "deliverable", "id": deliverable["id"]},
    }))
    g_tkt = ok(req(client, "POST", f"/steps/{leaf_b}/gates", {
        "title": "Race condition resolved", "target": {"type": "ticket", "id": ticket["id"]},
    }))
    g_q = ok(req(client, "POST", f"/steps/{leaf_b}/gates", {
        "title": "Region answered", "target": {"type": "question", "id": question["id"]},
    }))

    assert g_step["kind"] == "prerequisite"
    assert all(g["state"] == "open" and g["autoEvaluated"] for g in (g_step, g_del, g_tkt, g_q))

    # Linked gates refuse manual pass.
    err(req(client, "POST", f"/gates/{g_tkt['id']}/pass", {"revision": g_tkt["revision"]}), 422, "validation")

    # Duplicate active target on same step is rejected.
    err(req(client, "POST", f"/steps/{leaf_b}/gates", {
        "title": "Dup", "target": {"type": "ticket", "id": ticket["id"]},
    }), 409, "conflict")

    # leaf_b is blocked by all 4 linked gates.
    blocked = err(
        req(client, "PATCH", f"/steps/{leaf_b}/progress", {"revision": 1, "status": "done"}),
        409,
        "open_gates",
    )
    assert len(blocked["fields"]["gates"]) == 4

    # Satisfy each linked target cleanly.
    ok(req(client, "PATCH", f"/steps/{leaf_a}/progress", {"revision": 1, "status": "done"}))
    d_sub = ok(req(client, "POST", f"/deliverables/{deliverable['id']}/submit", {"revision": deliverable["revision"]}))
    d_acc = ok(req(client, "POST", f"/deliverables/{deliverable['id']}/accept", {"revision": d_sub["revision"]}))
    ok(req(client, "PATCH", f"/tickets/{ticket['id']}", {
        "revision": ticket["revision"], "status": "done", "resolution": "Fixed lock order",
    }))
    ok(req(client, "PATCH", f"/questions/{question['id']}", {
        "revision": question["revision"], "status": "answered", "answer": "us-east first",
    }))

    # All 4 gates auto-evaluate to passed -> leaf_b can complete!
    step_gates = ok(req(client, "GET", f"/steps/{leaf_b}/gates"))
    assert step_gates["summary"]["blocking"] == 0
    assert step_gates["summary"]["passed"] == 4
    done_b = ok(req(client, "PATCH", f"/steps/{leaf_b}/progress", {"revision": 1, "status": "done"}))
    assert done_b["status"] == "done"

    # Reopening the deliverable regresses g_del -> parent phase drops back from done to doing!
    ok(req(client, "POST", f"/deliverables/{deliverable['id']}/reopen", {
        "revision": d_acc["revision"], "reason": "Need v2 field",
    }))
    detail = ok(req(client, "GET", f"/roadmaps/{roadmap['id']}"))
    assert step_by_id(detail, leaf_b)["gateSummary"]["blocking"] == 1
    assert step_by_id(detail, phase)["status"] == "doing"

    # Archiving the linked ticket marks its gate 'unavailable' (fail-closed) until waived.
    t_latest = ok(req(client, "GET", f"/tickets/{ticket['id']}"))
    ok(req(client, "POST", f"/tickets/{ticket['id']}/archive", {"revision": t_latest["revision"]}))
    g_tkt_now = ok(req(client, "GET", f"/gates/{g_tkt['id']}"))
    assert g_tkt_now["state"] == "unavailable"
    assert g_tkt_now["satisfied"] is False
    # Human can waive an unavailable linked gate and reopen it back to auto-evaluation.
    waived = ok(req(client, "POST", f"/gates/{g_tkt['id']}/waive", {
        "revision": g_tkt_now["revision"], "reason": "Superseded by rewrite",
    }))
    assert waived["state"] == "waived"
    assert waived["autoEvaluated"] is False
    unwaived = ok(req(client, "POST", f"/gates/{g_tkt['id']}/reopen", {
        "revision": waived["revision"], "reason": "Re-check ticket",
    }))
    assert unwaived["state"] == "unavailable"
    assert unwaived["autoEvaluated"] is True


def test_step_gate_cycle_and_ancestor_deadlock_prevention_and_doctor(client):
    roadmap, phase, leaf_a, leaf_b = make_plan(client)
    # Self-gate refused.
    err(req(client, "POST", f"/steps/{leaf_a}/gates", {
        "title": "Self", "target": {"type": "step", "id": leaf_a},
    }), 422, "validation")
    # Gating a child on its own ancestor phase would deadlock -> refused.
    err(req(client, "POST", f"/steps/{leaf_a}/gates", {
        "title": "Wait on own phase", "target": {"type": "step", "id": phase},
    }), 422, "validation")
    # Mutual cycle A -> B -> A refused.
    ok(req(client, "POST", f"/steps/{leaf_a}/gates", {
        "title": "Wait on B", "target": {"type": "step", "id": leaf_b},
    }))
    err(req(client, "POST", f"/steps/{leaf_b}/gates", {
        "title": "Wait on A", "target": {"type": "step", "id": leaf_a},
    }), 422, "validation")
    # Moving leaf_b under leaf_a when leaf_a already gates on leaf_b is fine (parent -> child agrees),
    # whereas moving leaf_a under leaf_b creates cycle leaf_b -> leaf_a -> leaf_b -> refused!
    err(req(client, "POST", f"/steps/{leaf_a}/move", {
        "revision": roadmap["revision"], "parentId": leaf_b,
    }), 422, "validation")

    # Dependency module also honors open gates on a prerequisite step.
    step_c = ok(req(client, "POST", f"/roadmaps/{roadmap['id']}/steps", {
        "revision": roadmap["revision"], "title": "Downstream step",
    }))["id"]
    dep = ok(req(client, "POST", "/dependencies", {
        "dependent": {"type": "step", "id": step_c},
        "prerequisite": {"type": "step", "id": phase},
    }))
    phase_gate = ok(req(client, "POST", f"/steps/{phase}/gates", {"title": "Phase sign-off"}))
    # Complete leaf_b, then leaf_a (unblocked because leaf_b is done).
    ok(req(client, "PATCH", f"/steps/{leaf_b}/progress", {"revision": 1, "status": "done"}))
    ok(req(client, "PATCH", f"/steps/{leaf_a}/progress", {"revision": 1, "status": "done"}))
    # Phase still has open phase_gate, so dependency on phase stays 'waiting'.
    assert ok(req(client, "GET", f"/dependencies/{dep['id']}"))["state"] == "waiting"
    # Pass phase_gate -> dependency becomes 'satisfied'.
    ok(req(client, "POST", f"/gates/{phase_gate['id']}/pass", {"revision": phase_gate["revision"]}))
    assert ok(req(client, "GET", f"/dependencies/{dep['id']}"))["state"] == "satisfied"

    # Doctor reports clean on valid DB, and detects an injected cycle.
    db_path = client.data_dir / "worklog.sqlite3"
    report = doctor_database(db_path)
    assert report["ok"] is True
    assert report["checks"]["stepGates"]["problems"] == 0
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO step_gates(id,step_id,title,target_type,target_id,status,created_at,updated_at) "
            "VALUES('bad-cycle',?,?, 'step', ?, 'open', '2026-11-28T00:00:00Z', '2026-11-28T00:00:00Z')",
            (leaf_b, "Injected cycle", leaf_a),
        )
        conn.commit()
    bad_report = doctor_database(db_path)
    assert bad_report["ok"] is False
    assert bad_report["checks"]["stepGates"]["problems"] >= 1


def test_update_archive_recover_and_roadmap_level_listing(client):
    roadmap, phase, leaf_a, leaf_b = make_plan(client)
    g1 = ok(req(client, "POST", f"/steps/{leaf_a}/gates", {"title": "Initial gate"}))
    updated = ok(req(client, "PATCH", f"/gates/{g1['id']}", {
        "revision": g1["revision"],
        "title": "Updated gate",
        "criteria": "Run integration suite",
        "kind": "verification",
        "humanOnly": True,
    }))
    assert updated["title"] == "Updated gate"
    assert updated["kind"] == "verification"
    assert updated["humanOnly"] is True

    # Step detail includes active gates inline.
    step_detail = ok(req(client, "GET", f"/steps/{leaf_a}"))
    assert len(step_detail["gates"]) == 1
    assert step_detail["gates"][0]["id"] == g1["id"]

    # Roadmap listing returns phase context and supports onlyOpen / archived filters.
    rm_gates = ok(req(client, "GET", f"/roadmaps/{roadmap['id']}/gates?onlyOpen=true"))
    assert rm_gates["total"] == 1
    assert rm_gates["items"][0]["phaseId"] == phase
    assert rm_gates["summary"]["humanOnlyBlocking"] == 1

    archived = ok(req(client, "POST", f"/gates/{g1['id']}/archive", {"revision": updated["revision"]}))
    assert archived["archived"] is True
    assert ok(req(client, "GET", f"/steps/{leaf_a}/gates"))["total"] == 0
    assert ok(req(client, "GET", f"/steps/{leaf_a}/gates?archived=true"))["total"] == 1
    # Archiving the only gate allows marking leaf_a done.
    ok(req(client, "PATCH", f"/steps/{leaf_a}/progress", {"revision": 1, "status": "done"}))
    recovered = ok(req(client, "POST", f"/gates/{g1['id']}/recover", {"revision": archived["revision"]}))
    assert recovered["archived"] is False
