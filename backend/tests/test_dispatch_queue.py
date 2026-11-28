"""Dispatch-time admission queueing, auto-promotion read-only prep, Antigravity ready_manual, and restart durability."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_slot_worktrees import make_repo_pair
from worklog import session_launches
from worklog.app import create_app
from worklog.maintenance import prune_receipts
from worklog.slot_worktrees import inspect_slot_cleanliness

ORIGIN = "http://127.0.0.1:8877"


def _wait_status(client: TestClient, launch_id: str, wanted: set[str], timeout: float = 25.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/session-launches/{launch_id}")
        assert r.status_code == 200, r.text
        data = r.json()
        if data["status"] in wanted:
            return data
        time.sleep(0.02)
    pytest.fail(f"Launch {launch_id} did not reach {wanted}; last={data}")


def test_dispatch_queue_admission_auto_promotion_and_restart_open(tmp_path, monkeypatch):
    _, base = make_repo_pair(tmp_path, "aa-jobs")

    prepared: list[tuple[str, str, str, dict[str, str]]] = []
    opened: list[tuple[str, str, str]] = []

    def fake_prepare(provider: str, cwd: str, prompt: str, requested_options: dict[str, str]):
        prepared.append((provider, cwd, prompt, requested_options))
        return str(uuid.uuid4()), "gpt-5"

    def fake_open(provider: str, cwd: str, chat_id: str):
        opened.append((provider, cwd, chat_id))

    monkeypatch.setattr(session_launches, "_prepare_session", fake_prepare)
    monkeypatch.setattr(session_launches, "_open_desktop", fake_open)

    data_dir = tmp_path / "data"
    app = create_app(data_dir, testing=True)
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers["Origin"] = ORIGIN
        boot = client.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        client.headers["X-CSRF-Token"] = boot.json()["csrfToken"]

        repo = client.post(
            "/api/v1/repositories",
            json={"name": "aa-jobs", "url": ""},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).json()
        client.patch(
            f"/api/v1/repositories/{repo['id']}/slot-policy",
            json={"maxActiveLeases": 2, "baseCheckoutPath": str(base)},
        )
        item = client.post(
            "/api/v1/work-items",
            json={"title": "Implement batch retry", "repositoryIds": [repo["id"]]},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).json()

        # Launch #1 and #2 -> immediately fill slot-1 and slot-2.
        l1_init = client.post(
            f"/api/v1/work-items/{item['id']}/sessions",
            json={"provider": "codex", "repositoryId": repo["id"], "branch": "codex/job-1", "prompt": "Task 1"},
            headers={"Idempotency-Key": "dispatch-key-0001"},
        ).json()
        l1 = _wait_status(client, l1_init["id"], {"ready"})
        assert l1["slotIndex"] == 1 and Path(l1["worktreePath"]).name == "slot-1"
        assert "Worklog Checkout Slot Assignment:" in prepared[0][2]
        assert len(opened) == 1

        l2_init = client.post(
            f"/api/v1/work-items/{item['id']}/sessions",
            json={"provider": "claude", "repositoryId": repo["id"], "branch": "claude/job-2", "prompt": "Task 2"},
            headers={"Idempotency-Key": "dispatch-key-0002"},
        ).json()
        l2 = _wait_status(client, l2_init["id"], {"ready"})
        assert l2["slotIndex"] == 2 and Path(l2["worktreePath"]).name == "slot-2"
        assert len(prepared) == 2 and len(opened) == 2

        # Launch #3 -> repo full -> queued with ZERO provider spawn!
        l3 = client.post(
            f"/api/v1/work-items/{item['id']}/sessions",
            json={
                "provider": "claude", "repositoryId": repo["id"], "branch": "claude/job-3", "prompt": "Task 3",
                "model": "example-queued-model", "reasoningEffort": "xhigh",
            },
            headers={"Idempotency-Key": "dispatch-key-0003"},
        ).json()
        assert l3["status"] == "queued"
        assert l3["queuePosition"] == 1
        assert len(l3["activeHolders"]) == 2
        assert len(prepared) == 2  # zero provider spawn!
        requested = {"requestedModel": "example-queued-model", "reasoningEffort": "xhigh"}
        assert {key: l3[key] for key in requested} == requested
        with app.state.db.transaction() as conn:
            conn.execute("UPDATE receipts SET created_at='2000-01-01T00:00:00+00:00' WHERE key=?", (l3["id"],))
            assert prune_receipts(conn) == 0

        # Launch #4 (Antigravity) -> queued at #2, then cancelled cleanly.
        l4 = client.post(
            f"/api/v1/work-items/{item['id']}/sessions",
            json={"provider": "antigravity", "repositoryId": repo["id"], "branch": "ag/job-4", "prompt": "Task 4"},
            headers={"Idempotency-Key": "dispatch-key-0004"},
        ).json()
        assert l4["status"] == "queued" and l4["queuePosition"] == 2
        cancelled = client.post(
            f"/api/v1/dispatch-queue/{l4['id']}/cancel",
            json={"reason": "no longer needed"},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert client.get(f"/api/v1/leases/{l4['leaseId']}").json()["state"] == "cancelled"

        # Release lease #1 -> #3 auto-promotes and auto-prepares in read-only mode (WITHOUT calling _open_desktop)!
        lease1 = client.get(f"/api/v1/leases/{l1['leaseId']}").json()
        rel = client.post(
            f"/api/v1/leases/{lease1['id']}/release",
            json={"revision": lease1["revision"]},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert rel.status_code == 200, rel.text
        l3_ready = _wait_status(client, l3["id"], {"ready"})
        assert l3_ready["slotIndex"] == 1
        assert len(prepared) == 3
        assert prepared[2][3] == requested
        assert {key: l3_ready[key] for key in requested} == requested
        assert len(opened) == 2  # auto-promoted waits for user Open in app!
        project = Path(l3_ready["worktreePath"]) / ".claude"
        generated = json.loads((project / "settings.local.json").read_text(encoding="utf-8"))
        assert generated["model"] == "example-queued-model"
        assert generated["env"]["CLAUDE_CODE_EFFORT_LEVEL"] == "xhigh"
        assert inspect_slot_cleanliness(project.parent).clean

        # Durable dispatch queue listing shows the promoted entry.
        dq = client.get(f"/api/v1/dispatch-queue?repositoryId={repo['id']}").json()
        assert dq["total"] == 4

    # Simulate server restart: fresh create_app reads durable dispatch_queue and opens #3 cleanly!
    app2 = create_app(data_dir, testing=True)
    with TestClient(app2, base_url=ORIGIN) as client2:
        client2.headers["Origin"] = ORIGIN
        boot2 = client2.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        client2.headers["X-CSRF-Token"] = boot2.json()["csrfToken"]
        opened_res = client2.post(
            f"/api/v1/dispatch-queue/{l3['id']}/open",
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert opened_res.status_code == 200, opened_res.text
        assert opened_res.json()["status"] == "ready"
        assert opened_res.json()["reasoningEffort"] == "xhigh"
        assert len(opened) == 3

        # Claude Code records "don't ask again" rules in the file Worklog wrote. Recycling keeps
        # ignored files, so the release must still take it out of the slot, and keep a copy.
        edited = {**generated, "permissions": {"allow": ["Bash(npm test:*)"]}}
        (project / "settings.local.json").write_text(json.dumps(edited), encoding="utf-8")
        saved_lease = client2.get(f"/api/v1/leases/{l3['leaseId']}").json()
        released = client2.post(
            f"/api/v1/leases/{l3['leaseId']}/release",
            json={"revision": saved_lease["revision"]},
            headers={"Idempotency-Key": "dispatch-release-settings"},
        )
        assert released.status_code == 200, released.text
        assert not (project / "settings.local.json").exists()
        assert not (project / ".gitignore").exists()
        rescued = data_dir / "claude-checkout-settings" / "rescued" / f"{l3['leaseId']}.settings.local.json"
        assert json.loads(rescued.read_text(encoding="utf-8")) == edited

        default_launch = client2.post(
            f"/api/v1/work-items/{item['id']}/sessions",
            json={"provider": "claude", "repositoryId": repo["id"], "branch": "claude/default", "prompt": "Task 5"},
            headers={"Idempotency-Key": "dispatch-key-0005"},
        ).json()
        ready = _wait_status(client2, default_launch["id"], {"ready"})
        assert ready["worktreePath"] == l3_ready["worktreePath"]
        assert prepared[-1][3] == {}
        assert not (project / "settings.local.json").exists()
        assert len(opened) == 4

        with app2.state.db.transaction() as conn:
            prune_receipts(conn)
        stale_open = client2.post(f"/api/v1/dispatch-queue/{l3['id']}/open").json()
        assert stale_open["status"] == "failed"
        assert "active lease" in stale_open["message"]
        assert len(opened) == 4


def test_antigravity_dispatch_returns_ready_manual_copyable_brief(tmp_path):
    _, base = make_repo_pair(tmp_path, "ag-repo")
    app = create_app(tmp_path / "data", testing=True)
    with TestClient(app, base_url=ORIGIN) as client:
        client.headers["Origin"] = ORIGIN
        boot = client.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        client.headers["X-CSRF-Token"] = boot.json()["csrfToken"]

        repo = client.post(
            "/api/v1/repositories",
            json={"name": "ag-repo", "url": ""},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).json()
        client.patch(
            f"/api/v1/repositories/{repo['id']}/slot-policy",
            json={"maxActiveLeases": 1, "baseCheckoutPath": str(base)},
        )
        item = client.post(
            "/api/v1/work-items",
            json={"title": "Design slot UI", "repositoryIds": [repo["id"]]},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        ).json()

        res = client.post(
            f"/api/v1/work-items/{item['id']}/sessions",
            json={"provider": "antigravity", "repositoryId": repo["id"], "branch": "ag/slot-ui", "prompt": "Build UI"},
            headers={"Idempotency-Key": "ag-dispatch-0001"},
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "ready_manual"
        assert data["slotIndex"] == 1
        assert Path(data["worktreePath"]).name == "slot-1"
        assert "Worklog Checkout Slot Assignment:" in data["copyablePrompt"]
        assert "ag/slot-ui" in data["copyablePrompt"]
