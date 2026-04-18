"""Sandbox service: public API consumed by the harness.

Owns the lifecycle state machine for every provisioned sandbox and
delegates the actual work to a `SandboxBackend`. The harness treats
this as the single entry point — it never talks to a backend directly.

Single-process, in-memory state is deliberate: v0.1 runs harness and
sandbox in the same process (see CLAUDE.md). If a commercial version
splits them, this class grows a persistence layer; the contract with
the harness does not change.
"""

from __future__ import annotations

import logging
from typing import Any

from tename.sandbox.base import SandboxBackend
from tename.sandbox.state_machine import assert_transition
from tename.sandbox.types import SandboxRecipe, SandboxStatus, ToolResult

logger = logging.getLogger(__name__)


class Sandbox:
    """Public sandbox facade.

    Construct with an already-built backend — `DockerBackend` is the
    only implementation in v0.1. Callers never see backend-level
    primitives; all interaction is `provision → execute+ → destroy`.
    """

    def __init__(self, backend: SandboxBackend) -> None:
        self._backend = backend
        self._status: dict[str, SandboxStatus] = {}

    async def provision(self, recipe: SandboxRecipe) -> str:
        """Provision a sandbox from `recipe`; return its id."""
        # We can't assert_transition(PROVISIONING -> READY) without an
        # id, so we bookkeep the initial state after provision returns.
        logger.info("sandbox.provision.start", extra={"runtime": recipe.runtime})
        try:
            sandbox_id = await self._backend.provision(recipe)
        except Exception:
            logger.exception("sandbox.provision.fail")
            raise
        self._status[sandbox_id] = SandboxStatus.READY
        logger.info(
            "sandbox.provision.ok",
            extra={"sandbox_id": sandbox_id, "runtime": recipe.runtime},
        )
        return sandbox_id

    async def execute(self, sandbox_id: str, tool_name: str, input: dict[str, Any]) -> ToolResult:
        """Run a tool inside `sandbox_id`; update lifecycle state."""
        current = self._status.get(sandbox_id, SandboxStatus.READY)
        assert_transition(current, SandboxStatus.RUNNING, sandbox_id=sandbox_id)
        self._status[sandbox_id] = SandboxStatus.RUNNING
        try:
            result = await self._backend.execute(sandbox_id, tool_name, input)
        except Exception:
            logger.exception(
                "sandbox.execute.fail",
                extra={"sandbox_id": sandbox_id, "tool": tool_name},
            )
            self._transition(sandbox_id, SandboxStatus.ERROR)
            raise

        # On timeout the backend already killed the container; reflect
        # that here so the next execute won't pretend the sandbox is live.
        if result.is_error and result.error and "timeout" in result.error.lower():
            self._transition(sandbox_id, SandboxStatus.ERROR)
        else:
            self._transition(sandbox_id, SandboxStatus.IDLE)
        return result

    async def destroy(self, sandbox_id: str) -> None:
        """Tear down `sandbox_id`. Idempotent."""
        current = self._status.get(sandbox_id)
        if current is None:
            # Already destroyed (or never tracked) — keep behavior idempotent.
            await self._backend.destroy(sandbox_id)
            return
        assert_transition(current, SandboxStatus.DESTROYED, sandbox_id=sandbox_id)
        await self._backend.destroy(sandbox_id)
        self._status[sandbox_id] = SandboxStatus.DESTROYED

    async def status(self, sandbox_id: str) -> SandboxStatus:
        """Return the sandbox's current lifecycle state.

        Falls back to the backend's view (e.g. container gone / crashed)
        when the in-memory tracker doesn't have a record or when the
        backend reports DESTROYED / ERROR — that way the harness sees a
        crash-destroyed sandbox and can re-provision.
        """
        tracked = self._status.get(sandbox_id)
        backend_view = await self._backend.status(sandbox_id)
        if tracked is None:
            return backend_view
        # If the backend says the sandbox is gone/errored, trust it.
        if backend_view in {SandboxStatus.DESTROYED, SandboxStatus.ERROR}:
            self._status[sandbox_id] = backend_view
            return backend_view
        return tracked

    # ---- Internal ----------------------------------------------------------

    def _transition(self, sandbox_id: str, nxt: SandboxStatus) -> None:
        current = self._status.get(sandbox_id, SandboxStatus.READY)
        assert_transition(current, nxt, sandbox_id=sandbox_id)
        self._status[sandbox_id] = nxt


__all__ = ["Sandbox"]
