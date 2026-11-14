"""Schema v31 gives each roadmap step an outcome: what the step's work produces, the goal it is done
for. The upgrade adds the column, empty for every step, and changes no other record; the documented
way back gives v30's exact shape.

The person sets a step's outcome with its title and description (PATCH /steps/{id}), an agent proposes
it with step.update, and a structure save (roadmap.revise) writes it with the rest of the step. A save
or a restore that names no outcome for a step keeps the one stored, so a proposal drafted before
steps had outcomes, a client that doesn't know them, or a version saved before v31 never wipes one."""

import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import pytest
import sealed
from test_sealed_schemas import contents
from test_step_update import approve, change, client, ok, read, refused, submit, write  # noqa: F401
from worklog import migrations, recovery
from worklog.db import Database

# docs/UPGRADING.md: from version 31 to version 30, with Worklog stopped and after a manual backup.
WAY_BACK = """\
ALTER TABLE steps DROP COLUMN outcome;
PRAGMA user_version=30;
"""


def to_v30(path):
    """Take a current database back to v30 the documented way."""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(WAY_BACK)


@pytest.fixture
def plan(client):  # noqa: F811
    """A roadmap of two steps, A and B, with B under A, and an agent."""
    ids = {"A": str(uuid.uuid4()), "B": str(uuid.uuid4())}
    roadmap = ok(write(client, "POST", "/roadmaps", {"title": "Plan", "steps": [
        {"id": ids["A"], "title": "A", "description": "About A", "outcome": "A shipped parser"},
        {"id": ids["B"], "title": "B", "description": "About B", "parentId": ids["A"]}]}))
    agent = ok(write(client, "POST", "/credentials", {"label": "Planner"}))
    return {"id": roadmap["id"], "ids": ids, "agent": agent}


def steps(client, plan):  # noqa: F811
    return {s["title"]: s for s in ok(read(client, f"/roadmaps/{plan['id']}"))["steps"]}


def test_a_new_step_takes_its_outcome_or_none(client, plan):  # noqa: F811
    current = steps(client, plan)
    assert (current["A"]["outcome"], current["B"]["outcome"]) == ("A shipped parser", "")
    assert ok(read(client, f"/steps/{plan['ids']['A']}"))["outcome"] == "A shipped parser"


def test_the_person_sets_an_outcome_alone_and_it_is_found_by_search(client, plan):  # noqa: F811
    sid = plan["ids"]["B"]
    result = ok(write(client, "PATCH", f"/steps/{sid}", {"revision": 1, "outcome": "Releases cut by quillback"}))
    assert (result["title"], result["description"], result["outcome"], result["revision"]) == (
        "B", "About B", "Releases cut by quillback", 2)
    history = ok(read(client, f"/steps/{sid}/history"))["items"]
    assert history[0]["kind"] == "step.update"
    found = ok(read(client, "/search?q=quillback"))["items"]
    assert [hit["id"] for hit in found] == [sid]
    # The same outcome again changes nothing and records nothing.
    again = ok(write(client, "PATCH", f"/steps/{sid}", {"revision": 2, "outcome": "Releases cut by quillback"}))
    assert again["revision"] == 2


@pytest.mark.parametrize("outcome", [5, "x" * 100_001])
def test_an_outcome_that_is_not_text_or_too_long_is_refused(client, plan, outcome):  # noqa: F811
    refused(write(client, "PATCH", f"/steps/{plan['ids']['B']}", {"revision": 1, "outcome": outcome}), 422, "validation")
    assert steps(client, plan)["B"]["outcome"] == ""


def test_an_agent_proposes_an_outcome_and_approval_writes_it(client, plan):  # noqa: F811
    sid = plan["ids"]["B"]
    proposal = submit(client, plan["agent"], "Name the goal",
                      [change("step.update", id=sid, revision=1, outcome="Every release has notes")])
    assert ok(approve(client, proposal))["status"] == "approved"
    assert steps(client, plan)["B"]["outcome"] == "Every release has notes"


def test_a_save_that_names_no_outcome_keeps_each_one_stored(client, plan):  # noqa: F811
    current = ok(read(client, f"/roadmaps/{plan['id']}"))
    # As a client that doesn't know outcomes saves: a new title for A, a new step C, no outcomes.
    new = str(uuid.uuid4())
    saved = ok(write(client, "POST", f"/roadmaps/{plan['id']}/revisions", {
        "revision": current["revision"], "title": "Plan", "steps": [
            {"id": plan["ids"]["A"], "title": "A again", "description": "About A"},
            {"id": plan["ids"]["B"], "title": "B", "description": "About B", "parentId": plan["ids"]["A"]},
            {"id": new, "title": "C", "description": ""}]}))
    by_title = {s["title"]: s for s in saved["steps"]}
    assert (by_title["A again"]["outcome"], by_title["B"]["outcome"], by_title["C"]["outcome"]) == (
        "A shipped parser", "", "")
    # B, untouched, keeps its revision: an outcome left out is no change.
    assert by_title["B"]["revision"] == 1


