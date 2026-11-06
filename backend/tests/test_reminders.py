"""Reminders (schema v20): due on a day, or at a time on it, by the clock of the Worklog timezone; tied
to at most one record; set, snoozed and completed by the person and by agents; archived only by the
person."""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from worklog import reminders
from worklog.app import create_app
from worklog.common import AppError
from worklog.reminders import ReminderService

ORIGIN = "http://127.0.0.1:8877"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, testing=True)
    with TestClient(app, base_url=ORIGIN) as c:
        c.headers["Origin"] = ORIGIN
        response = c.post("/api/v1/session/bootstrap", headers={"X-Worklog-Bootstrap": "1"}, json={})
        c.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        yield c


@pytest.fixture
def clock(monkeypatch):
    """Stops the Worklog clock at one moment, given in Chicago's time: clock("2026-11-06 14:25"). It
    answers in whichever timezone Worklog asks for, and returns the timezones it was asked for."""
    asked: list[str] = []

    def stop_at(stamp: str) -> list[str]:
        moment = datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo("America/Chicago"))

        def local_now(tz: str) -> datetime:
            asked.append(tz)
            return moment.astimezone(ZoneInfo(tz))

        monkeypatch.setattr(reminders, "local_now", local_now)
        return asked

    stop_at("2026-11-06 08:00")
    return stop_at


def request(c, method, path, payload=None, agent=None, key=None):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if agent:
        headers["Authorization"] = "Bearer " + agent["token"]
    return c.request(method, "/api/v1" + path, headers=headers, json=payload)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]


def get(c, path, agent=None):
    return ok(request(c, "GET", path, agent=agent))


def add(c, title="Merge #113 once CI is green", date="2026-11-06", agent=None, **fields):
    return ok(request(c, "POST", "/reminders", {"title": title, "date": date, **fields}, agent))


def patch(c, reminder, agent=None, **fields):
    return ok(request(c, "PATCH", f"/reminders/{reminder['id']}", {"revision": reminder["revision"], **fields}, agent))


def credential(c, label="Implementer"):
    return ok(request(c, "POST", "/credentials", {"label": label}))


def titles(page):
    return [r["title"] for r in page["items"]]


def test_a_reminder_stays_open_until_done_and_records_who_set_it(client, clock):
    r = add(client, time="14:25", notes="Once *CI* is green")
    assert (r["title"], r["date"], r["time"], r["state"], r["target"], r["doneAt"], r["notes"]) == (
        "Merge #113 once CI is green", "2026-11-06", "14:25", "open", None, None, "Once *CI* is green")
    assert r["author"] == {"id": "local-user", "label": "You", "kind": "human", "avatar": None}
    assert (r["due"], r["overdue"], r["archived"], r["revision"]) == (False, False, False, 1)
    done = patch(client, r, state="done")
    assert (done["state"], done["due"]) == ("done", False) and done["doneAt"]
    edited = patch(client, done, title="Merge #113 and deploy")
    assert edited["doneAt"] == done["doneAt"]
    reopened = patch(client, edited, state="open")
    assert (reopened["state"], reopened["doneAt"]) == ("open", None)
    shown = get(client, f"/reminders/{r['id']}")
    assert [h["kind"] for h in shown["history"]] == ["reminder.reopen", "reminder.update", "reminder.done", "reminder.create"]
    assert set(shown["history"][0]) == {"id", "kind", "actorId", "actorLabel", "actorKind", "createdAt"}


def test_a_reminder_comes_due_at_its_time_and_an_all_day_one_from_midnight(client, clock):
    clock("2026-11-06 00:00")
    all_day = add(client, "Renew the certificate")
    timed = add(client, "Merge #113", time="14:25")
    earlier = add(client, "Reply to Ana", date="2026-11-04", time="16:00")
    add(client, "Plan the sprint", date="2026-11-09")
    assert titles(get(client, "/reminders?state=due")) == ["Reply to Ana", "Renew the certificate"]
    assert titles(get(client, "/reminders?state=upcoming")) == ["Merge #113", "Plan the sprint"]
    clock("2026-11-06 14:24")
    assert not get(client, f"/reminders/{timed['id']}")["due"]
    clock("2026-11-06 14:25")
    shown = get(client, f"/reminders/{timed['id']}")
    assert (shown["due"], shown["overdue"]) == (True, False)
    assert get(client, f"/reminders/{earlier['id']}")["overdue"]
    assert get(client, "/reminders?state=due")["total"] == 3
    clock("2026-11-07 08:00")
    assert get(client, f"/reminders/{all_day['id']}")["overdue"]
    patch(client, get(client, f"/reminders/{earlier['id']}"), state="done")
    assert titles(get(client, "/reminders?state=due")) == ["Renew the certificate", "Merge #113"]


