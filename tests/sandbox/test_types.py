"""Unit tests for Sandbox Pydantic types (frozen / extra=forbid rules).

Pure — no Docker required.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tename.sandbox import SandboxRecipe, ToolResult


def test_recipe_defaults() -> None:
    recipe = SandboxRecipe()
    assert recipe.runtime == "python:3.12-slim"
    assert recipe.cpu_limit == 2
    assert recipe.memory_limit_mb == 4096
    assert recipe.timeout_seconds == 600
    assert recipe.network_policy == "open"
    assert recipe.packages == []
    assert recipe.files == {}
    assert recipe.env == {}


def test_recipe_frozen() -> None:
    recipe = SandboxRecipe()
    with pytest.raises(ValidationError):
        recipe.runtime = "node:20"  # pyright: ignore[reportAttributeAccessIssue]


def test_recipe_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        SandboxRecipe.model_validate({"runtime": "python:3.12-slim", "unknown": 42})


def test_recipe_cpu_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SandboxRecipe(cpu_limit=0)


def test_recipe_memory_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SandboxRecipe(memory_limit_mb=0)


def test_recipe_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SandboxRecipe(timeout_seconds=0)


def test_tool_result_defaults() -> None:
    r = ToolResult()
    assert r.is_error is False
    assert r.content == ""
    assert r.stdout == ""
    assert r.stderr == ""
    assert r.exit_code is None
    assert r.error is None


def test_tool_result_frozen() -> None:
    r = ToolResult()
    with pytest.raises(ValidationError):
        r.content = "x"  # pyright: ignore[reportAttributeAccessIssue]
