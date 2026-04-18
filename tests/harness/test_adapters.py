"""Framework adapter registry and VanillaAdapter tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID, uuid4

import pytest

from tename.harness.adapters import (
    FrameworkAdapter,
    PendingEvent,
    UnknownAdapterError,
    VanillaAdapter,
    get_adapter,
    known_adapters,
    register_adapter,
)
from tename.harness.adapters import base as adapter_base
from tename.harness.profiles import Profile, ProfileLoader
from tename.router.types import (
    Message,
    ModelChunk,
    ToolDef,
    Usage,
    done_chunk,
    error_chunk,
    text_delta,
    tool_call_delta,
    tool_call_end,
    tool_call_start,
    usage_chunk,
)
from tename.sessions.models import Agent, Event, EventType


@pytest.fixture
def profile() -> Profile:
    return ProfileLoader().load("claude-opus-4-6")


def _event(event_type: EventType, payload: dict[str, object], sequence: int = 1) -> Event:
    return Event(
        id=uuid4(),
        session_id=UUID(int=1),
        sequence=sequence,
        type=event_type,
        payload=payload,
        created_at=datetime.now(UTC),
    )


def _agent(tools: list[str] | None = None) -> Agent:
    return Agent(
        id=uuid4(),
        tenant_id=UUID(int=0),
        name="test",
        model="claude-opus-4-6",
        framework="vanilla",
        system_prompt=None,
        tools=tools or [],
        sandbox_recipe=None,
        created_at=datetime.now(UTC),
    )


# ---- Registry --------------------------------------------------------------


def test_vanilla_is_auto_registered() -> None:
    assert "vanilla" in known_adapters()


def test_get_adapter_returns_vanilla_instance() -> None:
    adapter = get_adapter("vanilla")
    assert isinstance(adapter, VanillaAdapter)


def test_get_adapter_returns_fresh_instance_each_call() -> None:
    a = get_adapter("vanilla")
    b = get_adapter("vanilla")
    assert a is not b


def test_unknown_adapter_raises() -> None:
    with pytest.raises(UnknownAdapterError, match="deep_agents"):
        get_adapter("deep_agents")


def test_reregister_same_class_is_noop() -> None:
    register_adapter(VanillaAdapter)  # should not raise


def test_reregister_different_class_under_same_name_raises() -> None:
    class Imposter(FrameworkAdapter):
        name: ClassVar[str] = "vanilla"

        def build_context(self, events, profile):  # type: ignore[override]
            return []

        def chunk_to_event(self, chunk):  # type: ignore[override]
            return None

        def get_tools(self, agent):  # type: ignore[override]
            return []

    with pytest.raises(ValueError, match="already registered"):
        register_adapter(Imposter)


def test_register_and_unregister_custom_adapter() -> None:
    class MyAdapter(FrameworkAdapter):
        name: ClassVar[str] = "my_custom_adapter_for_test"

        def build_context(self, events, profile):  # type: ignore[override]
            return []

        def chunk_to_event(self, chunk):  # type: ignore[override]
            return None

        def get_tools(self, agent):  # type: ignore[override]
            return []

    try:
        register_adapter(MyAdapter)
        assert "my_custom_adapter_for_test" in known_adapters()
        assert isinstance(get_adapter("my_custom_adapter_for_test"), MyAdapter)
    finally:
        adapter_base._ADAPTERS.pop("my_custom_adapter_for_test", None)  # pyright: ignore[reportPrivateUsage]


# ---- VanillaAdapter.build_context ------------------------------------------


def test_build_context_user_and_assistant_messages(profile: Profile) -> None:
    events = [
        _event(EventType.USER_MESSAGE, {"content": "hello"}, sequence=1),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "hi there", "is_complete": True},
            sequence=2,
        ),
        _event(EventType.USER_MESSAGE, {"content": "what's 2+2?"}, sequence=3),
    ]
    adapter = VanillaAdapter()

    messages = adapter.build_context(events, profile)

    assert [m.role for m in messages] == ["user", "assistant", "user"]
    assert messages[0].content == "hello"
    assert messages[1].content == "hi there"
    assert messages[2].content == "what's 2+2?"


def test_build_context_injects_system_prompt_from_system_event(profile: Profile) -> None:
    events = [
        _event(
            EventType.SYSTEM_EVENT,
            {"type": "system_prompt", "content": "You are a helpful assistant."},
            sequence=1,
        ),
        _event(EventType.USER_MESSAGE, {"content": "hi"}, sequence=2),
    ]
    adapter = VanillaAdapter()

    messages = adapter.build_context(events, profile)

    assert [m.role for m in messages] == ["system", "user"]
    assert messages[0].content == "You are a helpful assistant."


def test_build_context_skips_incomplete_assistant_deltas(profile: Profile) -> None:
    """Incremental assistant_message events (is_complete=False) must not
    be replayed into context or we duplicate the assistant's output."""
    events = [
        _event(EventType.USER_MESSAGE, {"content": "q"}, sequence=1),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "ans", "is_complete": False},
            sequence=2,
        ),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "wer", "is_complete": False},
            sequence=3,
        ),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "answer", "is_complete": True},
            sequence=4,
        ),
    ]
    adapter = VanillaAdapter()

    messages = adapter.build_context(events, profile)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].content == "answer"


