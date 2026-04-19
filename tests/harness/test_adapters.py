"""Framework adapter registry and VanillaAdapter tests."""

from __future__ import annotations

import logging
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


def _event(
    event_type: EventType,
    payload: dict[str, object],
    sequence: int = 1,
    event_id: UUID | None = None,
) -> Event:
    return Event(
        id=event_id if event_id is not None else uuid4(),
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
    with pytest.raises(UnknownAdapterError, match="nonexistent_framework"):
        get_adapter("nonexistent_framework")


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
    # Assistant turns surface as a single text block so tool rounds can
    # append tool_use blocks alongside the text in the same message.
    assistant_content = messages[1].content
    assert isinstance(assistant_content, list)
    assert len(assistant_content) == 1
    assert assistant_content[0].type == "text"
    assert assistant_content[0].text == "hi there"
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
    assistant_content = messages[1].content
    assert isinstance(assistant_content, list)
    assert assistant_content[0].type == "text"
    assert assistant_content[0].text == "answer"


def test_build_context_carries_tool_rounds_through(profile: Profile) -> None:
    """A turn with text + tool_call folds into one assistant message with
    both blocks; the tool_result event lands as a tool-role message with
    a matching `tool_use_id`. This is what makes multi-turn tool use
    actually work against Anthropic (see v0.1 bug fix — any preamble text
    before a tool_use used to leave the conversation ending on an
    assistant turn and the next request would be rejected)."""
    tool_call_event_id = uuid4()
    events = [
        _event(EventType.USER_MESSAGE, {"content": "check file"}, sequence=1),
        _event(
            EventType.TOOL_CALL,
            {"tool_id": "toolu_123", "tool_name": "file_read", "input": {"path": "/f"}},
            sequence=2,
            event_id=tool_call_event_id,
        ),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "I'll read the file.", "is_complete": True},
            sequence=3,
        ),
        _event(
            EventType.TOOL_RESULT,
            {
                "tool_call_id": str(tool_call_event_id),
                "tool_name": "file_read",
                "is_error": False,
                "content": "file contents",
            },
            sequence=4,
        ),
        _event(EventType.USER_MESSAGE, {"content": "thanks"}, sequence=5),
    ]
    adapter = VanillaAdapter()

    messages = adapter.build_context(events, profile)

    assert [m.role for m in messages] == ["user", "assistant", "tool", "user"]

    assistant = messages[1].content
    assert isinstance(assistant, list)
    assert [b.type for b in assistant] == ["text", "tool_use"]
    assert assistant[0].text == "I'll read the file."
    assert assistant[1].id == "toolu_123"
    assert assistant[1].name == "file_read"

    tool_msg = messages[2].content
    assert isinstance(tool_msg, list)
    assert tool_msg[0].type == "tool_result"
    assert tool_msg[0].tool_use_id == "toolu_123"
    assert tool_msg[0].content == "file contents"


def test_build_context_skips_harness_events(profile: Profile) -> None:
    events = [
        _event(EventType.USER_MESSAGE, {"content": "hi"}, sequence=1),
        _event(EventType.HARNESS_EVENT, {"kind": "plan"}, sequence=2),
    ]
    adapter = VanillaAdapter()

    messages = adapter.build_context(events, profile)
    assert len(messages) == 1
    assert messages[0].role == "user"


def test_build_context_skips_orphan_tool_result(profile: Profile) -> None:
    """A tool_result whose matching tool_call is absent must be skipped
    or Anthropic will reject the request for a dangling `tool_use_id`."""
    events = [
        _event(EventType.USER_MESSAGE, {"content": "hi"}, sequence=1),
        _event(
            EventType.TOOL_RESULT,
            {
                "tool_call_id": str(uuid4()),
                "tool_name": "bash",
                "is_error": False,
                "content": "ok",
            },
            sequence=2,
        ),
    ]
    adapter = VanillaAdapter()

    messages = adapter.build_context(events, profile)
    assert [m.role for m in messages] == ["user"]


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


def test_get_tools_empty_when_agent_has_no_tools() -> None:
    tools = VanillaAdapter().get_tools(_agent(tools=[]))
    assert tools == []


def test_get_tools_surfaces_sandbox_builtins() -> None:
    """Sandbox built-in names in agent.tools → matching ToolDef schemas."""
    tools = VanillaAdapter().get_tools(_agent(tools=["python", "bash"]))
    assert [t.name for t in tools] == ["python", "bash"]
    assert all(isinstance(t, ToolDef) for t in tools)
    python_def = tools[0]
    assert "code" in python_def.input_schema["properties"]


def test_get_tools_dedupes_and_preserves_order() -> None:
    tools = VanillaAdapter().get_tools(_agent(tools=["python", "bash", "python"]))
    assert [t.name for t in tools] == ["python", "bash"]


def test_get_tools_skips_unknown_names_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="tename.harness.adapters.vanilla"):
        tools = VanillaAdapter().get_tools(_agent(tools=["python", "not_a_tool"]))
    assert [t.name for t in tools] == ["python"]
    assert any("not_a_tool" in rec.message for rec in caplog.records)


def test_supports_streaming_is_true() -> None:
    assert VanillaAdapter().supports_streaming() is True


# ---- Typing sanity: ensure signatures match ABC expectations ---------------


def test_vanilla_adapter_is_a_framework_adapter() -> None:
    assert issubclass(VanillaAdapter, FrameworkAdapter)


def test_build_context_output_is_list_of_messages(profile: Profile) -> None:
    events = [_event(EventType.USER_MESSAGE, {"content": "hi"})]
    messages = VanillaAdapter().build_context(events, profile)
    assert all(isinstance(m, Message) for m in messages)
