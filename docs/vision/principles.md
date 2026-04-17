# Architectural Principles

These are non-negotiable commitments. Every design decision is measured against them. Any code that violates them needs an explicit ADR justifying the exception.

## 1. The three interfaces are independently swappable

The session, harness, and sandbox communicate through narrow, stable APIs. Any one of them can be replaced without touching the others.

**What this means in practice:**
- Session service has one API. Its Postgres backing could be replaced with something else and nothing else in the system would need to change.
- Harness has one API. Its implementation could be rewritten in a different language (unlikely but possible) and sessions and sandboxes would still work.
- Sandbox has one API. The Docker backend in v0.1 is one of many possible backends.

**Why this matters:** Agent infrastructure evolves fast. Locking components together means the whole system has to evolve in lockstep. Decoupling means pieces can improve independently.

## 2. Profiles, not code, encode model-specific behavior

The harness is model-agnostic. Everything that differs between Claude, GPT, Gemini, and Llama lives in YAML profile files, not in the harness code.

**What this means in practice:**
- Adding a new model means writing a YAML file
- Customizing for a specific workload means forking a profile
- The harness never imports model-specific code
- Profile validation is strict — bad profiles are rejected at load time

**Why this matters:** Model code lock-in is the main failure mode of multi-model systems. If the harness grows `if model == "claude"` branches, we've lost. Profiles keep the harness clean forever.

## 3. The harness is stateless

The harness holds no state that survives a crash. All state lives in the session log.

**What this means in practice:**
- No in-memory event buffer
- No accumulated context — rebuilt from events on each iteration
- No sandbox references held in memory — stored in session events
- Kill the harness at any moment, start a new instance, call `wake(session_id)`, it resumes exactly where the old one stopped

**Why this matters:** Agents run for minutes to hours. Things fail. The fundamental reliability guarantee is that failures don't destroy state. Statelessness is what makes that guarantee real.

## 4. Idempotency on every write

Every write operation is safe to retry. Duplicates are silently handled.

**What this means in practice:**
- Every event has a client-supplied `event_id` (UUID)
- If the same event_id is submitted twice, the second call returns the existing event's data without error
- Session creation is idempotent via client-supplied request_id
- Sandbox provisioning is idempotent via recipe hash

**Why this matters:** Networks fail. Processes crash. Retries happen. Without idempotency, retries corrupt state. With it, retries are safe and recovery is clean.

## 5. Credentials never enter the sandbox

Secrets are stored separately from the execution environment. A tool proxy injects them at call time.

**What this means in practice:**
- Vault holds credentials (v0.1: encrypted file on disk; future: AWS Secrets Manager, HashiCorp Vault)
- Tool proxy is a process that the harness calls with a session token, NOT credentials
- Proxy fetches the right credential, makes the outbound call, returns the result
- Sandbox never sees API keys, OAuth tokens, or any secret

**Why this matters:** This is the structural answer to prompt injection. No matter how cleverly an attacker manipulates the model, there's nothing in the sandbox to steal. The security boundary is architectural, not just policy.

## 6. Append-only event log

Events are never modified or deleted (until the session is deleted).

**What this means in practice:**
- No UPDATE statements on the events table
- "Corrections" or "edits" are new events that reference older ones
- Compaction produces summary events; it doesn't modify original events
- The full history is always reconstructable

**Why this matters:** Append-only logs are the simplest concurrency model. No race conditions on updates. No lost writes. Debugging is possible because the full history is preserved. Audit trails are automatic.

## 7. Streaming is the default

Every operation that can stream, streams. No batching that delays user-visible output.

**What this means in practice:**
- Model responses are streamed token-by-token to the session log
- The SDK yields events as they arrive, not after the whole response is complete
- Tool output is streamed when the tool supports it
- Users see progress in real time

**Why this matters:** Agents that feel instant are adopted. Agents that feel sluggish are abandoned. Streaming is what makes the experience feel alive.

## 8. Local-first for v0.1

Everything runs on a developer's laptop via `docker compose up`. No cloud dependencies.

**What this means in practice:**
- Postgres runs in a local container
- Sandboxes are local Docker containers
- Models are called via the developer's own API key
- No cloud accounts required to try Tename

**Why this matters:** Friction kills adoption. If trying Tename requires an AWS account and three hours of setup, nobody tries it. If it requires `docker compose up` and 5 minutes, many try it. Cloud deployment matters later; adoption matters now.

## 9. Open source all the way down

All code is Apache 2.0. No proprietary components. No "source available" compromises. No commercial pressure on the core.

**What this means in practice:**
- Anyone can fork
- Anyone can audit
- Anyone can modify for their needs
- Anyone can run it without asking permission

**Why this matters:** Infrastructure needs trust. Trust is earned through transparency. Commercial optionality still exists (hosted services, enterprise features later) but the core must be genuinely free.

## 10. Preserve future optionality

Decisions made now should not foreclose reasonable future paths.

**What this means in practice:**
- The code is structured so multi-tenancy could be added later without major rewrite
- The interfaces are designed so a hosted service could be built on top
- The licensing doesn't prevent future commercial models
- The trademark is protected

**Why this matters:** We don't know what the future brings. What we know is that good choices today leave good options tomorrow. Bad choices today (wrong license, bad architecture, wrong contribution model) close doors permanently.

## How these principles get enforced

- Every pull request is reviewed against them
- Every ADR explicitly states which principles it touches
- Violations require explicit justification, documented and dated for review
- Contributors are pointed to this document before their first contribution
