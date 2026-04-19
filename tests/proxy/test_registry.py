"""Proxy registry + decorator unit tests."""

from __future__ import annotations

from typing import Any

import pytest

from tename.proxy.decorators import proxy_tool
from tename.proxy.registry import (
    ProxyTool,
    get_proxy_tool,
    proxy_tool_names,
    proxy_tool_schemas,
    register_proxy_tool,
)


def _schema() -> dict[str, Any]:
    return {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


def test_decorator_registers_tool() -> None:
    @proxy_tool(
        name="my_tool",
        credential_names=["my_key"],
        description="does a thing",
        input_schema=_schema(),
    )
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        return {"content": "ok"}

    tool = get_proxy_tool("my_tool")
    assert tool is not None
    assert tool.name == "my_tool"
    assert tool.credential_names == ("my_key",)
    assert tool.description == "does a thing"


def test_registry_lookup_none_when_unknown() -> None:
    assert get_proxy_tool("nope") is None


def test_proxy_tool_names_returns_immutable_set() -> None:
    @proxy_tool(
        name="t1",
        credential_names=[],
        description="",
        input_schema=_schema(),
    )
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        return {}

    names = proxy_tool_names()
    assert "t1" in names
    assert isinstance(names, frozenset)


def test_proxy_tool_schemas_returns_tool_defs() -> None:
    @proxy_tool(
        name="t_with_schema",
        credential_names=[],
        description="desc",
        input_schema=_schema(),
    )
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        return {}

    schemas = proxy_tool_schemas()
    assert "t_with_schema" in schemas
    assert schemas["t_with_schema"].name == "t_with_schema"
    assert schemas["t_with_schema"].description == "desc"
    assert schemas["t_with_schema"].input_schema == _schema()


def test_register_same_tool_twice_is_idempotent() -> None:
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        return {}

    tool = ProxyTool(
        name="dup",
        credential_names=(),
        description="",
        input_schema=_schema(),
        fn=_fn,
    )
    register_proxy_tool(tool)
    register_proxy_tool(tool)  # same instance — no-op
    assert get_proxy_tool("dup") is tool


def test_register_different_tool_under_same_name_raises() -> None:
    async def _fn1(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        return {}

    async def _fn2(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        return {}

    t1 = ProxyTool(
        name="clash",
        credential_names=(),
        description="",
        input_schema=_schema(),
        fn=_fn1,
    )
    t2 = ProxyTool(
        name="clash",
        credential_names=(),
        description="",
        input_schema=_schema(),
        fn=_fn2,
    )
    register_proxy_tool(t1)
    with pytest.raises(ValueError, match="already registered"):
        register_proxy_tool(t2)
