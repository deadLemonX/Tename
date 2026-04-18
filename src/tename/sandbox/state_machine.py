"""Sandbox lifecycle state machine.

Transition rules mirror the diagram in `docs/architecture/sandbox.md`.
Every transition is logged; invalid transitions raise
`InvalidTransitionError` rather than silently corrupting state. The
`Sandbox` service (not the backend) enforces these transitions.
"""

from __future__ import annotations

import logging

from tename.sandbox.types import SandboxStatus

logger = logging.getLogger(__name__)


class InvalidTransitionError(RuntimeError):
    """Raised when code attempts a sandbox state transition that is not
    allowed by the state diagram."""


# Allowed transitions: `current` -> set of permitted `next` states.
# DESTROYED is terminal; nothing is permitted out of it.
ALLOWED_TRANSITIONS: dict[SandboxStatus, frozenset[SandboxStatus]] = {
    SandboxStatus.PROVISIONING: frozenset(
        {SandboxStatus.READY, SandboxStatus.ERROR, SandboxStatus.DESTROYED}
    ),
    SandboxStatus.READY: frozenset(
        {SandboxStatus.RUNNING, SandboxStatus.DESTROYED, SandboxStatus.ERROR}
    ),
    SandboxStatus.RUNNING: frozenset(
        {SandboxStatus.IDLE, SandboxStatus.ERROR, SandboxStatus.DESTROYED}
    ),
    SandboxStatus.IDLE: frozenset(
        {SandboxStatus.RUNNING, SandboxStatus.DESTROYED, SandboxStatus.ERROR}
    ),
    SandboxStatus.ERROR: frozenset({SandboxStatus.DESTROYED}),
    SandboxStatus.DESTROYED: frozenset(),
}


def assert_transition(
    current: SandboxStatus, nxt: SandboxStatus, *, sandbox_id: str | None = None
) -> None:
    """Raise `InvalidTransitionError` unless `current → nxt` is allowed.

    Logs at INFO on success, WARNING on failure. `sandbox_id` is optional
    for better log messages when one is available; the state machine is
    id-free by itself so tests can exercise it without a real sandbox.
    """
    if nxt == current:
        # No-op: state machines can be polled for the same state without
        # re-logging every time.
        return
    permitted = ALLOWED_TRANSITIONS.get(current, frozenset())
    if nxt not in permitted:
        logger.warning(
            "sandbox.state.invalid_transition",
            extra={
                "sandbox_id": sandbox_id,
                "from": current.value,
                "to": nxt.value,
                "permitted": sorted(p.value for p in permitted),
            },
        )
        raise InvalidTransitionError(
            f"sandbox{f' {sandbox_id}' if sandbox_id else ''}: "
            f"cannot transition {current.value} -> {nxt.value}; "
            f"allowed: {sorted(p.value for p in permitted)}"
        )
    logger.info(
        "sandbox.state.transition",
        extra={
            "sandbox_id": sandbox_id,
            "from": current.value,
            "to": nxt.value,
        },
    )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "InvalidTransitionError",
    "assert_transition",
]
