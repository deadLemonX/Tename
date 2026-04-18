"""Sandbox module: isolated execution of LLM-generated code.

v0.1 ships a single Docker backend; the `SandboxBackend` ABC is
designed so Firecracker / E2B / Modal backends can slot in later
without touching the harness.

Public API:

    from tename.sandbox import Sandbox, DockerBackend, SandboxRecipe

    backend = DockerBackend()
    sandbox = Sandbox(backend)

    sandbox_id = await sandbox.provision(SandboxRecipe())
    result = await sandbox.execute(sandbox_id, "python", {"code": "print('hi')"})
    await sandbox.destroy(sandbox_id)
"""

from tename.sandbox.backends.docker import DockerBackend, SandboxNotFoundError
from tename.sandbox.base import SandboxBackend
from tename.sandbox.schemas import BUILTIN_TOOL_SCHEMAS
from tename.sandbox.service import Sandbox
from tename.sandbox.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    assert_transition,
)
from tename.sandbox.tools import BUILTIN_TOOL_NAMES, get_tool, is_builtin
from tename.sandbox.types import (
    NetworkPolicy,
    SandboxRecipe,
    SandboxStatus,
    ToolResult,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "BUILTIN_TOOL_NAMES",
    "BUILTIN_TOOL_SCHEMAS",
    "DockerBackend",
    "InvalidTransitionError",
    "NetworkPolicy",
    "Sandbox",
    "SandboxBackend",
    "SandboxNotFoundError",
    "SandboxRecipe",
    "SandboxStatus",
    "ToolResult",
    "assert_transition",
    "get_tool",
    "is_builtin",
]
