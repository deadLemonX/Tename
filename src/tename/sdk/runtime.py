"""Dedicated background event loop for the sync Tename client.

The Session Service is async-only (psycopg3 async, asyncpg-compatible
SQLAlchemy engine). The public docs show developers writing::

    for event in session.send("hello"):
        ...

— a synchronous iterator. The standard way to bridge these is a
dedicated event loop running on a daemon thread plus
`asyncio.run_coroutine_threadsafe`. That's what this module does. The
`AsyncTename` variant exposes the same surface with `async def`, and
internally shares the same underlying services so async users don't
pay the thread hop.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable


class BackgroundLoop:
    """A daemon thread running a dedicated asyncio event loop.

    Not a public API — only used internally by the sync SDK surface.
    Start once in `Tename.__init__`, stop in `Tename.close()`. The loop
    is a daemon so a forgotten close doesn't hang the interpreter on
    exit, but calling close() is strongly recommended to drain pending
    work and dispose the Session Service engine cleanly.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="tename-sdk-loop", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                # Cancel any still-pending tasks before closing.
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                loop.close()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("BackgroundLoop has not been started")
        return self._loop

    def run[T](self, coro: Awaitable[T]) -> T:
        """Schedule `coro` on the loop thread and block until it returns."""
        loop = self.loop
        future = asyncio.run_coroutine_threadsafe(_awaitable(coro), loop)
        return future.result()

    def submit[T](self, coro: Awaitable[T]) -> asyncio.Future[T]:
        """Schedule `coro` on the loop and return a thread-safe Future wrapper."""
        loop = self.loop
        return asyncio.run_coroutine_threadsafe(_awaitable(coro), loop)  # type: ignore[return-value]

    def stop(self) -> None:
        """Stop the loop and join the thread. Idempotent."""
        if self._loop is None or self._thread is None:
            return
        loop = self._loop
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=5.0)
        self._loop = None
        self._thread = None
        self._ready = threading.Event()


async def _awaitable[T](coro: Awaitable[T]) -> T:
    """Wrap a bare coroutine / awaitable as an awaitable we can schedule."""
    return await coro


__all__ = ["BackgroundLoop"]
