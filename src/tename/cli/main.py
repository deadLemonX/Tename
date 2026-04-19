"""`tename` CLI entry point.

Dispatches subcommands registered in sibling modules. The CLI is
deliberately thin: argparse + a `func` callback on each parser. No
click dependency; argparse is enough for v0.1 (vault + a version
command). When the CLI grows beyond a handful of commands we can
re-evaluate — see ADR 0003.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version

from tename.cli.vault_commands import add_vault_subparser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tename",
        description="Tename CLI — manage agents, sessions, credentials.",
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit.")

    subparsers = parser.add_subparsers(dest="command")
    add_vault_subparser(subparsers)

    return parser


def _resolve_version() -> str:
    try:
        return version("tename")
    except PackageNotFoundError:
        return "unknown"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"tename {_resolve_version()}")
        return 0

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return 2
    return int(func(args))


if __name__ == "__main__":  # pragma: no cover — real entry is pyproject script
    sys.exit(main())


__all__ = ["build_parser", "main"]
