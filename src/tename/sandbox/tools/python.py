"""python built-in: execute a Python snippet inside the sandbox.

Input: {"code": str}
Implementation: write the code to `/tmp/<uuid>.py` via `put_archive`,
then run `python /tmp/<uuid>.py`. Writing to a file avoids quoting
pitfalls around `python -c`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from tename.sandbox.tools._exec import put_file, run_exec
from tename.sandbox.types import SandboxRecipe, ToolResult

if TYPE_CHECKING:
    from docker.models.containers import Container


def python_tool(container: Container, input: dict[str, Any], recipe: SandboxRecipe) -> ToolResult:
    code = input.get("code")
    if not isinstance(code, str) or not code:
        return ToolResult(
            is_error=True,
            error="python: missing required input 'code' (non-empty string)",
            content="python: missing required input 'code' (non-empty string)",
        )

    script_path = f"/tmp/tename_{uuid4().hex}.py"
    try:
        put_file(container, script_path, code)
    except Exception as exc:
        return ToolResult(
            is_error=True,
            error=f"python: could not upload script: {exc}",
            content=f"python: could not upload script: {exc}",
        )

    exit_code, stdout, stderr = run_exec(container, ["python", script_path])
    # Best-effort cleanup; ignore failure — the sandbox is ephemeral.
    run_exec(container, ["rm", "-f", script_path])

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
        error=f"python exited with code {exit_code}" if is_error else None,
    )


__all__ = ["python_tool"]
