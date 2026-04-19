"""SDK surface for agent CRUD.

In v0.1 the Session Service doesn't expose an `Agent` CRUD API — the
harness reads agents with `get_agent`. To keep the SDK hello-world
usable, the SDK talks to the shared database directly through a tiny
query layer here. When S11 / commercial work introduces a proper
admin API this module is the single place we swap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tename.sandbox import SandboxRecipe
from tename.sdk.errors import NotFoundError, ValidationError
from tename.sessions.models import Agent


@dataclass(frozen=True)
class CreateAgentInput:
    """Structured args for `AgentsClient.create`."""

    name: str
    model: str
    system_prompt: str | None = None
    tools: tuple[str, ...] = ()
    framework: str = "vanilla"
    sandbox_recipe: SandboxRecipe | None = None


async def create_agent(engine: AsyncEngine, spec: CreateAgentInput) -> Agent:
    agent_uuid = uuid4()
    recipe_json: str | None = (
        json.dumps(spec.sandbox_recipe.model_dump()) if spec.sandbox_recipe else None
    )
    if not spec.name:
        raise ValidationError("agent name must be non-empty")
    if not spec.model:
        raise ValidationError("agent model must be non-empty")

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agents (id, name, model, framework, system_prompt, tools, sandbox_recipe) "
                "VALUES (:id, :name, :model, :framework, :system_prompt, :tools, "
                "CAST(:sandbox_recipe AS JSONB))"
            ),
            {
                "id": str(agent_uuid),
                "name": spec.name,
                "model": spec.model,
                "framework": spec.framework,
                "system_prompt": spec.system_prompt,
                "tools": json.dumps(list(spec.tools)),
                "sandbox_recipe": recipe_json,
            },
        )
    return await fetch_agent(engine, agent_uuid)


async def fetch_agent(engine: AsyncEngine, agent_id: UUID) -> Agent:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT * FROM agents WHERE id = :id"), {"id": str(agent_id)}
        )
        row = result.mappings().first()
    if row is None:
        raise NotFoundError(f"agent {agent_id} does not exist")
    return _row_to_agent(dict(row))


async def list_agents(engine: AsyncEngine) -> list[Agent]:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT * FROM agents ORDER BY created_at DESC"))
        rows = [dict(row) for row in result.mappings()]
    return [_row_to_agent(r) for r in rows]


async def delete_agent(engine: AsyncEngine, agent_id: UUID) -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM agents WHERE id = :id"), {"id": str(agent_id)}
        )
    if result.rowcount == 0:
        raise NotFoundError(f"agent {agent_id} does not exist")


def _row_to_agent(row: dict[str, Any]) -> Agent:
    return Agent(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        model=row["model"],
        framework=row["framework"],
        system_prompt=row["system_prompt"],
        tools=list(row["tools"]) if row["tools"] is not None else [],
        sandbox_recipe=dict(row["sandbox_recipe"]) if row["sandbox_recipe"] is not None else None,
        created_at=row["created_at"],
    )


__all__ = [
    "CreateAgentInput",
    "create_agent",
    "delete_agent",
    "fetch_agent",
    "list_agents",
]
