"""End-to-end Harness + Sandbox + live Anthropic test.

The full S9 success criterion in one test: ask a live Claude Opus 4.6 to
calculate `factorial(10)` using the `python` sandbox tool and assert the
result lands back in the session as a non-error tool_result containing
`3628800`.

Gated on `postgres`, `anthropic`, and `sandbox` markers. Each
dependency skips cleanly when absent.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tename.harness import HarnessRuntime
from tename.router.service import ModelRouter
from tename.sandbox import DockerBackend, Sandbox
from tename.sessions import EventType, SessionService
from tename.sessions.exceptions import FailedPreconditionError

from .conftest import has_env

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.anthropic,
    pytest.mark.sandbox,
]


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _skip_without_docker() -> None:  # pyright: ignore[reportUnusedFunction]
    if not _docker_available():
        pytest.skip("Docker daemon unreachable")


async def _insert_agent_with_python_tool(engine: AsyncEngine) -> UUID:
    agent_uuid = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agents (id, name, model, framework, system_prompt, tools) "
                "VALUES (:id, :name, :model, :framework, :system_prompt, :tools)"
            ),
            {
                "id": str(agent_uuid),
                "name": "sandbox-e2e-agent",
                "model": "claude-opus-4-6",
                "framework": "vanilla",
                "system_prompt": (
                    "You are a coding assistant. When asked to compute something, "
                    "use the `python` tool to run code in a sandbox. "
                    "Wait for the tool result, then reply with the final numeric answer."
                ),
                "tools": json.dumps(["python"]),
            },
        )
    return agent_uuid


@pytest.mark.skipif(not has_env("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
async def test_real_e2e_factorial(
    service: SessionService,
    engine: AsyncEngine,
    clean_db: None,
) -> None:
    """Live Opus 4.6 calculates factorial(10) via the python sandbox tool."""
    agent_id = await _insert_agent_with_python_tool(engine)
    session = await service.create_session(agent_id)
    await service.emit_event(
        session.id,
        event_id=uuid4(),
        event_type=EventType.USER_MESSAGE,
        payload={
            "content": (
                "Use the python tool to compute the factorial of 10. Print the numeric result."
            )
        },
    )

    sandbox = Sandbox(DockerBackend())
    runtime = HarnessRuntime(
        session_service=service,
        model_router=ModelRouter(),
        sandbox=sandbox,
    )
    await runtime.run_session(session.id)

    events = await service.get_events(session.id)
    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert tool_results, "model should have called the python tool at least once"
    python_results = [r for r in tool_results if r.payload.get("tool_name") == "python"]
    assert python_results, "expected at least one python tool_result"
    successful = [r for r in python_results if r.payload.get("is_error") is False]
    assert successful, "expected a non-error python tool_result"
    # factorial(10) == 3628800 — must appear in at least one result.
    assert any("3628800" in r.payload.get("content", "") for r in successful)

    # Sandbox was provisioned.
    provisioned = [
        e
        for e in events
        if e.type == EventType.SYSTEM_EVENT and e.payload.get("type") == "sandbox_provisioned"
    ]
    assert len(provisioned) >= 1

    # Session terminal.
    with pytest.raises(FailedPreconditionError):
        await service.wake(session.id)
