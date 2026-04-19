"""Typed exceptions the Python SDK raises.

Each error type is specifically catchable so user code can react —
e.g. retry on `ModelError`, prompt for vault passphrase on `VaultError`.
Internal exceptions (Session Service, Sandbox, Vault, ...) are wrapped
at the SDK boundary so downstream consumers have a stable import.
"""

from __future__ import annotations


class TenameError(Exception):
    """Base class for every error raised by the SDK."""


class ConfigurationError(TenameError):
    """Missing or invalid configuration. Actionable: fix env vars or args."""


class NotFoundError(TenameError):
    """Referenced resource does not exist. Actionable: check the id."""


class ValidationError(TenameError):
    """Input failed validation. Actionable: fix the payload per the message."""


class ModelError(TenameError):
    """Upstream model provider returned an error.

    Attributes match the `error` chunk shape from the Model Router so
    callers can branch on `code` / `retry_after`.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        code: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retry_after = retry_after


class SandboxError(TenameError):
    """Sandbox operation failed. Actionable: check Docker, container logs."""


class VaultError(TenameError):
    """Vault operation failed. Actionable: check passphrase, file permissions."""


__all__ = [
    "ConfigurationError",
    "ModelError",
    "NotFoundError",
    "SandboxError",
    "TenameError",
    "ValidationError",
    "VaultError",
]
