# 02 — Research agent (Deep Agents + web_search)

A Deep Agents-style agent that plans with `write_todos`, searches the
web with the proxy-backed `web_search` tool, and writes a short cited
answer.

## What it proves

- The Deep Agents framework adapter registers and drives a real
  multi-turn tool round (plan → search → answer).
- The tool proxy pulls the Tavily API key from the encrypted vault at
  call time — the credential never enters the sandbox, never lands in
  the session event log, and never touches the model's context window.
- Switching frameworks is one field: `framework="deep_agents"` on the
  agent.

## Prerequisites

- Python 3.12+
- Docker (for Postgres)
- An Anthropic API key
- **A Tavily API key.** The built-in `web_search` tool uses Tavily's
  search API. Grab a free key at <https://tavily.com/>.

## Setup

```bash
# From the repo root:
make dev
make migrate
uv sync --all-extras

# Store the Tavily key in the encrypted vault:
export TENAME_VAULT_PASSPHRASE='pick-something-long'
tename vault set web_search_api_key
# paste your Tavily key when prompted

cp examples/02-research-agent/.env.example examples/02-research-agent/.env
# edit .env and add ANTHROPIC_API_KEY
```

The vault file lives at `~/.tename/vault.json.enc` by default, encrypted
with `TENAME_VAULT_PASSPHRASE`.

## Run

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export TENAME_VAULT_PASSPHRASE='pick-something-long'
python examples/02-research-agent/main.py
```

Expected output (abbreviated):

```
user: Who won the 2024 Nobel Prize in Physics, and for what work?

[tool_call] write_todos({'todos': [...]})
[tool_result] ok
[tool_call] web_search({'query': '2024 Nobel Prize in Physics'})
[tool_result] [{"title": "...", "url": "https://...", ...}]

assistant: The 2024 Nobel Prize in Physics went to John J. Hopfield and
Geoffrey Hinton for foundational work on artificial neural networks...
```

## Cost

One invocation is ~2-3 Claude Opus 4.6 turns plus one Tavily call.
Expect well under 10 cents per run.

## Without a Tavily key

The agent will call `web_search`, receive a credential-missing error
from the tool proxy, and either give up or answer from its own
knowledge. That's a valid trip through the full stack — it just doesn't
showcase the web_search path.
