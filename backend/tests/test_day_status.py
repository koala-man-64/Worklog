"""Each day entry's statusOnDay: the status its work item ended that day with, which groups the
day's summary on Today, so a later change cannot regroup an old day."""

import uuid

import pytest
import worklog.common as common
from fastapi.testclient import TestClient
from worklog.app import create_app

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        assert response.status_code == 200
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


@pytest.fixture
def clock(monkeypatch):
    """The local date each change is recorded on."""
    day = ["2026-10-28"]
    monkeypatch.setattr(common, "today", lambda *args: day[0])
    return day


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def write(c, method, path, payload):
    return ok(c.request(method, "/api/v1" + path, headers={"Idempotency-Key": str(uuid.uuid4())}, json=payload))


def entry(c, date):
    (only,) = ok(c.get(f"/api/v1/days/{date}"))["entries"]
    return only


def test_an_entry_keeps_the_status_its_item_ended_the_day_with(client, clock):
    work = write(client, "POST", "/work-items", {"title": "Ship the release", "day": "2026-10-28", "worked": True})
    write(client, "PUT", f"/days/2026-10-29/entries/{work['id']}", {"revision": 0, "planned": True})
    assert entry(client, "2026-10-28")["statusOnDay"] == "open"
    write(client, "PATCH", f"/work-items/{work['id']}", {"revision": 1, "status": "doing"})
    clock[0] = "2026-10-29"
    write(client, "PATCH", f"/work-items/{work['id']}", {"revision": 2, "status": "done"})
    first, second = entry(client, "2026-10-28"), entry(client, "2026-10-29")
    assert first["item"]["status"] == "done" and first["statusOnDay"] == "doing"
    assert second["statusOnDay"] == "done"
    # Later changes reach no earlier day.
    clock[0] = "2026-10-30"
    write(client, "PATCH", f"/work-items/{work['id']}", {"revision": 3, "status": "blocked"})
    assert [entry(client, d)["statusOnDay"] for d in ("2026-10-28", "2026-10-29")] == ["doing", "done"]


def test_work_logged_after_its_day_shows_its_status_now(client, clock):
    clock[0] = "2026-10-29"
    work = write(client, "POST", "/work-items", {"title": "Catch up on Monday", "day": "2026-10-27", "worked": True})
    assert entry(client, "2026-10-27")["statusOnDay"] == "open"
    write(client, "PATCH", f"/work-items/{work['id']}", {"revision": 1, "status": "done"})
    assert entry(client, "2026-10-27")["statusOnDay"] == "done"
