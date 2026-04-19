"""BackgroundLoop tests — the sync-over-async bridge."""

from __future__ import annotations

import asyncio

import pytest

from tename.sdk.runtime import BackgroundLoop


def test_start_and_stop_roundtrip() -> None:
    loop = BackgroundLoop()
    loop.start()
    loop.stop()


def test_run_blocks_until_coroutine_completes() -> None:
    loop = BackgroundLoop()
    loop.start()
    try:

        async def _compute() -> int:
            await asyncio.sleep(0.01)
            return 42

        assert loop.run(_compute()) == 42
    finally:
        loop.stop()


def test_run_propagates_exceptions() -> None:
    loop = BackgroundLoop()
    loop.start()
    try:

        async def _raise() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"):
            loop.run(_raise())
    finally:
        loop.stop()


def test_loop_is_daemon_and_idempotent_stop() -> None:
    loop = BackgroundLoop()
    loop.start()
    loop.start()  # idempotent
    loop.stop()
    loop.stop()  # idempotent
