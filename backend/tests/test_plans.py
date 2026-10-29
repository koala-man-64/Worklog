import pytest
from worklog.common import AppError, uid
from worklog.db import Database
from worklog.plans import PlanService

HUMAN = {"id": "human", "label": "Rudy", "kind": "human"}


@pytest.fixture
def plans(tmp_path):
    db = Database(tmp_path / "worklog.db")
    db.initialize()
    return PlanService(db)


def apply(plans, action, payload):
    with plans.db.transaction() as conn:
        return plans.apply(conn, HUMAN, action, payload)


def test_version_retirement_restore_preserves_identity_and_live_progress(plans):
    parent, leaf = uid(), uid()
    steps = [{"id": parent, "title": "Parent"}, {"id": leaf, "title": "Leaf", "parentId": parent}]
    created = apply(plans, "roadmap.create", {"title": "Plan", "steps": steps})
    assert created["revision"] == 1
    original = plans.version(created["id"], 1)
    progress = apply(plans, "progress.set", {"id": leaf, "revision": 1, "status": "done"})
    current = plans.detail(created["id"])
    assert {s["status"] for s in current["steps"]} == {"done"}
    assert plans.version(created["id"], 1) == original
    with pytest.raises(AppError, match="Parent progress"):
        apply(plans, "progress.set", {"id": parent, "revision": 1, "status": "done"})
    retired = apply(plans, "roadmap.revise", {"id": created["id"], "revision": current["revision"], "title": "Empty", "steps": []})
    assert all(s["retired"] for s in retired["steps"])
    restored = apply(plans, "roadmap.restore", {"id": created["id"], "revision": retired["revision"], "number": 1})
    assert all(not s["retired"] for s in restored["steps"])
    assert next(s for s in restored["steps"] if s["id"] == leaf)["status"] == progress["status"]
    assert len(restored["versions"]) == 3
    assert {e["kind"] for e in plans.history(leaf)["items"]} >= {"step.retire", "step.restore", "progress.set"}


def test_tree_validation_is_atomic(plans):
    a, b = uid(), uid()
    for steps in ([{"id": a, "title": "A", "parentId": b}, {"id": b, "title": "B", "parentId": a}],
                  [{"id": a, "title": "A"}, {"id": a, "title": "Again"}],
                  [{"id": a, "title": "A", "parentId": b}]):
        with pytest.raises(AppError):
            apply(plans, "roadmap.create", {"title": "Invalid", "steps": steps})
    assert plans.list()["total"] == 0


def test_cross_roadmap_step_identity_cannot_be_stolen(plans):
    sid = uid()
    apply(plans, "roadmap.create", {"title": "A", "steps": [{"id": sid, "title": "Owned"}]})
    with pytest.raises(AppError, match="another roadmap"):
        apply(plans, "roadmap.create", {"title": "B", "steps": [{"id": sid, "title": "Stolen"}]})
    assert plans.list()["total"] == 1


def test_hostile_nested_import_selection_and_original_custody(plans):
    data = b'''<html><title>Title</title><body><p>Introduction</p><section><h1>Plan</h1>
    <div><p>First <strong>body</strong></p><section><h2>Nested</h2><p>Nested body</p></section></div>
    <h1>Last</h1><p>Final</p><script>evil()</script><iframe src="https://evil"></iframe>
    <a href="javascript:alert(1)" onclick="evil()">unsafe</a></section></body></html>'''
    preview = plans.preview(data, "../hostile.html", HUMAN)
    assert [s["title"] for s in preview["steps"]] == ["Plan", "Nested", "Last"]
    assert "First **body**" in preview["steps"][0]["description"]
    assert "Nested body" in preview["steps"][1]["description"]
    assert preview["steps"][1]["parentId"] == preview["steps"][0]["id"]
    assert preview["overview"] == "Introduction"
    assert "evil" not in str(preview)
    assert "javascript" not in str(preview)
    assert plans.import_download(preview["id"]) == (data, "hostile.html")
    selected = preview["steps"][-1:]
    plan = plans.import_commit(preview["id"], {"title": "Selected", "steps": selected}, HUMAN)
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["status"] == "open"
    with pytest.raises(AppError, match="already"):
        plans.import_commit(preview["id"], {"title": "Duplicate"}, HUMAN)