def test_build_context_skips_tool_and_harness_events(profile: Profile) -> None:
    events = [
        _event(EventType.USER_MESSAGE, {"content": "hi"}, sequence=1),
        _event(
            EventType.TOOL_CALL,
            {"tool_id": "t1", "tool_name": "bash", "input": {}},
            sequence=2,
        ),
        _event(
            EventType.TOOL_RESULT,
            {"call_id": "t1", "result": "ok"},
            sequence=3,
        ),
        _event(EventType.HARNESS_EVENT, {"kind": "plan"}, sequence=4),
    ]
    adapter = VanillaAdapter()

    messages = adapter.build_context(events, profile)
    assert len(messages) == 1
    assert messages[0].role == "user"


# ---- VanillaAdapter.chunk_to_event -----------------------------------------


def test_chunk_to_event_text_delta_produces_incremental_assistant() -> None:
    adapter = VanillaAdapter()
    pe = adapter.chunk_to_event(text_delta("hello"))

    assert pe is not None
    assert isinstance(pe, PendingEvent)
    assert pe.type == EventType.ASSISTANT_MESSAGE
    assert pe.payload == {"content": "hello", "is_complete": False}


def test_chunk_to_event_tool_call_end_produces_tool_call() -> None:
    adapter = VanillaAdapter()
    chunk = tool_call_end(tool_id="call_1", tool_name="python", tool_input={"code": "2+2"}, index=0)

    pe = adapter.chunk_to_event(chunk)
    assert pe is not None
    assert isinstance(pe, PendingEvent)
    assert pe.type == EventType.TOOL_CALL
    assert pe.payload == {
        "tool_id": "call_1",
        "tool_name": "python",
        "input": {"code": "2+2"},
    }


def test_chunk_to_event_error_produces_error_event() -> None:
    adapter = VanillaAdapter()
    chunk = error_chunk(message="boom", retryable=True, status_code=503)

    pe = adapter.chunk_to_event(chunk)
    assert pe is not None
    assert isinstance(pe, PendingEvent)
    assert pe.type == EventType.ERROR
    assert pe.payload["message"] == "boom"
    assert pe.payload["retryable"] is True
    assert pe.payload["status_code"] == 503


@pytest.mark.parametrize(
    "chunk",
    [
        done_chunk(),
        usage_chunk(Usage(input_tokens=10, output_tokens=5)),
        tool_call_start(tool_id="c1", tool_name="python", index=0),
        tool_call_delta(tool_id="c1", partial_json='{"code":', index=0),
    ],
    ids=["done", "usage", "tool_call_start", "tool_call_delta"],
)
def test_chunk_to_event_returns_none_for_streaming_plumbing(chunk: ModelChunk) -> None:
    assert VanillaAdapter().chunk_to_event(chunk) is None


# ---- VanillaAdapter.get_tools / supports_streaming -------------------------


def test_get_tools_returns_empty_list_in_v0_1() -> None:
    tools = VanillaAdapter().get_tools(_agent(tools=["bash", "python"]))
    assert tools == []
    # and type is list[ToolDef] in the sense of being iterable of ToolDef
    assert all(isinstance(t, ToolDef) for t in tools)


def test_supports_streaming_is_true() -> None:
    assert VanillaAdapter().supports_streaming() is True


# ---- Typing sanity: ensure signatures match ABC expectations ---------------


def test_vanilla_adapter_is_a_framework_adapter() -> None:
    assert issubclass(VanillaAdapter, FrameworkAdapter)


def test_build_context_output_is_list_of_messages(profile: Profile) -> None:
    events = [_event(EventType.USER_MESSAGE, {"content": "hi"})]
    messages = VanillaAdapter().build_context(events, profile)
    assert all(isinstance(m, Message) for m in messages)
