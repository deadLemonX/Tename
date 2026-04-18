"""HarnessRuntime: the stateless brain loop.

S7 fills in the core loop sketched in
`docs/architecture/harness-runtime.md`: wake the session, load agent +
profile + adapter, rebuild context from events each iteration, stream a
model completion, emit incremental events + a final consolidated
assistant message, stub tool execution (real sandbox routing lands in S9
/ S10), apply truncate-strategy compaction, and obey profile stop
conditions before marking the session complete.

The loop keeps no state across iterations beyond local counters. Every
conversational fact lives in the Session Service event log. Killing the
harness and starting a fresh instance on the same `session_id` produces
a consistent continuation; see ADR 0002.
"""

from __future__ import annotations

import logging
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from tename.harness.adapters.base import (
    FrameworkAdapter,
    PendingEvent,
    get_adapter,
)
from tename.harness.compaction import (
    apply_compaction_view,
    plan_truncate,
    should_compact,
)
from tename.harness.profiles import Profile, ProfileLoader
from tename.router.types import Message, ToolDef, Usage
from tename.sessions.models import Agent, Event, EventType

if TYPE_CHECKING:
    from tename.router.service import ModelRouter
    from tename.sessions.service import SessionService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_UUID_NAMESPACE = "tename:system-prompt"
"""Namespace string for the deterministic UUID of per-session system-prompt events."""


