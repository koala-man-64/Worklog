"""Synchronized release waves, freeze controls, consumer re-pins, and CLI wave commands (schema v35)."""

from __future__ import annotations

import io
import json
import uuid
from contextlib import redirect_stdout
from urllib.parse import urlsplit

from test_step_update import approve, change, client, ok, read, refused, submit, write
from worklog import cli

__all__ = ["client"]


def _credential(c, label="Agent"):
    return ok(write(c, "POST", "/credentials", {"label": label}))


def _repo(c, name, **extra):
    return ok(write(c, "POST", "/repositories", {"name": name, "url": f"https://github.com/org/{name}", **extra}))


def _roadmap(c, title="Roadmap", steps=None):
    return ok(write(c, "POST", "/roadmaps", {"title": title, "overview": "", "steps": steps or []}))


def test_wave_lifecycle_freeze_auto_seeds_repins_and_agent_repin_report(client):
    r_contracts = _repo(client, "contracts", tier=1, wipCap=1)
    r_common = _repo(client, "runtime-common", tier=1, wipCap=1, upstreamIds=[r_contracts["id"]])
    r_cp = _repo(client, "control-plane", tier=2, wipCap=1, upstreamIds=[r_contracts["id"], r_common["id"]])
    r_unrelated = _repo(client, "unrelated-consumer", tier=2, upstreamIds=[r_contracts["id"]])

    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    rm = _roadmap(client, steps=[
        {"id": s1, "title": "Update contract DTOs", "position": 0, "repositoryIds": [r_contracts["id"]]},
        {"id": s2, "title": "Adopt DTOs in control-plane", "position": 1, "repositoryIds": [r_cp["id"]]},
    ])

    # Create wave with openDiscussion=True.
    w = ok(write(client, "POST", f"/roadmaps/{rm['id']}/waves", {
        "name": "Wave 1: Contract Freeze",
        "summary": "Batch contract changes then re-pin consumers",
        "stepIds": [s1, s2],
        "repositories": [
            {"repositoryId": r_contracts["id"], "tier": 1},
            {"repositoryId": r_common["id"], "tier": 1},
        ],
        "openDiscussion": True,
    }))
    assert w["status"] == "planning"
    assert w["discussionId"] is not None
    # r_cp was auto-added to wave_repositories via s2.
    assert {r["repositoryId"] for r in w["repositories"]} == {r_contracts["id"], r_common["id"], r_cp["id"]}
    assert [g["tier"] for g in w["tierPipeline"]] == [1, 2]

    agent = _credential(client, "RePinBot")

    # Lifecycle status, freeze, verify, archive are HUMAN-only (403 for agents).
    refused(write(client, "POST", f"/waves/{w['id']}/status", {"revision": w["revision"], "status": "active"}, agent), 403, "agent_scope")
    refused(write(client, "POST", f"/waves/{w['id']}/freeze", {
        "revision": w["revision"], "repositoryId": r_contracts["id"], "freezeStatus": "frozen", "frozenRef": "v1.0.0",
    }, agent), 403, "agent_scope")
    refused(write(client, "POST", f"/waves/{w['id']}/archive", {"revision": w["revision"]}, agent), 403, "agent_scope")

    # Person activates wave and freezes contracts @ v1.0.0 -> auto-seeds pending re-pins ONLY for consumers in wave/roadmap.
    w = ok(write(client, "POST", f"/waves/{w['id']}/status", {"revision": w["revision"], "status": "active"}))
    assert w["activatedAt"] is not None
    w = ok(write(client, "POST", f"/waves/{w['id']}/freeze", {
        "revision": w["revision"],
        "repositoryId": r_contracts["id"],
        "freezeStatus": "frozen",
        "frozenRef": "contracts@v1.0.0",
        "notes": "Frozen for Wave 1",
    }))
    repin_pairs = {(rp["upstreamRepoId"], rp["consumerRepoId"]): rp for rp in w["repins"]}
    assert (r_contracts["id"], r_common["id"]) in repin_pairs
    assert (r_contracts["id"], r_cp["id"]) in repin_pairs
    assert (r_contracts["id"], r_unrelated["id"]) not in repin_pairs
    assert all(rp["status"] == "pending" for rp in w["repins"])

    # Agent reports in_progress re-pin with PR + commit SHA; cannot verify directly.
    reported = ok(write(client, "POST", f"/waves/{w['id']}/repins/report", {
        "upstreamRepoId": r_contracts["id"],
        "consumerRepoId": r_cp["id"],
        "pinnedRef": "contracts@v1.0.0",
        "prUrl": "https://github.com/org/control-plane/pull/42",
        "commitSha": "abcdef1234567890",
    }, agent))
    assert reported["status"] == "in_progress"
    assert reported["reportedByLabel"] == "RePinBot"
    assert reported["commitSha"] == "abcdef1234567890"
    refused(write(client, "POST", f"/waves/{w['id']}/repins/verify", {
        "revision": reported["revision"],
        "upstreamRepoId": r_contracts["id"],
        "consumerRepoId": r_cp["id"],
        "status": "verified",
    }, agent), 403, "agent_scope")

    # Human verifies control-plane re-pin and waives runtime-common re-pin.
    verified = ok(write(client, "POST", f"/waves/{w['id']}/repins/verify", {
        "revision": reported["revision"],
        "upstreamRepoId": r_contracts["id"],
        "consumerRepoId": r_cp["id"],
        "status": "verified",
    }))
    assert verified["status"] == "verified"
    assert verified["verifiedAt"] is not None

    # Agent cannot overwrite an already-verified re-pin.
    refused(write(client, "POST", f"/waves/{w['id']}/repins/report", {
        "upstreamRepoId": r_contracts["id"],
        "consumerRepoId": r_cp["id"],
        "pinnedRef": "contracts@v1.0.1",
    }, agent), 409, "conflict")

    # Re-freezing contracts with a NEW frozenRef invalidates the old verified re-pin back to pending!
    w = ok(read(client, f"/waves/{w['id']}"))
    w = ok(write(client, "POST", f"/waves/{w['id']}/freeze", {
        "revision": w["revision"],
        "repositoryId": r_contracts["id"],
        "freezeStatus": "frozen",
        "frozenRef": "contracts@v1.0.1",
    }))
    cp_repin = next(rp for rp in w["repins"] if rp["consumerRepoId"] == r_cp["id"])
    assert cp_repin["status"] == "pending"
    assert cp_repin["verifiedAt"] is None


