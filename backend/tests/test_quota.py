"""Quota Burndown integration: API routes, legacy compatibility, agent scope, and CLI."""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from worklog import cli
from worklog.app import create_app
from worklog.quota.config import get_paths as get_quota_paths
from worklog.quota.store import Sample, Store

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    quota_dir = tmp_path / "quota"
    quota_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    sample = Sample(
        ts=now - timedelta(minutes=10),
        provider="claude",
        window="7d (all models)",
        used=42.5,
        resets_at=now + timedelta(days=3),
        window_min=10080,
        source="api",
    )
    Store(get_quota_paths(quota_dir)).append([sample])

    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post(
            "/api/v1/session/bootstrap",
            headers={"X-Worklog-Bootstrap": "1"},
            json={},
        )
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


def request(c, method, path, payload=None, agent=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response, expected_status=200):
    assert response.status_code == expected_status, response.text
    return response.json()


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]


def credential(c, label="Implementer"):
    return ok(request(c, "POST", "/credentials", {"label": label}))


def test_quota_dashboard_and_snapshots_report_seeded_burndown(client):
    dashboard = ok(request(client, "GET", "/quota/dashboard"))
    assert dashboard["sample_count"] == 1
    assert any(card["provider"] == "claude" for card in dashboard["cards"])
    claude_card = next(c for c in dashboard["cards"] if c["provider"] == "claude")
    assert claude_card["used"] == pytest.approx(42.5)
    assert "<svg" in claude_card["chart_svg"]

    capacity = ok(request(client, "GET", "/quota/capacity"))
    assert "pools" in capacity
    assert "policy" in capacity

    status = ok(request(client, "GET", "/quota/status"))
    assert status["items"]
    assert "claude:7d (all models)" in status["latest"]

    usage_summary = ok(request(client, "GET", "/quota/usage?view=summary"))
    assert "summary" in usage_summary
    assert "by_model_effort" in usage_summary

    usage_efficiency = ok(request(client, "GET", "/quota/usage?view=efficiency"))
    assert "by_session" in usage_efficiency
    assert "daily_models" in usage_efficiency


def test_quota_policy_and_credit_plan_lifecycle(client):
    # Reserve buffer updates
    updated_policy = ok(
        request(client, "POST", "/quota/policy", {"reserve_pct": 20}),
        expected_status=202,
    )
    assert updated_policy == {"accepted": True}
    assert ok(request(client, "GET", "/quota/capacity"))["policy"]["reserve_pct"] == 20

    # Invalid reserve buffer rejected with 422
    bad_policy = request(client, "POST", "/quota/policy", {"reserve_pct": 15})
    assert bad_policy.status_code == 422

    # Credit plan creation, revision check, conflict, and clear
    initial = ok(request(client, "GET", "/quota/plans"))
    rev0 = initial["revision"]
    now = datetime.now(UTC)
    starts_at = now.isoformat()
    ends_at = (now + timedelta(days=7)).isoformat()

    created = ok(
        request(
            client,
            "POST",
            "/quota/plans",
            {
                "provider": "claude",
                "expected_revision": rev0,
                "plan": {
                    "amount": "50.00",
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "model": "claude-sonnet-4-6",
                    "speed": "standard",
                },
            },
        )
    )
    assert created["revision"] == rev0 + 1
    assert created["plans"]["claude"]["amount"] == "50.00"

    # Stale revision triggers 409 conflict
    stale = request(
        client,
        "POST",
        "/quota/plans",
        {
            "provider": "claude",
            "expected_revision": rev0,
            "plan": {
                "amount": "75.00",
                "starts_at": starts_at,
                "ends_at": ends_at,
                "model": "claude-sonnet-4-6",
                "speed": "standard",
            },
        },
    )
    assert stale.status_code == 409

    # Clear active plan
    cleared = ok(
        request(
            client,
            "POST",
            "/quota/plans",
            {
                "provider": "claude",
                "expected_revision": created["revision"],
                "plan": None,
            },
        )
    )
    assert cleared["revision"] == created["revision"] + 1
    assert cleared["plans"].get("claude") is None


def test_legacy_loopback_routes_remain_compatible(client):
    assert ok(client.get("/v1/capacity"))["policy"]["reserve_pct"] in (5, 10, 20)
    assert "revision" in ok(client.get("/v1/plans"))
    assert "by_session" in ok(client.get("/v1/usage"))
    assert "claude:7d (all models)" in ok(client.get("/latest.json"))
    assert isinstance(ok(client.get("/status.json")), list)
    assert "summary" in ok(client.get("/usage.json"))

    legacy_policy = ok(
        client.post("/v1/policy", json={"reserve_pct": 5}),
        expected_status=202,
    )
    assert legacy_policy == {"accepted": True}
    assert ok(client.get("/v1/capacity"))["policy"]["reserve_pct"] == 5


def test_agents_can_read_quota_gates_but_cannot_read_transcripts_or_mutate(client):
    agent = credential(client)

    # Allowed read-only pre-flight gates
    assert "pools" in ok(request(client, "GET", "/quota/capacity", agent=agent))
    assert "items" in ok(request(client, "GET", "/quota/status", agent=agent))
    assert "summary" in ok(request(client, "GET", "/quota/usage", agent=agent))

    # Blocked routes for agent tokens (privacy + write protection)
    error(request(client, "GET", "/quota/dashboard", agent=agent), 403, "agent_scope")
    error(
        request(client, "GET", "/quota/recent-text/req-1", agent=agent),
        403,
        "agent_scope",
    )
    error(
        request(client, "POST", "/quota/policy", {"reserve_pct": 20}, agent=agent),
        403,
        "agent_scope",
    )
    error(
        request(
            client,
            "POST",
            "/quota/plans",
            {"provider": "claude", "expected_revision": 0, "plan": None},
            agent=agent,
        ),
        403,
        "agent_scope",
    )
    error(
        request(client, "POST", "/quota/collect", {}, agent=agent),
        403,
        "agent_scope",
    )


def test_agent_quota_cli_and_top_level_quota_forwarding(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKLOG_AGENT_TOKEN", "private-agent-token")
    monkeypatch.delenv("WORKLOG_PORT", raising=False)
    captured = []

    def respond(req, **kwargs):
        captured.append(req)
        return io.BytesIO(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(cli, "_open", respond)

    assert cli.main(["agent", "quota"]) == 0
    assert urlsplit(captured[-1].full_url).path == "/api/v1/quota/capacity"

    assert cli.main(["agent", "quota", "status"]) == 0
    assert urlsplit(captured[-1].full_url).path == "/api/v1/quota/status"

    assert cli.main(["agent", "quota", "usage", "--view", "efficiency"]) == 0
    assert urlsplit(captured[-1].full_url).path == "/api/v1/quota/usage"
    assert parse_qs(urlsplit(captured[-1].full_url).query) == {"view": ["efficiency"]}

    # Top-level `worklog quota --home <tmp> status --json` delegates to worklog.quota.cli
    quota_dir = tmp_path / "cli_quota"
    quota_dir.mkdir(parents=True, exist_ok=True)
    assert cli.main(["quota", "--home", str(quota_dir), "status", "--json"]) == 0
