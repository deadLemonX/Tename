"""SDK session surface — send(), stream events, handle lifecycle.

`AsyncSessionHandle.send()` is the core primitive: emit the user
message, launch the harness in the background, and yield every new
event as it lands. The sync `SessionHandle` mirrors it via the
background event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from tename.sessions.models import Event, EventType, Session, SessionStatus

if TYPE_CHECKING:
    from tename.harness import HarnessRuntime
    from tename.sdk.runtime import BackgroundLoop
    from tename.sessions import SessionService

POLL_INTERVAL_SECONDS = 0.05
"""How often `send()` polls `get_events` for newly-emitted events.

50ms matches "feels like streaming" at human perception speed while
keeping Postgres load trivial for a single active session. Lower
values burn CPU without any UX benefit; higher values feel choppy.
"""


class AsyncSessionHandle:
    """Async handle to a single session.

    Not constructed directly — obtain one via `AsyncTename.sessions.create`
    or `.get`. Exposes `send()` for the classic "user message → stream
    back events" loop and `get_events()` for one-shot reads.
    """

    def __init__(
        self,
        *,
        session: Session,
        service: SessionService,
        harness: HarnessRuntime,
    ) -> None:
        self._session = session
        self._service = service
        self._harness = harness

    @property
    def id(self) -> UUID:
        return self._session.id

    @property
    def agent_id(self) -> UUID:
        return self._session.agent_id

    @property
    def status(self) -> SessionStatus:
        return self._session.status

    async def send(self, content: str) -> AsyncIterator[Event]:
        """Emit a user message, run the harness, stream events as they land.

        Yields every event with `sequence > last_seen`. Exits when the
        session reaches a terminal state (harness calls `mark_complete`)
        OR when the harness task raises.
        """
        # Establish the last-seen pointer BEFORE emitting the user message
        # so the user's own event streams back too — matches what Anthropic
        # and OpenAI SDKs do with their SSE streams.
        last = await self._service.get_events(self._session.id)
        last_sequence = last[-1].sequence if last else 0

        await self._service.emit_event(
            self._session.id,
            event_id=uuid4(),
            event_type=EventType.USER_MESSAGE,
            payload={"content": content},
        )

        harness_task = asyncio.create_task(self._harness.run_session(self._session.id))
        try:
            async for event in self._stream_events(last_sequence, harness_task):
                yield event
        finally:
            if not harness_task.done():
                harness_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await harness_task
            # Surface a harness error to the caller rather than swallowing it.
            if harness_task.done() and harness_task.exception() is not None:
                raise harness_task.exception()  # type: ignore[misc]

    async def _stream_events(
        self,
        last_sequence: int,
        harness_task: asyncio.Task[None],
    ) -> AsyncIterator[Event]:
        while True:
            events = await self._service.get_events(self._session.id, start=last_sequence + 1)
            for event in events:
                yield event
                last_sequence = event.sequence

            if harness_task.done():
                # Drain any events emitted between the last fetch and
                # harness exit, then stop.
                trailing = await self._service.get_events(self._session.id, start=last_sequence + 1)
                for event in trailing:
                    yield event
                return

            try:
                await asyncio.wait_for(asyncio.shield(harness_task), timeout=POLL_INTERVAL_SECONDS)
            except TimeoutError:
                continue

    async def get_events(
        self,
        *,
        start: int | None = None,
        end: int | None = None,
        types: list[EventType] | None = None,
        limit: int = 1000,
    ) -> list[Event]:
        return await self._service.get_events(
            self._session.id, start=start, end=end, types=types, limit=limit
        )

    async def complete(self) -> None:
        await self._service.mark_complete(self._session.id)


class SessionHandle:
    """Sync mirror of `AsyncSessionHandle`.

    Internally delegates to the async version on the background loop.
    Construction and lifecycle are managed by the top-level `Tename`
    client; instances are cheap and hold no state beyond the inner
    handle and the loop reference.
    """

    def __init__(
        self,
        *,
        async_handle: AsyncSessionHandle,
        loop: BackgroundLoop,
    ) -> None:
        self._handle = async_handle
        self._loop = loop

    @property
    def id(self) -> UUID:
        return self._handle.id

    @property
    def agent_id(self) -> UUID:
        return self._handle.agent_id

    @property
    def status(self) -> SessionStatus:
        return self._handle.status

    def send(self, content: str) -> Iterator[Event]:
        """Sync streaming send. Yields events as the harness emits them.

        Drains a thread-safe `queue.Queue` populated by a coroutine on
        the background loop. The coroutine stops when the async
        iterator terminates; we then raise whatever terminal sentinel
        arrived so exceptions from the harness surface to the caller.
        """
        import queue

        sentinel = object()
        q: queue.Queue[object] = queue.Queue()

        async def _drive() -> None:
            try:
                async for event in self._handle.send(content):
                    q.put(event)
            except BaseException as exc:
                q.put(exc)
            finally:
                q.put(sentinel)

        # Fire-and-forget on the background loop; termination comes via the
        # sentinel arriving in the queue.
        self._loop.submit(_drive())

        while True:
            item = q.get()
            if item is sentinel:
                return
            if isinstance(item, BaseException):
                raise item
            assert isinstance(item, Event)
            yield item

    def get_events(
        self,
        *,
        start: int | None = None,
        end: int | None = None,
        types: list[EventType] | None = None,
        limit: int = 1000,
    ) -> list[Event]:
        return self._loop.run(
            self._handle.get_events(start=start, end=end, types=types, limit=limit)
        )

    def complete(self) -> None:
        self._loop.run(self._handle.complete())


__all__ = ["AsyncSessionHandle", "SessionHandle"]
