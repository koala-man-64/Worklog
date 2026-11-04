"""Every test starts from the same environment, whatever the shell running pytest carries.

A shell set up beside a running Worklog can hold WORKLOG_PORT, WORKLOG_DEV, an agent
token, or a WORKLOG_DATA_DIR that names a real data directory. Each test starts with
none of them, and with WORKLOG_DATA_DIR pointing at a temporary folder of its own, so
code that falls back to the default data directory never reaches real data. A test that
needs one of these variables sets it itself with monkeypatch.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    for name in [key for key in os.environ if key.upper().startswith("WORKLOG_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("WORKLOG_DATA_DIR", str(tmp_path_factory.mktemp("default-data-dir")))
