# Tename

> **The production runtime for any agent framework.**
> Durable sessions, sandbox isolation, and per-model optimization — so the same agent code runs well on Claude, GPT, Gemini, or Llama without rewrites.

⚠️ **Status: v0.1 in development.** Not yet ready for production use. Star the repo to follow progress.

---

## What is Tename?

Tename is an open-source runtime that sits *beneath* your agent framework — whether that's Deep Agents, Claude Agent SDK, or your own custom code. It provides the infrastructure layer that most agent projects end up rebuilding from scratch:

- **Durable sessions** that survive crashes. Kill the process mid-run, restart, and the agent resumes exactly where it left off. No lost work.
- **Per-model optimization** via YAML profiles. Same agent code, different model, one config change. Each model gets tuned caching, context management, and quirk handling.
- **Sandbox isolation** for LLM-generated code. Docker containers with resource limits. Credentials never enter the sandbox.
- **Framework adapters** so you bring your existing code. Tename doesn't replace Deep Agents — it makes Deep Agents production-grade.

## Quick look

```python
from tename_sdk import Tename  # name TBD

client = Tename()

agent = client.agents.create(
    name="researcher",
    model="claude-opus-4-6",
    system_prompt="You are a research assistant.",
    tools=["web_search", "python"],
)

session = client.sessions.create(agent_id=agent.id)

for event in session.send("Research the EV charging market"):
    if event.type == "assistant_message":
        print(event.payload["content"], end="", flush=True)
```

Switch to GPT-5? Change one line:

```python
agent = client.agents.create(
    model="gpt-5",  # ← just this
    ...
)
```

The runtime handles the rest — different caching strategy, different tool format, different context management — all from the YAML profile.

## Why Tename exists

Teams building production AI agents face a forced choice:

| Option | Pros | Cons |
|--------|------|------|
| **Proprietary runtimes** (Anthropic Managed Agents, AWS Bedrock) | Production-grade infrastructure | Locked to one model provider |
| **Open frameworks** (Deep Agents, Claude Agent SDK) | Model flexibility, code ownership | You build all the infrastructure yourself |

Tename is the third option: production-grade infrastructure that works with any model and any framework.

## Architecture

```
Your Code → SDK → Harness Runtime → Model Router → Any Provider
                       ↕                ↕
                  Session Service    Sandbox (Docker)
                   (Postgres)           ↕
                                    Tool Proxy + Vault
```

Three decoupled interfaces (brain, hands, state) that fail and recover independently. Based on [Anthropic's published architecture](https://www.anthropic.com/engineering) for Managed Agents, extended to be model-agnostic and open.

## Features

**Shipped in v0.1:**
- Session Service with append-only event log and idempotent writes
- Harness Runtime with stateless loop and crash recovery
- Model Router supporting Anthropic, OpenAI, and OpenAI-compatible endpoints
- YAML profiles with per-model caching, context management, and quirk handling
- Docker sandbox with built-in tools (bash, python, file operations)
- Credential vault with encrypted storage
- Tool proxy that keeps secrets out of the sandbox
- Deep Agents framework adapter
- Python SDK with streaming
- 5 benchmark tasks for profile validation
- 3 worked examples

**Coming in v0.2:**
- GPT-5 and Gemini profiles
- Claude Agent SDK adapter
- Summarization compaction
- OpenTelemetry tracing
- More benchmark tasks

## Getting started

### Prerequisites

- Python 3.12+
- Docker
- An API key from Anthropic, OpenAI, or an OpenAI-compatible provider

### Install

```bash
pip install tename-sdk  # name TBD
```

### Run the local dev stack

```bash
tename dev  # starts Postgres in Docker
```

### Set your API key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Run the hello-world

```bash
python examples/01-hello-world/main.py
```

See [docs/dx/installation.md](docs/dx/installation.md) for full setup instructions.

## Documentation

| Doc | What it covers |
|-----|----------------|
| [Product Vision](docs/vision/product-vision.md) | What Tename is and why it exists |
| [Principles](docs/vision/principles.md) | Non-negotiable architectural commitments |
| [Architecture Overview](docs/architecture/overview.md) | How the system fits together |
| [Profile Format](docs/harness/profile-format.md) | How to write and customize model profiles |
| [SDK Design](docs/dx/sdk-design.md) | Python SDK API reference |
| [Installation](docs/dx/installation.md) | Setup and configuration |
| [Deployment](docs/operations/deployment.md) | Running Tename in production |

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for how to:

- Set up the development environment
- Run tests
- Submit pull requests
- Add a new model profile
- Add a new framework adapter
- Add a benchmark task

### Adding a model profile

The fastest way to contribute: write a YAML profile for a model we don't support yet. See [Profile Format](docs/harness/profile-format.md) for the schema and [Claude Opus 4.6 profile](docs/harness/profile-claude-opus-4-6.md) as a reference.

## How it compares

| | Tename | Anthropic Managed Agents | AWS Bedrock AgentCore | Deep Agents |
|---|---|---|---|---|
| **Type** | Open-source runtime | Proprietary managed service | Proprietary managed service | Open-source framework |
| **Models** | Any | Claude only | Bedrock catalog | Any (no tuning) |
| **Per-model tuning** | Yes (profiles) | Yes (internal) | Limited | No |
| **Durable sessions** | Yes | Yes | Yes | No (DIY) |
| **Sandbox isolation** | Yes (Docker) | Yes (microVMs) | Yes | No (DIY) |
| **Credential isolation** | Yes (vault + proxy) | Yes | Yes (IAM) | No (DIY) |
| **Self-hosted** | Yes | No | AWS only | N/A (library) |
| **License** | Apache 2.0 | Proprietary | Proprietary | Open source |

Tename doesn't compete with Deep Agents — it runs beneath it. Use Deep Agents for orchestration, Tename for infrastructure.

## License

Apache 2.0. See [LICENSE](LICENSE).

## Status and roadmap

v0.1 is in active development. Track progress in [docs/sessions/v0-roadmap.md](docs/sessions/v0-roadmap.md).

This project is maintained by an individual developer. It's not backed by a company (yet). If you find it useful, starring the repo and opening issues with feedback is the best way to support it.
