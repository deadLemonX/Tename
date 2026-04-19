# Tename

> **The open-source production runtime for any agent framework.**
> Durable sessions, sandbox isolation, and per-model YAML profiles —
> so the same agent code runs well on Claude today and any other model
> tomorrow.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Status: v0.1 initial release](https://img.shields.io/badge/status-v0.1%20initial%20release-green)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)

---

## What is Tename?

Tename is an open-source runtime that sits *beneath* your agent
framework — whether that's Deep Agents, Claude Agent SDK, or your own
custom code. It provides the infrastructure layer most teams end up
rebuilding from scratch:

- **Durable sessions** that survive crashes. Kill the process
  mid-stream, restart, and the agent resumes cleanly from an
  append-only event log.
- **Per-model optimization** via YAML profiles. Same agent code,
  different model, one config change. Each profile encodes caching
  strategy, context budget, tool format, and known quirks.
- **Sandbox isolation** for LLM-generated code. Docker containers
  with CPU, memory, and PID limits. Credentials never enter the
  sandbox.
- **Framework adapters** so you bring your existing code. Tename
  doesn't replace Deep Agents — it makes Deep Agents production-grade.

## 30-second look

```python
from tename import Tename

with Tename() as client:
    agent = client.agents.create(
        name="coding-agent",
        model="claude-opus-4-6",
        system_prompt="You are a careful software engineer.",
        tools=["python", "bash", "file_read", "file_write"],
    )

    session = client.sessions.create(agent_id=agent.id)

    for event in session.send("Compute the 50th Fibonacci number in Python."):
        if event.type == "assistant_message" and event.payload.get("is_complete"):
            print(event.payload["content"])
```

When GPT-5 ships a profile, switching models is one field change:

```python
agent = client.agents.create(
    model="gpt-5",   # ← just this
    ...
)
```

The runtime handles the rest — different caching strategy, different
tool format, different context management — all from the YAML profile.

## Why Tename exists

Teams building production AI agents face a forced choice:

| Option | Pros | Cons |
|---|---|---|
| **Proprietary runtimes** (Anthropic Managed Agents, AWS Bedrock AgentCore) | Production-grade infrastructure | Locked to one model provider |
| **Open frameworks** (Deep Agents, Claude Agent SDK) | Model flexibility, code ownership | You build all the infrastructure yourself |

Tename is the third option: production-grade infrastructure that works
with any model and any framework.

## Architecture

```
Your Code → Python SDK → Harness Runtime → Model Router → Any Provider
                               ↕                ↕
                         Session Service     Sandbox (Docker)
                          (Postgres)            ↕
                                          Tool Proxy + Vault
```

Three decoupled interfaces (brain, hands, state) that fail and recover
independently. See [docs/architecture/overview.md](docs/architecture/overview.md).

## What's in v0.1

- Session Service with append-only event log, idempotent writes, and
  advisory-lock-serialized concurrent emitters
- Stateless Harness Runtime with crash-safe resume
- Model Router with Anthropic provider and streaming
- YAML profile system with inheritance, validation, and a bundled
  Claude Opus 4.6 profile
- Docker sandbox with six built-in tools (`bash`, `python`,
  `file_read`, `file_write`, `file_edit`, `file_list`)
- Vault (PBKDF2 + Fernet) for encrypted credential storage
- Tool Proxy that injects credentials at call time — credentials
  never reach the sandbox or the session log
- Deep Agents framework adapter
- Vanilla adapter (no-framework fallback)
- Python SDK (sync + async)
- `tename vault` CLI for credential management
- 5 benchmark tasks for profile validation
- 3 worked examples

**Coming in v0.2:** GPT-5 and Gemini profiles, Claude Agent SDK adapter,
summarization compaction, OpenTelemetry tracing, TypeScript SDK.

## Getting started

### Prerequisites

- Python 3.12+
- Docker (for Postgres and the code sandbox)
- An Anthropic API key

### Install

```bash
pip install tename
```

### Point it at Postgres and apply the schema

You need a running Postgres. Either use a managed one or spin one up
with Docker:

```bash
docker run -d --name tename-postgres \
  -e POSTGRES_USER=tename -e POSTGRES_PASSWORD=tename \
  -e POSTGRES_DB=tename_dev -p 5433:5432 \
  postgres:16-alpine

export TENAME_DATABASE_URL='postgresql+psycopg://tename:tename@localhost:5433/tename_dev'
tename migrate     # applies the wheel-bundled schema
```

If you prefer cloning the repo: `git clone ...; make dev; make migrate`
does all three steps at once.

### Run the hello-world

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -c "
from tename import Tename
with Tename(enable_sandbox=False) as client:
    agent = client.agents.create(
        name='hi',
        model='claude-opus-4-6',
        system_prompt='You are a concise assistant.',
    )
    session = client.sessions.create(agent_id=agent.id)
    for ev in session.send('Tell me one interesting fact about octopuses.'):
        if ev.type == 'assistant_message' and ev.payload.get('is_complete'):
            print(ev.payload['content'])
"
```

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for the full 10-minute
walkthrough, including troubleshooting.

## Examples

- [01-hello-world](examples/01-hello-world/) — simplest possible agent
- [02-research-agent](examples/02-research-agent/) — Deep Agents
  planning + `web_search` (Tavily)
- [03-coding-agent](examples/03-coding-agent/) — writes and runs
  Python in the sandbox

## Documentation

| Doc | What it covers |
|---|---|
| [QUICKSTART](docs/QUICKSTART.md) | Clone → install → running agent in 10 minutes |
| [Product vision](docs/vision/product-vision.md) | What Tename is and why it exists |
| [Principles](docs/vision/principles.md) | Non-negotiable architectural commitments |
| [Architecture overview](docs/architecture/overview.md) | How the system fits together |
| [Profile format](docs/harness/profile-format.md) | YAML profile schema reference |
| [SDK design](docs/dx/sdk-design.md) | Python SDK API reference |
| [Deployment](docs/operations/deployment.md) | Running Tename in production |

## Contributing

We welcome contributions! The fastest way to contribute is a new model
profile — one YAML file. See [CONTRIBUTING.md](CONTRIBUTING.md) for how
to set up the dev environment, run tests, and submit PRs.

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
| **License** | Apache 2.0 | Proprietary | Proprietary | MIT |

Tename doesn't compete with Deep Agents — it runs beneath it. Use
Deep Agents for orchestration, Tename for infrastructure.

## License

Apache 2.0. See [LICENSE](LICENSE).

## Status

v0.1 is the initial public release. The runtime is feature-complete for
single-developer local use. Please file issues and PRs — the project
is maintained as an individual side-project and feedback genuinely
shapes v0.2.
