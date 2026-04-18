"""Deep Agents framework adapter.

Deep Agents (`langchain-ai/deepagents`) is a LangChain/LangGraph-based
harness built around a planning primitive (`write_todos`), a virtual
filesystem, and a subagent task spawn tool. Tename runs *beneath* it: our
stateless loop drives the model, but we speak Deep Agents' conventions so
agents written for that framework execute without modification.

This adapter does NOT import `deepagents` — we translate events and tool
schemas directly, so a user can opt into `framework: deep_agents` without
pulling LangGraph and LangChain into their venv. The tool schemas below
mirror the Deep Agents v0.5 conventions closely enough for an Anthropic-
style tool-calling model to drive the framework's built-ins.

Core difference from `VanillaAdapter`: `build_context` carries tool rounds
through as `tool_use`/`tool_result` content blocks instead of dropping
them, so multi-turn tool-using agents actually work. Anthropic rejects any
assistant turn with a `tool_use` block unless the next user turn has a
matching `tool_result`.

Concept mapping deferrals for v0.1:

- `harness_event(type=plan)` / `harness_event(type=subagent_spawn)` as
  sidecar records for `write_todos` / `task` tool calls are NOT emitted.
  The `tool_call` event itself carries the todo list / subagent spec in
  its input payload, and observability can filter by `tool_name`. See
  `docs/harness/adapter-deep-agents.md` for the rationale and the v0.2
  revisit plan.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, ClassVar
from uuid import UUID, uuid4

from tename.harness.adapters.base import FrameworkAdapter, PendingEvent, register_adapter
from tename.harness.profiles import Profile
from tename.router.types import ContentBlock, Message, ModelChunk, ToolDef
from tename.sessions.models import Agent, Event, EventType

logger = logging.getLogger(__name__)


# --- Deep Agents built-in tool schemas --------------------------------------
# Schemas mirror the Deep Agents v0.5 conventions: planning via
# `write_todos`, virtual-filesystem tools, and subagent `task` spawn. Users
# asking the adapter for any of these names in `agent.tools` get the
# definitions below surfaced to the model router.

_WRITE_TODOS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "description": (
                "The updated full todo list. Replace the prior list each time — "
                "the Deep Agents planning middleware stores the latest value."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "What the todo accomplishes.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed"],
                    },
                },
                "required": ["content", "status"],
            },
        }
    },
    "required": ["todos"],
}

_LS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_READ_FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "Path to read inside the agent's virtual filesystem.",
        },
        "offset": {
            "type": "integer",
            "description": "Optional 0-based line offset to start reading from.",
            "minimum": 0,
        },
        "limit": {
            "type": "integer",
            "description": "Optional line count cap; omit to read the whole file.",
            "minimum": 1,
        },
    },
    "required": ["file_path"],
}

_WRITE_FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["file_path", "content"],
}

_EDIT_FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "old_string": {"type": "string"},
        "new_string": {"type": "string"},
        "replace_all": {
            "type": "boolean",
            "description": "Replace every match instead of erroring on ambiguous matches.",
        },
    },
    "required": ["file_path", "old_string", "new_string"],
}

_TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "What the subagent should accomplish.",
        },
        "subagent_type": {
            "type": "string",
            "description": (
                "Which registered subagent to spawn. Use 'general-purpose' for "
                "the Deep Agents default research subagent."
            ),
        },
    },
    "required": ["description", "subagent_type"],
}


BUILTIN_TOOLS: dict[str, ToolDef] = {
    "write_todos": ToolDef(
        name="write_todos",
        description=(
            "Write or update the agent's todo list. Use for any multi-step task: "
            "plan first, then execute. Replaces the full list each call."
        ),
        input_schema=_WRITE_TODOS_SCHEMA,
    ),
    "ls": ToolDef(
        name="ls",
        description="List every path currently present in the virtual filesystem.",
        input_schema=_LS_SCHEMA,
    ),
    "read_file": ToolDef(
        name="read_file",
        description="Read a file from the virtual filesystem.",
        input_schema=_READ_FILE_SCHEMA,
    ),
    "write_file": ToolDef(
        name="write_file",
        description="Create or overwrite a file in the virtual filesystem.",
        input_schema=_WRITE_FILE_SCHEMA,
    ),
    "edit_file": ToolDef(
        name="edit_file",
        description=(
            "Apply a string-replacement edit to an existing file. "
            "Errors on ambiguous matches unless `replace_all` is true."
        ),
        input_schema=_EDIT_FILE_SCHEMA,
    ),
    "task": ToolDef(
        name="task",
        description=(
            "Spawn a subagent to handle an isolated task. Use when the subtask "
            "has its own context needs or produces output you don't want in the "
            "main agent's context."
        ),
        input_schema=_TASK_SCHEMA,
    ),
}
"""Deep Agents built-in tool definitions, keyed by tool name."""


class DeepAgentsAdapter(FrameworkAdapter):
    """Adapter for agents written against the Deep Agents framework.

    Carries tool_use/tool_result rounds through the context; surfaces the
    Deep Agents built-in tool schemas (write_todos, filesystem tools, task)
    to the model router.
    """

    name: ClassVar[str] = "deep_agents"

    def build_context(self, events: Sequence[Event], profile: Profile) -> list[Message]:
        """Fold the event log into an Anthropic-shaped message list.

        Folds each assistant turn — text (from the consolidated
        `assistant_message(is_complete=True)` closer) plus any `tool_call`
        events emitted during that turn's stream — into a single
        `Message(role="assistant", content=[text_block, tool_use_block,
        ...])`. Runs of subsequent `tool_result` events collapse into one
        `Message(role="tool", content=[tool_result_block, ...])`. System
        prompts and user messages pass through as plain text messages.

        The harness (S7) emits each turn's events in this order:
          1. incremental `assistant_message(is_complete=False)` deltas
             (skipped — ephemeral)
          2. `tool_call` events (as `tool_call_end` chunks arrive)
          3. one consolidated `assistant_message(is_complete=True)` closer
          4. stubbed `tool_result` events (one per tool_call)

        So tool_calls arrive BEFORE the closer. The algorithm buffers
        tool_uses as they appear and pairs them with the closer's text
        when it eventually arrives; the resulting assistant message has
        the text block first, tool_use blocks after — matching
        Anthropic's expected ordering.
        """
        # Map tool_call event id → model tool_use id, so tool_result
        # events (which reference the tool_call event id as
        # `tool_call_id`) can produce tool_result blocks with the right
        # `tool_use_id`.
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
            """Emit one assistant message from any buffered text + tool_uses."""
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
                    # Incremental deltas are ephemeral — skip, same as vanilla.
                    continue
                # Closer arrives AFTER the turn's tool_calls. The pending
                # tool_use buffer holds this turn's tool_calls. Any
                # pending_tool_results belong to a PREVIOUS turn and must
                # land as a tool message BEFORE this assistant turn.
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
                # A tool_call starting a fresh turn implies the prior
                # assistant turn is done (its closer already landed) and
                # its tool_results have already been collected — flush
                # both so this tool_call opens a clean new assistant turn.
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
                # A tool_result closes the assistant turn it responds to;
                # flush any buffered assistant content first so the
                # resulting ordering is assistant → tool → assistant …
                _flush_assistant()
                tool_call_id = payload.get("tool_call_id")
                tool_use_id: str | None = None
                if isinstance(tool_call_id, str):
                    try:
                        tool_use_id = tool_call_ids.get(UUID(tool_call_id))
                    except ValueError:
                        tool_use_id = None
                if tool_use_id is None:
                    # Orphaned tool_result (matching tool_call was dropped
                    # by compaction or never recorded). Skip — Anthropic
                    # would reject the request if tool_use_id didn't
                    # appear in the preceding assistant message.
                    logger.debug(
                        "deep_agents: skipping orphaned tool_result",
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
        """Translate a streaming chunk into a pending event.

        Shape matches `VanillaAdapter` deliberately: text deltas become
        incremental assistant messages, tool_call_end becomes a tool_call
        event (including `write_todos` and `task` — v0.1 does NOT split
        those into sidecar `harness_event` records; see module docstring).
        """
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
        """Return Deep Agents built-in tool definitions requested by the agent.

        Unknown tool names in `agent.tools` are dropped with a warning —
        user-defined tools arrive with the sandbox (S9) and tool proxy
        (S10). Returns a stable order matching `agent.tools`.
        """
        out: list[ToolDef] = []
        seen: set[str] = set()
        for name in agent.tools:
            if name in seen:
                continue
            seen.add(name)
            tool = BUILTIN_TOOLS.get(name)
            if tool is None:
                logger.warning(
                    "deep_agents: unknown tool '%s' — skipping (custom tools arrive with S9/S10)",
                    name,
                )
                continue
            out.append(tool)
        return out

    def supports_streaming(self) -> bool:
        return True


def _tool_result_block(tool_use_id: str, payload: dict[str, Any]) -> ContentBlock:
    """Build an Anthropic-shaped tool_result ContentBlock from a stored event.

    Prefers `payload.content` when present (future real results); falls
    back to `payload.error` for stubbed errors, then to a generic string
    representation of the payload.
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


register_adapter(DeepAgentsAdapter)


__all__ = ["BUILTIN_TOOLS", "DeepAgentsAdapter"]
