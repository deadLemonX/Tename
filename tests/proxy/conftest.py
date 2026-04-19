"""Shared fixtures for proxy tests.

Every test in `tests/proxy/` + the harness proxy integration tests
need a clean proxy registry so scratch tools registered in one test
don't leak into the next. Using an autouse fixture in one conftest
lets test modules stay focused on assertions.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tename.proxy.registry import clear_registry_for_testing


@pytest.fixture(autouse=True)
def fresh_proxy_registry() -> Iterator[None]:
    """Clear registry before and after each test, restoring built-ins.

    The registry is process-global, so any test that registers a
    scratch `@proxy_tool` pollutes subsequent tests. We wipe it before
    yielding, then re-register the built-in `web_search` tool so tests
    that expect it (or code paths downstream) still find it.
    """
    import importlib

    import tename.proxy.tools.web_search as ws

    clear_registry_for_testing()
    # Restore the built-in so every test starts in a known-good state.
    importlib.reload(ws)
    yield
    clear_registry_for_testing()
    importlib.reload(ws)
