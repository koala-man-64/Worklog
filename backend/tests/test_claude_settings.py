import json
import subprocess

from worklog.claude_settings import ManagedClaudeSettings


def test_cleanup_preserves_newer_lease_and_user_edited_project_settings(tmp_path):
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    managed = ManagedClaudeSettings(tmp_path)
    managed.prepare(str(checkout), "managed-lease", {"requestedModel": "sonnet", "reasoningEffort": "low"})

    settings = checkout / ".claude" / "settings.local.json"
    ignore = settings.parent / ".gitignore"
    had_local_ignore = ignore.is_file()
    managed.clear(checkout, "different-lease")
    assert settings.exists()

    contents = json.loads(settings.read_text(encoding="utf-8"))
    contents["model"] = "opus"
    settings.write_text(json.dumps(contents), encoding="utf-8")
    managed.clear(checkout, "managed-lease")
    assert json.loads(settings.read_text(encoding="utf-8")) == contents
    assert ignore.is_file() == had_local_ignore
