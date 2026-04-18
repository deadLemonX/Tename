"""Compaction helpers for the Harness Runtime.

Compaction is an append-only operation: it does NOT modify or delete prior
events. Instead, when the active context exceeds a profile's threshold, the
harness emits a single `harness_event` with `payload.type == "compaction"`
recording which sequences are dropped from the *active view*. Every
iteration after that filters the event list through `apply_compaction_view`
before handing it to the adapter's `build_context`.

v0.1 ships one strategy — `truncate` — which keeps:

1. The first `user_message` event (anchors the conversation);
2. The `keep_last_n_events` most recent events;
3. Every prior compaction event (so stacked compactions still resolve).

Token estimation is a character-count ÷ 4 approximation. It's intentionally
rough; the goal is to trigger compaction before real context overflow, and
a conservative estimate compacts slightly early rather than late.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from tename.harness.profiles import Profile
from tename.sessions.models import Event, EventType

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
"""Rough token estimator: 4 characters per token. Intentionally conservative."""


def estimate_event_tokens(events: Sequence[Event]) -> int:
    """Approximate token count for a list of events by payload character length."""
    total_chars = 0
    for event in events:
        try:
            total_chars += len(json.dumps(event.payload, ensure_ascii=False))
        except (TypeError, ValueError):
            # Non-serializable payload shouldn't happen in v0.1 (service
            # rejects them upstream), but fall back to a safe char count
            # so we never silently under-estimate.
            total_chars += len(str(event.payload))
    return max(1, total_chars // CHARS_PER_TOKEN)


def _is_compaction_event(event: Event) -> bool:
    return event.type == EventType.HARNESS_EVENT and event.payload.get("type") == "compaction"


def apply_compaction_view(events: Sequence[Event]) -> list[Event]:
    """Return the active view of `events` after honoring compaction records.

    Uses only the latest compaction record; earlier ones are superseded (their
    dropped sequences are either still dropped by the latest record or were
    kept for a reason that's no longer relevant).
    """
    latest: Event | None = None
    for event in events:
        if _is_compaction_event(event):
            latest = event
    if latest is None:
        return list(events)

    dropped_raw = latest.payload.get("dropped_sequences", [])
    dropped = {int(seq) for seq in dropped_raw if isinstance(seq, int)}
    return [e for e in events if e.sequence not in dropped]


@dataclass(frozen=True)
class CompactionDecision:
    """Plan for a single compaction pass."""

    dropped_sequences: list[int]
    kept_sequences: list[int]
    estimated_tokens_before: int
    estimated_tokens_after: int
    strategy: str = "truncate"

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "compaction",
            "strategy": self.strategy,
            "dropped_sequences": list(self.dropped_sequences),
            "kept_sequences": list(self.kept_sequences),
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
        }


def should_compact(active_events: Sequence[Event], profile: Profile) -> bool:
    """True when the active event view exceeds the profile's compaction threshold."""
    threshold = profile.context.resolved_compaction_threshold
    estimated = estimate_event_tokens(active_events)
    return estimated >= threshold


def plan_truncate(
    active_events: Sequence[Event],
    profile: Profile,
) -> CompactionDecision | None:
    """Compute a truncate-strategy compaction decision.

    Returns `None` if there is nothing to drop (the active view already
    satisfies "first user + last N + compaction summaries"). The harness
    uses this to avoid emitting empty compaction events.
    """
    keep_last_n = profile.context.keep_last_n_events

    first_user_seq: int | None = None
    for event in active_events:
        if event.type == EventType.USER_MESSAGE:
            first_user_seq = event.sequence
            break

    total = len(active_events)
    if total == 0:
        return None

    # Indices of events preserved regardless of age.
    keep_indices: set[int] = set()
    for idx, event in enumerate(active_events):
        if _is_compaction_event(event):
            keep_indices.add(idx)
        if first_user_seq is not None and event.sequence == first_user_seq:
            keep_indices.add(idx)
    # Tail window.
    tail_start = max(0, total - keep_last_n)
    for idx in range(tail_start, total):
        keep_indices.add(idx)

    kept = [active_events[i] for i in sorted(keep_indices)]
    dropped = [e for idx, e in enumerate(active_events) if idx not in keep_indices]

    if not dropped:
        return None

    return CompactionDecision(
        dropped_sequences=[e.sequence for e in dropped],
        kept_sequences=[e.sequence for e in kept],
        estimated_tokens_before=estimate_event_tokens(active_events),
        estimated_tokens_after=estimate_event_tokens(kept),
    )


__all__ = [
    "CHARS_PER_TOKEN",
    "CompactionDecision",
    "apply_compaction_view",
    "estimate_event_tokens",
    "plan_truncate",
    "should_compact",
]
