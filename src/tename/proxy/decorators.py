"""`@proxy_tool` decorator — the user-facing entry point for registration.

A decorated function becomes a `ProxyTool` and gets registered in the
global registry. The function itself stays callable from Python, which
is convenient for unit-testing the tool in isolation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from tename.proxy.registry import ProxyTool, ProxyToolFn, register_proxy_tool


def proxy_tool(
    *,
    name: str,
    credential_names: Iterable[str] = (),
    description: str,
    input_schema: dict[str, Any],
) -> Callable[[ProxyToolFn], ProxyToolFn]:
    """Decorate an async function to register it as a proxy tool.

    Args:
        name: Tool name the model will use (e.g. "web_search"). Must be
            unique across the registry.
        credential_names: Vault credential names the tool needs. The
            ToolProxy pulls each from the vault and passes them in the
            `credentials` dict at execution time.
        description: Human-readable description the model sees.
        input_schema: JSONSchema describing the tool's input.

    Example::

        @proxy_tool(
            name="web_search",
            credential_names=["web_search_api_key"],
            description="Search the web.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        async def web_search(input, credentials):
            ...
    """
    cred_tuple = tuple(credential_names)

    def _wrap(fn: ProxyToolFn) -> ProxyToolFn:
        tool = ProxyTool(
            name=name,
            credential_names=cred_tuple,
            description=description,
            input_schema=input_schema,
            fn=fn,
        )
        register_proxy_tool(tool)
        return fn

    return _wrap


__all__ = ["proxy_tool"]
