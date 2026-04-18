"""Live integration test against the real Anthropic API.

Gated on `ANTHROPIC_API_KEY`. When unset, the test is skipped cleanly.
Marked `anthropic` so it can be deselected via `-m 'not anthropic'`.
"""

from __future__ import annotations

import os

import pytest

from tename.router import ModelRouter
from tename.router.types import (
    Message,
    ModelConfig,
    RouterProfile,
    Sampling,
)

pytestmark = [pytest.mark.anthropic, pytest.mark.asyncio]


def _require_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set; live integration test skipped")
    return key


async def test_live_opus_4_6_streams_and_reports_cost() -> None:
    _require_key()
    router = ModelRouter()
    profile = RouterProfile(
        model=ModelConfig(provider="anthropic", model_id="claude-opus-4-6"),
        sampling=Sampling(temperature=0.0, top_p=1.0, max_tokens=64),
    )
    messages = [
        Message(role="user", content="Reply with a single word: hello"),
    ]

    text_chunks: list[str] = []
    usage_chunks: list[dict[str, object]] = []
    saw_done = False
    async for chunk in router.complete(profile, messages):
        if chunk.type == "text_delta":
            text_chunks.append(chunk.content["text"])
        elif chunk.type == "usage":
            usage_chunks.append(chunk.content)
        elif chunk.type == "done":
            saw_done = True
        elif chunk.type == "error":
            pytest.fail(f"unexpected error chunk: {chunk.content}")

    assert text_chunks, "expected at least one text_delta"
    assert "".join(text_chunks).strip()
    assert saw_done
    assert len(usage_chunks) == 1
    usage = usage_chunks[0]
    assert isinstance(usage["input_tokens"], int) and usage["input_tokens"] > 0
    assert isinstance(usage["output_tokens"], int) and usage["output_tokens"] > 0
    assert usage["cost_usd"] is not None
