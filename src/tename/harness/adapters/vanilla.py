"""Vanilla framework adapter: the no-framework fallback.

This adapter exists for user code that doesn't use Deep Agents or another
supported framework. It speaks the Session Service event log directly:
user/assistant messages become `Message`s, streaming text deltas become
incremental `assistant_message` events, tool-call terminations become
`tool_call` events, and errors become `error` events.

System prompts live in the event log as a `system_event` with
`payload.type == "system_prompt"`. The S7 harness loop emits that event
once at session start; this adapter picks it up during `build_context` so
the adapter itself never needs the `Agent` handle (keeping it stateless).

Tool rounds: `build_context` folds the harness's emit order
`(text deltas → tool_call → closer → tool_result)` back into Anthropic's
canonical ordering `(assistant[text + tool_use] → tool[tool_result] →
assistant[...])`. Without this, any turn where the model emits preamble
text before a tool_use would leave the context ending on an assistant
message, and the next request would be rejected with "conversation must
end with a user message." The logic mirrors `DeepAgentsAdapter` since
the underlying wire format is identical.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

from tename.harness.adapters.base import FrameworkAdapter, PendingEvent, register_adapter
from tename.harness.profiles import Profile
from tename.proxy import proxy_tool_schemas
from tename.router.types import ContentBlock, Message, ModelChunk, ToolDef
from tename.sandbox import BUILTIN_TOOL_SCHEMAS
from tename.sessions.models import Agent, Event, EventType

logger = logging.getLogger(__name__)


class VanillaAdapter(FrameworkAdapter):
    """Minimal adapter for agents that don't use a framework."""

    name = "vanilla"

    def build_context(self, events: Sequence[Event], profile: Profile) -> list[Message]:
        """Fold the event log into an Anthropic-shaped message list.

        Assistant text from the is_complete=True closer combines with that
        turn's `tool_call` events into ONE `Message(role="assistant",
        content=[text_block, tool_use_block, ...])`. Runs of subsequent
        `tool_result` events collapse into one `Message(role="tool",
        content=[tool_result_block, ...])`. System prompts and user
        messages pass through as plain text messages.

        Incremental `assistant_message(is_complete=False)` events are
        skipped — they're ephemeral streaming deltas, already superseded
        by the closer.
        """
        tool_call_ids: dict[UUID, str] = {}
        for ev in events:
            if ev.type == EventType.TOOL_CALL:
                tool_id = ev.payload.get("tool_id")
                if isinstance(tool_id, str):
                    tool_call_ids[ev.id] = tool_id

        messages: list[Message] = []
        pending_text: str = ""
        pending_tool_uses: list[ContentBlock] = []
        pending_tool_results: list[ContentBlock] = []

        def _flush_assistant() -> None:
            nonlocal pending_text, pending_tool_uses
            if not pending_text and not pending_tool_uses:
                return
            blocks: list[ContentBlock] = []
            if pending_text:
                blocks.append(ContentBlock(type="text", text=pending_text))
            blocks.extend(pending_tool_uses)
            messages.append(Message(role="assistant", content=blocks))
            pending_text = ""
            pending_tool_uses = []

        def _flush_tool_results() -> None:
            nonlocal pending_tool_results
            if not pending_tool_results:
                return
            messages.append(Message(role="tool", content=pending_tool_results))
            pending_tool_results = []

        for ev in events:
            etype = ev.type
            payload = ev.payload

            if etype == EventType.SYSTEM_EVENT and payload.get("type") == "system_prompt":
                _flush_assistant()
                _flush_tool_results()
                content = payload.get("content", "")
                if isinstance(content, str) and content:
                    messages.append(Message(role="system", content=content))
                continue

            if etype == EventType.USER_MESSAGE:
                _flush_assistant()
                _flush_tool_results()
                content = payload.get("content", "")
                if isinstance(content, str):
                    messages.append(Message(role="user", content=content))
                continue

            if etype == EventType.ASSISTANT_MESSAGE:
                if not payload.get("is_complete", False):
                    continue
                _flush_tool_results()
                text = payload.get("content", "")
                if isinstance(text, str):
                    pending_text = text
                continue

            if etype == EventType.TOOL_CALL:
                tool_id = payload.get("tool_id")
                tool_name = payload.get("tool_name")
                tool_input = payload.get("input", {})
                if not (isinstance(tool_id, str) and isinstance(tool_name, str)):
                    continue
                if pending_tool_results:
                    _flush_assistant()
                    _flush_tool_results()
                pending_tool_uses.append(
                    ContentBlock(
                        type="tool_use",
                        id=tool_id,
                        name=tool_name,
                        input=tool_input if isinstance(tool_input, dict) else {},
                    )
                )
                continue

            if etype == EventType.TOOL_RESULT:
                _flush_assistant()
                tool_call_id = payload.get("tool_call_id")
                tool_use_id: str | None = None
                if isinstance(tool_call_id, str):
                    try:
                        tool_use_id = tool_call_ids.get(UUID(tool_call_id))
                    except ValueError:
                        tool_use_id = None
                if tool_use_id is None:
                    logger.debug(
                        "vanilla: skipping orphaned tool_result",
                        extra={"event_id": str(ev.id), "tool_call_id": tool_call_id},
                    )
                    continue
                pending_tool_results.append(_tool_result_block(tool_use_id, payload))
                continue

            # harness_event, error: informational, do not enter context.
            continue

        _flush_assistant()
        _flush_tool_results()
        return messages

    def chunk_to_event(self, chunk: ModelChunk) -> PendingEvent | None:
        if chunk.type == "text_delta":
            return PendingEvent(
                id=uuid4(),
                type=EventType.ASSISTANT_MESSAGE,
                payload={
                    "content": chunk.content.get("text", ""),
                    "is_complete": False,
                },
            )
        if chunk.type == "tool_call_end":
            return PendingEvent(
                id=uuid4(),
                type=EventType.TOOL_CALL,
                payload={
                    "tool_id": chunk.content["tool_id"],
                    "tool_name": chunk.content["tool_name"],
                    "input": chunk.content.get("input", {}),
                },
            )
        if chunk.type == "error":
            return PendingEvent(
                id=uuid4(),
                type=EventType.ERROR,
                payload=dict(chunk.content),
            )
        return None

    def get_tools(self, agent: Agent) -> list[ToolDef]:
        """Return tool definitions for the tools this agent asked for.

        `agent.tools` is a list of tool names. Sandbox built-ins
        (bash, python, file_*) and registered proxy tools (e.g.
        `web_search`) both surface their `ToolDef` schemas here so
        the model router can forward them to the provider. Unknown
        names are dropped with a warning. Duplicate names dedupe
        while preserving first-seen order. Sandbox built-ins take
        precedence if a name somehow appears in both registries.
        """
        proxy_schemas = proxy_tool_schemas()
        out: list[ToolDef] = []
        seen: set[str] = set()
        for name in agent.tools:
            if name in seen:
                continue
            seen.add(name)
            tool = BUILTIN_TOOL_SCHEMAS.get(name) or proxy_schemas.get(name)
            if tool is None:
                logger.warning(
                    "vanilla: unknown tool '%s' — skipping (not a sandbox or proxy tool)",
                    name,
                )
                continue
            out.append(tool)
        return out

    def supports_streaming(self) -> bool:
        return True


def _tool_result_block(tool_use_id: str, payload: dict[str, Any]) -> ContentBlock:
    """Build an Anthropic-shaped tool_result ContentBlock from a stored event.

    Prefers `payload.content` when present; falls back to `payload.error`
    for stubbed errors, then to a generic string representation.
    """
    content = payload.get("content")
    if content is None:
        error = payload.get("error")
        content = error if isinstance(error, str) else ""
    if not isinstance(content, str | list):
        content = str(content)
    is_error = payload.get("is_error")
    return ContentBlock(
        type="tool_result",
        tool_use_id=tool_use_id,
        content=content,
        is_error=bool(is_error) if is_error is not None else None,
    )


register_adapter(VanillaAdapter)


__all__ = ["VanillaAdapter"]
