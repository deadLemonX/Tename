"""ToolProxy service tests — credential injection, error paths, logging."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from tename.proxy.decorators import proxy_tool
from tename.proxy.registry import ProxyTool, register_proxy_tool
from tename.proxy.service import ToolProxy
from tename.sandbox.types import ToolResult
from tename.vault import Vault


def _schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "required": []}


def _vault(tmp_path: Path) -> Vault:
    return Vault(path=tmp_path / "v.enc", passphrase="pw")


async def test_execute_unknown_tool_returns_error() -> None:
    proxy = ToolProxy(vault=None)
    result = await proxy.execute("nope", {}, uuid4())
    assert result.is_error is True
    assert "not registered" in result.content


async def test_execute_tool_with_no_credentials(tmp_path: Path) -> None:
    observed: list[tuple[dict[str, Any], dict[str, str]]] = []

    @proxy_tool(
        name="noauth",
        credential_names=[],
        description="no auth required",
        input_schema=_schema(),
    )
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        observed.append((input, credentials))
        return {"content": "hello", "is_error": False}

    proxy = ToolProxy(vault=_vault(tmp_path))
    result = await proxy.execute("noauth", {"q": "hi"}, uuid4())

    assert observed == [({"q": "hi"}, {})]
    assert result.is_error is False
    assert result.content == "hello"


async def test_execute_injects_vault_credentials(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.store("api_key", "SECRET-TOKEN-123")

    observed: list[dict[str, str]] = []

    @proxy_tool(
        name="with_auth",
        credential_names=["api_key"],
        description="needs an api key",
        input_schema=_schema(),
    )
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        observed.append(dict(credentials))
        return {"content": "done", "is_error": False}

    proxy = ToolProxy(vault=vault)
    result = await proxy.execute("with_auth", {}, uuid4())

    assert observed == [{"api_key": "SECRET-TOKEN-123"}]
    # Credential value never leaks into the ToolResult the harness sees.
    assert "SECRET-TOKEN-123" not in result.content
    assert result.error is None


async def test_execute_missing_credential_returns_error(tmp_path: Path) -> None:
    @proxy_tool(
        name="needs_cred",
        credential_names=["absent_key"],
        description="needs creds",
        input_schema=_schema(),
    )
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        return {"content": "unreachable"}

    proxy = ToolProxy(vault=_vault(tmp_path))
    result = await proxy.execute("needs_cred", {}, uuid4())
    assert result.is_error is True
    assert "absent_key" in result.content
    assert "not stored" in result.content


async def test_execute_with_credentialed_tool_and_no_vault_returns_error() -> None:
    @proxy_tool(
        name="needs_cred_no_vault",
        credential_names=["k"],
        description="needs creds",
        input_schema=_schema(),
    )
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        return {}

    proxy = ToolProxy(vault=None)
    result = await proxy.execute("needs_cred_no_vault", {}, uuid4())
    assert result.is_error is True
    assert "no vault is configured" in result.content


async def test_execute_tool_exception_surfaces_as_error(tmp_path: Path) -> None:
    @proxy_tool(
        name="boom",
        credential_names=[],
        description="raises",
        input_schema=_schema(),
    )
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        raise RuntimeError("kapow")

    proxy = ToolProxy(vault=_vault(tmp_path))
    result = await proxy.execute("boom", {}, uuid4())
    assert result.is_error is True
    assert "RuntimeError" in result.content
    assert "kapow" in result.content


async def test_execute_accepts_tool_result_return_value(tmp_path: Path) -> None:
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> Any:
        return ToolResult(content="explicit", is_error=False, stdout="so")

    register_proxy_tool(
        ProxyTool(
            name="tr_tool",
            credential_names=(),
            description="returns ToolResult",
            input_schema=_schema(),
            fn=_fn,
        )
    )

    proxy = ToolProxy(vault=_vault(tmp_path))
    result = await proxy.execute("tr_tool", {}, uuid4())
    assert result.is_error is False
    assert result.content == "explicit"
    assert result.stdout == "so"


async def test_execute_accepts_str_return_value(tmp_path: Path) -> None:
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> Any:
        return "plain string"

    register_proxy_tool(
        ProxyTool(
            name="str_tool",
            credential_names=(),
            description="returns str",
            input_schema=_schema(),
            fn=_fn,
        )
    )

    proxy = ToolProxy(vault=_vault(tmp_path))
    result = await proxy.execute("str_tool", {}, uuid4())
    assert result.is_error is False
    assert result.content == "plain string"


async def test_execute_rejects_unsupported_return_type(tmp_path: Path) -> None:
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> Any:
        return 42

    register_proxy_tool(
        ProxyTool(
            name="bad_return",
            credential_names=(),
            description="bad return",
            input_schema=_schema(),
            fn=_fn,
        )
    )

    proxy = ToolProxy(vault=_vault(tmp_path))
    result = await proxy.execute("bad_return", {}, uuid4())
    assert result.is_error is True
    assert "unsupported type" in result.content


async def test_credential_never_appears_in_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    vault = _vault(tmp_path)
    SECRET = "SUPER-SECRET-TOKEN-never-log-me"
    vault.store("key", SECRET)

    @proxy_tool(
        name="silent",
        credential_names=["key"],
        description="logs nothing",
        input_schema=_schema(),
    )
    async def _fn(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
        return {"content": "ok", "is_error": False}

    proxy = ToolProxy(vault=vault)
    with caplog.at_level(logging.DEBUG, logger="tename"):
        await proxy.execute("silent", {"q": "search"}, uuid4())

    for record in caplog.records:
        assert SECRET not in record.getMessage()
        for attr in vars(record).values():
            assert SECRET not in str(attr)
