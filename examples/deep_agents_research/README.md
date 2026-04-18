# Deep Agents research example

A minimal end-to-end demo of Tename running a Deep Agents-style agent
against Claude Opus 4.6. Creates an agent with `framework: deep_agents`,
wires up the `write_todos` planning tool, sends a short research prompt,
and prints the resulting session event log.

## What it proves

- The `DeepAgentsAdapter` registers and is selectable via
  `agent.framework = "deep_agents"`.
- The adapter surfaces the Deep Agents built-in tool schemas to the
  model router.
- Multi-turn tool rounds (assistant message with `tool_use` blocks →
  tool-role message with `tool_result` blocks → next assistant message)
  flow through correctly.
- Sessions survive the round trip: every event lands in the append-only
  log and the session terminates cleanly.

## What it does NOT prove (yet)

- **Real tool execution.** The `write_todos` tool is schema-only in
  v0.1; the harness stubs every tool call with
  `tool_result(is_error=True, error="not yet implemented")`. Real
  execution arrives with the sandbox (S9) and tool proxy (S10). The
  model may notice this and decline to plan — that's fine for a demo.

## Prerequisites

1. **Postgres running locally.** From the repo root:

   ```bash
   docker compose up -d
   make migrate
   ```

   This brings up Postgres at `localhost:5433` and applies the schema.

2. **An Anthropic API key** set as `ANTHROPIC_API_KEY` in your
   environment.

3. **Tename installed into your Python environment.** From the repo
   root:

   ```bash
   uv sync --all-extras
   # or, if not using uv:
   pip install -e ".[dev]"
   ```

## Run it

```bash
cp examples/deep_agents_research/.env.example examples/deep_agents_research/.env
# edit .env and add your ANTHROPIC_API_KEY

export ANTHROPIC_API_KEY=sk-ant-...
python examples/deep_agents_research/main.py
```

Expected output (abbreviated):

```
connecting to: postgresql+psycopg://tename:tename@localhost:5433/tename_dev
created agent: ...
created session: ...

running harness (this hits the Anthropic API) ...

--- Session event log ---
  [  1] system_event       (system_prompt)
  [  2] user_message       'Give me a two-sentence summary ...'
  [  3] assistant_message  'Claude Shannon is known for ...'
```

If the model decides to call `write_todos`, you'll also see:

```
  [  3] tool_call          write_todos({'todos': [...]})
  [  4] tool_result        error='tool execution not yet implemented (lands in S9/S10)'
  [  5] assistant_message  'Claude Shannon ...'
```

## Resetting between runs

Each run creates a new agent + session, so the log grows every time.
Reset the database with:

```bash
make dev-reset
make migrate
```

## Estimated cost

One invocation is a single small Claude Opus 4.6 prompt; expect a few
cents per run.
