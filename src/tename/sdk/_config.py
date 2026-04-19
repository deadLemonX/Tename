"""Configuration resolution for the SDK client.

Precedence (highest wins): explicit constructor args > environment
variables > compiled-in defaults. Missing required values raise
`ConfigurationError` with an actionable message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from tename.sdk.errors import ConfigurationError

DATABASE_URL_ENV = "TENAME_DATABASE_URL"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
VAULT_PASSPHRASE_ENV = "TENAME_VAULT_PASSPHRASE"
PROFILES_DIR_ENV = "TENAME_PROFILES_DIR"


@dataclass(frozen=True)
class ResolvedConfig:
    database_url: str
    anthropic_api_key: str | None
    profiles_dir: str | None
    vault_path: str | None
    vault_passphrase: str | None


def resolve_config(
    *,
    database_url: str | None,
    anthropic_api_key: str | None,
    profiles_dir: str | None,
    vault_path: str | None,
    vault_passphrase: str | None,
) -> ResolvedConfig:
    db_url = database_url or os.environ.get(DATABASE_URL_ENV)
    if not db_url:
        raise ConfigurationError(
            f"database_url is required (pass it explicitly or set {DATABASE_URL_ENV})"
        )

    api_key = anthropic_api_key or os.environ.get(ANTHROPIC_API_KEY_ENV)
    prof_dir = profiles_dir or os.environ.get(PROFILES_DIR_ENV)
    passphrase = vault_passphrase or os.environ.get(VAULT_PASSPHRASE_ENV)

    return ResolvedConfig(
        database_url=db_url,
        anthropic_api_key=api_key,
        profiles_dir=prof_dir,
        vault_path=vault_path,
        vault_passphrase=passphrase,
    )


__all__ = [
    "ANTHROPIC_API_KEY_ENV",
    "DATABASE_URL_ENV",
    "PROFILES_DIR_ENV",
    "VAULT_PASSPHRASE_ENV",
    "ResolvedConfig",
    "resolve_config",
]
