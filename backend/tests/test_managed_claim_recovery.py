"""Recovery of managed slot provisioning after an interrupted offer."""

from __future__ import annotations

import concurrent.futures
import sqlite3
import subprocess
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_leases import _agent_post, _create_repo, _issue
from test_leases import env as lease_env
from test_slot_worktrees import make_repo_pair
from worklog import slot_worktrees
from worklog.common import AppError
from worklog.leases import OFFER_GRACE_SECONDS

env = lease_env


def _interrupted_offer(client, tmp_path: Path, name: str) -> tuple[dict, dict, Path, str]:
    """Leave an offered lease with the canonical key persisted before Git provisioning."""
    _, base = make_repo_pair(tmp_path, name)
    repo = _create_repo(client, name, url="")
    root = tmp_path / f"{name}-slots"
    policy_response = client.patch(
        f"/api/v1/repositories/{repo['id']}/slot-policy",
        json={"maxActiveLeases": 0, "baseCheckoutPath": str(base), "slotRootPath": str(root)},
    )
    assert policy_response.status_code == 200, policy_response.text
    agent = _issue(client, f"{name}-agent")
    queued_response = _agent_post(
        client, agent["token"], "/leases/acquire",
        {"repositoryId": repo["id"], "branch": "codex/interrupted"},
    )
    assert queued_response.status_code == 200, queued_response.text
    queued = queued_response.json()
    assert queued["state"] == "queued"

    enabled_response = client.patch(
        f"/api/v1/repositories/{repo['id']}/slot-policy",
        json={"maxActiveLeases": 1, "revision": policy_response.json()["revision"]},
    )
    assert enabled_response.status_code == 200, enabled_response.text
    lease = client.get(f"/api/v1/leases/{queued['id']}").json()
    assert lease["state"] == "offered"
    target = slot_worktrees.resolve_slot_path(base, str(root), name, lease["slotIndex"])
    claim_key = slot_worktrees.worktree_claim_key(target)

    with closing(sqlite3.connect(tmp_path / "data" / "worklog.sqlite3")) as conn:
        updated = conn.execute(
            "UPDATE repository_leases SET worktree_claim_key=? WHERE id=? AND state='offered'",
            (claim_key, lease["id"]),
        )
        assert updated.rowcount == 1
        conn.commit()

    assert not target.exists(), "the simulated process interruption is after claim reservation and before Git setup"
    return repo, agent, target, lease["id"]


def test_poll_recovers_interrupted_managed_offer_with_existing_canonical_claim(env):
    client, tmp_path = env
    _, agent, target, lease_id = _interrupted_offer(client, tmp_path, "managed-recovery")

    response = _agent_post(client, agent["token"], f"/leases/{lease_id}/poll", {})

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "active"
    assert Path(response.json()["worktreePath"]).resolve() == target
    with closing(sqlite3.connect(tmp_path / "data" / "worklog.sqlite3")) as conn:
        row = conn.execute(
            "SELECT state,worktree_claim_key FROM repository_leases WHERE id=?", (lease_id,)
        ).fetchone()
    assert row == ("active", slot_worktrees.worktree_claim_key(target))


@pytest.mark.parametrize("during_fetch", [False, True], ids=["after-reservation", "during-fetch"])
def test_managed_offer_rejects_changed_slot_mapping_before_checkout_writes(env, monkeypatch, during_fetch):
    client, tmp_path = env
    _, agent, reserved_target, lease_id = _interrupted_offer(client, tmp_path, "managed-path-race")
    alternate_checkout = tmp_path / "alternate-checkout"
    alternate_checkout.mkdir()
    sentinel = alternate_checkout / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")

    resolve_slot_path = slot_worktrees.resolve_slot_path
    resolver_calls = 0

    def change_mapping_after_reservation(*args):
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 1 or (during_fetch and not git_calls):
            return resolve_slot_path(*args)
        return alternate_checkout

    git_calls = []

    def record_unexpected_git(cwd, *args, **kwargs):
        git_calls.append(args[0])
        if during_fetch and args[0] == "fetch":
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AppError(503, "slot_provisioning_failed", "Unexpected Git command during path validation")

    monkeypatch.setattr(slot_worktrees, "resolve_slot_path", change_mapping_after_reservation)
    monkeypatch.setattr(slot_worktrees, "_run_git", record_unexpected_git)

    response = _agent_post(client, agent["token"], f"/leases/{lease_id}/poll", {})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"
    assert git_calls == (["fetch"] if during_fetch else [])
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert list(alternate_checkout.iterdir()) == [sentinel]
    assert not reserved_target.exists()


def test_concurrent_polls_provision_interrupted_managed_offer_once(env, monkeypatch):
    client, tmp_path = env
    _, agent, target, lease_id = _interrupted_offer(client, tmp_path, "managed-concurrent-recovery")
    service = client.app.state.leases
    original_activate = service._provision_and_activate
    original_provision = slot_worktrees.provision_slot
    calls_lock = threading.Lock()
    activation_calls = 0
    activation_calls_ready = threading.Event()
    provisioning_started = threading.Event()
    allow_provisioning = threading.Event()
    provisioning_calls = 0

    def track_activation(*args, **kwargs):
        nonlocal activation_calls
        with calls_lock:
            activation_calls += 1
            if activation_calls == 2:
                activation_calls_ready.set()
        return original_activate(*args, **kwargs)

    def track_provision(*args, **kwargs):
        nonlocal provisioning_calls
        with calls_lock:
            provisioning_calls += 1
        provisioning_started.set()
        assert allow_provisioning.wait(timeout=10), "test did not release managed provisioning"
        return original_provision(*args, **kwargs)

    monkeypatch.setattr(service, "_provision_and_activate", track_activation)
    monkeypatch.setattr(slot_worktrees, "provision_slot", track_provision)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_agent_post, client, agent["token"], f"/leases/{lease_id}/poll", {})
        assert provisioning_started.wait(timeout=5), "first poll did not begin provisioning"
        second = pool.submit(_agent_post, client, agent["token"], f"/leases/{lease_id}/poll", {})
        assert activation_calls_ready.wait(timeout=5), "second poll did not reach the activation lock"
        try:
            stale_offer = (
                datetime.now(UTC) - timedelta(seconds=OFFER_GRACE_SECONDS + 1)
            ).isoformat()
            with closing(sqlite3.connect(tmp_path / "data" / "worklog.sqlite3")) as conn:
                updated = conn.execute(
                    "UPDATE repository_leases SET offered_at=? WHERE id=? AND state='offered'",
                    (stale_offer, lease_id),
                )
                assert updated.rowcount == 1
                conn.commit()

            sweep = service.sweep()
            assert sweep["expired"] == 0
            with closing(sqlite3.connect(tmp_path / "data" / "worklog.sqlite3")) as conn:
                row = conn.execute(
                    "SELECT state,offered_at FROM repository_leases WHERE id=?", (lease_id,)
                ).fetchone()
            assert row == ("offered", stale_offer)
        finally:
            allow_provisioning.set()
        results = [first.result(timeout=15), second.result(timeout=15)]

    assert [response.status_code for response in results] == [200, 200], [r.text for r in results]
    assert [response.json()["state"] for response in results] == ["active", "active"]
    assert all(Path(response.json()["worktreePath"]).resolve() == target for response in results)
    assert provisioning_calls == 1
