# Contributing to Tename

Thanks for your interest in contributing. Tename is an open-source, model-agnostic
runtime for AI agents, and we welcome pull requests, bug reports, new model profiles,
new framework adapters, and new benchmark tasks.

## Code of Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). By
participating, you agree to uphold it. Report unacceptable behavior to the
maintainer via the contact listed in that document.

## Development setup

Prerequisites:

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/) for package and environment management
- Docker (required to run sandboxed tools and integration tests; optional for
  scaffolding work)

Clone and install dev dependencies:

```bash
git clone https://github.com/deadLemonX/Tename.git
cd Tename
uv sync --all-extras
```

Install pre-commit hooks so your commits get linted and type-checked automatically:

```bash
uv run pre-commit install
```

## Dev workflow

All routine tasks run through `make`:

| Command          | What it does                                |
| ---------------- | ------------------------------------------- |
| `make install`   | `uv sync --all-extras`                      |
| `make test`      | `uv run pytest`                             |
| `make lint`      | `uv run ruff check .`                       |
| `make format`    | `uv run ruff format .` and auto-fix lint    |
| `make typecheck` | `uv run pyright`                            |
| `make check`     | lint + typecheck + test                     |
| `make clean`     | remove caches, build artifacts, and `.venv` |

Before opening a PR, at minimum run `make check` and ensure it passes.

## Architectural principles

Tename is opinionated. Before proposing significant changes, read
[docs/vision/principles.md](docs/vision/principles.md). The ten principles there
(decoupled interfaces, profiles-not-code, stateless harness, idempotency,
credentials-out-of-sandbox, append-only events, streaming-first, local-first,
open-source-all-the-way, preserve-future-optionality) are non-negotiable
commitments. PRs that violate them need an ADR justifying the exception.

## Pull request process

1. Open an issue first for non-trivial changes. Alignment on approach before code
   saves everyone time.
2. Fork the repo and create a topic branch from `main`.
3. Keep PRs focused. One logical change per PR.
4. Add or update tests. New code without tests will not be merged.
5. Run `make check` locally. CI runs the same commands.
6. Fill in the PR template, including which principles your change touches.
7. Expect review feedback. Tename is maintained as a side project on personal
   time; reviews may take days, not hours. Ping if a PR goes a week without
   response.

## Types of contributions we especially want

### New model profiles

The fastest way to contribute. Write a YAML profile at
`src/tename/profiles/<model-slug>.yaml` and validate it against the benchmark
suite. See `docs/harness/profile-format.md` for the schema and
`docs/harness/profile-claude-opus-4-6.md` as a reference.

### New framework adapters

Implement the `FrameworkAdapter` interface in a new module under
`src/tename/harness/adapters/`. Concept mapping notes for the first adapter
(Deep Agents) will land in `docs/harness/` when S8 is complete — use those as
a template for your adapter.

### New benchmark tasks

Add a YAML task to `benchmarks/tasks/`. See `docs/harness/benchmark-suite.md`.

### Bug reports and feature requests

Use the issue templates in `.github/ISSUE_TEMPLATE/`.

## Style

- Python 3.12+ only.
- `ruff format` decides formatting. Don't argue with it.
- Public APIs need type hints and docstrings.
- Keep comments for *why*, not *what*. Well-named code explains itself.
- No new runtime dependencies without discussion in an issue first.

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](LICENSE), the same license that covers the project.
