"""Fakes and helpers for Model Router tests.

The AnthropicProvider talks to `AsyncAnthropic` by calling
`client.messages.stream(**kwargs)` which returns an
`AsyncMessageStreamManager`. Entering the manager opens the HTTP stream
and returns an `AsyncMessageStream` whose `__aiter__` yields raw SSE events.

Real network is never wanted in unit tests. These fakes expose the same
shape so `AnthropicProvider` can be exercised against scripted event
sequences and scripted startup failures.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any


class FakeStream:
    """Stand-in for `AsyncMessageStream`.

    Iterating yields a caller-supplied list of events. A mid-stream exception
    can be scheduled by passing an `mid_stream_exc` that will be raised after
    `raise_after_n` events have been yielded.
    """

    def __init__(
        self,
        events: Iterable[Any],
        *,
        mid_stream_exc: BaseException | None = None,
        raise_after_n: int = 0,
    ) -> None:
        self._events = list(events)
        self._mid_stream_exc = mid_stream_exc
        self._raise_after_n = raise_after_n

    def __aiter__(self) -> FakeStream:
        self._i = 0
        return self

    async def __anext__(self) -> Any:
        if self._mid_stream_exc is not None and self._i >= self._raise_after_n:
            raise self._mid_stream_exc
        if self._i >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._i]
        self._i += 1
        return event


class FakeStreamManager:
    """Async context manager mirroring `AsyncMessageStreamManager`.

    Supports a startup exception (raised on `__aenter__`) so tests can drive
    retry behavior without real HTTP.
    """

    def __init__(
        self,
        stream: FakeStream | None,
        *,
        open_exc: BaseException | None = None,
    ) -> None:
        self._stream = stream
        self._open_exc = open_exc
        self.exited = False

    async def __aenter__(self) -> FakeStream:
        if self._open_exc is not None:
            raise self._open_exc
        assert self._stream is not None
        return self._stream

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        self.exited = True


class FakeMessages:
    """Stand-in for `client.messages`. Captures the last `stream(**kwargs)` call."""

    def __init__(self, managers: list[FakeStreamManager]) -> None:
        self._managers = managers
        self.calls: list[dict[str, Any]] = []
        self._idx = 0

    def stream(self, **kwargs: Any) -> FakeStreamManager:
        self.calls.append(kwargs)
        mgr = self._managers[min(self._idx, len(self._managers) - 1)]
        self._idx += 1
        return mgr


class FakeAnthropic:
    def __init__(self, managers: list[FakeStreamManager]) -> None:
        self.messages = FakeMessages(managers)


# ---- Event constructors matching the Anthropic SDK shape -------------------
#
# The AnthropicProvider reads `event.type` and dispatches. For unit tests we
# don't need real Pydantic instances; SimpleNamespace objects with the right
# attribute structure are equivalent for our purposes.


def message_start_event(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> Any:
    return SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
            )
        ),
    )


def content_block_start_text(*, index: int) -> Any:
    return SimpleNamespace(
        type="content_block_start",
        index=index,
        content_block=SimpleNamespace(type="text", text=""),
    )


def content_block_start_tool_use(*, index: int, tool_id: str, name: str) -> Any:
    return SimpleNamespace(
        type="content_block_start",
        index=index,
        content_block=SimpleNamespace(
            type="tool_use",
            id=tool_id,
            name=name,
            input={},
        ),
    )


def content_block_delta_text(*, index: int, text: str) -> Any:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def content_block_delta_input_json(*, index: int, partial_json: str) -> Any:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="input_json_delta", partial_json=partial_json),
    )


def content_block_stop(*, index: int) -> Any:
    return SimpleNamespace(type="content_block_stop", index=index)


def message_delta(
    *,
    output_tokens: int | None = None,
    input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
) -> Any:
    return SimpleNamespace(
        type="message_delta",
        usage=SimpleNamespace(
            output_tokens=output_tokens,
            input_tokens=input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        ),
        delta=SimpleNamespace(stop_reason="end_turn"),
    )


def message_stop() -> Any:
    return SimpleNamespace(type="message_stop")