def test_due_follows_the_clock_of_the_worklog_timezone(client, clock):
    # 23:30 in Chicago on 6 November is 14:30 on 7 November in Tokyo.
    asked = clock("2026-11-06 23:30")
    add(client, "Call the Tokyo office", date="2026-11-07", time="14:00")
    assert get(client, "/reminders?state=due")["total"] == 0
    assert asked[-1] == "America/Chicago"
    ok(request(client, "PATCH", "/settings", {"timezone": "Asia/Tokyo"}))
    assert get(client, "/reminders?state=due")["total"] == 1
    assert asked[-1] == "Asia/Tokyo"


def test_open_reminders_list_by_when_all_day_first_and_done_ones_latest_done_first(client, clock):
    add(client, "Late", time="17:00")
    add(client, "All day")
    add(client, "Early", time="09:00")
    add(client, "Yesterday evening", date="2026-11-05", time="23:00")
    patch(client, add(client, "Done first", date="2026-11-01"), state="done")
    patch(client, add(client, "Done second", date="2026-11-09"), state="done")
    assert titles(get(client, "/reminders")) == ["Yesterday evening", "All day", "Early", "Late"]
    assert titles(get(client, "/reminders?state=done")) == ["Done second", "Done first"]
    assert titles(get(client, "/reminders?state=all")) == [
        "Yesterday evening", "All day", "Early", "Late", "Done second", "Done first"]


def test_lists_keep_a_day_the_days_up_to_one_words_and_pages(client, clock):
    add(client, "Standup notes", notes="Mention the parser fix")
    add(client, "Parser release", date="2026-11-07")
    add(client, "Retro", date="2026-11-05")
    assert titles(get(client, "/reminders?date=2026-11-06")) == ["Standup notes"]
    assert titles(get(client, "/reminders?until=2026-11-06")) == ["Retro", "Standup notes"]
    assert sorted(titles(get(client, "/reminders?q=parse"))) == ["Parser release", "Standup notes"]
    page = get(client, "/reminders?limit=1&offset=1")
    assert (titles(page), page["total"], page["limit"], page["offset"]) == (["Standup notes"], 3, 1, 1)
    assert "notes" not in page["items"][0]
    assert error(request(client, "GET", "/reminders?date=2026-02-30"), 422, "validation")["fields"] == {"date": "invalid"}
    assert error(request(client, "GET", "/reminders?until=soon"), 422, "validation")["fields"] == {"until": "invalid"}
    error(request(client, "GET", "/reminders?state=later"), 422, "validation")
    error(request(client, "GET", "/reminders?targetType=item"), 422, "validation")


@pytest.mark.parametrize(("fields", "field"), [
    ({"title": "  "}, "title"), ({"date": "2026-13-01"}, "date"), ({"date": "06/11/2026"}, "date"),
    ({"time": "24:00"}, "time"), ({"time": "9:30"}, "time"), ({"time": "09:30:00"}, "time"),
    ({"targetType": "item"}, "targetId"), ({"targetId": "T-1"}, "targetType"),
    ({"notes": "x" * 100_001}, "notes"), ({"title": "x" * 1_001}, "title")])
def test_a_reminder_needs_a_title_a_calendar_date_and_a_clock_time(client, fields, field):
    refused = error(request(client, "POST", "/reminders", {"title": "Check CI", "date": "2026-11-06", **fields}), 422, "validation")
    assert field in refused["fields"]


