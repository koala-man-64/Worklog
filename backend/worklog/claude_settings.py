"""Project-local Claude settings and ownership of files generated in managed slots."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .common import AppError
from .slot_worktrees import _git_ok, _run_git

SETTINGS = "settings.local.json"
IGNORE = ".gitignore"
_MISMATCH = (
    "Claude's launch selections need matching values in .claude/settings.local.json. "
    "Choose a managed checkout or configure the selected folder first."
)


def _safe_path(path: Path) -> Path:
    if path.resolve() != path:
        raise AppError(422, "claude_settings", "Claude settings cannot use a link outside the checkout path")
    return path


def _settings_path(cwd: str | Path) -> Path:
    folder = _safe_path(Path(cwd).resolve() / ".claude")
    return _safe_path(folder / SETTINGS)


def require_claude_settings(cwd: str, options: dict[str, str]) -> None:
    """Check the documented folder overrides needed for the Desktop continuation."""
    if not options:
        return
    path = _settings_path(cwd)
    try:
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise AppError(422, "claude_settings", _MISMATCH) from exc
    if not isinstance(settings, dict) or not isinstance(settings.get("env", {}), dict):
        raise AppError(422, "claude_settings", _MISMATCH)
    env = settings.get("env", {})
    model = options.get("requestedModel")
    effort = options.get("reasoningEffort")
    if model and (settings.get("model") != model or env.get("ANTHROPIC_MODEL", model) != model):
        raise AppError(422, "claude_settings", _MISMATCH, {"model": "project_settings_mismatch"})
    if effort and env.get("CLAUDE_CODE_EFFORT_LEVEL") != effort:
        raise AppError(422, "claude_settings", _MISMATCH, {"reasoningEffort": "project_settings_mismatch"})


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ignored(cwd: Path, name: str) -> bool:
    result = _run_git(cwd, "check-ignore", "--quiet", "--", f".claude/{name}")
    if result.returncode not in {0, 1}:
        raise AppError(422, "claude_settings", "Could not check Git ignore rules for the Claude settings")
    return result.returncode == 0


class ManagedClaudeSettings:
    """Keep file ownership in Worklog's data directory, outside the checkout."""

    def __init__(self, data_dir: Path):
        self.directory = data_dir / "claude-checkout-settings"

    def _record(self, root: Path) -> Path:
        return self.directory / (_digest(os.path.normcase(str(root)).encode("utf-8")) + ".json")

    def prepare(self, cwd: str, lease_id: str, options: dict[str, str]) -> None:
        root = Path(cwd).resolve()
        path = _settings_path(root)
        if path.exists():
            require_claude_settings(cwd, options)
            return

        env: dict[str, str] = {}
        settings: dict[str, str | dict[str, str]] = {"env": env}
        if model := options.get("requestedModel"):
            settings["model"] = model
            env["ANTHROPIC_MODEL"] = model
        if effort := options.get("reasoningEffort"):
            settings["effortLevel"] = effort
            env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
        files: dict[str, bytes] = {}
        if not _ignored(root, SETTINGS):
            if _safe_path(path.parent / IGNORE).exists():
                raise AppError(422, "claude_settings", "Ignore .claude/settings.local.json in Git, or supply matching settings")
            files[IGNORE] = b"/.gitignore\n/settings.local.json\n"
        files[SETTINGS] = (json.dumps(settings, indent=2) + "\n").encode("utf-8")

        record = self._record(root)
        created: list[Path] = []
        recorded = False
        try:
            self.directory.mkdir(exist_ok=True)
            path.parent.mkdir(exist_ok=True)
            # Save ownership before touching the slot so a restart can recover the files.
            if record.exists():
                raise AppError(409, "claude_settings", "This checkout still has a Worklog Claude settings record")
            temporary = record.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump({"leaseId": lease_id, "files": {name: _digest(data) for name, data in files.items()}}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(record)
            recorded = True
            for name, data in files.items():
                target = _safe_path(path.parent / name)
                with target.open("xb") as stream:
                    created.append(target)
                    stream.write(data)
                if not _ignored(root, name):
                    raise AppError(422, "claude_settings", "Generated Claude settings must be ignored by Git in this checkout")
        except (AppError, OSError) as exc:
            for target in reversed(created):
                target.unlink(missing_ok=True)
            if recorded:
                record.unlink(missing_ok=True)
            if isinstance(exc, AppError):
                raise
            raise AppError(409, "claude_settings", "Could not write Claude settings in this managed checkout") from exc

    def clear(self, cwd: str | Path, lease_id: str | None = None) -> None:
        root = Path(cwd).resolve()
        record = self._record(root)
        if not record.exists():
            return
        try:
            saved = json.loads(record.read_text(encoding="utf-8"))
            names = saved["files"]
            if not isinstance(names, dict) or SETTINGS not in names or names.keys() - {SETTINGS, IGNORE}:
                raise ValueError("Invalid owned files")
            if lease_id is not None and saved["leaseId"] != lease_id:
                return
            if not root.exists():
                record.unlink()
                return
            path = _settings_path(root)
            tracked = set(_git_ok(root, "ls-files", "--", f".claude/{SETTINGS}", f".claude/{IGNORE}").splitlines())
            # A changed or newly tracked settings file belongs to the user, including its ignore rule.
            if path.exists() and (f".claude/{SETTINGS}" in tracked or _digest(path.read_bytes()) != names[SETTINGS]):
                record.unlink()
                return
            for name in (SETTINGS, IGNORE):
                target = _safe_path(path.parent / name)
                if name in names and f".claude/{name}" not in tracked and target.exists():
                    if _digest(target.read_bytes()) == names[name]:
                        target.unlink()
            record.unlink()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise AppError(409, "claude_settings", "Could not clear Worklog's Claude settings before checkout reuse") from exc
