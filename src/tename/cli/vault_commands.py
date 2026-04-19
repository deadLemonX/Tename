"""`tename vault ...` subcommands.

Lives in its own module so `main.py` stays thin and so tests can
exercise the command functions directly with argparse namespaces.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable
from typing import Any, TextIO

from tename.vault import (
    Vault,
    VaultCredentialNotFoundError,
    VaultError,
)

PromptFn = Callable[[str], str]
"""Signature for the secret-prompt callable; swapped out in tests."""


def _default_prompt(label: str) -> str:
    return getpass.getpass(label)


def _default_confirm(label: str) -> str:
    return input(label)


def _make_vault(args: argparse.Namespace) -> Vault:
    path = getattr(args, "vault_path", None)
    return Vault(path=path)


def _out(stream: TextIO | None) -> TextIO:
    """Resolve the output stream at call time.

    Binding `sys.stdout` as a default argument captures whatever object
    was live at import time — which is wrong under pytest's `capsys`
    and anywhere else that swaps `sys.stdout`. Call `_out(...)` inside
    each command function to always pick up the current stream.
    """
    return stream if stream is not None else sys.stdout


def cmd_set(
    args: argparse.Namespace,
    *,
    prompt_fn: PromptFn | None = None,
    stdout: TextIO | None = None,
) -> int:
    stream = _out(stdout)
    prompt_fn = prompt_fn or _default_prompt
    try:
        value = prompt_fn(f"value for {args.name}: ")
    except (KeyboardInterrupt, EOFError):
        stream.write("\n")
        return 130
    if not value:
        print(f"no value supplied for {args.name!r}; nothing stored", file=sys.stderr)
        return 2
    try:
        _make_vault(args).store(args.name, value)
    except VaultError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"stored credential {args.name!r}", file=stream)
    return 0


def cmd_list(args: argparse.Namespace, *, stdout: TextIO | None = None) -> int:
    stream = _out(stdout)
    try:
        names = _make_vault(args).list()
    except VaultError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not names:
        print("(no credentials stored)", file=stream)
        return 0
    for name in names:
        print(name, file=stream)
    return 0


def cmd_remove(
    args: argparse.Namespace,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    stdout: TextIO | None = None,
) -> int:
    stream = _out(stdout)
    confirm_fn = confirm_fn or _default_confirm
    if not args.yes:
        try:
            reply = confirm_fn(f"delete credential {args.name!r}? [y/N] ")
        except (KeyboardInterrupt, EOFError):
            stream.write("\n")
            return 130
        if reply.strip().lower() not in {"y", "yes"}:
            print("cancelled", file=stream)
            return 0
    try:
        existed = _make_vault(args).revoke(args.name)
    except VaultError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not existed:
        # Print to stderr so scripts can detect "missing" via exit code.
        print(f"credential {args.name!r} was not stored", file=sys.stderr)
        return 1
    print(f"removed credential {args.name!r}", file=stream)
    return 0


def cmd_get(args: argparse.Namespace, *, stdout: TextIO | None = None) -> int:
    """Print a stored credential. Hidden from `--help` because the whole
    point of the vault is that you don't print these, but useful for
    debugging and scripts with `--yes` confirmation semantics."""
    stream = _out(stdout)
    try:
        value = _make_vault(args).retrieve(args.name)
    except VaultCredentialNotFoundError:
        print(f"no credential named {args.name!r}", file=sys.stderr)
        return 1
    except VaultError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(value, file=stream)
    return 0


def add_vault_subparser(subparsers: Any) -> None:
    """Attach `vault` + its subcommands to a top-level argparse parser.

    The `subparsers` argument is an `argparse._SubParsersAction` — the
    type isn't exposed publicly by argparse, hence `Any`. argparse is a
    stable library; this signature has been stable since Python 3.2.
    """
    p = subparsers.add_parser(
        "vault",
        help="Manage encrypted credentials",
        description="Store, list, and remove credentials in the Tename vault.",
    )
    p.add_argument(
        "--vault-path",
        dest="vault_path",
        default=None,
        help="Path to the vault file (default: ~/.tename/vault.json.enc).",
    )
    vault_subs = p.add_subparsers(dest="vault_command", required=True)

    p_set = vault_subs.add_parser("set", help="Store a credential (prompts for value).")
    p_set.add_argument("name", help="Credential name (no whitespace).")
    p_set.set_defaults(func=cmd_set)

    p_list = vault_subs.add_parser("list", help="List credential names (values are not shown).")
    p_list.set_defaults(func=cmd_list)

    p_remove = vault_subs.add_parser("remove", help="Delete a credential.")
    p_remove.add_argument("name", help="Credential name to delete.")
    p_remove.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    p_remove.set_defaults(func=cmd_remove)

    # `get` is undocumented on purpose (hidden from the default help) but
    # useful for scripts. It's not a secret command; it's a quiet one.
    p_get = vault_subs.add_parser(
        "get",
        help=argparse.SUPPRESS,
    )
    p_get.add_argument("name")
    p_get.set_defaults(func=cmd_get)


__all__ = [
    "add_vault_subparser",
    "cmd_get",
    "cmd_list",
    "cmd_remove",
    "cmd_set",
]
