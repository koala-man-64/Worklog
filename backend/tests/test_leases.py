"""Atomic bounded per-repository checkout leases, FIFO queue, reaper, lifecycle, and CLI tests."""

from __future__ import annotations

import concurrent.futures
import io
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
import sealed
from fastapi.testclient import TestClient
from test_sealed_schemas import contents
from test_slot_worktrees import _git, make_repo_pair
from worklog import cli, migrations
from worklog.app import create_app
from worklog.db import Database
from worklog.transcripts import TranscriptRoots

ORIGIN = "http://127.0.0.1:8877"

# Version 34's documented downgrade to version 33: drop the three additive lease/slot/dispatch tables.
WAY_BACK = """\
DROP TABLE dispatch_queue;
DROP TABLE repository_leases;
DROP TABLE repository_slot_policies;
PRAGMA user_version=33;
"""

V34_TABLES = ("repository_slot_policies", "repository_leases", "dispatch_queue")


def to_v33(path):
    """Take a current database back to v33 the documented way."""
    from test_repo_execution_schema import to_v34

    to_v34(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


def before_v34(rows):
    """A database's rows as they read before v34: its three additive tables (and v35's) left out while empty."""
    from test_repo_execution_schema import before_v35

    rows = before_v35(rows)
    return {table: values for table, values in rows.items() if table not in V34_TABLES or values}


def _boot(client: TestClient) -> None:
    client.headers["Origin"] = ORIGIN
    boot = client.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
    assert boot.status_code == 200, boot.text
    client.headers["X-CSRF-Token"] = boot.json()["csrfToken"]


def _issue(client: TestClient, label: str) -> dict:
    r = client.post("/api/v1/credentials", json={"label": label}, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    return r.json()


def _create_repo(client: TestClient, name: str, url: str | None = None) -> dict:
    r = client.post(
        "/api/v1/repositories",
        json={"name": name, "url": f"https://github.com/example/{name}" if url is None else url},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _agent_post(client: TestClient, token: str, path: str, body: dict, key: str | None = None):
    return client.post(
        "/api/v1" + path,
        json=body,
        headers={"Authorization": "Bearer " + token, "Idempotency-Key": key or str(uuid.uuid4())},
    )


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path / "data", testing=True)
    with TestClient(app, base_url=ORIGIN) as client:
        _boot(client)
        yield client, tmp_path


@pytest.mark.parametrize(("path", "source"), [("", "unbound"), ("C:/old-pool/slot-1", "managed")])
def test_replaying_a_v37_receipt_classifies_the_original_path(env, path, source):
    client, _ = env
    repo = _create_repo(client, "old-receipt")
    owner = _issue(client, "Owner")
    payload = {"repositoryId": repo["id"], "branch": "feature/old-receipt"}
    key = "acquire-from-v37"
    acquired = _agent_post(client, owner["token"], "/leases/acquire", payload, key).json()
    released = _agent_post(client, owner["token"], f"/leases/{acquired['id']}/release",
                           {"revision": acquired["revision"]})
    assert released.status_code == 200, released.text
    legacy = dict(acquired, worktreePath=path)
    del legacy["worktreeSource"]
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE receipts SET response_json=? WHERE key=?", (json.dumps(legacy), key))
    replay = _agent_post(client, owner["token"], "/leases/acquire", payload, key)
    assert replay.status_code == 200, replay.text
    assert replay.json() == {**legacy, "worktreeSource": source}


def test_concurrent_acquire_enforces_cap_and_fifo_promotion(env):
    client, tmp_path = env
    repo = _create_repo(client, "jobs")
    agents = [_issue(client, f"Agent-{i}") for i in range(4)]

    def do_acquire(idx: int):
        return _agent_post(
            client,
            agents[idx]["token"],
            "/leases/acquire",
            {"repositoryId": repo["id"], "branch": f"feat/agent-{idx}", "reason": f"Task {idx}"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = [fut.result() for fut in [pool.submit(do_acquire, i) for i in range(4)]]
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    leases = [r.json() for r in results]

    active = [x for x in leases if x["state"] == "active"]
    queued = sorted((x for x in leases if x["state"] == "queued"), key=lambda x: x["queueSeq"])
    assert len(active) == 2
    assert len(queued) == 2
    assert {x["slotIndex"] for x in active} == {1, 2}
    assert [x["queuePosition"] for x in queued] == [1, 2]

    # Snapshot file exists and reflects 2 active, 2 queued.
    snap = json.loads((tmp_path / "data" / "leases-snapshot.json").read_text(encoding="utf-8"))
    assert snap["repositories"][repo["id"]]["activeCount"] == 2
    assert snap["repositories"][repo["id"]]["queuedCount"] == 2

    # Release first active lease -> first queued waiter is promoted to offered, then poll activates it!
    first = active[0]
    first_agent = next(a for a in agents if a["agentId"] == first["agentId"])
    rel = _agent_post(client, first_agent["token"], f"/leases/{first['id']}/release", {"revision": first["revision"]})
    assert rel.status_code == 200 and rel.json()["state"] == "released"

    head = queued[0]
    head_agent = next(a for a in agents if a["agentId"] == head["agentId"])
    polled = _agent_post(client, head_agent["token"], f"/leases/{head['id']}/poll", {})
    assert polled.status_code == 200, polled.text
    assert polled.json()["state"] == "active"
    assert polled.json()["slotIndex"] == first["slotIndex"]


def test_exclusive_mode_freeze_priority_and_anti_deadlock(env):
    client, _ = env
    repo_a = _create_repo(client, "control-plane")
    repo_b = _create_repo(client, "jobs")
    a1, a2, a3, a4 = [_issue(client, f"Worker-{i}") for i in range(4)]

    # Freeze repo_a (maxActiveLeases = 0): acquire always queues.
    patch = client.patch(f"/api/v1/repositories/{repo_a['id']}/slot-policy", json={"maxActiveLeases": 0})
    assert patch.status_code == 200, patch.text
    q0 = _agent_post(client, a1["token"], "/leases/acquire", {"repositoryId": repo_a["id"], "branch": "feat/a1"}).json()
    assert q0["state"] == "queued"

    # Unfreeze to 2 -> a1 promoted to offered -> poll activates.
    patch2 = client.patch(f"/api/v1/repositories/{repo_a['id']}/slot-policy", json={"maxActiveLeases": 2, "revision": patch.json()["revision"]})
    assert patch2.status_code == 200
    l1 = _agent_post(client, a1["token"], f"/leases/{q0['id']}/poll", {}).json()
    assert l1["state"] == "active"

    # Anti-deadlock: a1 cannot hold an active lease on repo_b simultaneously.
    blocked = _agent_post(client, a1["token"], "/leases/acquire", {"repositoryId": repo_b["id"], "branch": "feat/b1"})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "agent_lease_limit"

    # Exclusive request queues while a1 holds shared; subsequent shared request queues behind exclusive (no starvation).
    q_excl = _agent_post(client, a2["token"], "/leases/acquire", {"repositoryId": repo_a["id"], "mode": "exclusive", "branch": "feat/excl"}).json()
    assert q_excl["state"] == "queued"
    q_shared = _agent_post(client, a3["token"], "/leases/acquire", {"repositoryId": repo_a["id"], "mode": "shared", "branch": "feat/shared"}).json()
    assert q_shared["state"] == "queued"

    # Higher priority (incident) jumps ahead of normal when queued.
    q_inc = _agent_post(client, a4["token"], "/leases/acquire", {"repositoryId": repo_a["id"], "priority": "incident", "branch": "feat/hotfix"}).json()
    assert q_inc["state"] == "queued" and q_inc["queuePosition"] == 1

    # Releasing a1 promotes incident waiter (a4) first.
    _agent_post(client, a1["token"], f"/leases/{l1['id']}/release", {"revision": l1["revision"]})
    assert client.get(f"/api/v1/leases/{q_inc['id']}").json()["state"] == "offered"


@pytest.mark.parametrize(
    ("occupy_released_slot", "advance_remote"),
    [(False, False), (True, False), (False, True)],
    ids=["reused-slot", "new-slot", "newer-upstream"],
)
def test_reacquire_preserves_exact_feature_commit(env, occupy_released_slot, advance_remote):
    client, tmp_path = env
    _, base = make_repo_pair(tmp_path, "resume-feature")
    repo = _create_repo(client, "resume-feature", url="")
    policy = client.patch(
        f"/api/v1/repositories/{repo['id']}/slot-policy",
        json={"baseCheckoutPath": str(base), "maxActiveLeases": 2},
    )
    assert policy.status_code == 200, policy.text
    token = _issue(client, "Feature owner")["token"]
    branch = "feat/resume"
    payload = {"repositoryId": repo["id"], "branch": branch}

    first = _agent_post(client, token, "/leases/acquire", payload)
    assert first.status_code == 200, first.text
    lease = first.json()
    slot = Path(lease["worktreePath"])
    (slot / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(slot, "commit", "-am", "Feature checkpoint")
    feature_tip = _git(slot, "rev-parse", "HEAD")
    assert feature_tip != _git(base, "rev-parse", "refs/remotes/origin/main")

    released = _agent_post(client, token, f"/leases/{lease['id']}/release", {"revision": lease["revision"]})
    assert released.status_code == 200, released.text
    assert released.json()["state"] == "released"
    assert _git(base, "rev-parse", f"refs/heads/{branch}") == feature_tip
    if advance_remote:
        _git(base, "checkout", "-b", "upstream-update", feature_tip)
        _git(base, "commit", "--allow-empty", "-m", "Published after release")
        _git(base, "push", "origin", f"HEAD:refs/heads/{branch}")
        _git(base, "checkout", "main")
    if occupy_released_slot:
        other = _issue(client, "Other slot user")["token"]
        held = _agent_post(client, other, "/leases/acquire", {"repositoryId": repo["id"], "branch": "feat/other"})
        assert held.status_code == 200, held.text
        assert held.json()["slotIndex"] == lease["slotIndex"]

    resumed = _agent_post(client, token, "/leases/acquire", payload)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["state"] == "active"
    resumed_slot = Path(resumed.json()["worktreePath"])
    assert (resumed_slot != slot) == occupy_released_slot
    assert _git(resumed_slot, "symbolic-ref", "HEAD") == f"refs/heads/{branch}"
    assert _git(resumed_slot, "rev-parse", "HEAD") == feature_tip


@pytest.mark.parametrize("release_first", [False, True], ids=["occupied", "caller-retained"])
def test_managed_policy_cannot_take_over_a_caller_worktree(env, monkeypatch, release_first):
    from worklog import slot_worktrees

    client, tmp_path = env
    name = "caller-pool"
    _, base = make_repo_pair(tmp_path, name)
    repo = _create_repo(client, name)
    _git(base, "remote", "set-url", "origin", repo["url"])
    holder, waiter = _issue(client, "Caller"), _issue(client, "Next")
    branch = "feature/caller"
    claim = _agent_post(client, holder["token"], "/leases/acquire", {"repositoryId": repo["id"], "branch": branch}).json()
    root = tmp_path / "new-managed-pool"
    target = root / name / ("slot-1" if release_first else "slot-2")
    target.parent.mkdir(parents=True)
    _git(base, "worktree", "add", "-b", branch, str(target))
    bound = _agent_post(client, holder["token"], f"/leases/{claim['id']}/bind-worktree",
                        {"revision": claim["revision"], "worktreePath": str(target)})
    assert bound.status_code == 200, bound.text
    dirty = target / "untracked.txt"
    dirty.write_text("caller data", encoding="utf-8")
    if release_first:
        released = _agent_post(client, holder["token"], f"/leases/{claim['id']}/release",
                               {"revision": bound.json()["revision"]})
        assert released.status_code == 200, released.text
    policy = client.patch(f"/api/v1/repositories/{repo['id']}/slot-policy",
                          json={"baseCheckoutPath": str(base), "slotRootPath": str(root)})
    assert policy.status_code == 200, policy.text
    monkeypatch.setattr(slot_worktrees, "provision_slot", lambda *args: pytest.fail("entered caller worktree"))
    refused = _agent_post(client, waiter["token"], "/leases/acquire", {"repositoryId": repo["id"], "branch": "feature/next"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == ("caller_worktree" if release_first else "worktree_in_use")
    assert dirty.read_text(encoding="utf-8") == "caller data"
    assert _git(target, "symbolic-ref", "--short", "HEAD") == branch


def test_logical_lease_cannot_bind_inside_another_managed_pool(env):
    client, tmp_path = env
    repo = _create_repo(client, "caller-catalog")
    _, base = make_repo_pair(tmp_path, repo["name"])
    _git(base, "remote", "set-url", "origin", repo["url"])
    managed = _create_repo(client, "managed-catalog", url="")
    pool = tmp_path / "slots"
    policy = client.patch(f"/api/v1/repositories/{managed['id']}/slot-policy",
                          json={"baseCheckoutPath": str(base), "slotRootPath": str(pool)})
    assert policy.status_code == 200, policy.text
    owner = _issue(client, "Caller")
    lease = _agent_post(client, owner["token"], "/leases/acquire",
                        {"repositoryId": repo["id"], "branch": "feature/pool"}).json()
    target = pool / managed["name"] / "slot-1"
    target.parent.mkdir(parents=True, exist_ok=True)
    _git(base, "worktree", "add", "-b", lease["branch"], str(target))
    refused = _agent_post(client, owner["token"], f"/leases/{lease['id']}/bind-worktree",
                          {"revision": lease["revision"], "worktreePath": str(target)})
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "worktree_managed"
    assert client.get(f"/api/v1/leases/{lease['id']}").json() == lease
    assert _git(target, "symbolic-ref", "--short", "HEAD") == lease["branch"]


def test_caller_lease_heartbeat_wins_over_an_older_expiry_scan(env, monkeypatch):
    client, tmp_path = env
    repo = _create_repo(client, "caller-heartbeat")
    owner = _issue(client, "Caller")
    _, base = make_repo_pair(tmp_path, repo["name"])
    _git(base, "remote", "set-url", "origin", repo["url"])
    service = client.app.state.leases
    monkeypatch.setattr(service, "_dead_claude_sessions", lambda: set())
    acquire = {"repositoryId": repo["id"], "branch": "feature/heartbeat", "provider": "claude"}
    lease = _agent_post(client, owner["token"], "/leases/acquire", acquire).json()
    path = tmp_path / "caller"
    _git(base, "worktree", "add", "-b", lease["branch"], str(path))
    bound = _agent_post(client, owner["token"], f"/leases/{lease['id']}/bind-worktree",
                        {"revision": lease["revision"], "worktreePath": str(path)})
    assert bound.status_code == 200, bound.text
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE repository_leases SET expires_at=? WHERE id=?",
                  ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), lease["id"]))

    def refresh_after_scan():
        renewed = _agent_post(client, owner["token"], "/leases/acquire", acquire)
        assert renewed.status_code == 200, renewed.text
        assert renewed.json()["revision"] == bound.json()["revision"]
        return set()

    monkeypatch.setattr(service, "_dead_claude_sessions", refresh_after_scan)
    assert service.sweep()["expired"] == 0
    current = client.get(f"/api/v1/leases/{lease['id']}").json()
    assert (current["state"], current["worktreePath"]) == ("active", str(path))


def test_ttl_expiry_auto_rescue_and_human_rescue_when_disabled(env):
    client, tmp_path = env
    _, base = make_repo_pair(tmp_path, "rescue-repo")
    # Keep origin pointing at the local bare repo so git fetch stays offline and instant.
    repo = _create_repo(client, "rescue-repo", url="")
    pol = client.patch(
        f"/api/v1/repositories/{repo['id']}/slot-policy",
        json={"maxActiveLeases": 1, "baseCheckoutPath": str(base), "autoRescueDirty": True},
    )
    assert pol.status_code == 200, pol.text

    a1, a2 = _issue(client, "DirtyAgent"), _issue(client, "WaiterAgent")
    l1 = _agent_post(client, a1["token"], "/leases/acquire", {"repositoryId": repo["id"], "branch": "claude/work"}).json()
    assert l1["state"] == "active" and l1["worktreePath"]
    l2 = _agent_post(client, a2["token"], "/leases/acquire", {"repositoryId": repo["id"], "branch": "codex/next"}).json()
    assert l2["state"] == "queued"

    # Dirty the slot; agent normal release fails with 409 slot_dirty.
    slot1 = Path(l1["worktreePath"])
    (slot1 / "app.py").write_text("VALUE = 777\n", encoding="utf-8")
    dirty_rel = _agent_post(client, a1["token"], f"/leases/{l1['id']}/release", {"revision": l1["revision"]})
    assert dirty_rel.status_code == 409 and dirty_rel.json()["error"]["code"] == "slot_dirty"

    # Force TTL expiration in DB and run sweep -> auto-rescues to refs/worklog-rescue/<id> and promotes waiter!
    past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE repository_leases SET expires_at=? WHERE id=?", (past, l1["id"]))
    counts = client.app.state.leases.sweep()
    assert counts["rescued"] == 1 and counts["expired"] == 1

    l1_after = client.get(f"/api/v1/leases/{l1['id']}").json()
    assert l1_after["state"] == "expired"
    assert l1_after["rescueRef"] == f"refs/worklog-rescue/{l1['id']}"
    assert _git(base, "show", f"{l1_after['rescueRef']}:app.py") == "VALUE = 777"

    # Waiter polls and gets freshly reset slot-1!
    l2_active = _agent_post(client, a2["token"], f"/leases/{l2['id']}/poll", {}).json()
    assert l2_active["state"] == "active" and l2_active["slotIndex"] == 1

    # Now disable autoRescueDirty, dirty slot-1, expire it -> enters expired_dirty until human rescues.
    client.patch(f"/api/v1/repositories/{repo['id']}/slot-policy", json={"autoRescueDirty": False})
    (slot1 / "app.py").write_text("VALUE = 888\n", encoding="utf-8")
    with client.app.state.db.transaction() as c:
        c.execute("UPDATE repository_leases SET expires_at=? WHERE id=?", (past, l2_active["id"]))
    client.app.state.leases.sweep()
    l2_dirty = client.get(f"/api/v1/leases/{l2_active['id']}").json()
    assert l2_dirty["state"] == "expired_dirty"

    rescued = client.post(
        f"/api/v1/leases/{l2_active['id']}/rescue",
        json={"revision": l2_dirty["revision"], "reason": "human cleanup"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert rescued.status_code == 200, rescued.text
    assert rescued.json()["state"] == "released"
    assert rescued.json()["rescueRef"] == f"refs/worklog-rescue/{l2_active['id']}"


def test_slot_count_changes_from_the_list_while_a_moved_base_waits_to_be_fixed(env):
    """The Repositories list sends only the slot count, with the revision its slot summary carries. A
    stored base checkout path is not validated again, so a moved one blocks no count change; a new path is."""
    client, tmp_path = env
    _, base = make_repo_pair(tmp_path, "moved-repo")
    repo = _create_repo(client, "moved-repo", url="")
    policy_url = f"/api/v1/repositories/{repo['id']}/slot-policy"

    def listed_summary() -> dict:
        items = client.get("/api/v1/repositories", params={"q": "moved-repo"}).json()["items"]
        return items[0]["slotSummary"]

    assert listed_summary()["revision"] == 0 and listed_summary()["configured"] is False
    configured = client.patch(policy_url, json={"revision": 0, "baseCheckoutPath": str(base)})
    assert configured.status_code == 200, configured.text
    assert configured.json()["revision"] == 1 and listed_summary()["revision"] == 1

    base.rename(tmp_path / "moved-away")
    count = client.patch(policy_url, json={"revision": 1, "maxActiveLeases": 4})
    assert count.status_code == 200, count.text
    assert count.json()["maxActiveLeases"] == 4
    assert count.json()["baseCheckoutPath"] == configured.json()["baseCheckoutPath"]
    summary = listed_summary()
    assert (summary["maxActiveLeases"], summary["revision"]) == (4, 2)

    # The detail form sends every field; the unchanged path is not checked again either.
    fields = ("maxActiveLeases", "defaultTtlSeconds", "baseCheckoutPath", "slotRootPath", "defaultBranch", "autoRescueDirty")
    form = client.patch(policy_url, json={**{k: count.json()[k] for k in fields}, "revision": 2, "maxActiveLeases": 3})
    assert form.status_code == 200, form.text
    assert form.json()["maxActiveLeases"] == 3

    stale = client.patch(policy_url, json={"revision": 2, "maxActiveLeases": 5})
    assert stale.status_code == 409, stale.text
    moved = client.patch(policy_url, json={"revision": 3, "baseCheckoutPath": str(tmp_path / "nowhere")})
    assert moved.status_code == 422 and moved.json()["error"]["fields"] == {"baseCheckoutPath": "not_found"}
    assert client.get(policy_url).json()["maxActiveLeases"] == 3


def test_lifecycle_hooks_run_end_retire_continue_and_merge(env):
    client, tmp_path = env
    repo = _create_repo(client, "lifecycle-repo")
    sponsor = _issue(client, "Sponsor")

    # Register self-registered identity A in chat-1.
    tok_a = "A" * 20 + "abcdefghijklmnopqrstuvwxyz0123456"
    _agent_post(
        client, sponsor["token"], "/agent-registrations",
        {"label": "Worker A", "token": tok_a, "chatId": "chat-a-1"},
    ).json()
    run_a = _agent_post(
        client, tok_a, "/agent-runs",
        {"clientRunKey": "run-a", "displayName": "Worker A"},
    ).json()
    lease_a = _agent_post(
        client, tok_a, "/leases/acquire",
        {
            "repositoryId": repo["id"],
            "branch": "feat/a",
            "runId": run_a["id"],
            "chatId": "self-reported-acquisition-chat",
        },
    ).json()
    assert lease_a["state"] == "active"

    # Bind a clean, real caller worktree after acquisition so the first-use attestation is valid.
    _, base = make_repo_pair(tmp_path, "lifecycle-repo")
    _git(base, "remote", "set-url", "origin", repo["url"])
    caller_path = tmp_path / "caller-worktrees" / "lifecycle-a"
    caller_path.parent.mkdir(parents=True, exist_ok=True)
    _git(base, "worktree", "add", str(caller_path), "-b", "feat/a", "HEAD")
    caller_path = caller_path.resolve()
    bind_key = "lifecycle-bind-a"
    bound_a = _agent_post(
        client, tok_a, f"/leases/{lease_a['id']}/bind-worktree",
        {"revision": lease_a["revision"], "worktreePath": str(caller_path)},
        key=bind_key,
    )
    assert bound_a.status_code == 200, bound_a.text
    assert bound_a.json()["worktreeSource"] == "caller"
    with client.app.state.db.transaction() as c:
        bind_event = c.execute(
            "SELECT after_json FROM events WHERE kind='lease.bind_worktree' AND entity_id=? ORDER BY rowid DESC LIMIT 1",
            (lease_a["id"],),
        ).fetchone()
    assert bind_event is not None
    assert json.loads(bind_event[0])["chatId"] == "chat-a-1"
    assert lease_a["chatId"] == "self-reported-acquisition-chat"

    # Continue identity A into chat-a-2 -> lease ownership transfers to new credential & chat!
    tok_a2 = "B" * 20 + "abcdefghijklmnopqrstuvwxyz0123456"
    cont = _agent_post(
        client, tok_a, "/agent-registrations/continue",
        {"token": tok_a2, "chatId": "chat-a-2"},
    )
    assert cont.status_code == 200, cont.text
    lease_after_cont = client.get(f"/api/v1/leases/{lease_a['id']}").json()
    assert lease_after_cont["state"] == "active"
    assert lease_after_cont["credentialId"] == cont.json()["id"]
    assert lease_after_cont["chatId"] == "chat-a-2"
    assert lease_after_cont["worktreePath"] == str(caller_path)
    assert lease_after_cont["worktreeSource"] == "caller"

    # The revoked credential cannot replay its earlier successful bind receipt.
    old_replay = _agent_post(
        client, tok_a, f"/leases/{lease_a['id']}/bind-worktree",
        {"revision": lease_a["revision"], "worktreePath": str(caller_path)},
        key=bind_key,
    )
    assert old_replay.status_code == 401
    assert old_replay.json()["error"]["code"] == "authentication"
    assert client.get(f"/api/v1/leases/{lease_a['id']}").json()["revision"] == lease_after_cont["revision"]

    # Retirement releases the logical lease but leaves dirty caller data and its worktree intact.
    caller_note = caller_path / "caller-note.txt"
    caller_note.write_text("keep caller data\n", encoding="utf-8")

    # Retiring tok_a2 releases its active lease without cleaning up the caller's path.
    ret = _agent_post(client, tok_a2, "/agent-registrations/retire", {})
    assert ret.status_code == 200, ret.text
    retired_lease = client.get(f"/api/v1/leases/{lease_a['id']}").json()
    assert retired_lease["state"] == "released"
    assert retired_lease["worktreePath"] == str(caller_path)
    assert retired_lease["worktreeSource"] == "caller"
    assert caller_note.read_text(encoding="utf-8") == "keep caller data\n"
    assert _git(caller_path, "symbolic-ref", "--short", "HEAD") == "feat/a"

    # Start a new run & lease under sponsor, then end the run -> releases lease!
    run_s = _agent_post(client, sponsor["token"], "/agent-runs", {"clientRunKey": "run-s", "displayName": "Sponsor"}).json()
    lease_s = _agent_post(client, sponsor["token"], "/leases/acquire", {"repositoryId": repo["id"], "branch": "feat/s", "runId": run_s["id"]}).json()
    assert lease_s["state"] == "active"
    _agent_post(client, sponsor["token"], f"/agent-runs/{run_s['id']}/end", {"revision": run_s["revision"]})
    assert client.get(f"/api/v1/leases/{lease_s['id']}").json()["state"] == "released"

    # Repository merge migrates slot policy & leases to survivor.
    other = _create_repo(client, "old-repo", "https://github.com/example/old-repo")
    client.patch(f"/api/v1/repositories/{other['id']}/slot-policy", json={"maxActiveLeases": 3})
    l_old = _agent_post(client, sponsor["token"], "/leases/acquire", {"repositoryId": other["id"], "branch": "feat/old"}).json()
    merged = client.post(
        f"/api/v1/repositories/{other['id']}/merge",
        json={"intoId": repo["id"], "revision": other["revision"], "intoRevision": repo["revision"]},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert merged.status_code == 200, merged.text
    assert client.get(f"/api/v1/leases/{l_old['id']}").json()["repositoryId"] == repo["id"]
    assert client.get(f"/api/v1/repositories/{repo['id']}/slot-policy").json()["maxActiveLeases"] == 3


def test_dead_antigravity_and_claude_sessions_reaped_by_sweep(tmp_path):
    brain = tmp_path / "ag-brain"
    claude_dir = tmp_path / "claude-sessions"
    claude_dir.mkdir()
    app = create_app(
        tmp_path / "data",
        testing=True,
        transcript_roots=TranscriptRoots(claude_sessions=(claude_dir,)),
        antigravity_brain_root=brain,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        _boot(client)
        repo = _create_repo(client, "liveness-repo")
        a_ag = _issue(client, "AG Agent")
        a_cl = _issue(client, "Claude Agent")

        ag_chat = str(uuid.uuid4())
        cl_chat = str(uuid.uuid4())
        l_ag = _agent_post(
            client, a_ag["token"], "/leases/acquire",
            {"repositoryId": repo["id"], "branch": "feat/ag", "provider": "antigravity", "chatId": ag_chat},
        ).json()
        l_cl = _agent_post(
            client, a_cl["token"], "/leases/acquire",
            {"repositoryId": repo["id"], "branch": "feat/cl", "provider": "claude", "chatId": cl_chat},
        ).json()

        # Write ended Antigravity transcript and stale dead-PID Claude session record.
        tpath = brain / ag_chat / ".system_generated" / "logs" / "transcript.jsonl"
        tpath.parent.mkdir(parents=True)
        tpath.write_text(json.dumps({"type": "session_end", "status": "ended"}) + "\n", encoding="utf-8")
        (claude_dir / "99999999.json").write_text(
            json.dumps({"pid": 99999999, "sessionId": cl_chat, "status": "busy", "startedAt": 1700000000000}),
            encoding="utf-8",
        )
        old_hb = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        with app.state.db.transaction() as c:
            c.execute("UPDATE repository_leases SET last_heartbeat_at=? WHERE id IN (?,?)", (old_hb, l_ag["id"], l_cl["id"]))

        counts = app.state.leases.sweep()
        assert counts["expired"] == 2
        assert client.get(f"/api/v1/leases/{l_ag['id']}").json()["releaseReason"] == "session_ended"
        assert client.get(f"/api/v1/leases/{l_cl['id']}").json()["releaseReason"] == "session_dead"


def test_cli_agent_leases_and_human_repo_commands(env, monkeypatch, capsys):
    client, tmp_path = env
    _, base = make_repo_pair(tmp_path, "cli-repo")
    _git(base, "remote", "set-url", "origin", "https://github.com/example/cli-repo")
    repo = _create_repo(client, "cli-repo", "https://github.com/example/cli-repo")
    agent = _issue(client, "CLI Agent")

    # Human `worklog repo init-slots`, `lock-base`, `slot-status`, `unlock-base`.
    data_dir = str(tmp_path / "data")
    assert cli.main(["--data-dir", data_dir, "repo", "init-slots", "--repo", repo["id"], "--base-checkout", str(base), "--max-slots", "2"]) == 0
    init_out = json.loads(capsys.readouterr().out)
    assert init_out["policy"]["maxActiveLeases"] == 2 and len(init_out["slotPaths"]) == 2

    assert cli.main(["--data-dir", data_dir, "repo", "lock-base", "--repo", repo["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["baseReadonly"] is True

    assert cli.main(["--data-dir", data_dir, "repo", "slot-status", "--repo", repo["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["baseLockedOnDisk"] is True

    # Agent CLI `leases acquire`, `renew`, `status`, `list`, `release`.
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", agent["token"])

    def forward(request, **options):
        url = urlsplit(request.full_url)
        resp = client.request(
            request.get_method(),
            url.path + (f"?{url.query}" if url.query else ""),
            headers=dict(request.header_items()),
            content=request.data,
        )
        if resp.status_code >= 400:
            raise HTTPError(request.full_url, resp.status_code, "err", resp.headers, io.BytesIO(resp.content))
        return io.BytesIO(resp.content)

    monkeypatch.setattr(cli, "_open", forward)
    assert cli.main(["agent", "leases", "acquire", "--checkout", str(base), "--branch", "claude/cli-test", "--request-id", "cli-acq-1"]) == 0
    acquired = json.loads(capsys.readouterr().out)
    assert acquired["state"] == "active" and acquired["slotIndex"] == 1

    assert cli.main(["agent", "leases", "renew", acquired["id"], "--revision", str(acquired["revision"]), "--ttl", "1h", "--request-id", "cli-ren-1"]) == 0
    renewed = json.loads(capsys.readouterr().out)
    assert renewed["revision"] == acquired["revision"] + 1

    assert cli.main(["agent", "leases", "status", "--repo", repo["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["activeCount"] == 1

    assert cli.main(["agent", "leases", "list", "--mine"]) == 0
    assert len(json.loads(capsys.readouterr().out)["items"]) == 1

    assert cli.main(["agent", "leases", "release", acquired["id"], "--revision", str(renewed["revision"]), "--request-id", "cli-rel-1"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "released"

    assert cli.main(["--data-dir", data_dir, "repo", "unlock-base", "--repo", repo["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["baseReadonly"] is False


def test_the_v34_upgrade_changes_no_record_and_the_way_back_gives_v33s_exact_shape(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(data_dir, testing=True)
    with TestClient(app, base_url=ORIGIN) as client:
        _boot(client)
        repo = _create_repo(client, "v34-upgrade-repo")
        agent = _issue(client, "UpgradeAgent")
        _agent_post(client, agent["token"], "/leases/acquire", {"repositoryId": repo["id"], "branch": "feat/v34"})
    database = data_dir / "worklog.sqlite3"
    to_v33(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 33
    _, signature, before = contents(database)
    assert signature == sealed.shape(33)
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    for table in V34_TABLES:
        assert after[table] == []
    assert before_v34(after) == before
    snapshot, = (data_dir / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 33