def test_the_request_model_refuses_unknown_targets_fields_and_a_missing_date(client):
    for body in ({"title": "Check CI", "date": "2026-11-06", "targetType": "invoice", "targetId": "1"},
                 {"title": "Check CI", "date": "2026-11-06", "repeat": "daily"}, {"title": "Check CI"}):
        error(request(client, "POST", "/reminders", body), 422, "validation")
    assert get(client, "/reminders?state=all")["total"] == 0


def test_snoozing_moves_its_day_and_time_and_a_null_time_makes_it_all_day(client, clock):
    clock("2026-11-06 15:00")
    r = add(client, time="14:25")
    assert r["due"]
    snoozed = patch(client, r, time="16:00")
    assert (snoozed["date"], snoozed["time"], snoozed["due"]) == ("2026-11-06", "16:00", False)
    tomorrow = patch(client, snoozed, date="2026-11-07", time=None)
    assert (tomorrow["date"], tomorrow["time"], tomorrow["due"]) == ("2026-11-07", None, False)
    kept = patch(client, tomorrow, title="Merge #113 now")
    assert (kept["date"], kept["time"]) == ("2026-11-07", None)


def test_a_stale_revision_conflicts_and_a_retried_create_returns_the_same_reminder(client, clock):
    key, body = str(uuid.uuid4()), {"title": "Once", "date": "2026-11-06"}
    first = ok(request(client, "POST", "/reminders", body, key=key))
    assert ok(request(client, "POST", "/reminders", body, key=key))["id"] == first["id"]
    error(request(client, "POST", "/reminders", {**body, "title": "Twice"}, key=key), 409, "idempotency_conflict")
    patch(client, first, time="10:00")
    error(request(client, "PATCH", f"/reminders/{first['id']}", {"revision": first["revision"], "time": "11:00"}), 409, "conflict")
    error(client.post("/api/v1/reminders", json=body), 428, "idempotency")
    error(request(client, "GET", f"/reminders/{uuid.uuid4()}"), 404, "not_found")
    assert get(client, "/reminders?state=all")["total"] == 1


def test_a_reminder_names_one_record_of_each_kind_and_shows_it_as_it_stands(client, clock):
    item = ok(request(client, "POST", "/work-items", {"title": "Import format"}))
    ticket = ok(request(client, "POST", "/tickets", {"title": "Parser drops rows"}))
    roadmap = ok(request(client, "POST", "/roadmaps", {"title": "Q4 platform"}))
    person = ok(request(client, "POST", "/people", {"name": "Ana Silva"}))
    repository = ok(request(client, "POST", "/repositories", {"name": "worklog"}))
    chat = ok(request(client, "POST", "/chats", {"ownerType": "item", "ownerId": item["id"], "chatId": str(uuid.uuid4()),
                                                 "title": "Parser chat"}))
    named = {"item": (item["id"], "Import format"), "ticket": (ticket["id"], "T-1 Parser drops rows"),
             "roadmap": (roadmap["id"], "Q4 platform"), "person": (person["id"], "Ana Silva"),
             "repository": (repository["id"], "worklog"), "chat": (chat["id"], "Parser chat")}
    for kind, (identity, title) in named.items():
        # A ticket can be named by its key.
        ref = "T-1" if kind == "ticket" else identity
        r = add(client, "About the " + kind, targetType=kind, targetId=ref)
        assert r["target"] == {"type": kind, "id": identity, "title": title, "archived": False}
        assert titles(get(client, f"/reminders?targetType={kind}&targetId={ref}")) == ["About the " + kind]
    renamed = ok(request(client, "PATCH", f"/people/{person['id']}", {"revision": person["revision"], "name": "Ana Lima"}))
    assert get(client, f"/reminders?targetType=person&targetId={renamed['id']}")["items"][0]["target"]["title"] == "Ana Lima"
    error(request(client, "POST", "/reminders", {"title": "Ghost", "date": "2026-11-06", "targetType": "person",
                                                 "targetId": str(uuid.uuid4())}), 404, "not_found")
    error(request(client, "POST", "/reminders", {"title": "Ghost", "date": "2026-11-06", "targetType": "ticket",
                                                 "targetId": "T-99"}), 404, "not_found")
    error(request(client, "GET", "/reminders?targetType=ticket&targetId=T-99"), 404, "not_found")


