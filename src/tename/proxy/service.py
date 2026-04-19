"""ToolProxy: executes registered proxy tools with credential injection.

The proxy is the *only* component that reads credentials from the vault
and passes them to external-network code. It returns a `ToolResult`
whose payload structurally cannot contain the credential.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tename.proxy.registry import ProxyTool, get_proxy_tool
from tename.sandbox.types import ToolResult
from tename.vault.exceptions import (
    VaultConfigurationError,
    VaultCredentialNotFoundError,
    VaultError,
)

if TYPE_CHECKING:
    from tename.vault import Vault

logger = logging.getLogger(__name__)


class ToolProxy:
    """Per-session facade around the proxy-tool registry.

    Args:
        vault: Vault handle used to fetch credentials at execute time.
            May be `None` for tools that declare no credentials; if a
            tool with declared credentials is invoked without a vault,
            the execution returns `is_error=True` with a clear message.
    """

    def __init__(self, vault: Vault | None = None) -> None:
        self._vault = vault

    async def execute(
        self,
        tool_name: str,
        input: dict[str, Any],
        session_id: UUID,
    ) -> ToolResult:
        """Run the proxy tool named `tool_name`.

        Guarantees:
        1. Credential values never appear in the returned `ToolResult`.
        2. Credential values never appear in log records emitted by
           this function.
        3. Unknown tools, missing credentials, and tool exceptions all
           surface as `ToolResult(is_error=True)` with an informative
           message — never as an uncaught raise — so the model sees the
           failure and can decide what to do next.
        """
        log_ctx = {"tool_name": tool_name, "session_id": str(session_id)}

        tool = get_proxy_tool(tool_name)
        if tool is None:
            logger.warning("proxy.execute.unknown_tool", extra=log_ctx)
            return _error_result(f"proxy tool {tool_name!r} is not registered")

        try:
            credentials = self._resolve_credentials(tool)
        except _CredentialLookupError as exc:
            logger.warning(
                "proxy.execute.credential_error",
                extra={**log_ctx, "reason": exc.reason},
            )
            return _error_result(exc.user_message)

        logger.info(
            "proxy.execute.start",
            extra={
                **log_ctx,
                "credential_names": list(tool.credential_names),
            },
        )
        try:
            raw = await tool.fn(input, credentials)
        except Exception as exc:
            logger.exception("proxy.execute.fail", extra=log_ctx)
            return _error_result(f"proxy tool {tool_name!r} raised {type(exc).__name__}: {exc}")

        result = _normalize_result(raw)
        logger.info(
            "proxy.execute.ok",
            extra={**log_ctx, "is_error": result.is_error},
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_credentials(self, tool: ProxyTool) -> dict[str, str]:
        if not tool.credential_names:
            return {}
        if self._vault is None:
            raise _CredentialLookupError(
                reason="no_vault",
                user_message=(
                    f"proxy tool {tool.name!r} needs credentials "
                    f"({', '.join(tool.credential_names)}) but no vault is configured"
                ),
            )
        out: dict[str, str] = {}
        for cred_name in tool.credential_names:
            try:
                out[cred_name] = self._vault.retrieve(cred_name)
            except VaultCredentialNotFoundError as exc:
                raise _CredentialLookupError(
                    reason="credential_missing",
                    user_message=(
                        f"proxy tool {tool.name!r} requires credential {cred_name!r} "
                        "which is not stored in the vault"
                    ),
                ) from exc
            except VaultConfigurationError as exc:
                raise _CredentialLookupError(
                    reason="vault_misconfigured",
                    user_message=(f"vault is not usable: {exc}"),
                ) from exc
            except VaultError as exc:
                raise _CredentialLookupError(
                    reason="vault_error",
                    user_message=(
                        f"vault failed for credential {cred_name!r}: {type(exc).__name__}"
                    ),
                ) from exc
        return out


class _CredentialLookupError(Exception):
    """Internal signal for credential resolution failures.

    Carries a `reason` tag for structured logging and a `user_message`
    that's safe to return in the ToolResult. Never escapes the module.
    """

    def __init__(self, *, reason: str, user_message: str) -> None:
        super().__init__(user_message)
        self.reason = reason
        self.user_message = user_message


def _normalize_result(raw: object) -> ToolResult:
    """Coerce whatever the tool function returned into a `ToolResult`.

    Accepted shapes:
      - `ToolResult` → passthrough
      - `str` → becomes `ToolResult(content=...)`
      - `dict` → JSON-serialized into `content`; `is_error` honored
         when present; `error` string surfaced when present.
    Anything else is a programming error and surfaces as is_error=True.
    """
    if isinstance(raw, ToolResult):
        return raw
    if isinstance(raw, str):
        return ToolResult(content=raw)
    if isinstance(raw, dict):
        import json

        is_error = bool(raw.get("is_error", False))
        error = raw.get("error")
        content_field = raw.get("content")
        content = content_field if isinstance(content_field, str) else json.dumps(raw, default=str)
        return ToolResult(
            is_error=is_error,
            content=content,
            error=error if isinstance(error, str) else None,
        )
    return _error_result(
        f"proxy tool returned unsupported type {type(raw).__name__}; "
        "return a ToolResult, str, or dict"
    )


def _error_result(message: str) -> ToolResult:
    return ToolResult(is_error=True, content=message, error=message)


__all__ = ["ToolProxy"]
