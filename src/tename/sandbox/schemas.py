"""`ToolDef` schemas for the built-in sandbox tools.

Lives beside the tool implementations so a framework adapter can
surface them to the model router without importing docker. Schemas are
JSONSchema — the shape the Model Router accepts via `ToolDef`.

Keep these aligned with the input contracts in
`tename.sandbox.tools.*`; if a tool gains a parameter, update the
schema here too.
"""

from __future__ import annotations

from typing import Any

from tename.router.types import ToolDef


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


_BASH_SCHEMA: dict[str, Any] = _schema(
    {
        "command": {
            "type": "string",
            "description": "Shell command to run via `bash -lc`.",
        }
    },
    ["command"],
)

_PYTHON_SCHEMA: dict[str, Any] = _schema(
    {
        "code": {
            "type": "string",
            "description": (
                "Python source to execute. Runs as a script in the sandbox "
                "with stdout/stderr captured."
            ),
        }
    },
    ["code"],
)

_FILE_READ_SCHEMA: dict[str, Any] = _schema(
    {
        "path": {
            "type": "string",
            "description": "Absolute path to read inside the sandbox filesystem.",
        }
    },
    ["path"],
)

_FILE_WRITE_SCHEMA: dict[str, Any] = _schema(
    {
        "path": {"type": "string", "description": "Absolute path to write."},
        "content": {"type": "string", "description": "File content."},
    },
    ["path", "content"],
)

_FILE_EDIT_SCHEMA: dict[str, Any] = _schema(
    {
        "path": {"type": "string"},
        "old_str": {"type": "string"},
        "new_str": {"type": "string"},
        "replace_all": {
            "type": "boolean",
            "description": "Replace every match instead of erroring on ambiguous matches.",
        },
    },
    ["path", "old_str", "new_str"],
)

_FILE_LIST_SCHEMA: dict[str, Any] = _schema(
    {
        "path": {
            "type": "string",
            "description": "Directory to list (defaults to /workspace).",
        }
    },
    [],
)


BUILTIN_TOOL_SCHEMAS: dict[str, ToolDef] = {
    "bash": ToolDef(
        name="bash",
        description="Execute a shell command inside the sandbox. Returns stdout, stderr, and exit code.",
        input_schema=_BASH_SCHEMA,
    ),
    "python": ToolDef(
        name="python",
        description=(
            "Execute Python code inside the sandbox. Returns stdout, stderr, and the "
            "exit status. Use for calculations, data manipulation, and running scripts."
        ),
        input_schema=_PYTHON_SCHEMA,
    ),
    "file_read": ToolDef(
        name="file_read",
        description="Read a file from the sandbox filesystem by absolute path.",
        input_schema=_FILE_READ_SCHEMA,
    ),
    "file_write": ToolDef(
        name="file_write",
        description="Create or overwrite a file in the sandbox at an absolute path.",
        input_schema=_FILE_WRITE_SCHEMA,
    ),
    "file_edit": ToolDef(
        name="file_edit",
        description=(
            "Apply a string-replacement edit to an existing sandbox file. Errors on "
            "ambiguous matches unless `replace_all` is true."
        ),
        input_schema=_FILE_EDIT_SCHEMA,
    ),
    "file_list": ToolDef(
        name="file_list",
        description="List files in a sandbox directory. Defaults to /workspace.",
        input_schema=_FILE_LIST_SCHEMA,
    ),
}
"""Public map: sandbox tool name → `ToolDef` adapters surface to the model."""


__all__ = ["BUILTIN_TOOL_SCHEMAS"]
