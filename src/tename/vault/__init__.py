"""Vault module: encrypted on-disk credential storage.

Credentials stored in a vault never appear in event payloads, never
reach the sandbox, and are decrypted only at the moment a proxy tool
needs them. See `docs/architecture/tool-proxy.md` for the security
model; see ADR 0003 for the S10 design choices.

Public API::

    from tename.vault import Vault

    vault = Vault()           # reads TENAME_VAULT_PASSPHRASE from env
    vault.store("web_search_api_key", "sk-...")
    token = vault.retrieve("web_search_api_key")
    vault.revoke("web_search_api_key")
    names = vault.list()
"""

from tename.vault.exceptions import (
    VaultConfigurationError,
    VaultCredentialNotFoundError,
    VaultError,
    VaultLockedError,
)
from tename.vault.service import (
    DEFAULT_VAULT_PATH,
    VAULT_FILE_VERSION,
    VAULT_PASSPHRASE_ENV,
    Vault,
)

__all__ = [
    "DEFAULT_VAULT_PATH",
    "VAULT_FILE_VERSION",
    "VAULT_PASSPHRASE_ENV",
    "Vault",
    "VaultConfigurationError",
    "VaultCredentialNotFoundError",
    "VaultError",
    "VaultLockedError",
]
