# 03 — Coding agent (sandboxed Python)

An agent that writes Python, runs it in an isolated Docker sandbox, and
reports the result. Uses the built-in sandbox tools (`python`, `bash`,
`file_read`, `file_write`, `file_edit`, `file_list`).

## What it proves

- The harness routes sandbox-named tool calls (`python`, `bash`, ...) to
  a real Docker container with CPU, memory, and PID limits.
- The sandbox provisions lazily on the first tool call and persists
  across the session — a script written in one turn can be read and run
  in the next.
- The agent can self-verify by running its code and observing the
  output, turning "Claude hallucinated a number" into a testable claim.

## Prerequisites

- Python 3.12+
- **Docker running locally** (the sandbox spins up a `python:3.12-slim`
  container)
- Postgres running (`make dev` + `make migrate`)
- An Anthropic API key

## Setup

```bash
# From the repo root:
make dev
make migrate
uv sync --all-extras

cp examples/03-coding-agent/.env.example examples/03-coding-agent/.env
# edit .env and add ANTHROPIC_API_KEY
```

## Run

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python examples/03-coding-agent/main.py
```

Expected output (abbreviated):

```
user: Use the Python sandbox to compute the 50th Fibonacci number...

[tool_call] python({"code": "def fib(n): ..."}...)
[tool_result] 12586269025

A: The 50th Fibonacci number is 12586269025. I computed it by...
```

## Cost

One invocation is 2-4 Claude Opus 4.6 turns plus one sandbox provision
(~1-2 seconds once the image is cached). Expect well under 10 cents per
run. The first run pulls `python:3.12-slim` (~125 MB).

## Cleanup

Every Tename container is labeled `tename.sandbox=1`. To find
orphans from a crashed run:

```bash
docker ps -a -f label=tename.sandbox
```

The harness destroys sandboxes in a `finally` block, so orphans should
be rare — but the label lets you prune without affecting other
projects' containers.
