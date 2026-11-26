"""The calculated Active status: work items and steps an agent reports work on now (agent_activity)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from test_step_update import client, ok, read, write

__all__ = ["client"]


def _agent(c, label="Worker"):
    """A registered agent with an open run; returns (identity, run)."""
    sponsor = ok(write(c, "POST", "/credentials", {"label": "Sponsor " + label}))
    token = "tok-" + uuid.uuid4().hex + uuid.uuid4().hex
    chat = f"chat-{uuid.uuid4().hex[:8]}"
    agent = {**ok(write(c, "POST", "/agent-registrations", {"label": label, "chatId": chat, "token": token}, sponsor)),
             "token": token}
    run = ok(write(c, "POST", "/agent-runs", {"clientRunKey": chat, "displayName": label, "externalChatId": chat}, agent))
    return agent, run


def _participate(c, agent, run, *, item=None, step=None):
    body = {"runId": run["id"], "description": "working"}
    if item:
        body["itemId"] = item
    if step:
        body["stepId"] = step
    return ok(write(c, "POST", "/participations", body, agent))


def _item(c, title, **extra):
    return ok(write(c, "POST", "/work-items", {"title": title, **extra}))


def _status(c, item_id, status):
    current = ok(read(c, f"/work-items/{item_id}"))
    ok(write(c, "PATCH", f"/work-items/{item_id}", {"revision": current["revision"], "status": status}))


def _progress(c, step_id, status):
    current = ok(read(c, f"/steps/{step_id}"))
    ok(write(c, "PATCH", f"/steps/{step_id}/progress", {"revision": current["revision"], "status": status}))


def _age(c, participation_id, hours):
    seen = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with c.app.state.db.transaction() as conn:
        conn.execute("UPDATE participations SET last_seen_at=? WHERE id=?", (seen, participation_id))


def _listed(c, query=""):
    return {i["id"]: i for i in ok(read(c, "/work-items?limit=200" + query))["items"]}


def test_work_item_shows_active_only_while_an_agent_works_on_it_and_it_is_open_or_doing(client):
    agent, run = _agent(client)
    worked = _item(client, "Parser fix")
    idle = _item(client, "Docs")
    part = _participate(client, agent, run, item=worked["id"])

    listed = _listed(client)
    assert listed[worked["id"]]["agentActive"] is True
    assert listed[idle["id"]]["agentActive"] is False
    detail = ok(read(client, f"/work-items/{worked['id']}"))
    assert (detail["status"], detail["agentActive"]) == ("open", True)

    _status(client, worked["id"], "doing")
    assert ok(read(client, f"/work-items/{worked['id']}"))["agentActive"] is True
    # Waiting, blocked and done are the person's word and stay as stored.
    for status in ("waiting", "blocked", "done"):
        _status(client, worked["id"], status)
        assert ok(read(client, f"/work-items/{worked['id']}"))["agentActive"] is False, status
    _status(client, worked["id"], "open")

    # A participation that has not reported for two hours no longer counts.
    _age(client, part["id"], 3)
    assert ok(read(client, f"/work-items/{worked['id']}"))["agentActive"] is False
    _age(client, part["id"], 1)
    assert ok(read(client, f"/work-items/{worked['id']}"))["agentActive"] is True

    # Released participation, then an ended run: neither counts.
    ok(write(client, "POST", f"/participations/{part['id']}/release", {"revision": part["revision"]}, agent))
    assert ok(read(client, f"/work-items/{worked['id']}"))["agentActive"] is False


def test_ended_runs_and_revoked_credentials_do_not_count(client):
    ended_agent, ended_run = _agent(client, "Ended")
    revoked_agent, revoked_run = _agent(client, "Revoked")
    first = _item(client, "First")
    second = _item(client, "Second")
    _participate(client, ended_agent, ended_run, item=first["id"])
    _participate(client, revoked_agent, revoked_run, item=second["id"])
    assert all(_listed(client)[i]["agentActive"] for i in (first["id"], second["id"]))

    ok(write(client, "POST", f"/agent-runs/{ended_run['id']}/end", {"revision": ended_run["revision"]}, ended_agent))
    # A restore revokes every credential without ending runs; such a run's participation must not count.
    with client.app.state.db.transaction() as conn:
        conn.execute("UPDATE credentials SET revoked=1 WHERE id=?", (revoked_agent["id"],))
    listed = _listed(client)
    assert (listed[first["id"]]["agentActive"], listed[second["id"]]["agentActive"]) == (False, False)


def test_status_filter_active_matches_what_shows_active_alongside_stored_states(client):
    agent, run = _agent(client)
    open_worked = _item(client, "Open, worked")
    doing_worked = _item(client, "Doing, worked")
    done_worked = _item(client, "Done, worked")
    doing_idle = _item(client, "Doing, idle")
    _status(client, doing_worked["id"], "doing")
    _status(client, done_worked["id"], "done")
    _status(client, doing_idle["id"], "doing")
    for item in (open_worked, doing_worked, done_worked):
        _participate(client, agent, run, item=item["id"])

    assert set(_listed(client, "&status=active")) == {open_worked["id"], doing_worked["id"]}
    # States combine: any of them matches, so Doing adds the doing work an agent is not on.
    assert set(_listed(client, "&status=active&status=doing")) == {open_worked["id"], doing_worked["id"], doing_idle["id"]}
    # Doing alone still matches the stored status, as the row's status menu shows it.
    assert set(_listed(client, "&status=doing")) == {doing_worked["id"], doing_idle["id"]}
    assert ok(read(client, "/work-items?status=active"))["total"] == 2
    # With nobody at work, active matches nothing.
    with client.app.state.db.transaction() as conn:
        conn.execute("UPDATE participations SET released_at=?", (datetime.now(UTC).isoformat(),))
    assert _listed(client, "&status=active") == {}
    assert read(client, "/work-items?status=busy").status_code == 422


def test_steps_show_active_from_their_own_work_their_work_items_and_the_steps_under_them(client):
    phase, direct, via_item, blocked_leaf, done_leaf, quiet = (str(uuid.uuid4()) for _ in range(6))
    other_phase, blocked_kid, active_kid = (str(uuid.uuid4()) for _ in range(3))
    road = ok(write(client, "POST", "/roadmaps", {"title": "Plan", "overview": "", "steps": [
        {"id": phase, "title": "Phase", "position": 0},
        {"id": direct, "title": "Direct", "parentId": phase, "position": 1},
        {"id": via_item, "title": "Via item", "parentId": phase, "position": 2},
        {"id": done_leaf, "title": "Done leaf", "parentId": phase, "position": 3},
        {"id": quiet, "title": "Quiet", "position": 4},
        {"id": other_phase, "title": "Blocked phase", "position": 5},
        {"id": blocked_kid, "title": "Blocked kid", "parentId": other_phase, "position": 6},
        {"id": active_kid, "title": "Active kid", "parentId": other_phase, "position": 7},
        {"id": blocked_leaf, "title": "Blocked leaf", "position": 8},
    ]}))
    _progress(client, done_leaf, "done")
    _progress(client, blocked_kid, "blocked")
    _progress(client, blocked_leaf, "blocked")
    agent, run = _agent(client)
    item = _item(client, "Item on a step", stepId=via_item)
    finished = _item(client, "Finished item on a step", stepId=quiet)
    _status(client, finished["id"], "done")
    _participate(client, agent, run, step=direct)
    _participate(client, agent, run, item=item["id"])
    _participate(client, agent, run, item=finished["id"])
    _participate(client, agent, run, step=done_leaf)
    _participate(client, agent, run, step=active_kid)
    _participate(client, agent, run, step=blocked_leaf)

    detail = ok(read(client, f"/roadmaps/{road['id']}"))
    shown = {s["id"]: s["agentActive"] for s in detail["steps"]}
    assert shown == {
        phase: True,         # a step under it shows Active
        direct: True,        # its own participation
        via_item: True,      # a work item on it shows Active
        done_leaf: False,    # done stays done
        quiet: False,        # its only work item is done, so it does not show Active
        other_phase: False,  # derived Blocked from its blocked child
        blocked_kid: False,
        active_kid: True,
        blocked_leaf: False,
    }
    assert {w["id"]: w["agentActive"] for w in detail["workItems"]} == {item["id"]: True, finished["id"]: False}
    # The step's own read says the same.
    assert ok(read(client, f"/steps/{phase}"))["agentActive"] is True
    assert ok(read(client, f"/steps/{quiet}"))["agentActive"] is False

    # Retiring a step takes it out of the plan: it never shows Active, and neither does its parent through it.
    current = ok(read(client, f"/roadmaps/{road['id']}"))
    kept = [{k: s[k] for k in ("id", "title", "description", "parentId", "position", "outcome", "workClass", "repositoryIds")}
            for s in current["steps"] if s["id"] != active_kid]
    ok(write(client, "POST", f"/roadmaps/{road['id']}/revisions",
             {"revision": current["revision"], "title": "Plan", "overview": "", "steps": kept}))
    after = {s["id"]: s for s in ok(read(client, f"/roadmaps/{road['id']}"))["steps"]}
    assert after[active_kid]["retired"] is True and after[active_kid]["agentActive"] is False
    assert ok(read(client, f"/steps/{active_kid}"))["agentActive"] is False


def test_active_is_never_stored_in_history_or_saved_versions(client):
    step = str(uuid.uuid4())
    road = ok(write(client, "POST", "/roadmaps", {"title": "Plan", "overview": "", "steps": [
        {"id": step, "title": "Leaf", "position": 0}]}))
    agent, run = _agent(client)
    item = _item(client, "Item", stepId=step)
    _participate(client, agent, run, step=step)
    _participate(client, agent, run, item=item["id"])
    assert ok(read(client, f"/roadmaps/{road['id']}"))["steps"][0]["agentActive"] is True
    # A save after work started records the plan as stored, without the calculated status.
    _progress(client, step, "doing")
    current = ok(read(client, f"/roadmaps/{road['id']}"))
    ok(write(client, "POST", f"/roadmaps/{road['id']}/revisions", {
        "revision": current["revision"], "title": "Plan", "overview": "changed",
        "steps": [{"id": step, "title": "Leaf", "position": 0}]}))
    _status(client, item["id"], "doing")
    with client.app.state.db.read() as conn:
        snapshots = [r[0] for r in conn.execute("SELECT snapshot FROM roadmap_versions")]
        events = [r[0] + r[1] for r in conn.execute("SELECT before_json,after_json FROM events")]
    assert snapshots and all("agentActive" not in json.loads(s)["steps"][0] for s in snapshots)
    assert not any("agentActive" in e for e in events)


def test_archived_work_never_shows_active(client):
    agent, run = _agent(client)
    item = _item(client, "Put away", day="2026-11-20", worked=True)
    _participate(client, agent, run, item=item["id"])
    # Archiving releases participation and Worklog refuses new participation on archived work, so only
    # older data holds an archived item with a fresh participation; it stays off the plan.
    with client.app.state.db.transaction() as conn:
        conn.execute("UPDATE work_items SET archived=1 WHERE id=?", (item["id"],))
    assert ok(read(client, f"/work-items/{item['id']}"))["agentActive"] is False
    assert _listed(client, "&archived=true")[item["id"]]["agentActive"] is False
    assert _listed(client, "&archived=true&status=active") == {}
    entries = ok(read(client, "/days/2026-11-20"))["entries"]
    assert [e["item"]["agentActive"] for e in entries] == [False]


def test_a_days_entries_carry_the_calculated_status(client):
    agent, run = _agent(client)
    worked = _item(client, "Planned and worked", day="2026-11-20")
    idle = _item(client, "Planned", day="2026-11-20")
    _participate(client, agent, run, item=worked["id"])
    entries = ok(read(client, "/days/2026-11-20"))["entries"]
    assert {e["itemId"]: e["item"]["agentActive"] for e in entries} == {worked["id"]: True, idle["id"]: False}
