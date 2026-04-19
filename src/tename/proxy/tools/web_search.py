"""Minimal `web_search` proxy tool — a working example of the pattern.

Uses Tavily's search API because it's the simplest "POST query, get
results" service with a free tier. Users who prefer a different
provider can either override `TENAME_WEB_SEARCH_URL` or register their
own `web_search`-named proxy tool (which will raise the collision
error — so they should use a different name).

The credential name is `web_search_api_key`; store it with
`tename vault set web_search_api_key` or `Vault(...).store(...)`. The
tool raises no credential into stdout, stderr, or event payloads —
that's the whole point of the proxy.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from tename.proxy.decorators import proxy_tool

DEFAULT_ENDPOINT = "https://api.tavily.com/search"
ENDPOINT_ENV = "TENAME_WEB_SEARCH_URL"
DEFAULT_TIMEOUT_SECONDS = 15.0

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query string.",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum results to return (default 5, max 20).",
            "minimum": 1,
            "maximum": 20,
        },
    },
    "required": ["query"],
}


@proxy_tool(
    name="web_search",
    credential_names=["web_search_api_key"],
    description=(
        "Search the web. Returns a JSON string of result objects, each with "
        "`title`, `url`, and `content` fields. Use when the agent needs "
        "up-to-date information from the public internet."
    ),
    input_schema=_SCHEMA,
)
async def web_search(input: dict[str, Any], credentials: dict[str, str]) -> dict[str, Any]:
    """Call a Tavily-compatible endpoint and return a structured result.

    Returned dict shape::

        {
          "is_error": bool,
          "content": "<json-string-of-results-or-error-text>",
        }

    The ToolProxy normalizes this to a `ToolResult`. We return a dict
    rather than a `ToolResult` to keep the tool function framework-free
    — anyone reusing this pattern can copy-paste.
    """
    query = input.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"is_error": True, "content": "web_search requires a non-empty 'query' string"}

    max_results = input.get("max_results") or 5
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        return {"is_error": True, "content": "web_search 'max_results' must be an integer"}

    api_key = credentials.get("web_search_api_key")
    if not api_key:
        return {
            "is_error": True,
            "content": (
                "web_search credential 'web_search_api_key' is missing. "
                "Store one with `tename vault set web_search_api_key`."
            ),
        }

    endpoint = os.environ.get(ENDPOINT_ENV, DEFAULT_ENDPOINT)
    body = {
        "query": query,
        "api_key": api_key,
        "max_results": max(1, min(20, max_results)),
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
            response = await client.post(endpoint, json=body)
    except httpx.HTTPError as exc:
        return {"is_error": True, "content": f"web_search network error: {exc}"}

    if response.status_code >= 400:
        return {
            "is_error": True,
            "content": f"web_search HTTP {response.status_code}: {response.text[:500]}",
        }

    try:
        data = response.json()
    except ValueError:
        return {
            "is_error": True,
            "content": f"web_search non-JSON response: {response.text[:500]}",
        }

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        results = []
    summary = [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "content": item.get("content") or item.get("snippet"),
        }
        for item in results
        if isinstance(item, dict)
    ]
    return {"is_error": False, "content": json.dumps(summary)}


__all__ = ["DEFAULT_ENDPOINT", "ENDPOINT_ENV", "web_search"]
