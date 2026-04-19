"""Module-global registry for proxy tools.

Proxy tools register themselves at import time via `@proxy_tool(...)`.
The registry is process-global — the same pattern we use for framework
adapters. Tests reset it via `_clear_registry_for_testing()`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from tename.router.types import ToolDef

logger = logging.getLogger(__name__)

ProxyToolFn = Callable[[dict[str, Any], dict[str, str]], Awaitable[Any]]
"""Signature proxy-tool functions must satisfy.

First arg is the model-supplied `input` dict. Second arg is a credentials
dict, keys scoped to the tool's declared `credential_names`. Functions
may return a `dict` (used as tool_result content), a string, or a
`tename.sandbox.ToolResult`; the proxy normalizes all three.
"""


@dataclass(frozen=True)
class ProxyTool:
    """Registration record for a single proxy tool."""

    name: str
    credential_names: tuple[str, ...]
    description: str
    input_schema: dict[str, Any]
    fn: ProxyToolFn

    def to_tool_def(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )


_REGISTRY: dict[str, ProxyTool] = {}


def register_proxy_tool(tool: ProxyTool) -> None:
    """Register a proxy tool under `tool.name`.

    Idempotent when the same `ProxyTool` (by identity) is registered
    again under the same name — common with `from ... import` reloads.
    Raises `ValueError` when a *different* tool tries to take the same
    name, to make collisions loud at import time rather than at runtime.
    """
    existing = _REGISTRY.get(tool.name)
    if existing is tool:
        return
    if existing is not None:
        raise ValueError(
            f"proxy tool {tool.name!r} is already registered by a different implementation"
        )
    _REGISTRY[tool.name] = tool
    logger.debug("proxy.register", extra={"tool_name": tool.name})


def get_proxy_tool(name: str) -> ProxyTool | None:
    """Look up a registered proxy tool by name. None if unregistered."""
    return _REGISTRY.get(name)


def proxy_tool_names() -> frozenset[str]:
    """Return the immutable set of currently-registered proxy tool names."""
    return frozenset(_REGISTRY.keys())


def proxy_tool_schemas() -> dict[str, ToolDef]:
    """Return a fresh `name → ToolDef` mapping for currently-registered tools.

    Adapters call this every build_context to surface the user's
    currently-available proxy tools alongside sandbox built-ins.
    """
    return {name: tool.to_tool_def() for name, tool in _REGISTRY.items()}


def clear_registry_for_testing() -> None:
    """Clear the proxy-tool registry. Test-only.

    Production code MUST NOT call this. It exists so unit tests can
    register scratch tools without polluting subsequent tests. The
    public name is deliberately ugly so nobody reaches for it in
    application code by accident.
    """
    _REGISTRY.clear()


__all__ = [
    "ProxyTool",
    "ProxyToolFn",
    "clear_registry_for_testing",
    "get_proxy_tool",
    "proxy_tool_names",
    "proxy_tool_schemas",
    "register_proxy_tool",
]
