"""The Vault service — encrypted credential storage on disk.

A `Vault` is a handle around a single encrypted JSON file. Each
credential is individually encrypted with a Fernet key derived from a
user-supplied passphrase; the file format stores the per-vault salt and
iteration count in plaintext so the same passphrase can re-derive the
key on reopen.

All writes are atomic (tempfile + `os.replace`) so an interrupted store
can never corrupt an existing vault. File mode is `0o600` on POSIX.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

from tename.vault.crypto import (
    DEFAULT_ITERATIONS,
    KeyParameters,
    decrypt_value,
    derive_key,
    encrypt_value,
    generate_salt,
)
from tename.vault.exceptions import (
    VaultConfigurationError,
    VaultCredentialNotFoundError,
    VaultError,
)

logger = logging.getLogger(__name__)


DEFAULT_VAULT_PATH = "~/.tename/vault.json.enc"
"""Default on-disk location."""

VAULT_PASSPHRASE_ENV = "TENAME_VAULT_PASSPHRASE"
"""Environment variable consulted when no explicit passphrase is given."""

VAULT_FILE_VERSION = 1
"""Current on-disk format version. Bump for any non-backward-compatible change."""

FILE_MODE = 0o600
"""POSIX mode for the vault file (owner read/write only)."""

DIR_MODE = 0o700
"""POSIX mode for the vault's parent directory."""


