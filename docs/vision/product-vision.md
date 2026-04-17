# Product Vision

## The one-sentence pitch

Tename is an open-source production runtime that runs beneath any agent framework, providing durable sessions, sandbox isolation, and per-model optimization — so the same agent code runs well on Claude, GPT, Gemini, or Llama without rewrites.

## The problem we solve

Teams building AI agents today face a fragmented landscape. They can use proprietary managed runtimes (Anthropic Managed Agents, AWS Bedrock AgentCore) and get production infrastructure but lock into one model provider. Or they can use open frameworks (Deep Agents, Claude Agent SDK) and have model flexibility but rebuild the operational infrastructure themselves — sandboxes, session durability, secret management, failure recovery.

The gap is a runtime that provides production-grade primitives without the SaaS lock-in. Something that makes the agent framework you already use reliable in production, across any model, without forcing you into someone else's hosted service.

Tename fills this gap.

## Why it's open source

Three reasons this project is open source, not commercial:

**Adoption is the primary goal.** Developer infrastructure becomes valuable through widespread use. Open source maximizes that.

**Trust matters for infrastructure.** Developers running production agents on Tename need to audit it, modify it, and run it in their own infrastructure. Closed-source infrastructure is a non-starter for many teams. Open source makes trust earnable through transparency.

**Community compounds value.** Every contributor who adds a model profile, framework adapter, or benchmark task makes Tename better for everyone. Open source enables that compounding.

## What makes it different

**Model-agnostic with real per-model tuning.** Most "model-agnostic" systems work equally poorly with every model because they use lowest-common-denominator integrations. Tename uses YAML profiles that encode per-model optimizations — caching strategy, tool format, context management, quirks. Writing a new profile takes days, not weeks, and validation happens through an open benchmark suite.

**Framework-agnostic runtime.** Tename doesn't compete with Deep Agents or the Claude Agent SDK. It runs beneath them. The framework adapter pattern means you bring your existing agent code and Tename provides the runtime underneath. Switch frameworks, keep the runtime. Switch runtimes (if you want), keep the framework.

**Durable by default.** Sessions survive crashes. The harness is stateless — kill it mid-loop, start another instance, it resumes from the session log. This reliability guarantee is rare in open-source agent projects; most assume happy paths and lose state on failure.

**Proper sandbox isolation.** LLM-generated code runs in Docker containers (v0.1) or other isolated environments. Credentials never enter the sandbox. The tool proxy pattern structurally prevents the category of attacks where prompt injection tries to exfiltrate secrets.

## Who it's for

**Primary audience in v0.1:**

- Teams running AI agents in production and hitting reliability walls (lost state on failures, flaky tool execution, hard to debug)
- Teams that want to try multiple models but don't want to maintain separate integrations
- Teams that want production primitives (sandboxes, session durability) without building from scratch
- Open-source-committed teams that refuse managed services on principle
- Infrastructure engineers building agent-powered products where runtime reliability matters

**Not the primary audience yet:**

- Hobbyists experimenting with LLMs (they don't need this level of infrastructure)
- Enterprises wanting managed services with SLAs (that's commercial future)
- Teams fully satisfied with a single-model proprietary runtime (no pain to solve)

## The long-term vision

In 3-5 years, Tename becomes the default open-source runtime for AI agents, the way Kubernetes became the default for containers and Postgres became the default for relational data. Every major agent framework works on Tename. Every major model provider has a well-tuned Tename profile. Companies build on Tename because it's the neutral, trusted infrastructure layer.

Whether that infrastructure layer is maintained by a foundation, a commercial company, or both — that's a question for the future. The near-term goal is just: build something developers love and use.

## What we're not trying to be

- **Not a framework.** Deep Agents and Claude Agent SDK are frameworks. They define how to structure agent code. Tename doesn't. It's the runtime underneath. If you want framework opinions, use a framework on top of Tename.

- **Not a hosted service.** Not in v0.1. Developers run it themselves. A hosted version might exist in the future, but that's a separate product decision.

- **Not a compliance product.** SOC 2, HIPAA, audit logging — these matter for enterprise customers but not for open-source users. Skip them for now.

- **Not a dev tool or IDE.** No web UI for session replay in v0.1. The session log is a Postgres table you can query. Someone will eventually build a UI on top, but we're not building one.

- **Not a low-code agent builder.** Visual programming, drag-and-drop agents, form-based configuration — not our space. Tename is for engineers who write code and want better infrastructure.

## Success metrics for v0.1

- Public repo on GitHub with Apache 2.0 license
- At least 10 developers report using it in real projects
- At least 2 external contributors land PRs
- Documentation quality such that someone can go from `git clone` to running agent in under 10 minutes
- At least 3 model profiles shipped (Claude Opus, GPT-5, Gemini 3)
- At least 1 framework adapter working (Deep Agents)
- 500+ GitHub stars within 6 months of release

These are adoption-focused, not revenue-focused. Revenue isn't the goal yet. Adoption is the goal.
