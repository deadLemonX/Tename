"""Unit tests for the DeepAgentsAdapter.

Covers the adapter in isolation from the harness loop: registry wiring,
`build_context` message translation (including full tool rounds),
`chunk_to_event` chunk-to-event mapping, and `get_tools` filtering to
Deep Agents built-ins.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from tename.harness.adapters import (
    BUILTIN_TOOLS,
    DeepAgentsAdapter,
    FrameworkAdapter,
    PendingEvent,
    get_adapter,
    known_adapters,
)
from tename.harness.profiles import Profile, ProfileLoader
from tename.router.types import (
    ContentBlock,
    Message,
    ModelChunk,
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
    *,
    sequence: int = 1,
    event_id: UUID | None = None,
) -> Event:
    return Event(
        id=event_id or uuid4(),
        session_id=UUID(int=1),
        sequence=sequence,
        type=event_type,
        payload=payload,
        created_at=datetime.now(UTC),
    )


def _agent(*, tools: list[str] | None = None, system_prompt: str | None = None) -> Agent:
    return Agent(
        id=uuid4(),
        tenant_id=UUID(int=0),
        name="test",
        model="claude-opus-4-6",
        framework="deep_agents",
        system_prompt=system_prompt,
        tools=tools or [],
        sandbox_recipe=None,
        created_at=datetime.now(UTC),
    )


# ---- Registry --------------------------------------------------------------


def test_deep_agents_is_auto_registered() -> None:
    assert "deep_agents" in known_adapters()


def test_get_adapter_returns_deep_agents_instance() -> None:
    adapter = get_adapter("deep_agents")
    assert isinstance(adapter, DeepAgentsAdapter)


def test_deep_agents_adapter_is_a_framework_adapter() -> None:
    assert issubclass(DeepAgentsAdapter, FrameworkAdapter)


# ---- build_context: basic shape --------------------------------------------


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
    adapter = DeepAgentsAdapter()

    messages = adapter.build_context(events, profile)

    assert [m.role for m in messages] == ["user", "assistant", "user"]
    assert messages[0].content == "hello"
    # Assistant message with text only → single text ContentBlock
    assert isinstance(messages[1].content, list)
    assert len(messages[1].content) == 1
    assert messages[1].content[0].type == "text"
    assert messages[1].content[0].text == "hi there"
    assert messages[2].content == "what's 2+2?"


def test_build_context_injects_system_prompt(profile: Profile) -> None:
    events = [
        _event(
            EventType.SYSTEM_EVENT,
            {"type": "system_prompt", "content": "You are a deep agent."},
            sequence=1,
        ),
        _event(EventType.USER_MESSAGE, {"content": "hi"}, sequence=2),
    ]
    adapter = DeepAgentsAdapter()

    messages = adapter.build_context(events, profile)

    assert [m.role for m in messages] == ["system", "user"]
    assert messages[0].content == "You are a deep agent."


def test_build_context_skips_incomplete_assistant_deltas(profile: Profile) -> None:
    events = [
        _event(EventType.USER_MESSAGE, {"content": "q"}, sequence=1),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "par", "is_complete": False},
            sequence=2,
        ),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "partial answer", "is_complete": True},
            sequence=3,
        ),
    ]
    adapter = DeepAgentsAdapter()

    messages = adapter.build_context(events, profile)

    assert [m.role for m in messages] == ["user", "assistant"]
    content = messages[1].content
    assert isinstance(content, list)
    assert content[0].text == "partial answer"


def test_build_context_skips_harness_and_error_events(profile: Profile) -> None:
    events = [
        _event(EventType.USER_MESSAGE, {"content": "hi"}, sequence=1),
        _event(
            EventType.HARNESS_EVENT,
            {"type": "compaction", "dropped_sequences": []},
            sequence=2,
        ),
        _event(
            EventType.ERROR,
            {"message": "transient glitch", "retryable": True},
            sequence=3,
        ),
    ]
    messages = DeepAgentsAdapter().build_context(events, profile)
    assert len(messages) == 1
    assert messages[0].role == "user"


# ---- build_context: tool rounds --------------------------------------------


def test_build_context_single_tool_round(profile: Profile) -> None:
    """One tool call in a turn → assistant message with text+tool_use,
    then tool message with tool_result, then assistant follow-up."""
    tool_call_event_id = uuid4()
    events = [
        _event(EventType.USER_MESSAGE, {"content": "run python"}, sequence=1),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "let me try", "is_complete": True},
            sequence=2,
        ),
        _event(
            EventType.TOOL_CALL,
            {
                "tool_id": "toolu_1",
                "tool_name": "python",
                "input": {"code": "2+2"},
            },
            sequence=3,
            event_id=tool_call_event_id,
        ),
        _event(
            EventType.TOOL_RESULT,
            {
                "tool_call_id": str(tool_call_event_id),
                "tool_name": "python",
                "is_error": False,
                "content": "4",
            },
            sequence=4,
        ),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "the answer is 4", "is_complete": True},
            sequence=5,
        ),
    ]

    messages = DeepAgentsAdapter().build_context(events, profile)

    assert [m.role for m in messages] == ["user", "assistant", "tool", "assistant"]

    assistant_with_tool = messages[1].content
    assert isinstance(assistant_with_tool, list)
    assert [b.type for b in assistant_with_tool] == ["text", "tool_use"]
    assert assistant_with_tool[0].text == "let me try"
    tool_use = assistant_with_tool[1]
    assert tool_use.id == "toolu_1"
    assert tool_use.name == "python"
    assert tool_use.input == {"code": "2+2"}

    tool_msg = messages[2].content
    assert isinstance(tool_msg, list)
    assert len(tool_msg) == 1
    tool_result = tool_msg[0]
    assert tool_result.type == "tool_result"
    assert tool_result.tool_use_id == "toolu_1"  # the model's id, not event UUID
    assert tool_result.content == "4"
    assert tool_result.is_error is False

    final_assistant = messages[3].content
    assert isinstance(final_assistant, list)
    assert final_assistant[0].text == "the answer is 4"


def test_build_context_multiple_tool_calls_in_one_turn(profile: Profile) -> None:
    """Parallel tool calls → single assistant with multiple tool_use blocks,
    then single tool message with matching tool_result blocks."""
    call_a_id = uuid4()
    call_b_id = uuid4()
    events = [
        _event(EventType.USER_MESSAGE, {"content": "do two things"}, sequence=1),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "running both", "is_complete": True},
            sequence=2,
        ),
        _event(
            EventType.TOOL_CALL,
            {"tool_id": "toolu_a", "tool_name": "python", "input": {"code": "1"}},
            sequence=3,
            event_id=call_a_id,
        ),
        _event(
            EventType.TOOL_CALL,
            {"tool_id": "toolu_b", "tool_name": "ls", "input": {}},
            sequence=4,
            event_id=call_b_id,
        ),
        _event(
            EventType.TOOL_RESULT,
            {
                "tool_call_id": str(call_a_id),
                "tool_name": "python",
                "is_error": True,
                "error": "stubbed",
            },
            sequence=5,
        ),
        _event(
            EventType.TOOL_RESULT,
            {
                "tool_call_id": str(call_b_id),
                "tool_name": "ls",
                "is_error": True,
                "error": "stubbed",
            },
            sequence=6,
        ),
    ]

    messages = DeepAgentsAdapter().build_context(events, profile)
    assert [m.role for m in messages] == ["user", "assistant", "tool"]

    assistant_blocks = messages[1].content
    assert isinstance(assistant_blocks, list)
    assert [b.type for b in assistant_blocks] == ["text", "tool_use", "tool_use"]
    assert [b.id for b in assistant_blocks if b.type == "tool_use"] == ["toolu_a", "toolu_b"]

    tool_blocks = messages[2].content
    assert isinstance(tool_blocks, list)
    assert [b.type for b in tool_blocks] == ["tool_result", "tool_result"]
    assert [b.tool_use_id for b in tool_blocks] == ["toolu_a", "toolu_b"]
    assert all(b.is_error is True for b in tool_blocks)
    assert all(b.content == "stubbed" for b in tool_blocks)


def test_build_context_tool_call_without_preceding_assistant_opens_empty_message(
    profile: Profile,
) -> None:
    """Defensive: a tool_call without a preceding assistant_message still
    produces a valid assistant message with just the tool_use block."""
    call_id = uuid4()
    events = [
        _event(EventType.USER_MESSAGE, {"content": "go"}, sequence=1),
        _event(
            EventType.TOOL_CALL,
            {"tool_id": "toolu_1", "tool_name": "ls", "input": {}},
            sequence=2,
            event_id=call_id,
        ),
        _event(
            EventType.TOOL_RESULT,
            {"tool_call_id": str(call_id), "tool_name": "ls", "content": "[]"},
            sequence=3,
        ),
    ]
    messages = DeepAgentsAdapter().build_context(events, profile)
    assert [m.role for m in messages] == ["user", "assistant", "tool"]
    assistant = messages[1].content
    assert isinstance(assistant, list)
    assert len(assistant) == 1
    assert assistant[0].type == "tool_use"


def test_build_context_skips_orphaned_tool_result(profile: Profile) -> None:
    """A tool_result whose tool_call_id references a dropped/unknown event
    is skipped — including it would poison the Anthropic request."""
    events = [
        _event(EventType.USER_MESSAGE, {"content": "hi"}, sequence=1),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "ok", "is_complete": True},
            sequence=2,
        ),
        _event(
            EventType.TOOL_RESULT,
            {
                "tool_call_id": str(uuid4()),  # no matching TOOL_CALL in log
                "tool_name": "python",
                "content": "orphan",
            },
            sequence=3,
        ),
    ]
    messages = DeepAgentsAdapter().build_context(events, profile)
    assert [m.role for m in messages] == ["user", "assistant"]


def test_build_context_tool_result_falls_back_to_error_field(profile: Profile) -> None:
    """The stubbed tool_result from S7 carries `error` rather than
    `content`; the adapter should surface it as the tool_result block's text."""
    call_id = uuid4()
    events = [
        _event(EventType.USER_MESSAGE, {"content": "q"}, sequence=1),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "trying", "is_complete": True},
            sequence=2,
        ),
        _event(
            EventType.TOOL_CALL,
            {"tool_id": "toolu_x", "tool_name": "python", "input": {}},
            sequence=3,
            event_id=call_id,
        ),
        _event(
            EventType.TOOL_RESULT,
            {
                "tool_call_id": str(call_id),
                "tool_name": "python",
                "is_error": True,
                "error": "tool execution not yet implemented (lands in S9/S10)",
            },
            sequence=4,
        ),
    ]
    messages = DeepAgentsAdapter().build_context(events, profile)
    tool_blocks = messages[-1].content
    assert isinstance(tool_blocks, list)
    assert tool_blocks[0].content == "tool execution not yet implemented (lands in S9/S10)"
    assert tool_blocks[0].is_error is True


