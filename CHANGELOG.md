# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-04-19

The initial public release. Tename is an open-source, model-agnostic
runtime for AI agents: durable sessions, sandbox isolation, credential
vault, and per-model YAML profiles.

### Added

- **Session Service** — Postgres-backed append-only event log with
  idempotent writes on client-supplied `event_id`, advisory-lock-
  serialized sequence assignment, 256 KiB payload cap, keyset-paginated
  reads, and a FK-enforced data model (see
  `docs/architecture/data-model.md`).
- **Model Router** — Streaming provider interface with an Anthropic
  provider. Pricing table bundled in the wheel; usage chunks are
  enriched with computed `cost_usd`. Retry wraps startup only;
  mid-stream failures surface as a single `error` chunk.
- **Harness Runtime** — Stateless brain loop. Every iteration rebuilds
  context from the event log, so killing the process mid-stream and
  restarting resumes cleanly (verified by
  `test_crash_mid_stream_resumes_cleanly`). Supports compaction via
  truncation (summarize / file_offload deferred to v0.2).
- **Profile system** — YAML profiles with recursive `extends`
  inheritance, cycle detection, key-by-key dict merge, and field-level
  Pydantic validation. Bundled profile: `claude-opus-4-6`. New profiles
  land as a single YAML file in `tename.profiles`.
- **Sandbox** — `SandboxBackend` ABC with a `DockerBackend`
  implementation. Six built-in tools: `bash`, `python`, `file_read`,
  `file_write`, `file_edit`, `file_list`. Recipe supports per-agent
  CPU, memory, PIDs, timeout, and seeded files. Network policy is
  `open` in v0.1; `isolated` / `allowlist` accepted but not yet
  enforced.
- **Vault** — Encrypted credential store at `~/.tename/vault.json.enc`.
  PBKDF2-HMAC-SHA256 (600 k iterations) + Fernet (AES-128-CBC + HMAC)
  per credential. Atomic writes via `os.replace`; file mode `0o600`;
  coarse error messages (anti-oracle).
- **Tool Proxy** — `@proxy_tool` decorator + process-global registry.
  Credentials pull from the vault at call time and never reach the
  sandbox, the model context, the session event log, or any log
  record (three caplog-scan tests enforce the invariant).
- **Framework adapters** — `VanillaAdapter` for no-framework agents,
  `DeepAgentsAdapter` for the Deep Agents framework. Both support
  multi-turn tool rounds (assistant text + tool_use folds into one
  message; tool_result events become tool-role messages with matching
  `tool_use_id`).
- **Python SDK** — `from tename import Tename` (sync) or `AsyncTename`
  (async). Sub-clients for `agents`, `sessions`, `vault`. Sync
  implementation runs a dedicated background event loop so
  `for event in session.send(...)` works as a plain iterator.
- **CLI** — `tename vault {set, list, remove, get}` for credential
  management. `tename --version`. Installed via
  `[project.scripts] tename = "tename.cli.main:main"`.
- **Benchmark suite** — 5 tasks covering research, coding, data
  analysis, tool selection, and multi-turn context retention. Runner
  writes JSON results ready for manual grading against a documented
  rubric. All 5 pass for `claude-opus-4-6` (see
  `docs/memory/v0-validation-results.md`).
- **Examples** — `01-hello-world`, `02-research-agent` (Deep Agents +
  Tavily `web_search`), `03-coding-agent` (vanilla + sandboxed
  Python).
- **Docs** — `README.md` polish, `docs/QUICKSTART.md` (under 10 min
  from clone to running agent), architecture + DX + operations docs,
  profile format reference, benchmark suite spec, adapter concept
  mapping.
- **Infrastructure** — docker-compose for Postgres, alembic migrations
  in the wheel, `.env` auto-loading in Makefile targets and pytest,
  pyright strict mode, ruff lint/format, pre-commit hooks, GitHub
  issue / PR templates, Apache 2.0 LICENSE.

### Verified architectural properties

- Append-only event log with idempotent writes and concurrency safety
  (commitments #4, #6).
- Stateless harness with crash-safe resume (commitment #3, ADR 0002).
- Sandbox isolation: host files are not visible inside the sandbox
  (commitment #1; new tests in `tests/sandbox/test_isolation.py`).
- Credential isolation: credentials never appear in event payloads,
  tool results, or log records (commitment #5).
- Framework-agnostic harness: swapping `vanilla` ↔ `deep_agents` is a
  single agent-config field change.
- Multi-turn tool use: assistant + tool_use + tool_result rounds
  round-trip through both adapters without the Anthropic
  "conversation must end with a user message" error.

### Out of scope for 0.1 (deferred to 0.2+)

Multi-tenancy, auth systems, hosted cloud service, Web UI,
summarization / file_offload compaction, multiple sandbox backends,
TypeScript SDK, automated benchmark grading, additional profiles
(GPT-5, Gemini, Llama), additional framework adapters (Claude Agent
SDK, Pydantic AI).

[0.1.0]: https://github.com/deadLemonX/Tename/releases/tag/v0.1.0