def test_import_attach_does_not_merge_and_size_limit(plans):
    plan = apply(plans, "roadmap.create", {"title": "Existing"})
    preview = plans.preview(b"<h1>Incoming</h1><p>Text</p>", "source.html", HUMAN)
    attached = plans.import_commit(preview["id"], {"roadmapId": plan["id"], "revision": 1, "steps": preview["steps"]}, HUMAN)
    assert attached["title"] == "Existing" and attached["steps"] == []
    with pytest.raises(AppError):
        plans.preview(b"x" * 5_000_001, "large.html", HUMAN)


def test_import_preserves_loose_text_nested_wrappers_and_removes_obfuscated_urls(plans):
    preview = plans.preview(b'''<div>Before <em>heading</em><h1>First</h1>
    loose <strong>body</strong><section><h2>Second</h2><div>Deep body
    <a href="java&#10;script:alert(1)">safe label</a></div></section>
    <h1>Third</h1>Tail</div>''', "nested.html", HUMAN)
    assert "Before *heading*" in preview["overview"]
    assert "loose **body**" in preview["steps"][0]["description"]
    assert "Deep body" in preview["steps"][1]["description"]
    assert "safe label" in preview["steps"][1]["description"]
    assert "script:" not in str(preview)
    assert preview["steps"][2]["description"] == "Tail"


def test_pathological_nesting_is_rejected_with_validation_error(plans):
    with pytest.raises(AppError) as error:
        plans.preview(b"<div>" * 110 + b"body" + b"</div>" * 110, "nested.html", HUMAN)
    assert error.value.status == 422


def test_new_steps_ignore_caller_progress_and_roadmap_conflicts(plans):
    created = apply(plans, "roadmap.create", {"title": "Plan", "steps": [{"title": "New", "status": "done"}]})
    assert created["steps"][0]["status"] == "open"
    apply(plans, "progress.set", {"id": created["steps"][0]["id"], "revision": 1, "status": "doing"})
    with pytest.raises(AppError) as error:
        apply(plans, "roadmap.revise", {"id": created["id"], "revision": 1, "title": "Stale", "steps": []})
    assert error.value.status == 409


def test_import_commit_supplied_transaction_rolls_back_source_and_association(plans):
    preview = plans.preview(b"<h1>Atomic</h1><p>Body</p>", "atomic.html", HUMAN)
    payload = {"title": preview["title"], "steps": preview["steps"]}
    with pytest.raises(RuntimeError, match="index failure"):
        with plans.db.transaction() as conn:
            plans.import_commit(preview["id"], payload, HUMAN, conn=conn)
            raise RuntimeError("index failure")
    assert plans.list()["total"] == 0
    with plans.db.read() as conn:
        assert conn.execute("SELECT roadmap_id FROM plan_imports WHERE id=?", (preview["id"],)).fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='roadmap.create'").fetchone()[0] == 0
    result = plans.import_commit(preview["id"], payload, HUMAN)
    assert result["title"] == "Atomic"


def test_import_href_cannot_smuggle_an_extra_markdown_link(plans):
    data = b'<h1>Plan</h1><p><a href="https://x/a) [c](javascript:alert(1)">text</a></p>'
    preview = plans.preview(data, "smuggle.html", HUMAN)
    assert "](javascript:" not in str(preview)
    description = preview["steps"][0]["description"]
    assert description.count("](") == 1


def test_import_protocol_relative_and_whitespace_urls_are_normalized(plans):
    data = (b'<h1>Plan</h1><p><a href="//host/path">a</a> '
            b'<a href="https://x/a b&lt;c&gt;">b</a></p>')
    preview = plans.preview(data, "relative.html", HUMAN)
    description = preview["steps"][0]["description"]
    assert "(https://host/path)" in description
    assert "(https://x/a%20b%3Cc%3E)" in description
