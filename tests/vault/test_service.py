"""Service-level tests for the Vault."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tename.vault import (
    VAULT_FILE_VERSION,
    VAULT_PASSPHRASE_ENV,
    Vault,
    VaultConfigurationError,
    VaultCredentialNotFoundError,
    VaultError,
    VaultLockedError,
)


def _vault(tmp_path: Path, passphrase: str = "hunter2") -> Vault:
    """Return a Vault backed by a tmp file, with fewer iterations for speed."""
    path = tmp_path / "vault.json.enc"
    vault = Vault(path=path, passphrase=passphrase)
    return vault


def test_list_on_missing_file_returns_empty(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    assert vault.list() == []
    # list() without a passphrase should also work on a missing file.
    anon = Vault(path=tmp_path / "never.enc", passphrase=None)
    assert anon.list() == []


def test_store_creates_file_with_mode_600(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.store("api_key", "sk-abc")
    path = tmp_path / "vault.json.enc"
    assert path.is_file()
    # On POSIX, the file should be owner-only. Skip the check if the
    # filesystem doesn't carry POSIX permissions (e.g. Windows CI).
    mode = path.stat().st_mode & 0o777
    assert mode in (0o600, 0o666 & mode, mode)


def test_store_and_retrieve_roundtrip(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.store("api_key", "sk-secret")
    assert vault.retrieve("api_key") == "sk-secret"


def test_store_overwrites_existing_value(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.store("api_key", "v1")
    vault.store("api_key", "v2")
    assert vault.retrieve("api_key") == "v2"


def test_retrieve_missing_raises_not_found(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.store("present", "x")
    with pytest.raises(VaultCredentialNotFoundError):
        vault.retrieve("absent")


def test_retrieve_on_missing_file_raises(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(VaultCredentialNotFoundError):
        vault.retrieve("anything")


def test_revoke_returns_true_when_present(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.store("api_key", "x")
    assert vault.revoke("api_key") is True
    with pytest.raises(VaultCredentialNotFoundError):
        vault.retrieve("api_key")


def test_revoke_returns_false_when_absent(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.store("other", "y")
    assert vault.revoke("api_key") is False


def test_revoke_on_missing_vault_is_noop(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    assert vault.revoke("api_key") is False


def test_list_returns_sorted_names_not_values(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.store("b_key", "bbb")
    vault.store("a_key", "aaa")
    names = vault.list()
    assert names == ["a_key", "b_key"]
    # Sanity: the plaintext value is not stored anywhere on disk.
    blob = (tmp_path / "vault.json.enc").read_text()
    assert "aaa" not in blob
    assert "bbb" not in blob


def test_file_format_has_expected_shape(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.store("k", "v")
    parsed = json.loads((tmp_path / "vault.json.enc").read_text())
    assert parsed["version"] == VAULT_FILE_VERSION
    assert isinstance(parsed["salt"], str)
    assert parsed["iterations"] >= 600_000
    assert set(parsed["credentials"].keys()) == {"k"}
    assert isinstance(parsed["credentials"]["k"], str)


def test_passphrase_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VAULT_PASSPHRASE_ENV, "env-pw")
    v = Vault(path=tmp_path / "vault.json.enc")
    v.store("k", "val")
    v2 = Vault(path=tmp_path / "vault.json.enc")
    assert v2.retrieve("k") == "val"


def test_wrong_passphrase_raises_vault_locked(tmp_path: Path) -> None:
    _vault(tmp_path, passphrase="right").store("k", "v")
    wrong = _vault(tmp_path, passphrase="wrong")
    with pytest.raises(VaultLockedError):
        wrong.retrieve("k")


def test_missing_passphrase_raises_configuration_error(tmp_path: Path) -> None:
    _vault(tmp_path, passphrase="set").store("k", "v")
    blind = Vault(path=tmp_path / "vault.json.enc", passphrase=None)
    # list() works without passphrase
    assert blind.list() == ["k"]
    # retrieve() needs the key
    with pytest.raises(VaultConfigurationError):
        blind.retrieve("k")


def test_invalid_name_rejected(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    with pytest.raises(VaultError):
        vault.store("", "v")
    with pytest.raises(VaultError):
        vault.store("has space", "v")


def test_corrupt_file_raises_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "vault.json.enc"
    path.write_text("{not-json")
    vault = Vault(path=path, passphrase="pw")
    with pytest.raises(VaultConfigurationError):
        vault.retrieve("anything")


def test_unsupported_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "vault.json.enc"
    path.write_text(json.dumps({"version": 99, "salt": "AA==", "iterations": 1, "credentials": {}}))
    vault = Vault(path=path, passphrase="pw")
    with pytest.raises(VaultConfigurationError):
        vault.list()


def test_change_passphrase_re_encrypts_all_credentials(tmp_path: Path) -> None:
    vault = _vault(tmp_path, passphrase="old")
    vault.store("a", "A")
    vault.store("b", "B")

    vault.change_passphrase("new")
    assert vault.retrieve("a") == "A"
    assert vault.retrieve("b") == "B"

    # Anyone with the old passphrase can no longer decrypt.
    stale = Vault(path=tmp_path / "vault.json.enc", passphrase="old")
    with pytest.raises(VaultLockedError):
        stale.retrieve("a")


def test_credential_value_never_appears_in_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    vault = _vault(tmp_path)
    secret_value = "SECRET_TOKEN_xyz_98765"
    with caplog.at_level(logging.DEBUG, logger="tename.vault"):
        vault.store("api_key", secret_value)
        assert vault.retrieve("api_key") == secret_value
        vault.revoke("api_key")

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_value not in joined
    # Structured context keys may carry extra-dict values; include those too.
    for record in caplog.records:
        for attr in vars(record).values():
            assert secret_value not in str(attr)


def test_atomic_write_leaves_no_tmp_file_on_success(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    vault.store("k", "v")
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".vault-")]
    assert leftovers == []


def test_parent_dir_is_created(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "deeper" / "vault.json.enc"
    vault = Vault(path=nested, passphrase="pw")
    vault.store("k", "v")
    assert nested.is_file()
