"""HarnessRuntime skeleton tests."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from tename.harness import HarnessRuntime, ProfileLoader


@pytest.mark.asyncio
async def test_run_session_raises_not_implemented() -> None:
    """S6 ships the skeleton only; the real loop lands in S7."""
    runtime = HarnessRuntime(
        session_service=cast(Any, object()),
        model_router=cast(Any, object()),
    )

    with pytest.raises(NotImplementedError, match="S7"):
        await runtime.run_session(uuid4())


def test_default_profile_loader_can_find_bundled_profile() -> None:
    """The constructor's default ProfileLoader must reach the bundled
    tename.profiles package so an S7 implementation can simply construct
    `HarnessRuntime(svc, router)` and have profiles available."""
    runtime = HarnessRuntime(
        session_service=cast(Any, object()),
        model_router=cast(Any, object()),
    )
    loader = runtime._profile_loader  # pyright: ignore[reportPrivateUsage]
    assert isinstance(loader, ProfileLoader)
    profile = loader.load("claude-opus-4-6")
    assert profile.model.model_id == "claude-opus-4-6"
