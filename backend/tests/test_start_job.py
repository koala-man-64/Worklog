"""A started server outlives the Windows job of whatever ran start.

A Claude Code session runs its shells' processes in a job and ends them all when the session ends.
A server that stayed in that job ended with it, with no shutdown lines in server.log. Here start runs
in a job that ends its processes when its last handle closes, the test closes it, and the server must
still answer; in a job that forbids leaving it, start says so and the server ends with the job.
"""

import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from worklog import cli

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows job objects")

BACKEND = str(Path(cli.__file__).resolve().parents[1])
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x800
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
# The shell's part: join the named job, run start there, then stay in the job until it ends.
SHELL = r"""
import ctypes, sys
from ctypes import wintypes
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel.OpenJobObjectW.restype = wintypes.HANDLE
kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
job = kernel.OpenJobObjectW(0x0001, False, sys.argv[1])  # JOB_OBJECT_ASSIGN_PROCESS
if not job or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
    sys.exit(f"could not join the job: Windows error {ctypes.get_last_error()}")
kernel.CloseHandle(job)  # the test's handle stays the last, so closing it ends the job
from worklog.cli import main
code = main(sys.argv[2:])
print(f"start exited {code}", flush=True)
sys.stdin.read()
"""

if os.name == "nt":
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]


class Job:
    """A named job that ends every process in it when the test closes it, as a session ends its shells."""

    def __init__(self, allow_breakaway: bool) -> None:
        self.name = f"Local\\worklog-test-{uuid.uuid4().hex}"
        self.handle = kernel.CreateJobObjectW(None, self.name)
        assert self.handle, f"CreateJobObjectW failed: Windows error {ctypes.get_last_error()}"
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | (
            JOB_OBJECT_LIMIT_BREAKAWAY_OK if allow_breakaway else 0)
        assert kernel.SetInformationJobObject(self.handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                              ctypes.byref(limits), ctypes.sizeof(limits)), ctypes.get_last_error()

    def close(self) -> None:
        if self.handle:
            kernel.CloseHandle(self.handle)
            self.handle = None


def unusual_free_port() -> int:
    # An ephemeral port nothing listens on, far from 8877 and the usual test ports.
    with socket.socket() as probe:
        probe.bind((cli.HOST, 0))
        port = probe.getsockname()[1]
    assert port != cli.DEFAULT_PORT
    return port


def started(shell: subprocess.Popen, output: Path) -> dict:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        text = output.read_text(encoding="ascii", errors="replace")
        report, marker, code = text.partition("start exited")
        if marker:
            assert code.strip() == "0", text
            return json.loads(report)
        assert shell.poll() is None, f"the shell ended before start returned: {text}"
        time.sleep(0.1)
    pytest.fail(f"start did not return: {output.read_text(encoding='ascii', errors='replace')}")


def answered(port: int, within: float) -> dict | None:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if health := cli._health(port):
            return health
        time.sleep(0.1)
    return None


def gone(port: int, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not cli._port_busy(port):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("allow_breakaway", [True, False], ids=["job allows breakaway", "job forbids breakaway"])
def test_started_server_outlives_the_job_that_ran_start(tmp_path, allow_breakaway):
    data, port, output = tmp_path / "data", unusual_free_port(), tmp_path / "shell.out"
    environment = {**os.environ, "PYTHONPATH": BACKEND, "WORKLOG_DATA_DIR": str(data), "WORKLOG_PORT": str(port),
                   "WORKLOG_AUTO_BACKUP": "0"}
    environment.pop("WORKLOG_AGENT_TOKEN", None)
    job = Job(allow_breakaway)
    shell = None
    try:
        with output.open("wb") as sink:
            shell = subprocess.Popen(
                [sys.executable, "-c", SHELL, job.name, "--data-dir", str(data), "--port", str(port), "start"],
                stdin=subprocess.PIPE, stdout=sink, stderr=subprocess.STDOUT, env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW)
        report = started(shell, output)
        assert report["running"] is True and report["url"] == cli.local_url(port) and report["reused"] is False

        job.close()
        # The shell waits on its stdin forever; only the job ending can end it.
        shell.wait(timeout=10)
        if allow_breakaway:
            assert "warning" not in report
            health = answered(port, within=10)
            assert health and health["instanceId"] == report["instanceId"], (data / "server.log").read_text(errors="replace")
            assert cli.check(data, port)["pid"] == report["pid"]
            assert cli.stop(data, port) == {"running": False, "stopped": True}
            assert gone(port, within=10)
        else:
            assert report["warning"] == cli.JOB_WARNING
            assert f"worklog start: {cli.JOB_WARNING}" in (data / "server.log").read_text(errors="replace")
            # The warning holds: the server ended with the job.
            assert gone(port, within=10)
            assert cli.check(data, port)["running"] is False
    finally:
        job.close()
        if shell:
            shell.stdin.close()
            try:
                shell.wait(timeout=10)
            except subprocess.TimeoutExpired:
                shell.kill()
        # A server this test left running is stopped through its own identity check, never by port.
        cli.stop(data, port)
