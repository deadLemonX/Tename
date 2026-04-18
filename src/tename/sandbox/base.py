"""SandboxBackend ABC.

Every backend (Docker now, Firecracker / E2B / Modal later) implements
this async contract. The `Sandbox` service wraps a backend, owns the
state machine, and exposes the public API the harness actually calls.

Backends do NOT own lifecycle state; they just execute primitive
operations. Lifecycle bookkeeping lives in the `Sandbox` service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from tename.sandbox.types import SandboxRecipe, SandboxStatus, ToolResult


class SandboxBackend(ABC):
    """Pluggable backend contract.

    All methods are async so callers don't have to care whether the
    underlying implementation (Docker SDK, HTTP API, subprocess, ...)
    blocks. Docker's synchronous SDK is wrapped via `asyncio.to_thread`.
    """

    @abstractmethod
    async def provision(self, recipe: SandboxRecipe) -> str:
        """Create a sandbox from `recipe` and return its id.

        Blocks until the sandbox is ready to execute tools. Raises on
        unrecoverable provisioning failure; recoverable failures should
        be retried inside the backend.
        """

    @abstractmethod
    async def execute(self, sandbox_id: str, tool_name: str, input: dict[str, Any]) -> ToolResult:
        """Run a registered tool inside `sandbox_id` and return its result.

        Raises on infrastructure errors (sandbox missing, daemon down);
        tool-level failures (nonzero exit, timeouts, stderr) return a
        `ToolResult(is_error=True, ...)` — the model sees these as tool
        results, not as harness errors.
        """

    @abstractmethod
    async def destroy(self, sandbox_id: str) -> None:
        """Tear down `sandbox_id`. Idempotent — destroying an already-gone
        sandbox must not raise."""

    @abstractmethod
    async def status(self, sandbox_id: str) -> SandboxStatus:
        """Best-effort lifecycle lookup; missing sandboxes report as
        `DESTROYED` rather than raising."""


__all__ = ["SandboxBackend"]
