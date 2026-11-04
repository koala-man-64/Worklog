"""A stopping server cuts any request still open after the grace, so stop can rely on an exit."""

import asyncio
import http.client
import socket
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from worklog import cli


@pytest.fixture
def loopback():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
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


def test_a_request_that_never_ends_is_cut_after_the_grace(loopback):
    app = FastAPI()

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
