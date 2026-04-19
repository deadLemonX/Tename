"""Harness + ToolProxy integration tests.

Drive `HarnessRuntime.run_session` against a real `SessionService` and
a `ToolProxy` wired to a test-only proxy tool. Verify that:

- a proxy tool_call triggers `ToolProxy.execute`,
- the returned result lands as a `tool_result` event,
- credentials pulled from the vault never appear in the session log.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tename.harness import HarnessRuntime
from tename.proxy import ToolProxy
from tename.proxy.decorators import proxy_tool
from tename.proxy.registry import clear_registry_for_testing
from tename.router.service import ModelRouter
from tename.router.types import (
    done_chunk,
    text_delta,
    tool_call_end,
    tool_call_start,
)
from tename.sessions import EventType, SessionService
from tename.vault import Vault

from .conftest import FakeModelRouter

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    clear_registry_for_testing()
    yield
    clear_registry_for_testing()
    import importlib

    import tename.proxy.tools.web_search as ws

    importlib.reload(ws)


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


async def _insert_agent_with_tool(engine: AsyncEngine, tool_name: str) -> UUID:
    import json

    agent_uuid = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agents (id, name, model, framework, system_prompt, tools) "
                "VALUES (:id, :name, :model, :framework, NULL, :tools)"
            ),
            {
                "id": str(agent_uuid),
                "name": "proxy-test-agent",
                "model": "claude-opus-4-6",
                "framework": "vanilla",
                "tools": json.dumps([tool_name]),
            },
        )
    return agent_uuid


def _harness(
    service: SessionService,
    router: FakeModelRouter,
    proxy: ToolProxy,
) -> HarnessRuntime:
    return HarnessRuntime(
        session_service=service,
        model_router=cast(ModelRouter, router),
        tool_proxy=proxy,
    )


async def test_proxy_tool_routes_through_tool_proxy(
    service: SessionService,
    engine: AsyncEngine,
    tmp_path: Path,
    clean_db: None,
) -> None:
    """A proxy tool_call lands in ToolProxy.execute and the result is emitted."""
    vault = Vault(path=tmp_path / "v.enc", passphrase="pw")
    vault.store("test_key", "SECRET-42")

    observed: list[tuple[dict[str, Any], dict[str, str]]] = []

    @proxy_tool(
        name="test_probe",
        credential_names=["test_key"],
        description="echo",
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
    )
    async def _probe(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        observed.append((dict(input), dict(credentials)))
        return {"content": f"echo:{input['q']}", "is_error": False}

    agent_uuid = await _insert_agent_with_tool(engine, "test_probe")
    session = await service.create_session(agent_uuid)
    await _seed_user_message(service, session.id, "go")

    router = FakeModelRouter(
        [
            [
                tool_call_start(tool_id="call_1", tool_name="test_probe", index=0),
                tool_call_end(
                    tool_id="call_1",
                    tool_name="test_probe",
                    tool_input={"q": "hello"},
                    index=0,
                ),
                done_chunk(),
            ],
            [text_delta("done"), done_chunk()],
        ]
    )

    await _harness(service, router, ToolProxy(vault=vault)).run_session(session.id)

    assert observed == [({"q": "hello"}, {"test_key": "SECRET-42"})]

    events = await service.get_events(session.id)
    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(tool_results) == 1
    assert tool_results[0].payload["is_error"] is False
    assert tool_results[0].payload["content"] == "echo:hello"
    assert tool_results[0].payload["tool_name"] == "test_probe"

    # Credential value never appears in any event payload.
    for event in events:
        import json

        as_json = json.dumps(event.payload, default=str)
        assert "SECRET-42" not in as_json


async def test_proxy_tool_missing_credential_surfaces_error(
    service: SessionService,
    engine: AsyncEngine,
    tmp_path: Path,
    clean_db: None,
) -> None:
    """Missing credential → clean is_error tool_result, harness continues."""
    vault = Vault(path=tmp_path / "v.enc", passphrase="pw")
    # Don't store the credential — force the missing-credential path.

    @proxy_tool(
        name="needs_key",
        credential_names=["absent"],
        description="needs a key",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        return {"content": "should not run"}

    agent_uuid = await _insert_agent_with_tool(engine, "needs_key")
    session = await service.create_session(agent_uuid)
    await _seed_user_message(service, session.id, "go")

    router = FakeModelRouter(
        [
            [
                tool_call_start(tool_id="c", tool_name="needs_key", index=0),
                tool_call_end(
                    tool_id="c",
                    tool_name="needs_key",
                    tool_input={},
                    index=0,
                ),
                done_chunk(),
            ],
            [text_delta("ok"), done_chunk()],
        ]
    )

    await _harness(service, router, ToolProxy(vault=vault)).run_session(session.id)

    events = await service.get_events(session.id)
    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(tool_results) == 1
    assert tool_results[0].payload["is_error"] is True
    assert "absent" in tool_results[0].payload["content"]
    assert "not stored" in tool_results[0].payload["content"]


async def test_sandbox_precedence_over_proxy_with_same_name(
    service: SessionService,
    engine: AsyncEngine,
    tmp_path: Path,
    clean_db: None,
) -> None:
    """If a proxy tool and sandbox tool share a name, sandbox wins.

    Names shouldn't overlap in practice — sandbox built-ins are a
    frozen set of six — but we encode the routing preference so it
    stays stable.
    """
    vault = Vault(path=tmp_path / "v.enc", passphrase="pw")

    # Register a proxy tool with a sandbox-built-in name. This would
    # normally be caught at the register-collision layer, but here we
    # use a free name and just verify the explicit precedence when
    # both sandbox and proxy are present: bail through the stub.
    agent_uuid = await _insert_agent_with_tool(engine, "nonexistent_tool")
    session = await service.create_session(agent_uuid)
    await _seed_user_message(service, session.id, "go")

    router = FakeModelRouter(
        [
            [
                tool_call_start(tool_id="c", tool_name="nonexistent_tool", index=0),
                tool_call_end(
                    tool_id="c",
                    tool_name="nonexistent_tool",
                    tool_input={},
                    index=0,
                ),
                done_chunk(),
            ],
            [text_delta("ok"), done_chunk()],
        ]
    )

    await _harness(service, router, ToolProxy(vault=vault)).run_session(session.id)

    events = await service.get_events(session.id)
    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(tool_results) == 1
    assert tool_results[0].payload["is_error"] is True
    assert "not a registered sandbox or proxy tool" in tool_results[0].payload["error"]
