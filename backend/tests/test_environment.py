"""The suite gives the same result whatever the developer's shell carries."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_each_test_starts_without_the_shells_worklog_variables(tmp_path_factory):
    assert sorted(key for key in os.environ if key.upper().startswith("WORKLOG_")) == ["WORKLOG_DATA_DIR"]
    data_dir = Path(os.environ["WORKLOG_DATA_DIR"])
    assert data_dir.is_relative_to(tmp_path_factory.getbasetemp())


def test_a_shell_set_up_for_a_second_instance_still_passes_the_suite():
    # The README asks for WORKLOG_PORT before running e2e beside the everyday instance.
    # Before conftest.py cleared it, that shell failed 221 backend tests on the Host check.
    shell = {**os.environ, "WORKLOG_PORT": "8931", "WORKLOG_DEV": "1", "WORKLOG_AGENT_TOKEN": "not-a-real-token"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "backend/tests/test_api.py::test_authentication_origin_csrf_and_source_exposure",
         "backend/tests/test_api.py::test_stale_edit_and_idempotent_creation"],
        cwd=REPO, env=shell, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
