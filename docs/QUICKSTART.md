# Quickstart

Get a Tename agent talking to Claude Opus 4.6 in under 10 minutes.

## Prerequisites

- **Python 3.12 or newer.** `python --version`.
- **Docker.** Required to run Postgres locally and the code sandbox.
  `docker --version`.
- **An Anthropic API key.** `sk-ant-...`. Grab one at
  <https://console.anthropic.com/>.

If any of those fail, fix them first — Tename's container and sandbox
layer need them all.

## 1. Clone and install

```bash
git clone https://github.com/deadLemonX/Tename
cd Tename
```

Install with [uv](https://docs.astral.sh/uv/) (recommended — fast and
reproducible):

```bash
uv sync --all-extras
```

Or with pip:

```bash
pip install -e ".[dev]"
```

## 2. Start the local Postgres

Tename stores session state in Postgres (16+). The repo ships a
`docker-compose.yml` that brings one up in under ten seconds:

```bash
make dev
```

This starts `tename-postgres` on port 5433. When it reports "healthy",
apply the schema:

```bash
make migrate
```

You should see alembic print `Running upgrade -> 0001_initial_schema`.

## 3. Set your Anthropic key

Either export it in your shell:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Or copy `.env.example` to `.env` in the repo root and fill it in — the
Makefile and pytest auto-load `.env`.

## 4. Run the hello-world example

```bash
python examples/01-hello-world/main.py
```

Expected:

```
user: Hello! Tell me one interesting fact about octopuses.

assistant: Octopuses have three hearts: two pump blood through their gills
and one pumps it through the rest of their body.
```

If you see streamed output like that, Tename is working end-to-end.

## 5. Next: the coding agent

The coding agent writes Python, runs it in a Docker sandbox, and reports
the result:

```bash
python examples/03-coding-agent/main.py
```

First run pulls `python:3.12-slim` (~125 MB) — subsequent runs reuse
the cached image.

## 6. Next: the research agent (optional)

Example 02 adds Deep Agents planning plus a live web search tool. It
requires a [Tavily](https://tavily.com/) API key (they offer a free
tier):

```bash
export TENAME_VAULT_PASSPHRASE='pick-something-long'
tename vault set web_search_api_key    # paste Tavily key at the prompt
python examples/02-research-agent/main.py
```

## Troubleshooting

### "ANTHROPIC_API_KEY is not set"

Either export the env var or put it in `.env`. The examples and tests
both respect the "shell env wins over .env" convention — if `.env` has
a key and your shell has an empty one, the empty one wins. If in doubt:
`unset ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY=sk-ant-...`.

### "connection refused" to Postgres

`make dev` didn't succeed, or the container stopped. Check
`docker ps | grep tename-postgres`. If the container isn't up, run
`make dev` again. If it's up but unhealthy, `docker logs tename-postgres`
usually says why.

### "relation \"sessions\" does not exist"

You started Postgres but didn't run `make migrate`. Run it now.

### Sandbox tests hang, or agent never returns from a `python` tool call

Check that Docker is running and the current user can run
`docker ps` without sudo. The first `python` tool call pulls the
sandbox base image — that can take 30-60 seconds on a cold cache.

### "ModuleNotFoundError: No module named 'tename'"

You didn't install the package in the active virtualenv. `uv sync
--all-extras` creates `.venv/` and installs Tename there; activate it
with `source .venv/bin/activate`, or prefix commands with
`uv run ...`.

### "vault is locked" / "VaultLockedError"

`TENAME_VAULT_PASSPHRASE` is either unset or different from the value
used when the vault was created. Export the correct passphrase and try
again. The vault file lives at `~/.tename/vault.json.enc` by default —
delete it only if you're OK losing every stored credential.

### Something else

Open an issue at <https://github.com/deadLemonX/Tename/issues>. The
project is actively maintained — you won't be talking to the void.

## What to explore next

- [Architecture overview](architecture/overview.md) — how the pieces fit
- [Profile format](harness/profile-format.md) — how per-model tuning
  works
- [SDK design](dx/sdk-design.md) — the API reference
- `benchmarks/` — 5 tasks for validating a new profile
