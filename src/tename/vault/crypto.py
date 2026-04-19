"""Key derivation and symmetric encryption helpers for the Vault.

We use PBKDF2-HMAC-SHA256 to derive a Fernet key from a user-supplied
passphrase plus a per-vault salt, then encrypt each credential with
Fernet (AES-128-CBC + HMAC-SHA256 + random IV, authenticated).

Fernet is overkill for "one file on disk," but it forecloses whole
categories of mistakes: it authenticates, it randomizes IVs, and it
binds a timestamp so we could add expiry later without a format change.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from tename.vault.exceptions import VaultLockedError

DEFAULT_ITERATIONS = 600_000
"""PBKDF2 iteration count.

600k SHA-256 matches OWASP's 2023 guidance for PBKDF2 passwords. Users
can override via the file's stored `iterations` field for forward
compatibility; we read whatever is there on an existing vault rather
than assuming the default.
"""

SALT_BYTES = 16
"""Length of the per-vault random salt. 128 bits is standard."""


@dataclass(frozen=True)
class KeyParameters:
    """Parameters needed to re-derive the Fernet key.

    The salt and iteration count travel with the encrypted file in
    plaintext — they need to, to let us decrypt later. What stays
    secret is the passphrase.
    """

    salt: bytes
    iterations: int


def generate_salt() -> bytes:
    """Fresh 16-byte random salt for a new vault."""
    return os.urandom(SALT_BYTES)


def derive_key(passphrase: str, params: KeyParameters) -> bytes:
    """Return a url-safe base64 Fernet key derived from `passphrase`.

    Same passphrase + same salt + same iteration count = same key.
    Different salt = different key, even with the same passphrase.
    """
    if not passphrase:
        raise VaultLockedError("vault passphrase is empty")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=params.salt,
        iterations=params.iterations,
    )
    raw = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def encrypt_value(key: bytes, plaintext: str) -> str:
    """Encrypt `plaintext` with `key`, returning a Fernet token string."""
    fernet = Fernet(key)
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_value(key: bytes, token: str) -> str:
    """Decrypt a Fernet token string with `key`.

    Any failure — wrong key, corrupt token, tampered payload — raises
    `VaultLockedError`. We deliberately do NOT surface the underlying
    `InvalidToken` so the caller can't distinguish "wrong passphrase"
    from "file was tampered with".
    """
    fernet = Fernet(key)
    try:
        plaintext = fernet.decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        raise VaultLockedError("vault credential failed authenticated decryption") from exc
    return plaintext.decode("utf-8")


__all__ = [
    "DEFAULT_ITERATIONS",
    "SALT_BYTES",
    "KeyParameters",
    "decrypt_value",
    "derive_key",
    "encrypt_value",
    "generate_salt",
]
