# Installation

For a guided walkthrough from clone to running agent in under 10
minutes, see [QUICKSTART](../QUICKSTART.md). This document is the
reference version — same material, more detail.

## Prerequisites

- **Python 3.12 or later.** `python --version`.
- **Docker.** Required for the Postgres dev container and for the
  sandbox (where LLM-generated code runs). `docker --version`.
- **An API key from Anthropic.** Additional model providers arrive in
  v0.2+; v0.1 ships with the `claude-opus-4-6` profile only.

## Install Tename

```bash
pip install tename
```

Or with [uv](https://docs.astral.sh/uv/) (recommended for speed and
reproducibility):

```bash
uv pip install tename
```

This installs the `tename` library and the `tename` CLI entrypoint.

## Start Postgres

Tename stores all durable session state in Postgres. The easiest path
is the bundled docker-compose config — clone the repo and run:

```bash
git clone https://github.com/deadLemonX/Tename
cd Tename
make dev           # docker compose up postgres
make migrate       # apply alembic schema
```

Alternatively, point Tename at an existing Postgres by setting
`TENAME_DATABASE_URL` before starting the client:

```bash
export TENAME_DATABASE_URL="postgresql+psycopg://user:pass@host:5432/dbname"
```

Apply the schema from a checkout of the repo:

```bash
uv run alembic upgrade head
```

## Set your Anthropic key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

The Makefile and the pytest harness both auto-load `.env` from the
repo root, so dropping the value into `.env` works too — the shell env
wins on collision.

## Run the hello-world

```python
from tename import Tename

with Tename(enable_sandbox=False) as client:
    agent = client.agents.create(
        name="assistant",
        model="claude-opus-4-6",
        system_prompt="You are a helpful assistant.",
    )
    session = client.sessions.create(agent_id=agent.id)

    for event in session.send("What's 2 + 2?"):
        if event.type == "assistant_message" and event.payload.get("is_complete"):
            print(event.payload["content"])
```

A response streamed to your terminal means the install is working.
See `examples/01-hello-world/main.py` in the repo for the same flow
as a runnable script.

## Configuration

For v0.1 Tename is configured via environment variables or
keyword arguments to `Tename(...)`. A TOML/YAML config file is not
part of v0.1 (may arrive in v0.2 if there is demand).

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TENAME_DATABASE_URL` | none (required) | SQLAlchemy URL for the Postgres session store |
| `ANTHROPIC_API_KEY` | none | Consumed by the Anthropic provider |
| `TENAME_VAULT_PASSPHRASE` | none | Unlocks the encrypted vault for proxy tools |
| `TENAME_PROFILES_DIR` | none | Additional directory searched for YAML profiles before the bundled ones |

Every env var can be overridden by an explicit keyword argument to
`Tename(...)`; explicit args always win.

### CLI

The shipped CLI covers the vault only in v0.1:

```bash
tename --version
tename vault set <name>        # prompts for the value
tename vault list
tename vault get <name>        # hidden from --help; for scripting
tename vault remove <name>
```

More CLI surface (`tename doctor`, `tename migrate`, etc.) is not in
v0.1. Until those ship, use `make migrate` / `uv run alembic upgrade
head` from a repo checkout.

## Troubleshooting

### "ERROR: Could not find a version that satisfies the requirement tename"

`pip` thinks no compatible wheel exists. Almost always a Python
version problem — `tename` requires 3.12+. Run `python --version`;
if it's older, create a fresh virtualenv with a newer Python:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install tename
```

### "Docker is not running"

Start Docker Desktop (or the Docker daemon). The sandbox path needs
a running Docker engine on the host running the Tename client.

### "Could not connect to database"

Postgres isn't up or `TENAME_DATABASE_URL` is wrong. Run `make dev`
from a repo checkout, or verify the URL you've set.

### "ANTHROPIC_API_KEY not set"

Export the env var, or pass `anthropic_api_key="..."` to
`Tename(...)`. An empty shell export counts as "unset" — if you're
mixing `.env` and a shell export, either fix the export or `unset`
it and let `.env` through.

### "Profile not found: claude-opus-4-6"

Tename ships this profile inside the wheel. If it's missing, your
install is broken — reinstall with `pip install --force-reinstall
tename`. If it persists, file an issue with `pip show tename`
output.

### "ImportError: No module named tename"

You're not in the virtualenv where you ran `pip install`. Activate
it, or check `which python` matches `which pip`.

### Something else

Open an issue at <https://github.com/deadLemonX/Tename/issues> with
the error message, the output of `python -V` / `pip show tename`,
and what you were doing.

## Upgrading

```bash
pip install --upgrade tename
```

If you're using a repo checkout, re-apply migrations after upgrading:

```bash
cd Tename
git pull
make migrate
```

Breaking changes are documented in
[CHANGELOG.md](../../CHANGELOG.md). Tename follows semantic
versioning — major bumps signal breaking changes, minor versions
are backward-compatible additions, patches are bug fixes.

## Uninstalling

```bash
pip uninstall tename
```

This removes the code. Data in your Postgres database and the vault
file at `~/.tename/` is NOT deleted — remove those manually if
desired:

```bash
dropdb tename_dev          # if using the dev database
rm -rf ~/.tename/          # removes the encrypted vault
```