class Vault:
    """Encrypted credential store for a single user.

    Thread-safe: a module-level `RLock` serializes read/write operations
    against the backing file. Instances are cheap — create one per
    session/request if you prefer, or share one.

    Args:
        path: Filesystem location of the encrypted vault. Defaults to
            `~/.tename/vault.json.enc`. The parent directory is created
            on first write.
        passphrase: Passphrase for key derivation. Defaults to the
            `TENAME_VAULT_PASSPHRASE` environment variable. Missing
            passphrase raises `VaultConfigurationError` only at the
            point where the key is actually needed (store/retrieve/
            revoke); pure `list()` reads do not derive the key.

    The passphrase itself is never persisted. The salt and iteration
    count travel with the file (they have to, to re-derive the key),
    but disclosing them does not weaken security.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        passphrase: str | None = None,
    ) -> None:
        resolved = Path(os.path.expanduser(str(path or DEFAULT_VAULT_PATH)))
        self._path: Path = resolved
        self._passphrase: str | None = (
            passphrase if passphrase is not None else os.environ.get(VAULT_PASSPHRASE_ENV)
        )
        self._lock = RLock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """Resolved filesystem path to the vault file."""
        return self._path

    def store(self, name: str, value: str) -> None:
        """Encrypt and save a credential under `name`.

        Overwrites any existing entry with the same name. The value is
        encrypted with the vault's Fernet key; the name stays in
        plaintext so `list()` can enumerate without decrypting.
        """
        _validate_name(name)
        if not isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise VaultError("credential value must be a string")

        with self._lock:
            state, params = self._load_or_init_state()
            key = self._derive_key(params)
            state["credentials"][name] = encrypt_value(key, value)
            self._write_state(state, params)
        logger.info("vault.store.ok", extra={"credential_name": name})

    def retrieve(self, name: str) -> str:
        """Decrypt and return the credential stored under `name`.

        Raises:
            VaultCredentialNotFoundError: no credential by that name.
            VaultLockedError: passphrase is wrong or the value is
                corrupt. The error string does NOT distinguish between
                the two.
        """
        _validate_name(name)
        with self._lock:
            state, params = self._load_state_required()
            credentials = _credentials_dict(state)
            token = credentials.get(name)
            if token is None:
                raise VaultCredentialNotFoundError(f"no credential named {name!r}")
            key = self._derive_key(params)
            plaintext = decrypt_value(key, token)
        logger.info("vault.retrieve.ok", extra={"credential_name": name})
        return plaintext

    def revoke(self, name: str) -> bool:
        """Delete a credential. Returns True if it existed, False otherwise.

        Idempotent on both branches. We still write the file on a miss
        so the caller sees a consistent post-state — but only if the
        vault already exists; revoking from a missing vault is a no-op.
        """
        _validate_name(name)
        with self._lock:
            state, params = self._load_state_or_none()
            if state is None or params is None:
                return False
            credentials = _credentials_dict(state)
            if name not in credentials:
                return False
            del credentials[name]
            self._write_state(state, params)
        logger.info("vault.revoke.ok", extra={"credential_name": name})
        return True

    def list(self) -> list[str]:
        """Return credential names (sorted). Does NOT decrypt.

        Runs without deriving the Fernet key, so a missing passphrase
        is not an error for enumeration — useful for `tename vault list`
        when the user just wants to see what's stored.
        """
        with self._lock:
            state, _ = self._load_state_or_none()
        if state is None:
            return []
        return sorted(_credentials_dict(state).keys())

    def change_passphrase(self, new_passphrase: str) -> None:
        """Re-derive and re-encrypt every credential under a new passphrase.

        The old passphrase must be set on the instance (via constructor
        or env var). After this call the instance tracks the new
        passphrase in memory; the file carries a new salt so prior
        backups cannot be decrypted with the new passphrase alone.
        """
        if not new_passphrase:
            raise VaultConfigurationError("new vault passphrase must be non-empty")
        with self._lock:
            state, params = self._load_state_required()
            old_key = self._derive_key(params)
            credentials = _credentials_dict(state)
            plaintexts = {
                name: decrypt_value(old_key, token) for name, token in credentials.items()
            }

            new_params = KeyParameters(
                salt=generate_salt(),
                iterations=DEFAULT_ITERATIONS,
            )
            self._passphrase = new_passphrase
            new_key = self._derive_key(new_params)
            state["credentials"] = {
                name: encrypt_value(new_key, plaintext) for name, plaintext in plaintexts.items()
            }
            self._write_state(state, new_params)
        logger.info("vault.rotate_passphrase.ok", extra={"credential_count": len(plaintexts)})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _derive_key(self, params: KeyParameters) -> bytes:
        if self._passphrase is None:
            raise VaultConfigurationError(
                f"vault passphrase is not set (pass `passphrase=` or set {VAULT_PASSPHRASE_ENV})"
            )
        return derive_key(self._passphrase, params)

    def _load_or_init_state(self) -> tuple[dict[str, Any], KeyParameters]:
        state, params = self._load_state_or_none()
        if state is None or params is None:
            params = KeyParameters(salt=generate_salt(), iterations=DEFAULT_ITERATIONS)
            state = {
                "version": VAULT_FILE_VERSION,
                "salt": base64.b64encode(params.salt).decode("ascii"),
                "iterations": params.iterations,
                "credentials": {},
            }
        return state, params

    def _load_state_required(self) -> tuple[dict[str, Any], KeyParameters]:
        state, params = self._load_state_or_none()
        if state is None or params is None:
            raise VaultCredentialNotFoundError(
                f"vault file {self._path} does not exist yet; nothing to retrieve"
            )
        return state, params

    def _load_state_or_none(self) -> tuple[dict[str, Any] | None, KeyParameters | None]:
        if not self._path.exists():
            return None, None
        try:
            raw = self._path.read_bytes()
        except OSError as exc:
            raise VaultConfigurationError(f"vault file {self._path} is unreadable: {exc}") from exc
        try:
            state = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultConfigurationError(f"vault file {self._path} is not valid JSON") from exc

        if not isinstance(state, dict):
            raise VaultConfigurationError(f"vault file {self._path} is not a JSON object")
        version = state.get("version")
        if version != VAULT_FILE_VERSION:
            raise VaultConfigurationError(
                f"vault file {self._path} has unsupported version {version!r} "
                f"(this build handles version {VAULT_FILE_VERSION})"
            )
        salt_b64 = state.get("salt")
        iterations = state.get("iterations")
        if not isinstance(salt_b64, str) or not isinstance(iterations, int):
            raise VaultConfigurationError(
                f"vault file {self._path} is missing required 'salt' / 'iterations' fields"
            )
        try:
            salt = base64.b64decode(salt_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise VaultConfigurationError(
                f"vault file {self._path} has a malformed salt field"
            ) from exc
        state.setdefault("credentials", {})
        if not isinstance(state["credentials"], dict):
            raise VaultConfigurationError(
                f"vault file {self._path} has a non-object 'credentials' field"
            )
        return state, KeyParameters(salt=salt, iterations=iterations)

    def _write_state(self, state: dict[str, Any], params: KeyParameters) -> None:
        state["version"] = VAULT_FILE_VERSION
        state["salt"] = base64.b64encode(params.salt).decode("ascii")
        state["iterations"] = params.iterations
        state.setdefault("credentials", {})

        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        # Non-POSIX filesystems (e.g. Windows CI) may reject chmod; the
        # file-mode chmod below is best-effort for the same reason.
        with contextlib.suppress(OSError, NotImplementedError):
            os.chmod(parent, DIR_MODE)

        serialized = json.dumps(state, indent=2, sort_keys=True).encode("utf-8")
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=".vault-",
            suffix=".tmp",
            dir=str(parent),
        )
        tmp_path = Path(tmp_path_str)
        try:
            try:
                os.write(fd, serialized)
            finally:
                os.close(fd)
            with contextlib.suppress(OSError, NotImplementedError):
                os.chmod(tmp_path, FILE_MODE)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise VaultConfigurationError(
                f"failed to write vault file {self._path}: {exc}"
            ) from exc


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise VaultError("credential name must be a non-empty string")
    if any(ch.isspace() for ch in name):
        raise VaultError(f"credential name {name!r} must not contain whitespace")


def _credentials_dict(state: dict[str, Any]) -> dict[str, str]:
    creds = state.get("credentials")
    if not isinstance(creds, dict):
        state["credentials"] = {}
        return state["credentials"]  # type: ignore[no-any-return]
    return creds  # type: ignore[no-any-return]


__all__ = [
    "DEFAULT_VAULT_PATH",
    "VAULT_FILE_VERSION",
    "VAULT_PASSPHRASE_ENV",
    "Vault",
]
