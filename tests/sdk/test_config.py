"""Config resolution precedence tests."""

from __future__ import annotations

import pytest

from tename.sdk._config import (
    ANTHROPIC_API_KEY_ENV,
    DATABASE_URL_ENV,
    VAULT_PASSPHRASE_ENV,
    resolve_config,
)
from tename.sdk.errors import ConfigurationError


def test_explicit_args_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql://env")
    monkeypatch.setenv(ANTHROPIC_API_KEY_ENV, "env-key")
    monkeypatch.setenv(VAULT_PASSPHRASE_ENV, "env-pw")

    cfg = resolve_config(
        database_url="postgresql://arg",
        anthropic_api_key="arg-key",
        profiles_dir="/tmp/profiles",
        vault_path="/tmp/v.enc",
        vault_passphrase="arg-pw",
    )
    assert cfg.database_url == "postgresql://arg"
    assert cfg.anthropic_api_key == "arg-key"
    assert cfg.profiles_dir == "/tmp/profiles"
    assert cfg.vault_path == "/tmp/v.enc"
    assert cfg.vault_passphrase == "arg-pw"


def test_env_used_when_args_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql://env")
    monkeypatch.setenv(ANTHROPIC_API_KEY_ENV, "env-key")
    monkeypatch.setenv(VAULT_PASSPHRASE_ENV, "env-pw")

    cfg = resolve_config(
        database_url=None,
        anthropic_api_key=None,
        profiles_dir=None,
        vault_path=None,
        vault_passphrase=None,
    )
    assert cfg.database_url == "postgresql://env"
    assert cfg.anthropic_api_key == "env-key"
    assert cfg.vault_passphrase == "env-pw"


def test_missing_database_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
    with pytest.raises(ConfigurationError, match="database_url"):
        resolve_config(
            database_url=None,
            anthropic_api_key=None,
            profiles_dir=None,
            vault_path=None,
            vault_passphrase=None,
        )