def test_build_context_output_is_list_of_messages(profile: Profile) -> None:
    events = [_event(EventType.USER_MESSAGE, {"content": "hi"})]
    messages = DeepAgentsAdapter().build_context(events, profile)
    assert all(isinstance(m, Message) for m in messages)


# ---- chunk_to_event --------------------------------------------------------


def test_chunk_to_event_text_delta_produces_incremental_assistant() -> None:
    pe = DeepAgentsAdapter().chunk_to_event(text_delta("hello"))
    assert isinstance(pe, PendingEvent)
    assert pe.type == EventType.ASSISTANT_MESSAGE
    assert pe.payload == {"content": "hello", "is_complete": False}


def test_chunk_to_event_tool_call_end_produces_tool_call() -> None:
    chunk = tool_call_end(
        tool_id="toolu_42",
        tool_name="write_todos",
        tool_input={"todos": [{"content": "plan", "status": "pending"}]},
        index=0,
    )
    pe = DeepAgentsAdapter().chunk_to_event(chunk)
    assert isinstance(pe, PendingEvent)
    assert pe.type == EventType.TOOL_CALL
    assert pe.payload["tool_id"] == "toolu_42"
    assert pe.payload["tool_name"] == "write_todos"
    assert pe.payload["input"]["todos"][0]["content"] == "plan"