def test_an_archived_record_takes_no_new_reminder_but_keeps_those_it_has(client, clock):
    item = ok(request(client, "POST", "/work-items", {"title": "Old work"}))
    kept = add(client, "Follow up", targetType="item", targetId=item["id"])
    ok(request(client, "POST", f"/work-items/{item['id']}/archive", {"revision": item["revision"]}))
    error(request(client, "POST", "/reminders", {"title": "New", "date": "2026-11-06", "targetType": "item",
                                                 "targetId": item["id"]}), 409, "archived")
    shown = get(client, f"/reminders/{kept['id']}")
    assert shown["target"] == {"type": "item", "id": item["id"], "title": "Old work", "archived": True}
    # Naming the record it already names again is no new tie.
    again = patch(client, shown, title="Follow up anyway", targetType="item", targetId=item["id"])
    assert again["target"]["id"] == item["id"]
    other = ok(request(client, "POST", "/work-items", {"title": "New work"}))
    moved = patch(client, again, targetType="item", targetId=other["id"])
    assert moved["target"]["title"] == "New work"
    error(request(client, "PATCH", f"/reminders/{kept['id']}", {"revision": moved["revision"], "targetType": "item",
                                                                "targetId": item["id"]}), 409, "archived")
    unlinked = patch(client, moved, targetType=None, targetId=None)
    assert unlinked["target"] is None


def test_only_the_person_archives_and_an_archived_reminder_is_read_only_and_out_of_search(client, clock):
    r = add(client, "Rotate the API key", notes="Before the audit")
    found = get(client, "/search?q=audit")["items"]
    assert [(h["type"], h["routeType"], h["routeId"]) for h in found] == [("reminder", "reminders", r["id"])]
    archived = ok(request(client, "POST", f"/reminders/{r['id']}/archive", {"revision": r["revision"]}))
    assert archived["archived"]
    error(request(client, "PATCH", f"/reminders/{r['id']}", {"revision": archived["revision"], "title": "x"}), 409, "archived")
    assert get(client, "/search?q=audit")["total"] == 0
    assert get(client, "/search?q=audit&archived=true")["total"] == 1
    assert get(client, "/reminders")["total"] == 0 and titles(get(client, "/reminders?archived=true")) == ["Rotate the API key"]
    recovered = ok(request(client, "POST", f"/reminders/{r['id']}/recover", {"revision": archived["revision"]}))
    assert not recovered["archived"] and get(client, "/search?q=audit")["total"] == 1
    error(request(client, "POST", f"/reminders/{r['id']}/delete", {"revision": recovered["revision"]}), 404, "not_found")
    kinds = [h["kind"] for h in get(client, f"/reminders/{r['id']}")["history"]]
    assert kinds == ["reminder.recover", "reminder.archive", "reminder.create"]
    with client.app.state.db.transaction() as conn, pytest.raises(AppError) as refused:
        ReminderService().set_archived(conn, {"id": "agent", "label": "Agent", "kind": "agent"}, r["id"],
                                       {"revision": recovered["revision"]}, True)
    assert refused.value.code == "agent_scope"


def test_a_repository_merge_moves_its_reminders_to_the_survivor_without_new_revisions(client, clock):
    source = ok(request(client, "POST", "/repositories", {"name": "worklog-old"}))
    survivor = ok(request(client, "POST", "/repositories", {"name": "worklog"}))
    r = add(client, "Tag the release", targetType="repository", targetId=source["id"])
    ok(request(client, "POST", f"/repositories/{source['id']}/merge",
               {"revision": source["revision"], "intoId": survivor["id"], "intoRevision": survivor["revision"]}))
    shown = get(client, f"/reminders/{r['id']}")
    assert shown["target"] == {"type": "repository", "id": survivor["id"], "title": "worklog", "archived": False}
    assert shown["revision"] == r["revision"]
    assert titles(get(client, f"/reminders?targetType=repository&targetId={survivor['id']}")) == ["Tag the release"]
    merge = [e for e in get(client, f"/history?entityId={survivor['id']}")["items"] if e["kind"] == "repository.merge"]
    assert merge[0]["after"]["moved"]["reminders"] == 1


