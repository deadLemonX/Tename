# CLI Design

Tename ships a `tename` CLI entry point. v0.1 keeps the surface
intentionally small — everything that matters runs through the Python
SDK. The CLI exists for operations that don't require writing Python.

## Design principles

1. **The CLI is a thin wrapper over the SDK.** It never adds
   functionality the SDK doesn't have.
2. **Helpful defaults, explicit overrides.** Most commands work with
   no flags.
3. **Interactive where it helps, scripted where it doesn't.** Use
   `--yes` for scripting; `getpass` prompts for secrets by default.

## What ships in v0.1

```bash
tename --version       # print the installed version
tename --help          # top-level help
tename vault --help    # vault sub-help

tename vault set <name>                      # prompts for the value
tename vault set <name> --vault-path <path>  # override vault file
tename vault list
tename vault list --vault-path <path>
tename vault remove <name>
tename vault remove <name> --yes             # skip confirmation
tename vault get <name>                      # hidden from --help; for scripting
```

Global flags on every `vault` subcommand: `--vault-path <path>`
(defaults to `~/.tename/vault.json.enc`).

The vault's passphrase comes from `$TENAME_VAULT_PASSPHRASE` by
default. `tename vault list` does not need a passphrase — credential
names are plaintext. Any other command that reads or writes a
credential requires the passphrase.

## What the CLI deliberately does NOT ship in v0.1

Everything below is routed through `make` targets (from a repo
checkout) or direct SDK calls. Centralizing them in the CLI is a v0.2
scope item — once there's feedback on what CLI shape actually helps
users beyond what they already have.

| Not shipped | Do this instead |
|---|---|
| `tename dev` | `make dev` (starts Postgres via docker compose) |
| `tename migrate` | `make migrate` or `uv run alembic upgrade head` |
| `tename agents create/list/show/delete` | `client.agents.*` via the SDK |
| `tename sessions run/list/show/replay/delete` | `client.sessions.*` via the SDK |
| `tename profiles list/show/validate` | `ProfileLoader.load(...)` via the SDK |
| `tename benchmark run/results` | `python benchmarks/run.py --task ... --profile ...` |
| `tename doctor` | Not yet; file a v0.2 feature request if this would help |
| `tename info` | Not yet; file a v0.2 feature request if this would help |
| Shell completion | Not yet |

## Implementation

Uses `argparse` from the standard library — zero new runtime deps. A
heavier framework like `click` may land in v0.2 once the subcommand
count justifies it.

Source: `src/tename/cli/`:
- `main.py` — entry point (`build_parser()`, `main(argv=None) -> int`)
- `vault_commands.py` — `cmd_set`, `cmd_list`, `cmd_remove`, `cmd_get`
- `__main__.py` — `python -m tename.cli` dispatch

Installed via `[project.scripts] tename = "tename.cli.main:main"` in
`pyproject.toml`. After `pip install tename` (or `uv sync`) the
`tename` command lives on `$PATH`.

## Testing

`tests/cli/test_vault_cli.py` covers every subcommand end-to-end
against an on-disk vault file. The tests write to `tmp_path` and use
`monkeypatch.setenv` for the passphrase, so they never touch
`~/.tename/`.

## Not in v0.1 (aspirational, preserved for design reference)

Things that were sketched during S1 planning and deferred to v0.2+:

- `tename serve` — running Tename as a client-server process pair.
  The SDK is in-process only in v0.1.
- `tename deploy` — cloud deployment commands. Belongs to the
  commercial-future track, not the OSS core.
- A TUI / interactive dashboard.
- Shell completion scripts (bash, zsh, fish).

These come back as concrete proposals once demand emerges.
