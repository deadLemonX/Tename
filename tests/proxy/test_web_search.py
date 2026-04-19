"""Tests for the built-in `web_search` proxy tool."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tename.proxy import ToolProxy
from tename.proxy.registry import get_proxy_tool
from tename.proxy.tools.web_search import ENDPOINT_ENV
from tename.vault import Vault


def _vault_with_key(tmp_path: Path) -> Vault:
    v = Vault(path=tmp_path / "v.enc", passphrase="pw")
    v.store("web_search_api_key", "fake-key")
    return v


def test_web_search_is_auto_registered() -> None:
    tool = get_proxy_tool("web_search")
    assert tool is not None
    assert "web_search_api_key" in tool.credential_names


async def test_web_search_missing_query_returns_error(tmp_path: Path) -> None:
    proxy = ToolProxy(vault=_vault_with_key(tmp_path))
    result = await proxy.execute("web_search", {"query": ""}, uuid4())
    assert result.is_error is True
    assert "non-empty" in result.content


async def test_web_search_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENDPOINT_ENV, "https://fake.test/search")

    captured_bodies: list[dict[str, object]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "A", "url": "https://a", "content": "first"},
                    {"title": "B", "url": "https://b", "content": "second"},
                ]
            },
        )

    transport = httpx.MockTransport(_handler)

    # Patch the AsyncClient constructor used inside the tool so it
    # uses our mock transport.
    original = httpx.AsyncClient

    def _client_factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)

    proxy = ToolProxy(vault=_vault_with_key(tmp_path))
    result = await proxy.execute("web_search", {"query": "coffee"}, uuid4())

    assert result.is_error is False
    parsed = json.loads(result.content)
    assert parsed == [
        {"title": "A", "url": "https://a", "content": "first"},
        {"title": "B", "url": "https://b", "content": "second"},
    ]
    # The api_key crossed the wire, but the ToolResult content should
    # not expose it back to the harness.
    assert captured_bodies == [{"query": "coffee", "api_key": "fake-key", "max_results": 5}]
    assert "fake-key" not in result.content


async def test_web_search_http_error_surfaces_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENDPOINT_ENV, "https://fake.test/search")
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="internal oops"))
    original = httpx.AsyncClient

    def _client_factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)

    proxy = ToolProxy(vault=_vault_with_key(tmp_path))
    result = await proxy.execute("web_search", {"query": "x"}, uuid4())
    assert result.is_error is True
    assert "HTTP 500" in result.content


async def test_web_search_network_error_surfaces_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENDPOINT_ENV, "https://fake.test/search")

    def _raise(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns exploded")

    transport = httpx.MockTransport(_raise)
    original = httpx.AsyncClient

    def _client_factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)

    proxy = ToolProxy(vault=_vault_with_key(tmp_path))
    result = await proxy.execute("web_search", {"query": "x"}, uuid4())
    assert result.is_error is True
    assert "network error" in result.content


async def test_web_search_missing_credential_returns_clean_error(tmp_path: Path) -> None:
    # Vault exists but the key is not stored.
    vault = Vault(path=tmp_path / "v.enc", passphrase="pw")
    vault.store("other_key", "x")
    proxy = ToolProxy(vault=vault)
    result = await proxy.execute("web_search", {"query": "x"}, uuid4())
    assert result.is_error is True
    assert "web_search_api_key" in result.content
