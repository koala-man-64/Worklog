"""Where the person's data lives (audit finding O2, roadmap step 1.5).

The data folder is --data-dir, else the process's WORKLOG_DATA_DIR, else on Windows the user-level
WORKLOG_DATA_DIR, else the default folder. The user-level variable is read for a process that lacks
it, as a shell started before the variable was set, or by a program that did not pass it on, does:
such a shell used to land on the default folder, and a start there while the real server was down
opened a new, empty database on the person's port. A folder from the user-level variable or the
default is one nobody named, and `start` refuses to create a database in it while another candidate
already holds one. This module imports nothing heavy, so every CLI command can use it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VARIABLE = "WORKLOG_DATA_DIR"
DATABASE = "worklog.sqlite3"


@dataclass(frozen=True)
class DataFolder:
    path: Path
    # Named by --data-dir or the process's own WORKLOG_DATA_DIR: such a folder may always be created.
    named: bool
    # For a folder nobody named, every folder the person's data may be in, this one included.
    candidates: tuple[Path, ...]


def user_environment_variable(name: str) -> str | None:
    """The user-level environment variable `name` as Windows keeps it, or None elsewhere or unset."""
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return winreg.ExpandEnvironmentStrings(value) if kind == winreg.REG_EXPAND_SZ else value


def default_folder() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise RuntimeError("LOCALAPPDATA is unavailable; supply --data-dir")
        return Path(base) / "Worklog"
    return Path.home() / ".local" / "share" / "Worklog"


def _resolved(value: str) -> Path:
    return Path(value).expanduser().resolve()


def locate(value: str | None = None) -> DataFolder:
    """The data folder for --data-dir `value`, or for this process's environment."""
    given = value or os.environ.get(VARIABLE)
    if given:
        return DataFolder(_resolved(given), True, ())
    default = default_folder()
    user = user_environment_variable(VARIABLE)
    if user:
        path = _resolved(user)
        return DataFolder(path, False, tuple(dict.fromkeys((path, default))))
    return DataFolder(default, False, (default,))


def refuse_unnamed_empty(folder: DataFolder) -> None:
    """Refuse a folder nobody named that holds no database while another candidate holds one."""
    if folder.named or (folder.path / DATABASE).exists():
        return
    held = [candidate for candidate in folder.candidates
            if candidate != folder.path and (candidate / DATABASE).is_file()]
    if held:
        raise RuntimeError(
            f"{folder.path} holds no Worklog database, and nobody named it (it came from the user-level "
            f"{VARIABLE} or the default), while {held[0]} holds one. Nothing was created. Name the folder "
            f"you mean with --data-dir or {VARIABLE}: that folder, even a new one, is used as given.")
