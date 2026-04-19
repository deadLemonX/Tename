# 01 — Hello world

The simplest possible Tename agent: one model, one message, no tools.

## What it proves

- The SDK wires the full stack (Session Service, Harness, Model Router)
  in one constructor call.
- A plain chat agent runs end-to-end against Claude Opus 4.6 with a
  durable session on the append-only event log.

## Prerequisites

- Python 3.12+
- Docker (for Postgres — not strictly required since this example
  disables the sandbox, but Postgres still needs to be running)
- An Anthropic API key

## Setup

```bash
# From the repo root:
make dev          # starts Postgres in docker compose
make migrate      # applies the schema
uv sync --all-extras  # or: pip install -e ".[dev]"

cp examples/01-hello-world/.env.example examples/01-hello-world/.env
# edit .env and add ANTHROPIC_API_KEY
```

## Run

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python examples/01-hello-world/main.py
```

Expected output (abbreviated):

```
user: Hello! Tell me one interesting fact about octopuses.

assistant: Octopuses have three hearts: two pump blood through their gills
and one pumps it through the rest of their body.
```

## Cost

A single small Claude Opus 4.6 call — a fraction of a cent.
