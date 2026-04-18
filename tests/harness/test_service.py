"""HarnessRuntime constructor-level tests.

These exercise the wiring that doesn't require a running database: the
default `ProfileLoader` can reach the bundled profile, and the
constructor accepts the sandbox/tool_proxy slots as optional `None`
placeholders that later sessions (S9, S10) will fill in.

The end-to-end loop behavior lives in `test_loop.py` and is gated on the
`postgres` pytest marker.
"""

from __future__ import annotations

from typing import Any, cast

from tename.harness import HarnessRuntime, ProfileLoader


def test_default_profile_loader_can_find_bundled_profile() -> None:
    """The default `ProfileLoader` must reach `tename.profiles` so callers
    can simply write `HarnessRuntime(svc, router)` and have profiles
    available without extra setup."""
    runtime = HarnessRuntime(
        session_service=cast(Any, object()),
        model_router=cast(Any, object()),
    )
    loader = runtime._profile_loader  # pyright: ignore[reportPrivateUsage]
    assert isinstance(loader, ProfileLoader)
    profile = loader.load("claude-opus-4-6")
    assert profile.model.model_id == "claude-opus-4-6"


def test_constructor_accepts_none_sandbox_and_tool_proxy() -> None:
    """S7 doesn't wire real tool execution yet — `None` is the default for
    both slots. S9 / S10 will fill them in."""
    runtime = HarnessRuntime(
        session_service=cast(Any, object()),
        model_router=cast(Any, object()),
        sandbox=None,
        tool_proxy=None,
    )
    assert runtime._sandbox is None  # pyright: ignore[reportPrivateUsage]
    assert runtime._tool_proxy is None  # pyright: ignore[reportPrivateUsage]