def test_a_save_that_changes_only_an_outcome_writes_that_step_and_a_version(client, plan):  # noqa: F811
    current = ok(read(client, f"/roadmaps/{plan['id']}"))
    saved = ok(write(client, "POST", f"/roadmaps/{plan['id']}/revisions", {
        "revision": current["revision"], "title": "Plan", "steps": [
            {"id": plan["ids"]["A"], "title": "A", "description": "About A", "outcome": "A shipped parser"},
            {"id": plan["ids"]["B"], "title": "B", "description": "About B", "parentId": plan["ids"]["A"],
             "outcome": "B's goal"}]}))
    by_title = {s["title"]: s for s in saved["steps"]}
    assert (by_title["A"]["revision"], by_title["B"]["revision"], by_title["B"]["outcome"]) == (1, 2, "B's goal")
    assert [v["number"] for v in saved["versions"]] == [1, 2]
    # The version holds the outcome, and restoring version 1 takes B's outcome back to none.
    assert ok(read(client, f"/roadmaps/{plan['id']}/revisions/2"))["steps"][1]["outcome"] == "B's goal"
    restored = ok(write(client, "POST", f"/roadmaps/{plan['id']}/restore", {"revision": saved["revision"], "number": 1}))
    assert {s["title"]: s["outcome"] for s in restored["steps"]} == {"A": "A shipped parser", "B": ""}


def test_a_version_saved_before_outcomes_reads_with_none_and_restores_without_wiping_them(client, plan):  # noqa: F811
    with closing(sqlite3.connect(client.db_path)) as conn:
        (snapshot,), = conn.execute("SELECT snapshot FROM roadmap_versions WHERE roadmap_id=? AND number=1", (plan["id"],))
        old = json.loads(snapshot)
        for step in old["steps"]:
            del step["outcome"]
        old["steps"][1]["title"] = "B before"
        conn.execute("UPDATE roadmap_versions SET snapshot=? WHERE roadmap_id=? AND number=1", (json.dumps(old), plan["id"]))
        conn.commit()
    assert [s["outcome"] for s in ok(read(client, f"/roadmaps/{plan['id']}/revisions/1"))["steps"]] == ["", ""]
    current = ok(read(client, f"/roadmaps/{plan['id']}"))
    restored = ok(write(client, "POST", f"/roadmaps/{plan['id']}/restore", {"revision": current["revision"], "number": 1}))
    assert {s["title"]: s["outcome"] for s in restored["steps"]} == {"A": "A shipped parser", "B before": ""}


def worked(data):
    """A current database with a roadmap whose steps have outcomes, as the step list stores them."""
    from test_chat_label_links import call, session

    client = session(data)  # noqa: F811
    try:
        call(client, "POST", "/roadmaps", {"title": "Parser", "steps": [
            {"title": "Cut the branch", "description": "From main", "outcome": "A branch"}, {"title": "Merge it"}]})
    finally:
        client.__exit__(None, None, None)
    return data / "worklog.sqlite3"


def test_the_upgrade_gives_every_step_no_outcome_and_changes_no_other_record(tmp_path):
    database = worked(tmp_path / "data")
    to_v30(database)
    recovery.validate_database(database)
    _, _, before = contents(database)
    Database(database).initialize()
    version, signature, after = contents(database)
    assert (version, signature) == (migrations.SCHEMA_VERSION, sealed.current())
    # Each step's row is as it was, with an empty outcome after it.
    assert after["steps"] == sorted((row + ("",) for row in before["steps"]), key=repr) and len(before["steps"]) == 2
    assert {table: rows for table, rows in after.items() if table != "steps"} == {
        table: rows for table, rows in before.items() if table != "steps"}
    snapshot, = (tmp_path / "data" / "backups").glob("worklog-pre-migration-*.sqlite3")
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 30


def test_the_way_back_gives_v30s_exact_shape_and_upgrades_again(tmp_path):
    database = worked(tmp_path / "data")
    to_v30(database)
    recovery.validate_database(database)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 30
        assert recovery._schema_signature(conn) == sealed.shape(30)
    Database(database).initialize()
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert recovery._schema_signature(conn) == sealed.current()
    # The SQL tested here is the SQL docs/UPGRADING.md gives the person.
    assert "```sql\n" + WAY_BACK + "```" in (Path(__file__).resolve().parents[2] / "docs" / "UPGRADING.md").read_text(encoding="utf-8")
