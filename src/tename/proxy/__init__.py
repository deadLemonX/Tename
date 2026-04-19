"""Tool Proxy: executes credentialed tools outside the sandbox.

The proxy sits between the harness and external APIs (search, MCP
servers, custom HTTP tools). Credentials live in the `Vault`; the
proxy pulls them at call time and passes them to the tool function,
which makes the outbound request. The model never sees the credential
and the sandbox never touches it.

Public API::

    from tename.proxy import ToolProxy, proxy_tool

    @proxy_tool(
        name="my_tool",
        credential_names=["my_api_key"],
        description="...",
        input_schema={...},
    )
    async def my_tool(input, credentials):
        ...

    proxy = ToolProxy(vault)
    result = await proxy.execute("my_tool", {"...": "..."}, session_id)

See `docs/architecture/tool-proxy.md` for the security model.
"""

# Import built-in tools for their registration side-effects. Users who
# want to register their own proxy tools import their module somewhere
# early in application setup; the registry is process-global.
from tename.proxy import tools as _tools  # pyright: ignore[reportUnusedImport]
from tename.proxy.decorators import proxy_tool
from tename.proxy.registry import (
    ProxyTool,
    get_proxy_tool,
    proxy_tool_names,
    proxy_tool_schemas,
    register_proxy_tool,
)
from tename.proxy.service import ToolProxy

__all__ = [
    "ProxyTool",
    "ToolProxy",
    "get_proxy_tool",
    "proxy_tool",
    "proxy_tool_names",
    "proxy_tool_schemas",
    "register_proxy_tool",
]