def test_chunk_to_event_write_todos_is_plain_tool_call_not_harness_event() -> None:
    """v0.1 does NOT split write_todos into a separate harness_event(type=plan).
    The tool_call is the plan record; observability filters by tool_name."""
    chunk = tool_call_end(
        tool_id="toolu_1",
        tool_name="write_todos",
        tool_input={"todos": []},
        index=0,
    )
    pe = DeepAgentsAdapter().chunk_to_event(chunk)
    assert pe is not None
    assert pe.type == EventType.TOOL_CALL
    assert pe.type != EventType.HARNESS_EVENT


def test_chunk_to_event_task_is_plain_tool_call() -> None:
    """Same deferral for the `task` (subagent_spawn) tool."""
    chunk = tool_call_end(
        tool_id="toolu_2",
        tool_name="task",
        tool_input={"description": "research", "subagent_type": "general-purpose"},
        index=0,
    )
    pe = DeepAgentsAdapter().chunk_to_event(chunk)
    assert pe is not None
    assert pe.type == EventType.TOOL_CALL


def test_chunk_to_event_error_produces_error_event() -> None:
    chunk = error_chunk(message="boom", retryable=True, status_code=503)
    pe = DeepAgentsAdapter().chunk_to_event(chunk)
    assert isinstance(pe, PendingEvent)
    assert pe.type == EventType.ERROR
    assert pe.payload["message"] == "boom"
    assert pe.payload["retryable"] is True


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
    assert DeepAgentsAdapter().chunk_to_event(chunk) is None


