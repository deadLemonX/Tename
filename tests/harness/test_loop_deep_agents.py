"""Integration tests for the harness loop driving a deep_agents-framework agent.

These run against a live Postgres + scripted `FakeModelRouter`, same
pattern as `test_loop.py`. They verify that the DeepAgentsAdapter is
selectable via `agent.framework = "deep_agents"` and that the harness
produces the expected event sequence for the adapter's tool-carrying
message shape. No live model calls — those live in `test_deep_agents_e2e.py`.
"""

from __future__ import annotations

import json
from typing import cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tename.harness import HarnessRuntime
from tename.router.service import ModelRouter
from tename.router.types import (
    ContentBlock,
    Message,
    done_chunk,
    text_delta,
    tool_call_end,
    tool_call_start,
)
from tename.sessions import EventType, SessionService
from tename.sessions.exceptions import FailedPreconditionError

from .conftest import FakeModelRouter

pytestmark = pytest.mark.postgres


# ---- Fixtures --------------------------------------------------------------


@pytest_asyncio.fixture
async def deep_agents_agent(engine: AsyncEngine, clean_db: None) -> UUID:
    """Insert an agent row configured with framework='deep_agents'."""
    agent_uuid = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agents "
                "(id, name, model, framework, system_prompt, tools) "
                "VALUES (:id, :name, :model, :framework, :system_prompt, "
                "(:tools)::jsonb)"
            ),
            {
                "id": str(agent_uuid),
                "name": "deep-agent-test",
                "model": "claude-opus-4-6",
                "framework": "deep_agents",
                "system_prompt": "You are a deep agent.",
                "tools": json.dumps(["write_todos", "ls", "read_file"]),
            },
        )
    return agent_uuid


def _harness(service: SessionService, router: FakeModelRouter) -> HarnessRuntime:
    return HarnessRuntime(
        session_service=service,
        model_router=cast(ModelRouter, router),
    )


async def _seed_user(service: SessionService, session_id: UUID, content: str) -> None:
    await service.emit_event(
        session_id,
        event_id=uuid4(),
        event_type=EventType.USER_MESSAGE,
        payload={"content": content},
    )


# ---- Tests -----------------------------------------------------------------


async def test_deep_agents_single_turn_text_only(
    service: SessionService,
    deep_agents_agent: UUID,
) -> None:
    """Plain text response via the deep_agents adapter completes cleanly."""
    session = await service.create_session(deep_agents_agent)
    await _seed_user(service, session.id, "Say hello.")

    router = FakeModelRouter(
        [
            [
                text_delta("hi"),
                text_delta(" there"),
                done_chunk(),
            ]
        ]
    )
    await _harness(service, router).run_session(session.id)

    events = await service.get_events(session.id)
    types = [e.type for e in events]
    assert EventType.SYSTEM_EVENT in types  # system prompt got seeded
    finals = [
        e for e in events if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete")
    ]
    assert len(finals) == 1
    assert finals[0].payload["content"] == "hi there"

    # Router was called once with the expected tool set surfaced.
    assert len(router.calls) == 1
    tool_names = [t.name for t in router.calls[0].tools]
    assert tool_names == ["write_todos", "ls", "read_file"]

    # The system prompt and user message both show up — order follows the
    # event log (user was seeded before run_session emitted the system
    # prompt). Our Anthropic provider pulls system messages to the
    # top-level `system` param regardless of position.
    roles = [m.role for m in router.calls[0].messages]
    assert "system" in roles
    assert "user" in roles

    with pytest.raises(FailedPreconditionError):
        await service.wake(session.id)


