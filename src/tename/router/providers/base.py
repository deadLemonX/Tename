"""ProviderInterface: the ABC every model provider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence

from tename.router.types import Message, ModelChunk, RouterProfile, ToolDef


class ProviderInterface(ABC):
    """Abstract base for a model-provider adapter.

    Implementations translate our `Message` / `ToolDef` shapes to the
    provider's wire format, open a streaming connection, and yield
    `ModelChunk`s in order: any mix of text_deltas and tool_call events,
    followed by a single `usage` chunk, followed by `done`. On a failure
    that cannot be retried, yield an `error` chunk and end the stream.

    Retries for transient startup errors (5xx, connection errors) are the
    provider's responsibility — they must happen before any chunk has been
    yielded. Once chunks have been emitted, errors propagate as `error`
    chunks without retry.
    """

    @abstractmethod
    def complete(
        self,
        profile: RouterProfile,
        messages: Sequence[Message],
        tools: Sequence[ToolDef] | None = None,
    ) -> AsyncIterator[ModelChunk]:
        """Stream a completion as `ModelChunk`s."""
        raise NotImplementedError


__all__ = ["ProviderInterface"]