def test_agents_read_reminders_and_only_the_person_archives_them(client, clock):
    agent = credential(client)
    r = add(client, "Review the release notes")
    assert titles(get(client, "/reminders", agent=agent)) == ["Review the release notes"]
    assert get(client, f"/reminders/{r['id']}", agent=agent)["notes"] == ""
    for verb in ("archive", "recover"):
        error(request(client, "POST", f"/reminders/{r['id']}/{verb}", {"revision": r["revision"]}, agent), 403, "agent_scope")


def test_agent_writes_set_snooze_and_complete_reminders_as_the_agent_across_a_rotation(client, clock):
    agent = credential(client)
    key, body = str(uuid.uuid4()), {"title": "Merge #113 once CI is green", "date": "2026-11-06", "time": "14:25"}
    r = ok(request(client, "POST", "/reminders", body, agent, key=key))
    assert (r["author"]["id"], r["author"]["label"], r["author"]["kind"]) == (agent["agentId"], "Implementer", "agent")
    assert r["author"]["avatar"]
    rotated = ok(request(client, "POST", f"/credentials/{agent['id']}/rotate", {}))
    # The receipt belongs to the identity, so a retry after the rotation returns the same reminder.
    assert ok(request(client, "POST", "/reminders", body, rotated, key=key))["id"] == r["id"]
    snoozed = patch(client, r, agent=rotated, time="15:00")
    done = patch(client, snoozed, agent=rotated, state="done")
    history = get(client, f"/reminders/{r['id']}", agent=rotated)["history"]
    assert [h["kind"] for h in history] == ["reminder.done", "reminder.update", "reminder.create"]
    assert {h["actorId"] for h in history} == {agent["agentId"]}
    # Agents complete the person's reminders too.
    mine = add(client, "Read the incident report")
    assert patch(client, mine, agent=rotated, state="done")["state"] == "done"
    error(request(client, "POST", f"/reminders/{r['id']}/archive", {"revision": done["revision"]}, rotated), 403, "agent_scope")
    ok(request(client, "POST", f"/credentials/{rotated['id']}/revoke", {}))
    error(request(client, "POST", "/reminders", {"title": "Too late", "date": "2026-11-06"}, rotated), 401, "authentication")
    error(request(client, "PATCH", f"/reminders/{r['id']}", {"revision": done["revision"], "state": "open"}, rotated),
          401, "authentication")
    assert get(client, f"/reminders/{r['id']}")["state"] == "done"


def test_agent_writes_name_only_active_records(client, clock):
    agent = credential(client)
    item = ok(request(client, "POST", "/work-items", {"title": "Old work"}))
    r = add(client, "Follow up", agent=agent)
    ok(request(client, "POST", f"/work-items/{item['id']}/archive", {"revision": item["revision"]}))
    error(request(client, "PATCH", f"/reminders/{r['id']}", {"revision": r["revision"], "targetType": "item",
                                                             "targetId": item["id"]}, agent), 409, "archived")
    error(request(client, "POST", "/reminders", {"title": "New", "date": "2026-11-06", "targetType": "item",
                                                 "targetId": item["id"]}, agent), 409, "archived")
    assert get(client, f"/reminders/{r['id']}")["target"] is None


def test_an_all_day_reminder_comes_due_at_midnight_and_a_timed_one_at_its_minute(client, clock):
    clock("2026-11-06 23:59")
    add(client, "Start the import", date="2026-11-07")
    add(client, "Check the import", date="2026-11-07", time="00:00")
    add(client, "Read the report", date="2026-11-07", time="00:01")
    assert get(client, "/reminders?state=due")["total"] == 0
    clock("2026-11-07 00:00")
    assert titles(get(client, "/reminders?state=due")) == ["Start the import", "Check the import"]
    assert titles(get(client, "/reminders?state=upcoming")) == ["Read the report"]
    clock("2026-11-07 00:01")
    assert get(client, "/reminders?state=due")["total"] == 3
