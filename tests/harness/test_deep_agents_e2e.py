"""Live end-to-end test: Deep Agents adapter against real Anthropic.

Gated on both `anthropic` and `postgres` markers; requires
`ANTHROPIC_API_KEY` and `TENAME_TEST_DATABASE_URL`. Proves the adapter
pattern works end-to-end: harness → DeepAgentsAdapter → ModelRouter →
AnthropicProvider, with a real tool-capable agent, stubbed tool
execution, and a clean terminal session.

Scope is deliberately conservative — Claude may or may not call
`write_todos` for a simple prompt; the assertions are about structural
correctness (streaming happened, session terminated, tool schemas were
surfaced), not about exact tool-use counts.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tename.harness import HarnessRuntime
from tename.router.service import ModelRouter
from tename.sessions import EventType, SessionService
from tename.sessions.exceptions import FailedPreconditionError

from .conftest import has_env

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.anthropic,
]


@pytest_asyncio.fixture
async def deep_agents_live_agent(engine: AsyncEngine, clean_db: None) -> UUID:
    """Agent row configured for the live e2e test: deep_agents framework,
    planning tool wired, concise system prompt."""
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
                "name": "deep-agents-e2e",
                "model": "claude-opus-4-6",
                "framework": "deep_agents",
                "system_prompt": (
                    "You are a research assistant. Be concise. "
                    "If the request has multiple steps, call write_todos "
                    "to sketch a short plan before answering."
                ),
                "tools": json.dumps(["write_todos"]),
            },
        )
    return agent_uuid


@pytest.mark.skipif(not has_env("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
async def test_real_e2e_deep_agents(
    service: SessionService,
    deep_agents_live_agent: UUID,
) -> None:
    """Live test: Deep Agents adapter drives a short research-style prompt."""
    session = await service.create_session(deep_agents_live_agent)
    await service.emit_event(
        session.id,
        event_id=uuid4(),
        event_type=EventType.USER_MESSAGE,
        payload={
            "content": (
                "In two sentences, say what the number 42 is commonly "
                "associated with in pop culture."
            ),
        },
    )

    runtime = HarnessRuntime(session_service=service, model_router=ModelRouter())
    await runtime.run_session(session.id)

    events = await service.get_events(session.id)
    types = [e.type for e in events]
    assert EventType.USER_MESSAGE in types
    assert EventType.SYSTEM_EVENT in types

    incrementals = [
        e
        for e in events
        if e.type == EventType.ASSISTANT_MESSAGE and not e.payload.get("is_complete")
    ]
    finals = [
        e for e in events if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete")
    ]
    assert incrementals, "streaming should emit at least one incremental assistant_message"
    assert finals, "at least one consolidated is_complete=True"

    # If the model chose to plan, every tool_call must have a paired tool_result.
    tool_calls = [e for e in events if e.type == EventType.TOOL_CALL]
    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(tool_results) == len(tool_calls), (
        "every tool_call in the log must have a matching tool_result "
        "(stubbed is fine in v0.1); otherwise the Anthropic contract breaks"
    )

    with pytest.raises(FailedPreconditionError):
        await service.wake(session.id)
