"""A stopping server ends quota event streams at once and never waits on them, or on slow quota threads."""

import asyncio
import http.client
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from worklog import cli
from worklog.app import create_app
from worklog.quota.capacity import CapacityState
from worklog.quota.config import get_paths as get_quota_paths
from worklog.quota.service import CapacityService, WriterLease


@pytest.fixture
def loopback(monkeypatch):
    """A bound loopback socket, with the app's host check expecting its port."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    monkeypatch.setenv("WORKLOG_PORT", str(sock.getsockname()[1]))
    yield sock
    sock.close()


def serving(app, sock, grace):
    server = cli._server(app, sock.getsockname()[1], grace)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    return server, thread


def first_line(sock, path):
    connection = http.client.HTTPConnection("127.0.0.1", sock.getsockname()[1], timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    assert response.status == 200
    return response, response.readline()


def stop_and_time(server, thread):
    started = time.monotonic()
    # What stop's marker makes the serving process do.
    server.should_exit = True
    thread.join(timeout=10)
    assert not thread.is_alive(), "server did not finish shutting down"
    return time.monotonic() - started


def test_a_stopping_server_ends_open_quota_event_streams_at_once(tmp_path, loopback):
    app = create_app(tmp_path, testing=True)
    # Running quota threads keep a stream open, as they do while Worklog serves.
    app.state.quota.start()
    try:
        # A long grace, so only ending the stream can let the server finish in time.
        server, thread = serving(app, loopback, grace=30)
        response, line = first_line(loopback, "/v1/capacity/events")
        assert line.startswith(b"id: ")
        assert stop_and_time(server, thread) < 3
        response.read()  # The stream has ended rather than being left open.
    finally:
        app.state.quota.close(timeout=2)


def test_a_request_that_never_ends_is_cut_after_the_grace(loopback):
    app = FastAPI()
    app.state.quota = SimpleNamespace(end_streams=lambda: None)

    @app.get("/forever")
    async def forever() -> StreamingResponse:
        async def ticks():
            while True:
                yield b"tick\n"
                await asyncio.sleep(0.1)

        return StreamingResponse(ticks())

    server, thread = serving(app, loopback, grace=1)
    response, line = first_line(loopback, "/forever")
    assert line == b"tick\n"
    assert stop_and_time(server, thread) < 4


def test_a_snapshot_wait_returns_once_told_to_stop(tmp_path):
    state, stop, returned = CapacityState(tmp_path), threading.Event(), []
    waiter = threading.Thread(target=lambda: returned.append(state.wait(state.revision, 15, stop)))
    waiter.start()
    time.sleep(0.2)
    started = time.monotonic()
    stop.set()
    with state.condition:
        state.condition.notify_all()
    waiter.join(timeout=5)
    assert returned and time.monotonic() - started < 1


def test_close_leaves_a_slow_thread_behind_and_keeps_the_writer_lease_until_it_ends(tmp_path):
    paths = get_quota_paths(tmp_path / "quota")
    service = CapacityService(paths, collectors=False).start()
    release = threading.Event()
    service._spawn("slow-collector", lambda: release.wait(10))
    started = time.monotonic()
    service.close(timeout=0.5)
    assert time.monotonic() - started < 1.5
    with pytest.raises(RuntimeError), WriterLease(paths.home):
        pass
    release.set()
    deadline = time.monotonic() + 5
    while True:
        try:
            with WriterLease(paths.home):
                break
        except RuntimeError:
            assert time.monotonic() < deadline, "the writer lease was never released"
            time.sleep(0.05)
