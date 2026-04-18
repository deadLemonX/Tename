"""Sandbox module public types.

Recipes describe how a sandbox should look. `ToolResult` is what every
built-in tool returns. `SandboxStatus` tracks a sandbox's lifecycle; the
valid transitions between states live in `state_machine.py`.

Credentials never enter a `SandboxRecipe` (see `docs/architecture/sandbox.md`).
Tool-proxy credential handoff lands in S10.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NetworkPolicy = Literal["open", "isolated", "allowlist"]


class SandboxStatus(StrEnum):
    """Lifecycle state of a single sandbox.

    See `docs/architecture/sandbox.md` for the full state-machine doc.
    """

    PROVISIONING = "provisioning"
    READY = "ready"
    RUNNING = "running"
    IDLE = "idle"
    DESTROYED = "destroyed"
    ERROR = "error"


class SandboxRecipe(BaseModel):
    """Description of a sandbox to provision.

    Credentials are intentionally absent; the tool proxy (S10) injects
    them out-of-band. `files` is a path→content mapping written into the
    container at provision time (before the first tool call).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime: str = "python:3.12-slim"
    """Docker image tag. v0.1 defaults to python:3.12-slim so every
    built-in tool (bash, python, file_*) works out of the box."""

    packages: list[str] = Field(default_factory=list)
    """pip packages to install inside the sandbox after provision.
    Honored only when `runtime` starts with `python:`; ignored otherwise
    for v0.1 to avoid a shelling-out rabbit hole across runtimes."""

    files: dict[str, str] = Field(default_factory=dict)
    """Absolute path → file content to write at provision time."""

    env: dict[str, str] = Field(default_factory=dict)
    """Non-secret environment variables for the container. Credentials
    never go here (principle #5)."""

    cpu_limit: int = Field(default=2, gt=0)
    """Max CPU cores enforced via docker --cpus."""

    memory_limit_mb: int = Field(default=4096, gt=0)
    """Max memory (MiB) enforced via docker --memory."""

    timeout_seconds: int = Field(default=600, gt=0)
    """Per-tool-call timeout. The harness caps any single `execute()`
    at this many seconds; on timeout the container is killed and the
    sandbox transitions to ERROR."""

    network_policy: NetworkPolicy = "open"
    """`open` is the only policy implemented in v0.1; `isolated` and
    `allowlist` are accepted but not enforced, to avoid schema churn
    when they land later."""


class ToolResult(BaseModel):
    """Return value from a single sandbox tool invocation.

    `content` is the tool_result block text the model sees; `stdout` /
    `stderr` / `exit_code` are kept for observability (session log,
    human debugging). `is_error=True` flips the tool_result event's
    `is_error` flag so the model sees the failure mode in context.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_error: bool = False
    content: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: str | None = None


__all__ = [
    "NetworkPolicy",
    "SandboxRecipe",
    "SandboxStatus",
    "ToolResult",
]