class HarnessRuntime:
    """Stateless brain loop runner.

    The harness holds no state across `run_session` calls. Every piece of
    durable state lives in the Session Service; every model-specific knob
    lives in the `Profile`.

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
        """Drive the agent loop for `session_id` to completion.

        Terminates when the model produces no tool calls (the standard
        stop), when the turn or duration budget is exhausted, when the
        model returns an error chunk, or when there is no user turn to
        respond to. Marks the session COMPLETED on exit.
        """
        session = await self._session_service.wake(session_id)
        agent = await self._session_service.get_agent(session.agent_id)
        profile = self._profile_loader.load(agent.model)
        adapter = get_adapter(agent.framework)

        log_ctx = {
            "session_id": str(session_id),
            "agent_id": str(agent.id),
            "framework": agent.framework,
            "model": agent.model,
        }
        logger.info("harness.run.start", extra=log_ctx)

        await self._seed_system_prompt_if_needed(session_id, agent)

        max_turns = profile.stop_conditions.max_turns
        max_duration = profile.stop_conditions.max_duration_seconds
        started_at = monotonic()
        turn = 0
        stop_reason = "max_turns"

        while turn < max_turns:
            turn += 1

            if max_duration is not None and monotonic() - started_at > max_duration:
                stop_reason = "max_duration"
                break

            events = await self._session_service.get_events(session_id)

            if should_compact(apply_compaction_view(events), profile):
                await self._emit_compaction(session_id, events, profile)
                events = await self._session_service.get_events(session_id)

            active = apply_compaction_view(events)
            messages = adapter.build_context(active, profile)
            tools = adapter.get_tools(agent)

            if not any(m.role == "user" for m in messages):
                logger.info(
                    "harness.run.no_user_turn",
                    extra={**log_ctx, "turn": turn},
                )
                stop_reason = "no_user_turn"
                break

            turn_result = await self._run_turn(
                session_id=session_id,
                turn=turn,
                profile=profile,
                adapter=adapter,
                messages=messages,
                tools=tools,
            )

            if turn_result.errored:
                stop_reason = "model_error"
                break

            if not turn_result.tool_calls:
                stop_reason = "no_tool_calls"
                break

            await self._stub_tool_results(session_id, turn_result.tool_calls)
        else:
            # Loop exited because `turn == max_turns` without a break.
            stop_reason = "max_turns"

        await self._session_service.mark_complete(session_id)
        logger.info(
            "harness.run.done",
            extra={**log_ctx, "turns": turn, "stop_reason": stop_reason},
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _seed_system_prompt_if_needed(self, session_id: UUID, agent: Agent) -> None:
        """Emit a `system_event` carrying the agent's system prompt on first wake.

        Idempotent across replays: the event id is derived via uuid5 from
        the session id, so a retry collapses into an existing row inside
        `SessionService.emit_event`.
        """
        if not agent.system_prompt:
            return

        deterministic_id = uuid5(
            NAMESPACE_URL,
            f"{SYSTEM_PROMPT_UUID_NAMESPACE}:{session_id}",
        )
        await self._session_service.emit_event(
            session_id,
            event_id=deterministic_id,
            event_type=EventType.SYSTEM_EVENT,
            payload={"type": "system_prompt", "content": agent.system_prompt},
        )

    async def _run_turn(
        self,
        *,
        session_id: UUID,
        turn: int,
        profile: Profile,
        adapter: FrameworkAdapter,
        messages: list[Message],
        tools: list[ToolDef],
    ) -> _TurnResult:
        """Stream one model completion and emit the resulting events."""
        text_parts: list[str] = []
        tool_calls: list[PendingEvent] = []
        latest_usage: Usage | None = None
        errored = False

        tool_defs: list[ToolDef] | None = tools if tools else None
        async for chunk in self._model_router.complete(
            profile.to_router_profile(), messages, tool_defs
        ):
            if chunk.type == "text_delta":
                text_parts.append(str(chunk.content.get("text", "")))
            elif chunk.type == "usage":
                latest_usage = Usage.model_validate(chunk.content)

            pending = adapter.chunk_to_event(chunk)
            if pending is None:
                continue

            await self._session_service.emit_event(
                session_id,
                event_id=pending.id,
                event_type=pending.type,
                payload=pending.payload,
            )

            if pending.type == EventType.TOOL_CALL:
                tool_calls.append(pending)
            elif pending.type == EventType.ERROR:
                errored = True
                break

        if text_parts and not errored:
            full_text = "".join(text_parts)
            payload: dict[str, Any] = {"content": full_text, "is_complete": True}
            if latest_usage is not None:
                payload["usage"] = latest_usage.model_dump()
            await self._session_service.emit_event(
                session_id,
                event_id=uuid4(),
                event_type=EventType.ASSISTANT_MESSAGE,
                payload=payload,
            )

        logger.info(
            "harness.turn.done",
            extra={
                "session_id": str(session_id),
                "turn": turn,
                "tool_calls": len(tool_calls),
                "errored": errored,
                "had_text": bool(text_parts),
            },
        )
        return _TurnResult(tool_calls=tool_calls, errored=errored)

    async def _stub_tool_results(
        self,
        session_id: UUID,
        tool_calls: list[PendingEvent],
    ) -> None:
        """Emit placeholder `tool_result` events for each tool call.

        Real tool execution arrives in S9 (sandbox tools) and S10 (proxy
        tools). Until then, every tool call produces an `is_error` result
        so the model sees the failure mode in context.
        """
        for call in tool_calls:
            tool_name = call.payload.get("tool_name")
            result_id = uuid5(NAMESPACE_URL, f"tename:tool-result:{call.id}")
            await self._session_service.emit_event(
                session_id,
                event_id=result_id,
                event_type=EventType.TOOL_RESULT,
                payload={
                    "tool_call_id": str(call.id),
                    "tool_name": tool_name,
                    "is_error": True,
                    "error": "tool execution not yet implemented (lands in S9/S10)",
                },
            )

    async def _emit_compaction(
        self,
        session_id: UUID,
        events: list[Event],
        profile: Profile,
    ) -> None:
        """Emit a compaction harness_event if there is work to drop."""
        active = apply_compaction_view(events)
        decision = plan_truncate(active, profile)
        if decision is None:
            return

        # Deterministic id keyed on the set of dropped sequences so a
        # retry after a crash collapses back into the same event.
        dropped_fingerprint = ",".join(str(s) for s in decision.dropped_sequences)
        event_id = uuid5(
            NAMESPACE_URL,
            f"tename:compaction:{session_id}:{dropped_fingerprint}",
        )
        await self._session_service.emit_event(
            session_id,
            event_id=event_id,
            event_type=EventType.HARNESS_EVENT,
            payload=decision.to_payload(),
        )
        logger.info(
            "harness.compaction.emit",
            extra={
                "session_id": str(session_id),
                "dropped": len(decision.dropped_sequences),
                "kept": len(decision.kept_sequences),
                "tokens_before": decision.estimated_tokens_before,
                "tokens_after": decision.estimated_tokens_after,
            },
        )


class _TurnResult:
    """Internal per-turn summary consumed by `run_session`."""

    __slots__ = ("errored", "tool_calls")

    def __init__(self, *, tool_calls: list[PendingEvent], errored: bool) -> None:
        self.tool_calls = tool_calls
        self.errored = errored


__all__ = ["SYSTEM_PROMPT_UUID_NAMESPACE", "HarnessRuntime"]