# ---- get_tools -------------------------------------------------------------


def test_get_tools_returns_builtins_requested_by_agent() -> None:
    agent = _agent(tools=["write_todos", "ls", "read_file"])
    tools = DeepAgentsAdapter().get_tools(agent)
    assert [t.name for t in tools] == ["write_todos", "ls", "read_file"]
    # Each must have a non-trivial schema.
    for tool in tools:
        assert tool.input_schema.get("type") == "object"


def test_get_tools_empty_when_agent_has_no_tools() -> None:
    assert DeepAgentsAdapter().get_tools(_agent(tools=[])) == []


def test_get_tools_skips_unknown_tools_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    agent = _agent(tools=["write_todos", "not_a_builtin", "task"])
    with caplog.at_level(logging.WARNING, logger="tename.harness.adapters.deep_agents"):
        tools = DeepAgentsAdapter().get_tools(agent)
    assert [t.name for t in tools] == ["write_todos", "task"]
    assert any("not_a_builtin" in record.message for record in caplog.records)


def test_get_tools_deduplicates() -> None:
    agent = _agent(tools=["write_todos", "write_todos", "ls"])
    tools = DeepAgentsAdapter().get_tools(agent)
    assert [t.name for t in tools] == ["write_todos", "ls"]


def test_get_tools_preserves_order_from_agent() -> None:
    agent = _agent(tools=["task", "edit_file", "write_file"])
    tools = DeepAgentsAdapter().get_tools(agent)
    assert [t.name for t in tools] == ["task", "edit_file", "write_file"]


def test_builtin_tools_module_constant_covers_documented_set() -> None:
    expected = {"write_todos", "ls", "read_file", "write_file", "edit_file", "task"}
    assert set(BUILTIN_TOOLS.keys()) == expected


def test_write_todos_schema_requires_todos_with_status_enum() -> None:
    schema = BUILTIN_TOOLS["write_todos"].input_schema
    assert schema["required"] == ["todos"]
    status_enum = schema["properties"]["todos"]["items"]["properties"]["status"]["enum"]
    assert set(status_enum) == {"pending", "in_progress", "completed"}


# ---- supports_streaming ----------------------------------------------------


def test_supports_streaming_is_true() -> None:
    assert DeepAgentsAdapter().supports_streaming() is True


# ---- Sanity: ContentBlock frozen/valid ------------------------------------


def test_tool_use_block_has_valid_fields(profile: Profile) -> None:
    """Ensure the ContentBlocks we produce round-trip through pydantic
    validation (no hidden extra fields or missing required ones)."""
    events = [
        _event(EventType.USER_MESSAGE, {"content": "go"}, sequence=1),
        _event(
            EventType.ASSISTANT_MESSAGE,
            {"content": "trying", "is_complete": True},
            sequence=2,
        ),
        _event(
            EventType.TOOL_CALL,
            {"tool_id": "toolu_x", "tool_name": "python", "input": {"code": "1"}},
            sequence=3,
        ),
    ]
    messages = DeepAgentsAdapter().build_context(events, profile)
    assistant = messages[-1].content
    assert isinstance(assistant, list)
    # Re-validate each block via pydantic to be sure the adapter's output
    # passes the frozen/extra=forbid contract.
    for block in assistant:
        ContentBlock.model_validate(block.model_dump())
