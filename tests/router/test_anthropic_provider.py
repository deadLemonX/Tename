"""Unit tests for AnthropicProvider.

These tests drive the provider through scripted event sequences without any
real HTTP. The conftest fakes expose the same async-context-manager + async
iterator shape the real Anthropic SDK presents.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from tename.router.providers.anthropic import AnthropicProvider
from tename.router.types import (
    ContentBlock,
    Message,
    ModelConfig,
    RouterProfile,
    Sampling,
    ToolDef,
)

from .conftest import (
    FakeAnthropic,
    FakeStream,
    FakeStreamManager,
    content_block_delta_input_json,
    content_block_delta_text,
    content_block_start_text,
    content_block_start_tool_use,
    content_block_stop,
    message_delta,
    message_start_event,
    message_stop,
)

# ---- Helpers ---------------------------------------------------------------

PROFILE = RouterProfile(
    model=ModelConfig(provider="anthropic", model_id="claude-opus-4-6"),
    sampling=Sampling(temperature=0.2, top_p=1.0, max_tokens=1024),
)


def _provider_with(managers: list[FakeStreamManager]) -> tuple[AnthropicProvider, FakeAnthropic]:
    fake = FakeAnthropic(managers)
    prov = AnthropicProvider(client=cast(Any, fake))
    return prov, fake


async def _collect(provider: AnthropicProvider, **kw: Any) -> list[Any]:
    return [c async for c in provider.complete(PROFILE, **kw)]


# ---- Tests -----------------------------------------------------------------


async def test_streaming_yields_text_deltas_in_order() -> None:
    events = [
        message_start_event(input_tokens=10, output_tokens=1),
        content_block_start_text(index=0),
        content_block_delta_text(index=0, text="Hel"),
        content_block_delta_text(index=0, text="lo"),
        content_block_delta_text(index=0, text=", "),
        content_block_delta_text(index=0, text="world"),
        content_block_delta_text(index=0, text="!"),
        content_block_stop(index=0),
        message_delta(output_tokens=7),
        message_stop(),
    ]
    provider, _ = _provider_with([FakeStreamManager(FakeStream(events))])
    chunks = await _collect(provider, messages=[Message(role="user", content="hi")])

    text_chunks = [c for c in chunks if c.type == "text_delta"]
    assert [c.content["text"] for c in text_chunks] == ["Hel", "lo", ", ", "world", "!"]
    # order: text_deltas then usage then done
    assert chunks[-1].type == "done"
    assert chunks[-2].type == "usage"


async def test_tool_use_translation() -> None:
    events = [
        message_start_event(input_tokens=20),
        content_block_start_tool_use(index=0, tool_id="toolu_1", name="bash"),
        content_block_delta_input_json(index=0, partial_json='{"cmd":'),
        content_block_delta_input_json(index=0, partial_json=' "ls -la"}'),
        content_block_stop(index=0),
        message_delta(output_tokens=5),
        message_stop(),
    ]
    provider, _ = _provider_with([FakeStreamManager(FakeStream(events))])
    chunks = await _collect(provider, messages=[Message(role="user", content="list files")])

    kinds = [c.type for c in chunks]
    assert kinds == [
        "tool_call_start",
        "tool_call_delta",
        "tool_call_delta",
        "tool_call_end",
        "usage",
        "done",
    ]
    end = chunks[3]
    assert end.content["tool_id"] == "toolu_1"
    assert end.content["tool_name"] == "bash"
    assert end.content["input"] == {"cmd": "ls -la"}


async def test_usage_captured_from_stream() -> None:
    events = [
        message_start_event(input_tokens=500, output_tokens=2, cache_read_input_tokens=120),
        content_block_start_text(index=0),
        content_block_delta_text(index=0, text="ok"),
        content_block_stop(index=0),
        message_delta(output_tokens=42),
        message_stop(),
    ]
    provider, _ = _provider_with([FakeStreamManager(FakeStream(events))])
    chunks = await _collect(provider, messages=[Message(role="user", content="x")])
    usage = next(c for c in chunks if c.type == "usage")
    assert usage.content["input_tokens"] == 500
    assert usage.content["cached_input_tokens"] == 120
    assert usage.content["output_tokens"] == 42


async def test_retry_on_500_then_success() -> None:
    err = anthropic.APIStatusError(
        "upstream boom",
        response=_fake_response(500),
        body={"error": "boom"},
    )
    good_events = [
        message_start_event(input_tokens=3),
        content_block_start_text(index=0),
        content_block_delta_text(index=0, text="hi"),
        content_block_stop(index=0),
        message_delta(output_tokens=1),
        message_stop(),
    ]
    managers = [
        FakeStreamManager(None, open_exc=err),
        FakeStreamManager(FakeStream(good_events)),
    ]
    # Tighten backoff so the test doesn't sleep a real second.
    profile = PROFILE.model_copy(
        update={
            "error_handling": PROFILE.error_handling.model_copy(
                update={"backoff_base_seconds": 0.001, "backoff_multiplier": 1.0}
            )
        }
    )
    provider, fake = _provider_with(managers)
    chunks = [c async for c in provider.complete(profile, [Message(role="user", content="x")])]
    assert len(fake.messages.calls) == 2
    # No error chunk in output — retry succeeded.
    assert not any(c.type == "error" for c in chunks)
    assert chunks[-1].type == "done"


async def test_no_retry_on_400() -> None:
    err = anthropic.APIStatusError(
        "bad request",
        response=_fake_response(400),
        body={"error": "bad"},
    )
    provider, fake = _provider_with([FakeStreamManager(None, open_exc=err)])
    chunks = [c async for c in provider.complete(PROFILE, [Message(role="user", content="x")])]
    assert len(fake.messages.calls) == 1
    assert len(chunks) == 1
    assert chunks[0].type == "error"
    assert chunks[0].content["status_code"] == 400
    assert chunks[0].content["retryable"] is False


async def test_retries_exhausted_yields_error_chunk() -> None:
    err = anthropic.APIConnectionError(request=_fake_request())
    managers = [FakeStreamManager(None, open_exc=err) for _ in range(4)]
    profile = PROFILE.model_copy(
        update={
            "error_handling": PROFILE.error_handling.model_copy(
                update={
                    "max_retries": 3,
                    "backoff_base_seconds": 0.001,
                    "backoff_multiplier": 1.0,
                }
            )
        }
    )
    provider, fake = _provider_with(managers)
    chunks = [c async for c in provider.complete(profile, [Message(role="user", content="x")])]
    # 1 initial attempt + 3 retries = 4 stream() calls.
    assert len(fake.messages.calls) == 4
    assert len(chunks) == 1
    assert chunks[0].type == "error"
    assert chunks[0].content["retryable"] is True


async def test_mid_stream_error_emits_error_chunk_and_exits() -> None:
    events = [
        message_start_event(input_tokens=5),
        content_block_start_text(index=0),
        content_block_delta_text(index=0, text="ok"),
    ]
    err = anthropic.APIConnectionError(request=_fake_request())
    stream = FakeStream(events, mid_stream_exc=err, raise_after_n=len(events))
    provider, _ = _provider_with([FakeStreamManager(stream)])
    chunks = [c async for c in provider.complete(PROFILE, [Message(role="user", content="x")])]
    # We should see the text_delta we got before the error, then an error chunk.
    assert any(c.type == "text_delta" for c in chunks)
    assert chunks[-1].type == "error"
    assert chunks[-1].content["retryable"] is True


async def test_system_prompt_cache_control_applied_when_profile_requests() -> None:
    from tename.router.types import CachingBreakpoint, CachingConfig

    profile = PROFILE.model_copy(
        update={
            "caching": CachingConfig(
                provider_strategy="explicit_breakpoints",
                breakpoints=[CachingBreakpoint(after="system_prompt")],
            ),
        }
    )
    provider, fake = _provider_with([FakeStreamManager(FakeStream([message_stop()]))])
    _ = [
        c
        async for c in provider.complete(
            profile,
            [
                Message(role="system", content="You are helpful."),
                Message(role="user", content="hi"),
            ],
        )
    ]
    call_kwargs = fake.messages.calls[0]
    sys_blocks = call_kwargs["system"]
    assert sys_blocks[-1]["cache_control"] == {"type": "ephemeral"}


async def test_system_prompt_cache_control_absent_by_default() -> None:
    provider, fake = _provider_with([FakeStreamManager(FakeStream([message_stop()]))])
    _ = [
        c
        async for c in provider.complete(
            PROFILE,
            [
                Message(role="system", content="sys"),
                Message(role="user", content="hi"),
            ],
        )
    ]
    sys_blocks = fake.messages.calls[0]["system"]
    assert "cache_control" not in sys_blocks[-1]


async def test_tool_role_folds_into_user_with_tool_result_block() -> None:
    provider, fake = _provider_with([FakeStreamManager(FakeStream([message_stop()]))])
    msgs = [
        Message(role="user", content="run ls"),
        Message(
            role="assistant",
            content=[ContentBlock(type="tool_use", id="t1", name="bash", input={"cmd": "ls"})],
        ),
        Message(
            role="tool",
            content=[ContentBlock(type="tool_result", tool_use_id="t1", content="a b c")],
        ),
    ]
    _ = [c async for c in provider.complete(PROFILE, msgs)]
    api_messages = fake.messages.calls[0]["messages"]
    assert [m["role"] for m in api_messages] == ["user", "assistant", "user"]
    assert api_messages[2]["content"][0]["type"] == "tool_result"
    assert api_messages[2]["content"][0]["tool_use_id"] == "t1"


async def test_temperature_and_top_p_are_mutually_exclusive() -> None:
    # Default top_p=1.0 → send temperature, omit top_p (matches Opus 4.6 API).
    provider, fake = _provider_with([FakeStreamManager(FakeStream([message_stop()]))])
    _ = [c async for c in provider.complete(PROFILE, [Message(role="user", content="x")])]
    call = fake.messages.calls[0]
    assert "temperature" in call
    assert "top_p" not in call

    # When the profile narrows top_p < 1.0, forward top_p and drop temperature.
    narrowed = PROFILE.model_copy(
        update={"sampling": PROFILE.sampling.model_copy(update={"top_p": 0.5})}
    )
    provider, fake = _provider_with([FakeStreamManager(FakeStream([message_stop()]))])
    _ = [c async for c in provider.complete(narrowed, [Message(role="user", content="x")])]
    call = fake.messages.calls[0]
    assert "top_p" in call
    assert call["top_p"] == 0.5
    assert "temperature" not in call


async def test_tools_are_forwarded() -> None:
    provider, fake = _provider_with([FakeStreamManager(FakeStream([message_stop()]))])
    tools = [
        ToolDef(
            name="bash",
            description="run a shell command",
            input_schema={"type": "object", "properties": {"cmd": {"type": "string"}}},
        )
    ]
    _ = [c async for c in provider.complete(PROFILE, [Message(role="user", content="hi")], tools)]
    api_tools = fake.messages.calls[0]["tools"]
    assert api_tools[0]["name"] == "bash"
    assert "input_schema" in api_tools[0]


# ---- httpx stub helpers used to construct APIStatusError -------------------


def _fake_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _fake_response(status_code: int) -> httpx.Response:
    # MagicMock avoids us hand-rolling a whole httpx.Response; anthropic's
    # APIStatusError only peeks at `.status_code`/`.headers`.
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = {}
    resp.request = _fake_request()
    return cast(httpx.Response, resp)


pytestmark = pytest.mark.asyncio
