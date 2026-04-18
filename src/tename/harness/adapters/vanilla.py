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
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

from tename.harness.adapters.base import FrameworkAdapter, PendingEvent, register_adapter
from tename.harness.profiles import Profile
from tename.router.types import ContentBlock, Message, ModelChunk, ToolDef
from tename.sessions.models import Agent, Event, EventType


class VanillaAdapter(FrameworkAdapter):
    """Minimal adapter for agents that don't use a framework."""

    name = "vanilla"

    def build_context(self, events: Sequence[Event], profile: Profile) -> list[Message]:
        messages: list[Message] = []
        for event in events:
            message = self._event_to_message(event)
            if message is not None:
                messages.append(message)
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
        # done, usage, tool_call_start, tool_call_delta: no event on their
        # own; the S7 loop handles usage aggregation separately.
        return None

    def get_tools(self, agent: Agent) -> list[ToolDef]:
        """Return tool definitions in generic JSON-schema shape.

        Vanilla agents carry only tool names in `agent.tools`; concrete
        tool schemas are resolved by the sandbox (S9) and tool proxy
        (S10). Until those modules ship, this returns an empty list so
        the skeleton stays honest.
        """
        return []

    def supports_streaming(self) -> bool:
        return True

    @staticmethod
    def _event_to_message(event: Event) -> Message | None:
        payload = event.payload
        if event.type == EventType.USER_MESSAGE:
            content = payload.get("content", "")
            return Message(role="user", content=_as_message_content(content))
        if event.type == EventType.ASSISTANT_MESSAGE:
            # Only surface completed assistant turns. Incremental deltas
            # (is_complete=False) accumulate client-side; replaying them
            # into context would produce duplicate output.
            if not payload.get("is_complete", False):
                return None
            content = payload.get("content", "")
            return Message(role="assistant", content=_as_message_content(content))
        if event.type == EventType.SYSTEM_EVENT and payload.get("type") == "system_prompt":
            content = payload.get("content", "")
            return Message(role="system", content=_as_message_content(content))
        # tool_call / tool_result / harness_event / error don't map to
        # plain chat messages in the vanilla adapter; frameworks that
        # need them should subclass.
        return None


def _as_message_content(value: object) -> str | list[ContentBlock]:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        blocks: list[ContentBlock] = []
        for entry in value:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(entry, ContentBlock):
                blocks.append(entry)
            elif isinstance(entry, dict):
                blocks.append(ContentBlock.model_validate(entry))
            else:
                raise TypeError(
                    f"vanilla adapter: cannot interpret content entry of type "
                    f"{type(entry).__name__}"
                )
        return blocks
    raise TypeError(
        f"vanilla adapter: message content must be str or list, got {type(value).__name__}"
    )


register_adapter(VanillaAdapter)


__all__ = ["VanillaAdapter"]
