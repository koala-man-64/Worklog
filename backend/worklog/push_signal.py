"""Wake connected push receivers when a Worklog transaction commits."""

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager


class CommitSignal:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: dict[asyncio.Event, asyncio.AbstractEventLoop] = {}

    @contextmanager
    def listen(self) -> Iterator[asyncio.Event]:
        signal = asyncio.Event()
        loop = asyncio.get_running_loop()
        with self._lock:
            self._listeners[signal] = loop
        try:
            yield signal
        finally:
            with self._lock:
                del self._listeners[signal]

    def notify(self) -> None:
        # Commits can run in FastAPI's thread pool or in the local service's maintenance thread.
        with self._lock:
            for signal, loop in self._listeners.items():
                try:
                    loop.call_soon_threadsafe(signal.set)
                except RuntimeError:
                    # The receiver's event loop can close concurrently with this committed write.
                    continue
