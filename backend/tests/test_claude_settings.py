import json
import subprocess

import pytest
from worklog.claude_settings import ManagedClaudeSettings
from worklog.common import AppError


def _checkout(tmp_path):
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    return checkout


def test_cleanup_preserves_newer_lease_and_rescues_edited_project_settings(tmp_path):
    checkout = _checkout(tmp_path)
    managed = ManagedClaudeSettings(tmp_path)
    managed.prepare(str(checkout), "managed-lease", {"requestedModel": "sonnet", "reasoningEffort": "low"})

    settings = checkout / ".claude" / "settings.local.json"
    ignore = settings.parent / ".gitignore"  # absent when a global ignore rule already covers the file
    managed.clear(checkout, "different-lease")
    assert settings.exists()

    # Claude Code adds permission rules to the file; the next lease must not inherit them.
    contents = json.loads(settings.read_text(encoding="utf-8"))
    contents["permissions"] = {"allow": ["Bash(npm test:*)"]}
    settings.write_text(json.dumps(contents), encoding="utf-8")
    managed.clear(checkout, "managed-lease")
    assert not settings.exists()
    assert not ignore.exists()
    assert json.loads(managed.rescued_path("managed-lease").read_text(encoding="utf-8")) == contents

    # The record went with the files, so the next lease starts clean.
    managed.prepare(str(checkout), "next-lease", {"requestedModel": "opus"})
    assert json.loads(settings.read_text(encoding="utf-8"))["model"] == "opus"


def test_cleanup_leaves_settings_the_repository_tracks(tmp_path):
    checkout = _checkout(tmp_path)
    managed = ManagedClaudeSettings(tmp_path)
    managed.prepare(str(checkout), "managed-lease", {"reasoningEffort": "high"})
    settings = checkout / ".claude" / "settings.local.json"
    subprocess.run(["git", "-C", str(checkout), "add", "-f", ".claude/settings.local.json"], check=True)
    settings.write_text('{"env": {"CLAUDE_CODE_EFFORT_LEVEL": "max"}}\n', encoding="utf-8")

    managed.clear(checkout, "managed-lease")
    assert settings.read_text(encoding="utf-8") == '{"env": {"CLAUDE_CODE_EFFORT_LEVEL": "max"}}\n'
    assert not managed.rescued_path("managed-lease").exists()
    assert not list(managed.directory.glob("*.json"))


def test_a_lease_replaces_its_own_deleted_settings_but_not_another_leases(tmp_path):
    checkout = _checkout(tmp_path)
    managed = ManagedClaudeSettings(tmp_path)
    managed.prepare(str(checkout), "managed-lease", {"requestedModel": "sonnet"})
    settings = checkout / ".claude" / "settings.local.json"

    settings.unlink()
    managed.prepare(str(checkout), "managed-lease", {"requestedModel": "opus", "reasoningEffort": "xhigh"})
    written = json.loads(settings.read_text(encoding="utf-8"))
    assert written["model"] == "opus"
    assert written["env"]["CLAUDE_CODE_EFFORT_LEVEL"] == "xhigh"

    settings.unlink()
    with pytest.raises(AppError) as refused:
        managed.prepare(str(checkout), "other-lease", {"requestedModel": "haiku"})
    assert (refused.value.status, refused.value.code) == (409, "claude_settings")
    assert not settings.exists()


def test_a_slot_with_other_settings_refuses_without_pointing_to_a_managed_checkout(tmp_path):
    checkout = _checkout(tmp_path)
    managed = ManagedClaudeSettings(tmp_path)
    managed.prepare(str(checkout), "managed-lease", {"requestedModel": "sonnet"})

    with pytest.raises(AppError) as refused:
        managed.prepare(str(checkout), "managed-lease", {"requestedModel": "opus"})
    assert (refused.value.status, refused.value.code) == (422, "claude_settings")
    assert refused.value.fields == {"model": "project_settings_mismatch"}
    assert "Worklog slot" in refused.value.message
    assert "managed checkout" not in refused.value.message
