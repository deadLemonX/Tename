"""HarnessRuntime skeleton.

S6 ships only the constructor and a stubbed `run_session` that raises
`NotImplementedError`. The core loop — profile lookup, event replay,
streaming model call, tool routing, compaction — lands in S7. The
constructor signature is already what S7 will consume, so dependent
modules can wire it up now.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tename.harness.profiles import ProfileLoader

if TYPE_CHECKING:
    from tename.router.service import ModelRouter
    from tename.sessions.service import SessionService

logger = logging.getLogger(__name__)


class HarnessRuntime:
    """Stateless brain loop runner.

    The harness holds no state across `run_session` calls. Every piece of
    durable state lives in the Session Service; every model-specific knob
    lives in the `Profile`. S7 fills in the loop.

    Args:
        session_service: The Session Service instance backing event
            durability and sequence assignment.
        model_router: The Model Router used for streaming completions.
        sandbox: Pluggable sandbox handle for code-execution tools. Real
            type arrives in S9; typed as `Any` for now so callers can
            pass `None` in the skeleton.
        tool_proxy: Pluggable tool-proxy handle for external-API tools.
            Real type arrives in S10.
        profile_loader: Optional custom loader. Defaults to one that
            reads the bundled `tename.profiles` package.
    """

    def __init__(
        self,
        session_service: SessionService,
        model_router: ModelRouter,
        sandbox: Any | None = None,
        tool_proxy: Any | None = None,
        *,
        profile_loader: ProfileLoader | None = None,
    ) -> None:
        self._session_service = session_service
        self._model_router = model_router
        self._sandbox = sandbox
        self._tool_proxy = tool_proxy
        self._profile_loader = profile_loader or ProfileLoader()

    async def run_session(self, session_id: UUID) -> None:
        """Drive the agent loop to completion. Implemented in S7."""
        raise NotImplementedError(
            "HarnessRuntime.run_session lands in S7 (Harness core loop). "
            f"Called with session_id={session_id}."
        )


__all__ = ["HarnessRuntime"]
