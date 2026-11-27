from __future__ import annotations

import os
import time

import pytest
from worklog.windows_file_time import change_time_ns


@pytest.mark.skipif(os.name != "nt", reason="Windows FILE_BASIC_INFO is Windows-only")
def test_change_time_covers_file_directory_and_save_with_restored_mtime(tmp_path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("before\n", encoding="utf-8")
    before_stat = file_path.stat()

    before_change = change_time_ns(file_path)
    directory_change = change_time_ns(tmp_path)

    time.sleep(0.02)
    file_path.write_text("after!\n", encoding="utf-8")
    os.utime(file_path, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
    after_change = change_time_ns(file_path)

    assert before_change is not None and before_change > 0
    assert directory_change is not None and directory_change > 0
    assert file_path.stat().st_mtime_ns == before_stat.st_mtime_ns
    assert after_change is not None and after_change > before_change
