"""HarnessRuntime: the stateless brain loop.

S7 shipped the core loop (wake → per-turn stream → consolidated
assistant_message → stub tool_results → compaction → mark_complete).
S9 replaced the sandbox tool stub: sandbox built-ins route through a
`Sandbox` when one is wired. S10 wires the last gap — proxy tools now
route through a `ToolProxy`, which injects credentials from the vault
and runs the tool outside the sandbox. Each `tool_result` payload has
the same base shape whether sandbox-backed or proxy-backed, so
adapters don't need to branch on execution path.

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
from tename.proxy import ToolProxy, proxy_tool_names
from tename.router.types import Message, ToolDef, Usage
from tename.sandbox import BUILTIN_TOOL_NAMES, Sandbox, SandboxRecipe, SandboxStatus
from tename.sessions.models import Agent, Event, EventType

if TYPE_CHECKING:
    from tename.router.service import ModelRouter
    from tename.sessions.service import SessionService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_UUID_NAMESPACE = "tename:system-prompt"
"""Namespace string for the deterministic UUID of per-session system-prompt events."""

SANDBOX_PROVISIONED_UUID_NAMESPACE = "tename:sandbox-provisioned"
"""Namespace for `system_event(type='sandbox_provisioned')` ids. Each
provision emits a fresh event id so a second provision after an ERROR
sandbox lands as a new row rather than colliding with the first."""

SYSTEM_EVENT_SANDBOX_PROVISIONED = "sandbox_provisioned"
"""Payload discriminator for sandbox-provisioned system events."""


class HarnessRuntime:
    """Stateless brain loop runner.

    The harness holds no state across `run_session` calls. Every piece of
    durable state lives in the Session Service; every model-specific knob
    lives in the `Profile`.

    Args:
        session_service: The Session Service instance backing event
            durability and sequence assignment.
        model_router: The Model Router used for streaming completions.
        sandbox: Optional `Sandbox` service. When present, sandbox tool
            calls (bash, python, file_*) route through it; when absent,
            they surface a stub `is_error` result.
        tool_proxy: Optional `ToolProxy`. When present, tool calls that
            name a registered proxy tool (e.g. `web_search`) execute
            via the proxy with credentials pulled from the vault. When
            absent, proxy tool calls surface a stub `is_error` result.
        profile_loader: Optional custom loader. Defaults to one that
            reads the bundled `tename.profiles` package.
    """

    def __init__(
        self,
        session_service: SessionService,
        model_router: ModelRouter,
        sandbox: Sandbox | None = None,
        tool_proxy: ToolProxy | None = None,
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
        respond to. Marks the session COMPLETED on exit. If a sandbox
        was provisioned for the session, it's destroyed in `finally`.
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
        provisioned_sandbox_ids: set[str] = set()

        try:
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

                await self._execute_tool_calls(
                    session_id=session_id,
                    agent=agent,
                    tool_calls=turn_result.tool_calls,
                    provisioned_sandbox_ids=provisioned_sandbox_ids,
                )
            else:
                # Loop exited because `turn == max_turns` without a break.
                stop_reason = "max_turns"
        finally:
            await self._destroy_sandboxes(provisioned_sandbox_ids)

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

    async def _execute_tool_calls(
        self,
        *,
        session_id: UUID,
        agent: Agent,
        tool_calls: list[PendingEvent],
        provisioned_sandbox_ids: set[str],
    ) -> None:
        """Dispatch each tool call to the sandbox or stub its result.

        Sandbox built-ins (bash/python/file_*) with a wired `Sandbox`
        instance run for real; anything else (proxy tools in S10,
        sandbox tools with no backend configured) emits a stubbed
        `is_error` result so the model sees the failure mode.
        """
        for call in tool_calls:
            tool_name_raw = call.payload.get("tool_name")
            tool_name = tool_name_raw if isinstance(tool_name_raw, str) else ""
            tool_input = call.payload.get("input", {})
            if not isinstance(tool_input, dict):
                tool_input = {}

            if tool_name in BUILTIN_TOOL_NAMES and self._sandbox is not None:
                payload = await self._run_sandbox_tool(
                    session_id=session_id,
                    agent=agent,
                    call=call,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    provisioned_sandbox_ids=provisioned_sandbox_ids,
                )
            elif tool_name in proxy_tool_names() and self._tool_proxy is not None:
                payload = await self._run_proxy_tool(
                    session_id=session_id,
                    call=call,
                    tool_name=tool_name,
                    tool_input=tool_input,
                )
            else:
                payload = _stub_payload(call, tool_name, self._sandbox, self._tool_proxy)

            await self._session_service.emit_event(
                session_id,
                event_id=uuid5(NAMESPACE_URL, f"tename:tool-result:{call.id}"),
                event_type=EventType.TOOL_RESULT,
                payload=payload,
            )

    async def _run_sandbox_tool(
        self,
        *,
        session_id: UUID,
        agent: Agent,
        call: PendingEvent,
        tool_name: str,
        tool_input: dict[str, Any],
        provisioned_sandbox_ids: set[str],
    ) -> dict[str, Any]:
        """Lazily provision + execute a sandbox tool; return a tool_result payload."""
        assert self._sandbox is not None  # for type-checker; caller guards it.
        try:
            sandbox_id = await self._get_or_provision_sandbox(
                session_id=session_id,
                agent=agent,
                provisioned_sandbox_ids=provisioned_sandbox_ids,
            )
        except Exception as exc:
            logger.exception(
                "harness.sandbox.provision_fail",
                extra={"session_id": str(session_id), "tool": tool_name},
            )
            return {
                "tool_call_id": str(call.id),
                "tool_name": tool_name,
                "is_error": True,
                "error": f"sandbox provisioning failed: {exc}",
                "content": f"sandbox provisioning failed: {exc}",
            }

        try:
            result = await self._sandbox.execute(sandbox_id, tool_name, tool_input)
        except Exception as exc:
            logger.exception(
                "harness.sandbox.execute_fail",
                extra={
                    "session_id": str(session_id),
                    "sandbox_id": sandbox_id,
                    "tool": tool_name,
                },
            )
            return {
                "tool_call_id": str(call.id),
                "tool_name": tool_name,
                "is_error": True,
                "error": f"sandbox execute failed: {exc}",
                "content": f"sandbox execute failed: {exc}",
                "sandbox_id": sandbox_id,
            }

        return {
            "tool_call_id": str(call.id),
            "tool_name": tool_name,
            "is_error": result.is_error,
            "content": result.content,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "error": result.error,
            "sandbox_id": sandbox_id,
        }

    async def _run_proxy_tool(
        self,
        *,
        session_id: UUID,
        call: PendingEvent,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a proxy tool and shape its result into a tool_result payload.

        All failure modes (unknown tool, missing credential, tool
        exception) surface as `is_error=True` — the ToolProxy itself
        never raises out to the harness. Credentials don't appear in
        the payload; see `proxy.service.ToolProxy.execute`.
        """
        assert self._tool_proxy is not None
        result = await self._tool_proxy.execute(tool_name, tool_input, session_id)
        return {
            "tool_call_id": str(call.id),
            "tool_name": tool_name,
            "is_error": result.is_error,
            "content": result.content,
            "error": result.error,
        }

    async def _get_or_provision_sandbox(
        self,
        *,
        session_id: UUID,
        agent: Agent,
        provisioned_sandbox_ids: set[str],
    ) -> str:
        """Return a live sandbox id for this session, provisioning if needed.

        Reuses an existing `system_event(type='sandbox_provisioned')` when
        the tracked sandbox is still alive. Provisions fresh (and emits a
        new system_event) when either no record exists or the previously
        recorded sandbox is gone/errored (e.g. after a timeout kill).
        """
        assert self._sandbox is not None
        events = await self._session_service.get_events(session_id)
        existing_id = _latest_sandbox_id(events)

        if existing_id is not None:
            status = await self._sandbox.status(existing_id)
            if status in {SandboxStatus.READY, SandboxStatus.IDLE, SandboxStatus.RUNNING}:
                provisioned_sandbox_ids.add(existing_id)
                return existing_id
            logger.info(
                "harness.sandbox.reprovision",
                extra={
                    "session_id": str(session_id),
                    "stale_sandbox_id": existing_id,
                    "stale_status": status.value,
                },
            )

        recipe = _recipe_from_agent(agent)
        new_id = await self._sandbox.provision(recipe)
        provisioned_sandbox_ids.add(new_id)

        # Deterministic id keyed on session + sandbox_id so a replay
        # after a mid-provision crash collapses into the same row (the
        # backend will return the same short id on the same container).
        event_id = uuid5(
            NAMESPACE_URL,
            f"{SANDBOX_PROVISIONED_UUID_NAMESPACE}:{session_id}:{new_id}",
        )
        await self._session_service.emit_event(
            session_id,
            event_id=event_id,
            event_type=EventType.SYSTEM_EVENT,
            payload={
                "type": SYSTEM_EVENT_SANDBOX_PROVISIONED,
                "sandbox_id": new_id,
                "runtime": recipe.runtime,
            },
        )
        return new_id

    async def _destroy_sandboxes(self, sandbox_ids: set[str]) -> None:
        if self._sandbox is None:
            return
        for sandbox_id in sandbox_ids:
            try:
                await self._sandbox.destroy(sandbox_id)
            except Exception:
                logger.exception(
                    "harness.sandbox.destroy_fail",
                    extra={"sandbox_id": sandbox_id},
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


def _stub_payload(
    call: PendingEvent,
    tool_name: str,
    sandbox: Sandbox | None,
    tool_proxy: ToolProxy | None,
) -> dict[str, Any]:
    """Construct the stub tool_result payload for tools the harness can't run.

    Three distinct failure modes, kept distinct so the model sees a
    useful error string:
      - sandbox tool requested but no Sandbox was wired in
      - proxy tool requested but no ToolProxy was wired in
      - unknown tool — neither a sandbox built-in nor a registered proxy tool
    """
    if tool_name in BUILTIN_TOOL_NAMES and sandbox is None:
        error = (
            f"tool '{tool_name}' is a sandbox tool but no sandbox is configured for this runtime"
        )
    elif tool_name in proxy_tool_names() and tool_proxy is None:
        error = (
            f"tool '{tool_name}' is a proxy tool but no tool proxy is configured for this runtime"
        )
    else:
        error = f"tool '{tool_name}' is not a registered sandbox or proxy tool"
    return {
        "tool_call_id": str(call.id),
        "tool_name": tool_name,
        "is_error": True,
        "error": error,
        "content": error,
    }


def _latest_sandbox_id(events: list[Event]) -> str | None:
    """Pull the most recent sandbox_id from `system_event` records."""
    for event in reversed(events):
        if event.type != EventType.SYSTEM_EVENT:
            continue
        if event.payload.get("type") != SYSTEM_EVENT_SANDBOX_PROVISIONED:
            continue
        sandbox_id = event.payload.get("sandbox_id")
        if isinstance(sandbox_id, str) and sandbox_id:
            return sandbox_id
    return None


def _recipe_from_agent(agent: Agent) -> SandboxRecipe:
    """Build a `SandboxRecipe` from `agent.sandbox_recipe`, defaults otherwise."""
    raw = agent.sandbox_recipe or {}
    return SandboxRecipe.model_validate(raw)


__all__ = [
    "SANDBOX_PROVISIONED_UUID_NAMESPACE",
    "SYSTEM_EVENT_SANDBOX_PROVISIONED",
    "SYSTEM_PROMPT_UUID_NAMESPACE",
    "HarnessRuntime",
]