def test_single_active_wave_per_step_and_agent_wave_proposal_names(client):
    agent = _credential(client, "WavePlanner")
    r1 = _repo(client, "contracts", tier=1)
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    rm = _roadmap(client, steps=[
        {"id": s1, "title": "Step 1", "position": 0, "repositoryIds": [r1["id"]]},
        {"id": s2, "title": "Step 2", "position": 1, "repositoryIds": [r1["id"]]},
    ])

    # Agent proposes wave.create named @wave1, followed by wave.update and wave.steps referencing @wave1.
    prop = submit(client, agent, "Create Wave 1", [
        change("wave.create", id="@wave1", roadmapId=rm["id"], name="Wave 1", stepIds=[s1]),
        change("wave.update", id="@wave1", revision=1, summary="Updated summary"),
        change("wave.steps", id="@wave1", revision=2, mode="add", stepIds=[s2]),
    ])
    ok(approve(client, prop))

    waves = ok(read(client, f"/roadmaps/{rm['id']}/waves"))["items"]
    assert len(waves) == 1
    w1 = ok(read(client, f"/waves/{waves[0]['id']}"))
    assert w1["summary"] == "Updated summary"
    assert set(w1["stepIds"]) == {s1, s2}

    # Creating Wave 2 with s2 automatically moves s2 out of planning Wave 1 (single active/planning wave invariant).
    w2 = ok(write(client, "POST", f"/roadmaps/{rm['id']}/waves", {"name": "Wave 2", "stepIds": [s2]}))
    assert ok(read(client, f"/waves/{w1['id']}"))["stepIds"] == [s1]
    assert ok(read(client, f"/waves/{w2['id']}"))["stepIds"] == [s2]

    # Completing Wave 2, moving s2 back to Wave 1, then attempting to reopen Wave 2 raises 409 conflict.
    w2 = ok(write(client, "POST", f"/waves/{w2['id']}/status", {"revision": w2["revision"], "status": "completed"}))
    w1 = ok(read(client, f"/waves/{w1['id']}"))
    ok(write(client, "POST", f"/waves/{w1['id']}/steps", {"revision": w1["revision"], "mode": "add", "stepIds": [s2]}))
    refused(write(client, "POST", f"/waves/{w2['id']}/status", {"revision": w2["revision"], "status": "active"}), 409, "conflict")


def test_agent_cli_waves_and_dispatch_commands(client, monkeypatch):
    r1 = _repo(client, "contracts", tier=1, wipCap=1)
    r2 = _repo(client, "service", tier=2, wipCap=1, upstreamIds=[r1["id"]])
    s1 = str(uuid.uuid4())
    rm = _roadmap(client, steps=[{"id": s1, "title": "Schema v1", "position": 0, "repositoryIds": [r1["id"]]}])
    w = ok(write(client, "POST", f"/roadmaps/{rm['id']}/waves", {
        "name": "Wave CLI",
        "stepIds": [s1],
        "repositories": [{"repositoryId": r1["id"], "tier": 1}, {"repositoryId": r2["id"], "tier": 2}],
    }))
    w = ok(write(client, "POST", f"/waves/{w['id']}/status", {"revision": w["revision"], "status": "active"}))
    ok(write(client, "POST", f"/waves/{w['id']}/freeze", {
        "revision": w["revision"], "repositoryId": r1["id"], "freezeStatus": "frozen", "frozenRef": "v2.0",
    }))
    agent = _credential(client, "CLIBot")

    def fake_request(self, method, path, payload=None, *, request_id=None, raw=None, content_type=None):
        headers = {"Authorization": "Bearer " + agent["token"]}
        if method != "GET":
            headers["Idempotency-Key"] = request_id or str(uuid.uuid4())
        parsed = urlsplit(path)
        full = "/api/v1" + parsed.path + (("?" + parsed.query) if parsed.query else "")
        res = client.request(method, full, json=payload, headers=headers)
        assert res.status_code == 200, (method, full, res.text)
        return res.json()

    monkeypatch.setattr(cli.AgentClient, "request", fake_request)
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", agent["token"])

    def run_cli(*argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(list(argv))
        return json.loads(buf.getvalue())

    listed = run_cli("agent", "waves", "list", "--roadmap", rm["id"])
    assert listed["total"] == 1
    shown = run_cli("agent", "waves", "show", w["id"])
    assert shown["name"] == "Wave CLI"
    repin = run_cli(
        "agent", "waves", "repin", w["id"],
        "--upstream", r1["id"], "--consumer", r2["id"], "--ref", "v2.0", "--request-id", "repin-req-1",
    )
    assert repin["status"] == "in_progress"
    disp = run_cli("agent", "roadmap", "dispatch", rm["id"])
    assert disp["activeWave"]["id"] == w["id"]
    q = run_cli("agent", "repositories", "queue", r1["id"])
    assert q["repositoryId"] == r1["id"]
