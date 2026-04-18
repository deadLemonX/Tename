"""Filesystem built-ins: file_read, file_write, file_edit, file_list.

All paths are inside the sandbox container; the caller is the agent.
`file_read` and `file_list` read via `cat`/`ls`; `file_write` and
`file_edit` upload via tarfile (`put_archive`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tename.sandbox.tools._exec import put_file, run_exec
from tename.sandbox.types import SandboxRecipe, ToolResult

if TYPE_CHECKING:
    from docker.models.containers import Container


def file_read_tool(
    container: Container, input: dict[str, Any], recipe: SandboxRecipe
) -> ToolResult:
    path = input.get("path")
    if not isinstance(path, str) or not path:
        return _missing("file_read", "path")

    exit_code, stdout, stderr = run_exec(container, ["cat", "--", path])
    if exit_code != 0:
        msg = stderr.strip() or f"file_read exited with code {exit_code}"
        return ToolResult(
            is_error=True,
            content=msg,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            error=msg,
        )
    return ToolResult(
        is_error=False,
        content=stdout,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
    )


def file_write_tool(
    container: Container, input: dict[str, Any], recipe: SandboxRecipe
) -> ToolResult:
    path = input.get("path")
    content = input.get("content")
    if not isinstance(path, str) or not path:
        return _missing("file_write", "path")
    if not isinstance(content, str):
        return ToolResult(
            is_error=True,
            error="file_write: 'content' must be a string",
            content="file_write: 'content' must be a string",
        )

    try:
        put_file(container, path, content)
    except Exception as exc:
        return ToolResult(
            is_error=True,
            error=f"file_write: {exc}",
            content=f"file_write: {exc}",
        )
    return ToolResult(
        is_error=False,
        content=f"wrote {len(content)} bytes to {path}",
    )


def file_edit_tool(
    container: Container, input: dict[str, Any], recipe: SandboxRecipe
) -> ToolResult:
    path = input.get("path")
    old_str = input.get("old_str")
    new_str = input.get("new_str")
    replace_all = bool(input.get("replace_all", False))
    if not isinstance(path, str) or not path:
        return _missing("file_edit", "path")
    if not isinstance(old_str, str):
        return ToolResult(
            is_error=True,
            error="file_edit: 'old_str' must be a string",
            content="file_edit: 'old_str' must be a string",
        )
    if not isinstance(new_str, str):
        return ToolResult(
            is_error=True,
            error="file_edit: 'new_str' must be a string",
            content="file_edit: 'new_str' must be a string",
        )

    read_code, original, read_err = run_exec(container, ["cat", "--", path])
    if read_code != 0:
        msg = read_err.strip() or f"file_edit: cannot read {path} (exit {read_code})"
        return ToolResult(
            is_error=True,
            content=msg,
            stderr=read_err,
            exit_code=read_code,
            error=msg,
        )

    matches = original.count(old_str)
    if matches == 0:
        msg = f"file_edit: old_str not found in {path}"
        return ToolResult(is_error=True, content=msg, error=msg)
    if matches > 1 and not replace_all:
        msg = (
            f"file_edit: old_str matches {matches} locations in {path}; "
            "pass replace_all=true to replace every match"
        )
        return ToolResult(is_error=True, content=msg, error=msg)

    if replace_all:
        updated = original.replace(old_str, new_str)
    else:
        updated = original.replace(old_str, new_str, 1)

    try:
        put_file(container, path, updated)
    except Exception as exc:
        return ToolResult(
            is_error=True,
            error=f"file_edit: {exc}",
            content=f"file_edit: {exc}",
        )

    return ToolResult(
        is_error=False,
        content=f"edited {path} ({matches} replacement{'s' if matches != 1 else ''})",
    )


def file_list_tool(
    container: Container, input: dict[str, Any], recipe: SandboxRecipe
) -> ToolResult:
    path = input.get("path", "/workspace")
    if not isinstance(path, str) or not path:
        return _missing("file_list", "path")

    exit_code, stdout, stderr = run_exec(container, ["ls", "-la", "--", path])
    if exit_code != 0:
        msg = stderr.strip() or f"file_list exited with code {exit_code}"
        return ToolResult(
            is_error=True,
            content=msg,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            error=msg,
        )
    return ToolResult(
        is_error=False,
        content=stdout,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
    )


def _missing(tool: str, field: str) -> ToolResult:
    msg = f"{tool}: missing required input '{field}' (non-empty string)"
    return ToolResult(is_error=True, error=msg, content=msg)


__all__ = [
    "file_edit_tool",
    "file_list_tool",
    "file_read_tool",
    "file_write_tool",
]
