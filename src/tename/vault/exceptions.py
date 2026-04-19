"""Exceptions raised by the Vault.

Kept in their own module so callers can catch them without importing
service or crypto internals.
"""

from __future__ import annotations


class VaultError(Exception):
    """Base for all vault-related failures."""


class VaultLockedError(VaultError):
    """Raised when the vault cannot be opened.

    Typical causes: passphrase is missing, wrong passphrase, or the
    encrypted file is corrupt. The error message is intentionally
    generic — we do NOT want to leak "passphrase is wrong" vs. "file
    is truncated" to an attacker with read access to stderr.
    """


class VaultCredentialNotFoundError(VaultError):
    """Raised when `retrieve(name)` is called for an unknown credential."""


class VaultConfigurationError(VaultError):
    """Raised for structural configuration problems.

    Examples: unreadable vault file, unsupported version number in
    an existing vault, passphrase missing at open time.
    """


__all__ = [
    "VaultConfigurationError",
    "VaultCredentialNotFoundError",
    "VaultError",
    "VaultLockedError",
]
