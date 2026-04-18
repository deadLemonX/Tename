"""AnthropicProvider: streams completions from the Anthropic Messages API.

Responsibilities:
- Translate our `Message` / `ToolDef` shapes to Anthropic wire format.
- Place `cache_control` markers on the system prompt (and, eventually, on
  compaction summaries) per the profile's caching config.
- Iterate the streaming events and yield `ModelChunk`s.
- Retry transient failures at stream startup; propagate mid-stream failures
  as `error` chunks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import anthropic
from anthropic import AsyncAnthropic

from tename.router.providers.base import ProviderInterface
from tename.router.types import (
    Message,
    ModelChunk,
    RouterProfile,
    ToolDef,
    Usage,
    done_chunk,
    error_chunk,
    text_delta,
    tool_call_delta,
    tool_call_end,
    tool_call_start,
    usage_chunk,
)

logger = logging.getLogger(__name__)


class AnthropicProvider(ProviderInterface):
    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        # When the caller passes a client we don't own its lifecycle. When
        # we construct one ourselves we close it at the end of each call so
        # the httpx socket pool tears down cleanly.
        self._client = client
        self._owns_client = client is None

    async def complete(
        self,
        profile: RouterProfile,
        messages: Sequence[Message],
        tools: Sequence[ToolDef] | None = None,
    ) -> AsyncIterator[ModelChunk]:
        client = self._client if self._client is not None else AsyncAnthropic()
        try:
            async for chunk in self._stream(client, profile, messages, tools):
                yield chunk
        finally:
            if self._owns_client:
                await client.close()

    # -- internal helpers ---------------------------------------------------

    async def _stream(
        self,
        client: AsyncAnthropic,
        profile: RouterProfile,
        messages: Sequence[Message],
        tools: Sequence[ToolDef] | None,
    ) -> AsyncIterator[ModelChunk]:
        request = self._build_request(profile, messages, tools)

        opened = await self._open_stream_with_retry(client, profile, request)
        if isinstance(opened, ModelChunk):
            # Non-retryable or retries-exhausted startup failure.
            yield opened
            return
        manager, stream = opened

        # Track per-index tool_use state so we can emit tool_call_end with
        # a parsed `input` on content_block_stop.
        tool_blocks: dict[int, _ToolBlockState] = {}

        # Usage accumulates across message_start (inputs) and message_delta
        # (outputs, plus any cache_read updates).
        input_tokens = 0
        cached_input_tokens = 0
        output_tokens = 0

        mid_stream_error: ModelChunk | None = None
        try:
            try:
                async for event in stream:
                    etype = event.type
                    if etype == "message_start":
                        au = event.message.usage
                        input_tokens = au.input_tokens or 0
                        cached_input_tokens = au.cache_read_input_tokens or 0
                        output_tokens = au.output_tokens or 0
                    elif etype == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            tool_blocks[event.index] = _ToolBlockState(
                                tool_id=block.id,
                                tool_name=block.name,
                            )
                            yield tool_call_start(
                                tool_id=block.id,
                                tool_name=block.name,
                                index=event.index,
                            )
                    elif etype == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield text_delta(delta.text)
                        elif delta.type == "input_json_delta":
                            state = tool_blocks.get(event.index)
                            if state is not None:
                                state.partial_json_buf += delta.partial_json
                                yield tool_call_delta(
                                    tool_id=state.tool_id,
                                    partial_json=delta.partial_json,
                                    index=event.index,
                                )
                    elif etype == "content_block_stop":
                        state = tool_blocks.pop(event.index, None)
                        if state is not None:
                            parsed_input = _safe_json_loads(state.partial_json_buf)
                            yield tool_call_end(
                                tool_id=state.tool_id,
                                tool_name=state.tool_name,
                                tool_input=parsed_input,
                                index=event.index,
                            )
                    elif etype == "message_delta":
                        u = event.usage
                        if u.output_tokens is not None:
                            output_tokens = u.output_tokens
                        if u.cache_read_input_tokens is not None:
                            cached_input_tokens = u.cache_read_input_tokens
                        if u.input_tokens is not None:
                            input_tokens = max(input_tokens, u.input_tokens)
                    # message_stop: exiting the async for handles teardown.
            except anthropic.APIStatusError as exc:
                logger.warning(
                    "anthropic mid-stream status error",
                    extra={"status_code": exc.status_code, "message": str(exc)},
                )
                mid_stream_error = error_chunk(
                    message=str(exc),
                    retryable=exc.status_code >= 500,
                    status_code=exc.status_code,
                )
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
                logger.warning("anthropic mid-stream connection error", exc_info=exc)
                mid_stream_error = error_chunk(message=str(exc), retryable=True, status_code=None)
        finally:
            await manager.__aexit__(None, None, None)

        if mid_stream_error is not None:
            yield mid_stream_error
            return

        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
        yield usage_chunk(usage)
        yield done_chunk()

    async def _open_stream_with_retry(
        self,
        client: AsyncAnthropic,
        profile: RouterProfile,
        request: dict[str, Any],
    ) -> tuple[Any, Any] | ModelChunk:
        """Open and enter a streaming connection with retries around startup.

        Returns (manager, stream) on success — the caller is responsible for
        calling `manager.__aexit__` to tear down the HTTP connection. On a
        non-retryable or retries-exhausted failure, returns an error chunk.
        """
        eh = profile.error_handling
        max_attempts = (eh.max_retries + 1) if eh.retry_on_transient else 1
        last_retryable_exc: Exception | None = None

        for attempt in range(max_attempts):
            manager = client.messages.stream(**request)
            try:
                stream = await manager.__aenter__()
            except anthropic.APIStatusError as exc:
                if exc.status_code < 500:
                    logger.info(
                        "anthropic non-retryable status",
                        extra={"status_code": exc.status_code},
                    )
                    return error_chunk(
                        message=str(exc),
                        retryable=False,
                        status_code=exc.status_code,
                    )
                last_retryable_exc = exc
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
                last_retryable_exc = exc
            else:
                return manager, stream

            if attempt + 1 < max_attempts:
                backoff = eh.backoff_base_seconds * (eh.backoff_multiplier**attempt)
                logger.info(
                    "anthropic transient failure, retrying",
                    extra={"attempt": attempt + 1, "backoff_s": backoff},
                )
                await asyncio.sleep(backoff)

        exc = cast(Exception, last_retryable_exc)
        status = getattr(exc, "status_code", None)
        return error_chunk(
            message=f"anthropic stream startup failed after retries: {exc}",
            retryable=True,
            status_code=status if isinstance(status, int) else None,
        )

    def _build_request(
        self,
        profile: RouterProfile,
        messages: Sequence[Message],
        tools: Sequence[ToolDef] | None,
    ) -> dict[str, Any]:
        system_blocks, anthropic_messages = _split_system(messages)
        self._apply_system_cache_control(system_blocks, profile)

        request: dict[str, Any] = {
            "model": profile.model.model_id,
            "messages": anthropic_messages,
            "max_tokens": profile.sampling.max_tokens,
            "temperature": profile.sampling.temperature,
        }
        # Anthropic (Opus 4.6+) rejects temperature and top_p together.
        # Temperature is the canonical knob we expose; only forward top_p
        # when the profile explicitly narrows it below the "no restriction"
        # default of 1.0.
        if profile.sampling.top_p < 1.0:
            request.pop("temperature")
            request["top_p"] = profile.sampling.top_p
        if system_blocks:
            request["system"] = system_blocks
        if tools:
            request["tools"] = [_tool_to_anthropic(t) for t in tools]
        return request

    @staticmethod
    def _apply_system_cache_control(
        system_blocks: list[dict[str, Any]], profile: RouterProfile
    ) -> None:
        caching = profile.caching
        if caching.provider_strategy != "explicit_breakpoints":
            return
        if not any(bp.after == "system_prompt" for bp in caching.breakpoints):
            return
        if not system_blocks:
            return
        system_blocks[-1]["cache_control"] = {"type": "ephemeral"}


class _ToolBlockState:
    __slots__ = ("partial_json_buf", "tool_id", "tool_name")

    def __init__(self, *, tool_id: str, tool_name: str) -> None:
        self.tool_id = tool_id
        self.tool_name = tool_name
        self.partial_json_buf = ""


def _safe_json_loads(buf: str) -> dict[str, Any]:
    """Parse tool_use input JSON, tolerating empty/partial inputs.

    Anthropic sends `{}` for zero-arg tools as a single delta, but if the
    buffer is empty or unparseable we return an empty dict rather than crash.
    """
    if not buf.strip():
        return {}
    try:
        value = json.loads(buf)
    except json.JSONDecodeError:
        logger.warning("tool_use input JSON malformed; returning empty dict")
        return {}
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {"_value": value}


def _split_system(
    messages: Sequence[Message],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate system messages from the rest.

    Anthropic's API takes `system` as a separate top-level field. Tool-role
    messages are folded back into user messages using `tool_result` blocks,
    matching Anthropic's wire format.
    """
    system_blocks: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system_blocks.extend(_to_text_blocks(m.content))
            continue
        role = "user" if m.role == "tool" else m.role
        out.append({"role": role, "content": _content_to_anthropic(m.content)})
    return system_blocks, out


def _to_text_blocks(content: str | list[Any]) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks: list[dict[str, Any]] = []
    for b in content:
        text = getattr(b, "text", None)
        if text:
            blocks.append({"type": "text", "text": text})
    return blocks


def _content_to_anthropic(content: str | list[Any]) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    out: list[dict[str, Any]] = []
    for b in content:
        btype = getattr(b, "type", None)
        if btype == "text":
            out.append({"type": "text", "text": b.text or ""})
        elif btype == "tool_use":
            out.append(
                {
                    "type": "tool_use",
                    "id": b.id,
                    "name": b.name,
                    "input": b.input or {},
                }
            )
        elif btype == "tool_result":
            tool_result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": b.tool_use_id,
                "content": b.content if b.content is not None else "",
            }
            if b.is_error:
                tool_result["is_error"] = True
            out.append(tool_result)
    return out


def _tool_to_anthropic(tool: ToolDef) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


__all__ = ["AnthropicProvider"]
