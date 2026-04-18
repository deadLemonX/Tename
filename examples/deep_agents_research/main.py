"""Minimal Deep Agents example running on Tename.

This script wires up the full stack manually (no SDK yet — the SDK lands
in S10) to demonstrate that a Deep Agents-style agent runs end-to-end on
Tename's harness. It creates an agent with `framework: deep_agents` and
the `write_todos` planning tool, opens a session, seeds a research
prompt, and streams assistant output + tool-call logs to stdout.

In v0.1 the built-in Deep Agents tools (write_todos, filesystem tools,
task) are schema-only — every tool call returns a stubbed error. Real
execution lands in S9 (sandbox) and S10 (tool proxy). For this example
that means the model may see a tool-unavailable error and wrap up
without planning; the session still completes cleanly.

Prerequisites:
  - `docker compose up` (Postgres running at localhost:5433)
  - `ANTHROPIC_API_KEY` set in the environment
  - Migrations applied: `make migrate`
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tename.harness import HarnessRuntime
from tename.router.service import ModelRouter
from tename.sessions import EventType, SessionService

DEFAULT_DATABASE_URL = "postgresql+psycopg://tename:tename@localhost:5433/tename_dev"

SYSTEM_PROMPT = (
    "You are a concise research assistant. "
    "For multi-step tasks, use the write_todos tool to sketch a short plan "
    "BEFORE answering, then respond in two or three sentences."
)

USER_PROMPT = (
    "Give me a two-sentence summary of what Claude Shannon is known for, "
    "focusing on his contribution to information theory."
)


async def _insert_deep_agent(database_url: str) -> uuid.UUID:
    """Create a deep_agents-framework agent row and return its id."""
    engine = create_async_engine(database_url)
    agent_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO agents "
                    "(id, name, model, framework, system_prompt, tools) "
                    "VALUES (:id, :name, :model, :framework, :system_prompt, "
                    "(:tools)::jsonb)"
                ),
                {
                    "id": str(agent_id),
                    "name": "research-agent-example",
                    "model": "claude-opus-4-6",
                    "framework": "deep_agents",
                    "system_prompt": SYSTEM_PROMPT,
                    "tools": json.dumps(["write_todos"]),
                },
            )
    finally:
        await engine.dispose()
    return agent_id


async def _stream_session_events(service: SessionService, session_id: uuid.UUID) -> None:
    """Pretty-print the session's event log after run completes."""
    events = await service.get_events(session_id)
    print("\n--- Session event log ---")
    for ev in events:
        if ev.type == EventType.SYSTEM_EVENT:
            label = ev.payload.get("type", "system")
            print(f"  [{ev.sequence:>3}] {ev.type.value:<18} ({label})")
        elif ev.type == EventType.ASSISTANT_MESSAGE:
            if ev.payload.get("is_complete"):
                content = cast(str, ev.payload.get("content", ""))
                print(f"  [{ev.sequence:>3}] {ev.type.value:<18} {content!r}")
        elif ev.type == EventType.TOOL_CALL:
            name = ev.payload.get("tool_name")
            inp = ev.payload.get("input")
            print(f"  [{ev.sequence:>3}] {ev.type.value:<18} {name}({inp})")
        elif ev.type == EventType.TOOL_RESULT:
            err = ev.payload.get("error", "")
            print(f"  [{ev.sequence:>3}] {ev.type.value:<18} error={err!r}")
        elif ev.type == EventType.USER_MESSAGE:
            content = cast(str, ev.payload.get("content", ""))
            print(f"  [{ev.sequence:>3}] {ev.type.value:<18} {content!r}")
        else:
            print(f"  [{ev.sequence:>3}] {ev.type.value:<18} {ev.payload}")


async def _run() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1

    database_url = os.getenv("TENAME_DATABASE_URL", DEFAULT_DATABASE_URL)
    print(f"connecting to: {database_url}")

    agent_id = await _insert_deep_agent(database_url)
    print(f"created agent: {agent_id}")

    service = SessionService(database_url)
    try:
        session = await service.create_session(agent_id)
        print(f"created session: {session.id}")

        await service.emit_event(
            session.id,
            event_id=uuid.uuid4(),
            event_type=EventType.USER_MESSAGE,
            payload={"content": USER_PROMPT},
        )

        print("\nrunning harness (this hits the Anthropic API) ...")
        runtime = HarnessRuntime(session_service=service, model_router=ModelRouter())
        await runtime.run_session(session.id)

        await _stream_session_events(service, session.id)
    finally:
        await service.close()

    return 0


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
