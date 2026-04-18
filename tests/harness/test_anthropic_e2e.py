"""End-to-end Harness Runtime test against live Anthropic + live Postgres.

Gated on both the `anthropic` and `postgres` pytest markers. Requires
`ANTHROPIC_API_KEY` and `TENAME_TEST_DATABASE_URL` to be set. Verifies
that the full stack — SessionService, HarnessRuntime, VanillaAdapter,
ModelRouter, AnthropicProvider — strings together: streaming deltas show
up as events, a consolidated assistant_message(is_complete=True) is
emitted, and the session marks COMPLETED.

Treated as a smoke test. Cost per run is a few cents; skip locally when
not needed with `pytest -m 'not anthropic'`.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from tename.harness import HarnessRuntime
from tename.router.service import ModelRouter
from tename.sessions import EventType, SessionService
from tename.sessions.exceptions import FailedPreconditionError

from .conftest import has_env

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.anthropic,
]


@pytest.mark.skipif(not has_env("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
async def test_real_e2e_single_turn(
    service: SessionService,
    agent_with_prompt: UUID,
) -> None:
    """Live test: hello-world prompt round-trips through the harness."""
    session = await service.create_session(agent_with_prompt)
    await service.emit_event(
        session.id,
        event_id=uuid4(),
        event_type=EventType.USER_MESSAGE,
        payload={"content": "What is 2+2? Answer with the number only."},
    )

    runtime = HarnessRuntime(session_service=service, model_router=ModelRouter())
    await runtime.run_session(session.id)

    events = await service.get_events(session.id)
    types = [e.type for e in events]
    assert EventType.USER_MESSAGE in types
    assert EventType.SYSTEM_EVENT in types  # agent_with_prompt has a system prompt

    incrementals = [
        e
        for e in events
        if e.type == EventType.ASSISTANT_MESSAGE and not e.payload.get("is_complete")
    ]
    finals = [
        e for e in events if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete")
    ]
    assert incrementals, "streaming should emit at least one incremental assistant_message"
    assert len(finals) == 1, "exactly one consolidated is_complete=True"
    assert "4" in finals[0].payload["content"]

    # Session should be terminal.
    with pytest.raises(FailedPreconditionError):
        await service.wake(session.id)
