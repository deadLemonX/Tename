"""Harness ↔ Sandbox integration tests.

These drive `HarnessRuntime.run_session` with a real `Sandbox` (Docker
backend) and a scripted `FakeModelRouter`. Verifies the S9 wiring:

- Sandbox tools execute for real and their output lands in
  `tool_result` events.
- A sandbox is provisioned lazily — only on first sandbox-tool call —
  and a `system_event(type='sandbox_provisioned')` records its id.
- Subsequent sandbox-tool calls in the same session reuse the same id
  (filesystem state persists within a session).
- The sandbox is destroyed when the session ends.

All tests carry both markers (`postgres` + `sandbox`) and self-skip
when either dependency is missing.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

import pytest

from tename.harness import HarnessRuntime
from tename.router.service import ModelRouter
from tename.router.types import done_chunk, text_delta, tool_call_end, tool_call_start
from tename.sandbox import DockerBackend, Sandbox, SandboxStatus
from tename.sessions import EventType, SessionService

from .conftest import FakeModelRouter

pytestmark = [pytest.mark.postgres, pytest.mark.sandbox]


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


async def _seed_user_message(
    service: SessionService, session_id: UUID, content: str = "go"
) -> None:
    await service.emit_event(
        session_id,
        event_id=uuid4(),
        event_type=EventType.USER_MESSAGE,
        payload={"content": content},
    )


def _harness(service: SessionService, router: FakeModelRouter, sandbox: Sandbox) -> HarnessRuntime:
    return HarnessRuntime(
        session_service=service,
        model_router=cast(ModelRouter, router),
        sandbox=sandbox,
    )


async def test_python_tool_routes_to_sandbox(
    service: SessionService,
    agent_no_prompt: UUID,
) -> None:
    """Agent calls python → sandbox provisions → code runs → real result lands."""
    sandbox = Sandbox(DockerBackend())
    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "compute 2+2")

    router = FakeModelRouter(
        [
            [
                tool_call_start(tool_id="call_1", tool_name="python", index=0),
                tool_call_end(
                    tool_id="call_1",
                    tool_name="python",
                    tool_input={"code": "print(2 + 2)"},
                    index=0,
                ),
                done_chunk(),
            ],
            [text_delta("Answer: 4"), done_chunk()],
        ]
    )
    await _harness(service, router, sandbox).run_session(session.id)

    events = await service.get_events(session.id)
    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(tool_results) == 1
    payload = tool_results[0].payload
    assert payload["is_error"] is False
    assert "4" in payload["content"]
    assert payload["tool_name"] == "python"
    assert "sandbox_id" in payload

    # One system_event recording the provisioned sandbox.
    provisioned = [
        e
        for e in events
        if e.type == EventType.SYSTEM_EVENT and e.payload.get("type") == "sandbox_provisioned"
    ]
    assert len(provisioned) == 1
    sandbox_id = provisioned[0].payload["sandbox_id"]
    assert sandbox_id == payload["sandbox_id"]

    # And at session end, the harness destroyed it.
    assert await sandbox.status(sandbox_id) == SandboxStatus.DESTROYED


async def test_sandbox_reused_across_tool_calls(
    service: SessionService,
    agent_no_prompt: UUID,
) -> None:
    """Two sandbox tools in one session share a single sandbox (filesystem persists)."""
    sandbox = Sandbox(DockerBackend())
    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "write then read")

    router = FakeModelRouter(
        [
            # Turn 1: write a file.
            [
                tool_call_start(tool_id="w", tool_name="file_write", index=0),
                tool_call_end(
                    tool_id="w",
                    tool_name="file_write",
                    tool_input={"path": "/workspace/note.txt", "content": "remembered"},
                    index=0,
                ),
                done_chunk(),
            ],
            # Turn 2: read it back.
            [
                tool_call_start(tool_id="r", tool_name="file_read", index=0),
                tool_call_end(
                    tool_id="r",
                    tool_name="file_read",
                    tool_input={"path": "/workspace/note.txt"},
                    index=0,
                ),
                done_chunk(),
            ],
            # Turn 3: wrap up.
            [text_delta("done."), done_chunk()],
        ]
    )
    await _harness(service, router, sandbox).run_session(session.id)

    events = await service.get_events(session.id)
    provisioned = [
        e
        for e in events
        if e.type == EventType.SYSTEM_EVENT and e.payload.get("type") == "sandbox_provisioned"
    ]
    # Only one sandbox across the whole session.
    assert len(provisioned) == 1

    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(tool_results) == 2
    # The read sees what the write left behind — proof of shared filesystem.
    read_result = next(r for r in tool_results if r.payload["tool_name"] == "file_read")
    assert read_result.payload["is_error"] is False
    assert read_result.payload["content"] == "remembered"


async def test_unknown_tool_still_stubs_when_sandbox_wired(
    service: SessionService,
    agent_no_prompt: UUID,
) -> None:
    """Non-sandbox tools stay stubbed even when a Sandbox is configured."""
    sandbox = Sandbox(DockerBackend())
    session = await service.create_session(agent_no_prompt)
    await _seed_user_message(service, session.id, "search")

    router = FakeModelRouter(
        [
            [
                tool_call_start(tool_id="s", tool_name="web_search", index=0),
                tool_call_end(
                    tool_id="s",
                    tool_name="web_search",
                    tool_input={"q": "x"},
                    index=0,
                ),
                done_chunk(),
            ],
            [text_delta("ok."), done_chunk()],
        ]
    )
    await _harness(service, router, sandbox).run_session(session.id)

    events = await service.get_events(session.id)
    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(tool_results) == 1
    assert tool_results[0].payload["is_error"] is True
    assert "proxy tools land in S10" in tool_results[0].payload["error"]

    # No sandbox was provisioned since no sandbox tool ran.
    provisioned = [
        e
        for e in events
        if e.type == EventType.SYSTEM_EVENT and e.payload.get("type") == "sandbox_provisioned"
    ]
    assert provisioned == []
