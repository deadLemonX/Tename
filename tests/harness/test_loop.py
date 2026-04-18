"""Harness core loop: integration tests.

These drive `HarnessRuntime.run_session` against a real `SessionService`
backed by the test Postgres (via `TENAME_TEST_DATABASE_URL`) and a
scripted `FakeModelRouter`. They verify that:

- A single-turn conversation produces the expected event sequence and
  the session ends COMPLETED.
- Streaming emits one incremental `assistant_message(is_complete=False)`
  per `text_delta` chunk, followed by a consolidated
  `assistant_message(is_complete=True)`.
- The system prompt is seeded once; a replay doesn't double-emit.
- Tool calls produce stubbed `tool_result(is_error=True)` events.
- `max_turns` caps a runaway agent.
- An error chunk terminates the run cleanly.
- A mid-stream crash leaves the log consistent and a fresh runtime
  resumes to completion without duplicate IDs or sequence gaps.
- Compaction triggers when the profile's threshold is exceeded.

Each test constructs its own `HarnessRuntime` wired to the shared
`SessionService` fixture and a per-test `FakeModelRouter`.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tename.harness import HarnessRuntime, ProfileLoader
from tename.harness.service import SYSTEM_PROMPT_UUID_NAMESPACE
from tename.router.service import ModelRouter
from tename.router.types import (
    ModelChunk,
    done_chunk,
    error_chunk,
    text_delta,
    tool_call_end,
    tool_call_start,
    usage_chunk,
)
from tename.router.types import (
    Usage as RouterUsage,
)
from tename.sessions import EventType, SessionService, SessionStatus
from tename.sessions.exceptions import FailedPreconditionError

from .conftest import FakeModelRouter

pytestmark = pytest.mark.postgres


# ---- Helpers ---------------------------------------------------------------


async def _seed_user_message(
    service: SessionService,
    session_id: UUID,
    content: str = "Hello",
) -> None:
    await service.emit_event(
        session_id,
        event_id=uuid4(),
        event_type=EventType.USER_MESSAGE,
        payload={"content": content},
    )


def _harness(
    service: SessionService,
    router: FakeModelRouter,
    *,
    profile_loader: ProfileLoader | None = None,
) -> HarnessRuntime:
    return HarnessRuntime(
        session_service=service,
        model_router=cast(ModelRouter, router),
        profile_loader=profile_loader,
    )


# ---- Tests -----------------------------------------------------------------


async def test_single_turn_completes(
    service: SessionService,
    agent_no_prompt: UUID,
) -> None:
    """User message → streamed text → consolidated assistant → session COMPLETED."""
    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "What is 2+2?")

    router = FakeModelRouter(
        [
            [
                text_delta("4"),
                text_delta("."),
                usage_chunk(RouterUsage(input_tokens=5, output_tokens=2)),
                done_chunk(),
            ]
        ]
    )
    await _harness(service, router).run_session(session.id)

    events = await service.get_events(session.id)
    types = [e.type for e in events]
    assert EventType.USER_MESSAGE in types
    incrementals = [
        e
        for e in events
        if e.type == EventType.ASSISTANT_MESSAGE and not e.payload.get("is_complete")
    ]
    finals = [
        e for e in events if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete")
    ]
    assert len(incrementals) == 2, "one assistant_message per text_delta"
    assert len(finals) == 1, "one consolidated is_complete=True"
    assert finals[0].payload["content"] == "4."
    assert "usage" in finals[0].payload

    # Session should be terminal now; wake() raises on terminal state.
    with pytest.raises(FailedPreconditionError):
        await service.wake(session.id)


async def test_streaming_emission_preserves_order(
    service: SessionService,
    agent_no_prompt: UUID,
) -> None:
    """5 text_deltas produce 5 incremental events in order, then 1 closer."""
    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "Go")

    chunks = [text_delta(f"part{i}") for i in range(5)]
    router = FakeModelRouter(
        [
            [
                *chunks,
                usage_chunk(RouterUsage(input_tokens=1, output_tokens=5)),
                done_chunk(),
            ]
        ]
    )
    await _harness(service, router).run_session(session.id)

    events = await service.get_events(session.id)
    incrementals = [
        e
        for e in events
        if e.type == EventType.ASSISTANT_MESSAGE and not e.payload.get("is_complete")
    ]
    assert [e.payload["content"] for e in incrementals] == [f"part{i}" for i in range(5)]
    finals = [
        e for e in events if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete")
    ]
    assert len(finals) == 1
    assert finals[0].payload["content"] == "part0part1part2part3part4"


async def test_system_prompt_seeded_once(
    service: SessionService,
    agent_with_prompt: UUID,
) -> None:
    """First wake emits the system prompt; a second run on the same session does not re-emit."""
    session = await service.create_session(agent_with_prompt)
    await _seed_user_message(service, session.id, "hello")

    router1 = FakeModelRouter([[text_delta("hi"), done_chunk()]])
    await _harness(service, router1).run_session(session.id)

    # First run completes — emit a new user message and wake a new session
    # to drive a second run. The first session is now COMPLETED, so we
    # instead verify the system_event idempotency at the event level.
    events = await service.get_events(session.id)
    system_events = [e for e in events if e.type == EventType.SYSTEM_EVENT]
    assert len(system_events) == 1
    assert system_events[0].payload == {
        "type": "system_prompt",
        "content": "You are a helpful assistant.",
    }

    # The system-prompt event id is deterministic (uuid5). A retry of the
    # seed would collapse into this row via the service's idempotency on
    # (session_id, event_id).
    deterministic_id = uuid5(NAMESPACE_URL, f"{SYSTEM_PROMPT_UUID_NAMESPACE}:{session.id}")
    assert system_events[0].id == deterministic_id


async def test_no_system_prompt_means_no_system_event(
    service: SessionService,
    agent_no_prompt: UUID,
) -> None:
    """Agents without a system_prompt do not emit a system_event."""
    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "hi")

    router = FakeModelRouter([[text_delta("ok"), done_chunk()]])
    await _harness(service, router).run_session(session.id)

    events = await service.get_events(session.id)
    assert not [e for e in events if e.type == EventType.SYSTEM_EVENT]


async def test_tool_call_produces_stubbed_tool_result(
    service: SessionService,
    agent_no_prompt: UUID,
) -> None:
    """A tool_call event from the model gets a stub tool_result with is_error=True."""
    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "run something")

    router = FakeModelRouter(
        [
            # Turn 1: model requests a tool call.
            [
                tool_call_start(tool_id="call_1", tool_name="python", index=0),
                tool_call_end(
                    tool_id="call_1",
                    tool_name="python",
                    tool_input={"code": "print('x')"},
                    index=0,
                ),
                done_chunk(),
            ],
            # Turn 2: model sees the error and wraps up (no tool call).
            [text_delta("Sorry, tool failed."), done_chunk()],
        ]
    )
    await _harness(service, router).run_session(session.id)

    events = await service.get_events(session.id)
    tool_calls = [e for e in events if e.type == EventType.TOOL_CALL]
    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(tool_calls) == 1
    assert len(tool_results) == 1
    assert tool_results[0].payload["is_error"] is True
    assert "not yet implemented" in tool_results[0].payload["error"]
    assert tool_results[0].payload["tool_call_id"] == str(tool_calls[0].id)


async def test_max_turns_caps_runaway_agent(
    service: SessionService,
    engine: AsyncEngine,
    agent_no_prompt: UUID,
    tmp_path: Path,
) -> None:
    """A model that keeps calling tools is stopped at max_turns."""
    # Build a profile with very small max_turns so the test runs fast,
    # and point the agent at that profile so the harness loads it.
    profile_loader, _ = await _install_override_profile(
        engine,
        tmp_path,
        agent_id=agent_no_prompt,
        overrides={"stop_conditions": {"max_turns": 3, "no_tool_calls_for": 1}},
    )

    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "loop forever")

    # Each turn emits a tool_use; the harness stubs the result and loops.
    def tool_turn(i: int) -> list[ModelChunk]:
        return [
            tool_call_start(tool_id=f"c{i}", tool_name="python", index=0),
            tool_call_end(tool_id=f"c{i}", tool_name="python", tool_input={"i": i}, index=0),
            done_chunk(),
        ]

    router = FakeModelRouter([tool_turn(i) for i in range(10)])
    await _harness(service, router, profile_loader=profile_loader).run_session(session.id)

    # Exactly 3 router calls == 3 turns executed.
    assert len(router.calls) == 3
    events = await service.get_events(session.id)
    assert len([e for e in events if e.type == EventType.TOOL_CALL]) == 3


async def test_error_chunk_terminates(
    service: SessionService,
    agent_no_prompt: UUID,
) -> None:
    """An error chunk emits an error event and ends the run."""
    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "hi")

    router = FakeModelRouter(
        [
            [
                text_delta("partial"),
                error_chunk(message="boom", retryable=False, status_code=500),
                done_chunk(),
            ]
        ]
    )
    await _harness(service, router).run_session(session.id)

    events = await service.get_events(session.id)
    errors = [e for e in events if e.type == EventType.ERROR]
    assert len(errors) == 1
    # After an error we do NOT emit a consolidated is_complete=True event.
    finals = [
        e for e in events if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete")
    ]
    assert finals == []
    # And only one router call was made — no retry loop.
    assert len(router.calls) == 1


async def test_stop_on_no_tool_calls(
    service: SessionService,
    agent_no_prompt: UUID,
) -> None:
    """A turn with no tool calls stops the loop (no_tool_calls_for=1)."""
    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "hi")

    router = FakeModelRouter(
        [[text_delta("done"), done_chunk()], [text_delta("unused"), done_chunk()]]
    )
    await _harness(service, router).run_session(session.id)
    assert len(router.calls) == 1


async def test_crash_mid_stream_resumes_cleanly(
    service: SessionService,
    agent_no_prompt: UUID,
) -> None:
    """A mid-stream crash leaves the log consistent; a new runtime completes the session."""
    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "go")

    class _Boom(RuntimeError):
        pass

    router1 = FakeModelRouter(
        [
            [text_delta("pa"), text_delta("rt"), text_delta("ial"), done_chunk()],
        ],
        raise_on_turn=0,
        raise_after_n_chunks=2,
        raise_exc=_Boom("stream killed"),
    )
    with pytest.raises(_Boom):
        await _harness(service, router1).run_session(session.id)

    # Session should still be ACTIVE (mark_complete was not reached).
    refreshed = await service.wake(session.id)
    assert refreshed.status == SessionStatus.ACTIVE

    # Fresh runtime on the same session_id with a new scripted response.
    router2 = FakeModelRouter([[text_delta("resumed"), done_chunk()]])
    await _harness(service, router2).run_session(session.id)

    events = await service.get_events(session.id)
    # All sequences are contiguous starting at 1.
    sequences = [e.sequence for e in events]
    assert sequences == list(range(1, len(events) + 1))
    # Event ids are unique — no idempotency collisions corrupted the log.
    assert len({e.id for e in events}) == len(events)
    # A final consolidated assistant from run2 exists.
    finals = [
        e for e in events if e.type == EventType.ASSISTANT_MESSAGE and e.payload.get("is_complete")
    ]
    assert len(finals) == 1
    assert finals[0].payload["content"] == "resumed"

    with pytest.raises(FailedPreconditionError):
        await service.wake(session.id)


async def test_compaction_triggers_when_over_threshold(
    service: SessionService,
    engine: AsyncEngine,
    agent_no_prompt: UUID,
    tmp_path: Path,
) -> None:
    """A low-threshold profile + a fat user message triggers a compaction event."""
    profile_loader, _ = await _install_override_profile(
        engine,
        tmp_path,
        agent_id=agent_no_prompt,
        overrides={
            "context": {
                "max_tokens": 200_000,
                "effective_budget": 160_000,
                "compaction_threshold": 20,
                "compaction_strategy": "truncate",
                "keep_last_n_events": 1,
            },
        },
    )

    session = await service.create_session(agent_no_prompt)
    # Seed enough user messages that tokens(events) >> 20.
    for i in range(4):
        await _seed_user_message(service, session.id, "x" * 200 + f" msg{i}")

    router = FakeModelRouter([[text_delta("ok"), done_chunk()]])
    await _harness(service, router, profile_loader=profile_loader).run_session(session.id)

    events = await service.get_events(session.id)
    compaction_events = [
        e
        for e in events
        if e.type == EventType.HARNESS_EVENT and e.payload.get("type") == "compaction"
    ]
    assert len(compaction_events) >= 1
    payload = compaction_events[0].payload
    assert payload["strategy"] == "truncate"
    assert payload["estimated_tokens_before"] > payload["estimated_tokens_after"]
    # First user_message is anchored; at least one earlier message was dropped.
    assert len(payload["dropped_sequences"]) >= 1


# ---- Local helpers ---------------------------------------------------------


async def _install_override_profile(
    engine: AsyncEngine,
    tmp_path: Path,
    *,
    agent_id: UUID,
    overrides: dict[str, object],
    base_name: str = "claude-opus-4-6",
) -> tuple[ProfileLoader, str]:
    """Write an override profile on disk and point `agent_id` at it.

    The override profile uses a fresh name (the agent's uuid) so it
    cannot collide with the base profile during `extends` resolution.
    Returns the loader plus the new model name; the test must pass the
    loader to `HarnessRuntime`.
    """
    model_name = f"test-profile-{agent_id}"
    doc: dict[str, object] = {"extends": base_name, **overrides}
    (tmp_path / f"{model_name}.yaml").write_text(yaml.safe_dump(doc))

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE agents SET model = :model WHERE id = :id"),
            {"model": model_name, "id": str(agent_id)},
        )

    return ProfileLoader(search_paths=[tmp_path]), model_name
