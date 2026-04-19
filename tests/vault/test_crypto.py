"""Crypto-level tests: key derivation + Fernet roundtrip."""

from __future__ import annotations

import pytest

from tename.vault.crypto import (
    DEFAULT_ITERATIONS,
    KeyParameters,
    decrypt_value,
    derive_key,
    encrypt_value,
    generate_salt,
)
from tename.vault.exceptions import VaultLockedError


def _params() -> KeyParameters:
    return KeyParameters(salt=b"\x00" * 16, iterations=10_000)


def test_derive_key_is_deterministic() -> None:
    params = _params()
    assert derive_key("hunter2", params) == derive_key("hunter2", params)


def test_derive_key_depends_on_passphrase() -> None:
    params = _params()
    assert derive_key("a", params) != derive_key("b", params)


def test_derive_key_depends_on_salt() -> None:
    a = KeyParameters(salt=b"\x00" * 16, iterations=10_000)
    b = KeyParameters(salt=b"\x11" * 16, iterations=10_000)
    assert derive_key("shared", a) != derive_key("shared", b)


def test_derive_key_rejects_empty_passphrase() -> None:
    with pytest.raises(VaultLockedError):
        derive_key("", _params())


def test_encrypt_decrypt_roundtrip() -> None:
    key = derive_key("pw", _params())
    token = encrypt_value(key, "hello world")
    assert token != "hello world"
    assert decrypt_value(key, token) == "hello world"


def test_decrypt_with_wrong_key_raises_vault_locked() -> None:
    correct = derive_key("pw", _params())
    wrong = derive_key("other", _params())
    token = encrypt_value(correct, "secret")
    with pytest.raises(VaultLockedError):
        decrypt_value(wrong, token)


def test_decrypt_tampered_token_raises_vault_locked() -> None:
    key = derive_key("pw", _params())
    token = encrypt_value(key, "secret")
    mutated = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    with pytest.raises(VaultLockedError):
        decrypt_value(key, mutated)


def test_generate_salt_has_expected_length() -> None:
    assert len(generate_salt()) == 16


def test_generate_salt_is_non_deterministic() -> None:
    assert generate_salt() != generate_salt()


def test_default_iterations_meets_owasp_floor() -> None:
    assert DEFAULT_ITERATIONS >= 600_000