async def test_deep_agents_tool_round_produces_stubbed_result_and_wraps_up(
    service: SessionService,
    deep_agents_agent: UUID,
) -> None:
    """Model → write_todos tool_call → stub tool_result → model wraps up.

    Verifies that on turn 2 the adapter's `build_context` feeds a proper
    assistant(tool_use) + tool(tool_result) pair back to the model.
    """
    session = await service.create_session(deep_agents_agent)
    await _seed_user(service, session.id, "Plan a research task.")

    router = FakeModelRouter(
        [
            # Turn 1: model plans via write_todos.
            [
                text_delta("Let me plan."),
                tool_call_start(tool_id="toolu_plan", tool_name="write_todos", index=0),
                tool_call_end(
                    tool_id="toolu_plan",
                    tool_name="write_todos",
                    tool_input={
                        "todos": [
                            {"content": "Gather sources", "status": "pending"},
                            {"content": "Summarize", "status": "pending"},
                        ]
                    },
                    index=0,
                ),
                done_chunk(),
            ],
            # Turn 2: model acknowledges stubbed failure and finishes.
            [
                text_delta("Planning tool unavailable; finishing up."),
                done_chunk(),
            ],
        ]
    )
    await _harness(service, router).run_session(session.id)

    events = await service.get_events(session.id)
    tool_calls = [e for e in events if e.type == EventType.TOOL_CALL]
    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(tool_calls) == 1
    assert tool_calls[0].payload["tool_name"] == "write_todos"
    assert tool_calls[0].payload["input"]["todos"][0]["content"] == "Gather sources"
    assert len(tool_results) == 1
    assert tool_results[0].payload["is_error"] is True
    assert tool_results[0].payload["tool_call_id"] == str(tool_calls[0].id)

    # Two router calls; the SECOND one sees an assistant message carrying
    # both the text and the tool_use block, followed by a tool message
    # with the matching tool_result block.
    assert len(router.calls) == 2
    turn2_messages = router.calls[1].messages
    roles = [m.role for m in turn2_messages]
    assert "assistant" in roles
    assert "tool" in roles

    assistant_idx = roles.index("assistant")
    assistant_content = turn2_messages[assistant_idx].content
    assert isinstance(assistant_content, list)
    assert any(b.type == "text" and b.text == "Let me plan." for b in assistant_content)
    tool_use_blocks = [b for b in assistant_content if b.type == "tool_use"]
    assert len(tool_use_blocks) == 1
    assert tool_use_blocks[0].id == "toolu_plan"
    assert tool_use_blocks[0].name == "write_todos"

    tool_idx = roles.index("tool")
    tool_content = turn2_messages[tool_idx].content
    assert isinstance(tool_content, list)
    assert len(tool_content) == 1
    tool_result_block = tool_content[0]
    assert tool_result_block.type == "tool_result"
    assert tool_result_block.tool_use_id == "toolu_plan"
    assert tool_result_block.is_error is True

    finals = [
        e for e in events if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete")
    ]
    # One closer per turn that produced text (turns 1 and 2 both did).
    assert len(finals) == 2
    assert finals[-1].payload["content"] == "Planning tool unavailable; finishing up."


async def test_deep_agents_adapter_surfaces_agent_tools_to_router(
    service: SessionService,
    deep_agents_agent: UUID,
) -> None:
    """The agent's declared tools flow through get_tools into ModelRouter.complete."""
    session = await service.create_session(deep_agents_agent)
    await _seed_user(service, session.id, "hi")

    router = FakeModelRouter([[text_delta("ok"), done_chunk()]])
    await _harness(service, router).run_session(session.id)

    assert len(router.calls) == 1
    tool_names = {t.name for t in router.calls[0].tools}
    # Matches the fixture's agent.tools.
    assert tool_names == {"write_todos", "ls", "read_file"}
    # And the schemas are non-empty.
    write_todos_tool = next(t for t in router.calls[0].tools if t.name == "write_todos")
    assert write_todos_tool.input_schema["required"] == ["todos"]


async def test_deep_agents_build_context_round_trips_through_content_blocks(
    service: SessionService,
    deep_agents_agent: UUID,
) -> None:
    """Round-trip sanity: every Message the router sees is a valid pydantic
    Message whose ContentBlocks validate — caught early so a future change
    to the router types surfaces here, not in production."""
    session = await service.create_session(deep_agents_agent)
    await _seed_user(service, session.id, "go")

    router = FakeModelRouter(
        [
            [
                tool_call_start(tool_id="toolu_1", tool_name="ls", index=0),
                tool_call_end(
                    tool_id="toolu_1",
                    tool_name="ls",
                    tool_input={},
                    index=0,
                ),
                done_chunk(),
            ],
            [text_delta("done"), done_chunk()],
        ]
    )
    await _harness(service, router).run_session(session.id)

    for call in router.calls:
        for message in call.messages:
            # Each must be a Message; content must be str or list[ContentBlock].
            Message.model_validate(message.model_dump())
            if isinstance(message.content, list):
                for block in message.content:
                    ContentBlock.model_validate(block.model_dump())
