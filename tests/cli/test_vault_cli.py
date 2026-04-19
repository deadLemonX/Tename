"""CLI tests for `tename vault ...`."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Any

import pytest

from tename.cli.main import build_parser, main
from tename.cli.vault_commands import cmd_get, cmd_list, cmd_remove, cmd_set


def _args(tmp_path: Path, **extra: Any) -> argparse.Namespace:
    base: dict[str, Any] = {"vault_path": str(tmp_path / "vault.json.enc")}
    base.update(extra)
    return argparse.Namespace(**base)


def test_cmd_set_stores_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENAME_VAULT_PASSPHRASE", "pw")
    stdout = io.StringIO()
    args = _args(tmp_path, name="api_key")
    rc = cmd_set(args, prompt_fn=lambda _label: "sk-abc", stdout=stdout)
    assert rc == 0
    assert "stored credential 'api_key'" in stdout.getvalue()

    # Re-read via cmd_list to confirm.
    list_out = io.StringIO()
    rc2 = cmd_list(_args(tmp_path), stdout=list_out)
    assert rc2 == 0
    assert "api_key" in list_out.getvalue()


def test_cmd_set_empty_value_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENAME_VAULT_PASSPHRASE", "pw")
    stdout = io.StringIO()
    rc = cmd_set(_args(tmp_path, name="api_key"), prompt_fn=lambda _l: "", stdout=stdout)
    assert rc == 2


def test_cmd_list_empty(tmp_path: Path) -> None:
    stdout = io.StringIO()
    rc = cmd_list(_args(tmp_path), stdout=stdout)
    assert rc == 0
    assert "no credentials stored" in stdout.getvalue()


def test_cmd_remove_with_yes_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENAME_VAULT_PASSPHRASE", "pw")
    cmd_set(
        _args(tmp_path, name="k"),
        prompt_fn=lambda _l: "v",
        stdout=io.StringIO(),
    )

    stdout = io.StringIO()
    rc = cmd_remove(_args(tmp_path, name="k", yes=True), stdout=stdout)
    assert rc == 0
    assert "removed credential 'k'" in stdout.getvalue()


def test_cmd_remove_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENAME_VAULT_PASSPHRASE", "pw")
    cmd_set(
        _args(tmp_path, name="k"),
        prompt_fn=lambda _l: "v",
        stdout=io.StringIO(),
    )

    stdout = io.StringIO()
    rc = cmd_remove(
        _args(tmp_path, name="k", yes=False),
        confirm_fn=lambda _l: "n",
        stdout=stdout,
    )
    assert rc == 0
    assert "cancelled" in stdout.getvalue()

    # Credential still present.
    list_out = io.StringIO()
    cmd_list(_args(tmp_path), stdout=list_out)
    assert "k" in list_out.getvalue()


def test_cmd_remove_missing_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TENAME_VAULT_PASSPHRASE", "pw")
    rc = cmd_remove(_args(tmp_path, name="absent", yes=True), stdout=io.StringIO())
    assert rc == 1


def test_cmd_get_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENAME_VAULT_PASSPHRASE", "pw")
    cmd_set(_args(tmp_path, name="k"), prompt_fn=lambda _l: "val", stdout=io.StringIO())

    stdout = io.StringIO()
    rc = cmd_get(_args(tmp_path, name="k"), stdout=stdout)
    assert rc == 0
    assert stdout.getvalue().strip() == "val"


def test_parser_set_defaults_func() -> None:
    parser = build_parser()
    args = parser.parse_args(["vault", "list"])
    assert args.command == "vault"
    assert args.vault_command == "list"
    assert callable(args.func)


def test_main_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--version"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.startswith("tename ")


def test_main_help_when_no_subcommand() -> None:
    rc = main([])
    assert rc == 2


def test_main_vault_list_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TENAME_VAULT_PASSPHRASE", "pw")
    vault_file = tmp_path / "v.enc"
    rc = main(["vault", "--vault-path", str(vault_file), "list"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "no credentials stored" in captured.out
