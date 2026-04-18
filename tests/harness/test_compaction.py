"""Unit tests for the compaction helpers.

These exercise `estimate_event_tokens`, `apply_compaction_view`, and
`plan_truncate` against synthetic event lists — no DB, no model. The
integration-level behavior (harness actually emits a compaction event
when a real session gets big) lives in `test_service.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from tename.harness.compaction import (
    apply_compaction_view,
    estimate_event_tokens,
    plan_truncate,
    should_compact,
)
from tename.harness.profiles import (
    ContextConfig,
    Profile,
    StopConditions,
)
from tename.router.types import (
    CachingConfig,
    ErrorHandling,
    ModelConfig,
    Sampling,
)
from tename.sessions.models import Event, EventType


def _event(
    sequence: int,
    event_type: EventType,
    payload: dict[str, Any] | None = None,
) -> Event:
    return Event(
        id=uuid4(),
        session_id=UUID(int=1),
        sequence=sequence,
        type=event_type,
        payload=payload or {},
        created_at=datetime.now(UTC),
    )


def _profile(
    *,
    max_tokens: int = 200_000,
    effective_budget: int = 160_000,
    compaction_threshold: int = 100,
    keep_last_n_events: int = 3,
) -> Profile:
    return Profile(
        model=ModelConfig(provider="anthropic", model_id="claude-opus-4-6"),
        context=ContextConfig(
            max_tokens=max_tokens,
            effective_budget=effective_budget,
            compaction_threshold=compaction_threshold,
            compaction_strategy="truncate",
            keep_last_n_events=keep_last_n_events,
        ),
        caching=CachingConfig(),
        tool_format="anthropic_tool_use",
        stop_conditions=StopConditions(),
        error_handling=ErrorHandling(),
        sampling=Sampling(),
    )


# ---- estimate_event_tokens -------------------------------------------------


def test_estimate_event_tokens_scales_with_content() -> None:
    short = [_event(1, EventType.USER_MESSAGE, {"content": "hi"})]
    long = [_event(1, EventType.USER_MESSAGE, {"content": "x" * 400})]
    assert estimate_event_tokens(long) > estimate_event_tokens(short)


def test_estimate_event_tokens_never_zero_for_non_empty() -> None:
    events = [_event(1, EventType.USER_MESSAGE, {"content": "hi"})]
    assert estimate_event_tokens(events) >= 1


def test_should_compact_below_threshold() -> None:
    profile = _profile(compaction_threshold=10_000)
    events = [_event(1, EventType.USER_MESSAGE, {"content": "hi"})]
    assert should_compact(events, profile) is False


def test_should_compact_above_threshold() -> None:
    profile = _profile(compaction_threshold=10)
    events = [_event(1, EventType.USER_MESSAGE, {"content": "x" * 200})]
    assert should_compact(events, profile) is True


# ---- apply_compaction_view -------------------------------------------------


def test_apply_compaction_view_passthrough_without_record() -> None:
    events = [
        _event(1, EventType.USER_MESSAGE),
        _event(2, EventType.ASSISTANT_MESSAGE),
        _event(3, EventType.USER_MESSAGE),
    ]
    assert apply_compaction_view(events) == events


def test_apply_compaction_view_drops_by_sequence() -> None:
    events = [
        _event(1, EventType.USER_MESSAGE, {"content": "first"}),
        _event(2, EventType.ASSISTANT_MESSAGE, {"content": "hello"}),
        _event(3, EventType.USER_MESSAGE, {"content": "second"}),
        _event(
            4,
            EventType.HARNESS_EVENT,
            {
                "type": "compaction",
                "strategy": "truncate",
                "dropped_sequences": [2],
                "kept_sequences": [1, 3],
            },
        ),
        _event(5, EventType.USER_MESSAGE, {"content": "third"}),
    ]
    view = apply_compaction_view(events)
    assert [e.sequence for e in view] == [1, 3, 4, 5]


def test_apply_compaction_view_uses_latest_record_only() -> None:
    events = [
        _event(1, EventType.USER_MESSAGE),
        _event(2, EventType.USER_MESSAGE),
        _event(
            3,
            EventType.HARNESS_EVENT,
            {"type": "compaction", "dropped_sequences": [1]},
        ),
        _event(4, EventType.USER_MESSAGE),
        _event(
            5,
            EventType.HARNESS_EVENT,
            {"type": "compaction", "dropped_sequences": [2, 4]},
        ),
        _event(6, EventType.USER_MESSAGE),
    ]
    view = apply_compaction_view(events)
    # Latest record drops 2 and 4; earlier "drop 1" no longer applies.
    assert [e.sequence for e in view] == [1, 3, 5, 6]


# ---- plan_truncate ---------------------------------------------------------


def test_plan_truncate_nothing_to_drop() -> None:
    profile = _profile(keep_last_n_events=10)
    events = [
        _event(1, EventType.USER_MESSAGE),
        _event(2, EventType.ASSISTANT_MESSAGE),
    ]
    assert plan_truncate(events, profile) is None


def test_plan_truncate_keeps_first_user_and_tail() -> None:
    profile = _profile(keep_last_n_events=2)
    events = [
        _event(1, EventType.USER_MESSAGE, {"content": "anchor"}),
        _event(2, EventType.ASSISTANT_MESSAGE, {"content": "a"}),
        _event(3, EventType.USER_MESSAGE, {"content": "mid"}),
        _event(4, EventType.ASSISTANT_MESSAGE, {"content": "b"}),
        _event(5, EventType.USER_MESSAGE, {"content": "tail1"}),
        _event(6, EventType.ASSISTANT_MESSAGE, {"content": "tail2"}),
    ]
    decision = plan_truncate(events, profile)
    assert decision is not None
    assert decision.strategy == "truncate"
    assert decision.kept_sequences == [1, 5, 6]
    assert decision.dropped_sequences == [2, 3, 4]


def test_plan_truncate_retains_prior_compaction_records() -> None:
    profile = _profile(keep_last_n_events=1)
    events = [
        _event(1, EventType.USER_MESSAGE, {"content": "first"}),
        _event(
            2,
            EventType.HARNESS_EVENT,
            {"type": "compaction", "dropped_sequences": []},
        ),
        _event(3, EventType.USER_MESSAGE, {"content": "middle"}),
        _event(4, EventType.ASSISTANT_MESSAGE, {"content": "tail"}),
    ]
    decision = plan_truncate(events, profile)
    assert decision is not None
    # first user (1), prior compaction (2), and the last-1 event (4) are kept.
    assert decision.kept_sequences == [1, 2, 4]
    assert decision.dropped_sequences == [3]


def test_plan_truncate_empty_input() -> None:
    profile = _profile()
    assert plan_truncate([], profile) is None


def test_compaction_decision_payload_shape() -> None:
    profile = _profile(keep_last_n_events=1)
    events = [
        _event(1, EventType.USER_MESSAGE, {"content": "first"}),
        _event(2, EventType.ASSISTANT_MESSAGE, {"content": "drop-me"}),
        _event(3, EventType.USER_MESSAGE, {"content": "keep"}),
    ]
    decision = plan_truncate(events, profile)
    assert decision is not None
    payload = decision.to_payload()
    assert payload["type"] == "compaction"
    assert payload["strategy"] == "truncate"
    assert payload["dropped_sequences"] == [2]
    assert payload["kept_sequences"] == [1, 3]
    assert payload["estimated_tokens_before"] >= payload["estimated_tokens_after"]
