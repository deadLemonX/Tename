"""Built-in sandbox tools.

Each tool is a sync callable (they run on worker threads via
`asyncio.to_thread`) taking a docker container handle, an input dict,
and a recipe, and returning a `ToolResult`. The registry maps tool
names to implementations; `DockerBackend.execute` dispatches via this
registry.

Why sync and not async: every operation a sandbox tool does is either a
blocking docker SDK call or a trivial string transform. Making the
callables themselves sync keeps the tool code free of the ceremony
(`await`, `asyncio.to_thread`, etc.) that belongs at the backend
boundary. The backend is what owns the async contract.

v0.1 ships six built-ins: bash, python, file_read, file_write,
file_edit, file_list. User-defined tools are out of scope until the
SDK lands in S10.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tename.sandbox.tools.bash import bash_tool
from tename.sandbox.tools.file_ops import (
    file_edit_tool,
    file_list_tool,
    file_read_tool,
    file_write_tool,
)
from tename.sandbox.tools.python import python_tool
from tename.sandbox.types import SandboxRecipe, ToolResult

if TYPE_CHECKING:
    from docker.models.containers import Container

SandboxToolFn = Callable[["Container", dict[str, Any], SandboxRecipe], ToolResult]
"""The contract every built-in tool implementation satisfies."""


_BUILTIN_TOOLS: dict[str, SandboxToolFn] = {
    "bash": bash_tool,
    "python": python_tool,
    "file_read": file_read_tool,
    "file_write": file_write_tool,
    "file_edit": file_edit_tool,
    "file_list": file_list_tool,
}


BUILTIN_TOOL_NAMES: frozenset[str] = frozenset(_BUILTIN_TOOLS)
"""Names of all built-in sandbox tools. Used by the harness to route
tool calls: anything in this set goes to the sandbox; anything else
is either a proxy tool (S10) or unknown."""


def get_tool(name: str) -> SandboxToolFn:
    """Look up a built-in tool by name. Raises `KeyError` on unknown."""
    return _BUILTIN_TOOLS[name]


def is_builtin(name: str) -> bool:
    """Check if a tool name is a v0.1 built-in."""
    return name in _BUILTIN_TOOLS


__all__ = [
    "BUILTIN_TOOL_NAMES",
    "SandboxToolFn",
    "get_tool",
    "is_builtin",
]
