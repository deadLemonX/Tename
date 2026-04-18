"""bash built-in: run a shell command via `bash -lc` inside the sandbox.

Input: {"command": str}
Result content: combined stdout + (if nonzero) stderr.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tename.sandbox.tools._exec import run_exec
from tename.sandbox.types import SandboxRecipe, ToolResult

if TYPE_CHECKING:
    from docker.models.containers import Container


def bash_tool(container: Container, input: dict[str, Any], recipe: SandboxRecipe) -> ToolResult:
    command = input.get("command")
    if not isinstance(command, str) or not command.strip():
        return ToolResult(
            is_error=True,
            error="bash: missing required input 'command' (non-empty string)",
            content="bash: missing required input 'command' (non-empty string)",
        )

    exit_code, stdout, stderr = run_exec(container, ["bash", "-lc", command])
    is_error = exit_code != 0
    content = stdout
    if stderr:
        content = f"{stdout}\n[stderr]\n{stderr}" if stdout else f"[stderr]\n{stderr}"
    return ToolResult(
        is_error=is_error,
        content=content,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        error=f"bash exited with code {exit_code}" if is_error else None,
    )


__all__ = ["bash_tool"]
