# Sandbox

## Purpose

Executes LLM-generated code in isolation. Untrusted by design.
Bounded lifetime and resources. One backend in v0.1 (Docker); the
`SandboxBackend` ABC is designed so Firecracker / E2B / Modal
backends can slot in later without touching the harness.

## Public API

```python
from tename.sandbox import Sandbox, DockerBackend, SandboxRecipe

backend = DockerBackend()         # lazy docker.from_env() on first use
sandbox = Sandbox(backend)

sandbox_id = await sandbox.provision(SandboxRecipe())
result     = await sandbox.execute(sandbox_id, "python", {"code": "print('hi')"})
await sandbox.destroy(sandbox_id)
```

`Sandbox` is the service facade; `SandboxBackend` is the pluggable
engine.

```python
class SandboxBackend(ABC):
    async def provision(self, recipe: SandboxRecipe) -> str: ...
    async def execute(self, sandbox_id: str, tool: str, input: dict) -> ToolResult: ...
    async def destroy(self, sandbox_id: str) -> None: ...
    async def status(self, sandbox_id: str) -> SandboxStatus: ...
```

The backend owns primitives; the service owns lifecycle state
(`assert_transition` around every change, reflects backend
DESTROYED/ERROR reports back into the in-memory tracker).

## Sandbox recipes

```python
class SandboxRecipe(BaseModel):
    runtime:         str = "python:3.12-slim"   # Docker image tag
    packages:        list[str] = []              # pip-installed post-provision (python:* only)
    files:           dict[str, str] = {}         # absolute path -> contents, written at provision
    env:             dict[str, str] = {}         # non-secret env for the container
    cpu_limit:       int = 2                     # --cpus
    memory_limit_mb: int = 4096                  # --memory
    timeout_seconds: int = 600                   # per-tool-call wall-clock cap
    network_policy:  Literal["open", "isolated", "allowlist"] = "open"
```

Frozen Pydantic model; `extra="forbid"`. Credentials are
intentionally absent — the Tool Proxy injects them out-of-band at
call time and they never enter the sandbox (principle #5).

`network_policy`: only `"open"` is enforced in v0.1. `"isolated"` and
`"allowlist"` parse successfully but behave the same as `"open"`
until the policy engine lands. Documented so the schema doesn't
churn when enforcement arrives.

## State between tool calls

**Tool calls share state via the container's filesystem, not via
interpreter memory.** Each `python` tool invocation writes the code
to a fresh `/tmp/tename_<uuid>.py` and runs it as a standalone
subprocess; Python's module globals reset between calls.

Practically, for the model that means:

- Files written in turn 1 (`file_write`, or `python` writing to
  `/workspace/...`) are readable in turn 2.
- `import pandas as pd` in turn 1 does NOT make `pd` available in
  turn 2 — each `python` call must re-import what it needs.
- Environment variables set inside the code (`os.environ[...] = ...`)
  reset between calls; use `recipe.env` for values that must persist.

This is also true for the `bash` tool: each call is a fresh
`bash -lc '...'` invocation. Shell variables don't survive across
calls; write to a file if you need persistence.

If a future backend needs REPL-style interpreter persistence (e.g. a
Jupyter kernel), that's a new tool name — not a change to `python`.
Changing `python`'s contract would break every session log that
depends on the "subprocess per call" shape.

## Lifecycle state machine

```
    provisioning → ready → running → idle → destroyed
                      ↑         ↓       ↓       ↑
                      └─────────┴───────┘       │
                                                │
                                      error ────┘
```

States (`SandboxStatus` in `tename.sandbox.types`):
- **provisioning** — `docker pull` + `docker create` + `put_archive`
- **ready** — container is running a long-lived `tail -f /dev/null`
  so it stays alive between tool calls
- **running** — `docker exec` is in flight
- **idle** — previous exec returned; awaiting the next
- **destroyed** — stop + remove complete (terminal)
- **error** — poisoned (most commonly by a tool timeout); the next
  tool call re-provisions

Transitions live in `state_machine.py::ALLOWED_TRANSITIONS`. Invalid
transitions raise `InvalidTransitionError`. Self-transitions no-op
silently. Every valid transition logs `sandbox.state.transition` at
INFO.

**Timeout behavior:** on `asyncio.wait_for(...)` expiry the backend
`container.kill()`s and returns a timeout `ToolResult`; the service
moves the sandbox to ERROR. The harness's
`_get_or_provision_sandbox` sees a non-READY/IDLE status and
provisions fresh on the next tool call (a new
`system_event(type="sandbox_provisioned")` is emitted). A runaway
session can accrue multiple `sandbox_provisioned` events — that's
intentional: each is a distinct container worth auditing.

## Built-in tools

Six tools ship in v0.1. All take `input: dict` and return
`ToolResult`. Names are load-bearing — the harness decides "sandbox
vs proxy" by checking `tool_name in BUILTIN_TOOL_NAMES`.

| Tool | Input | Notes |
|---|---|---|
| `bash` | `{"command": str}` | `bash -lc <command>`; `is_error` on nonzero exit |
| `python` | `{"code": str}` | Writes to `/tmp/tename_<uuid>.py` and runs; subprocess per call |
| `file_read` | `{"path": str}` | `cat -- <path>` |
| `file_write` | `{"path": str, "content": str}` | tar-uploads via `put_archive` (`mkdir -p` parent first) |
| `file_edit` | `{"path": str, "old_str": str, "new_str": str, "replace_all"?: bool}` | Exact string replace; raises unless `replace_all=True` when `old_str` matches multiply |
| `file_list` | `{"path"?: str}` | `ls -la`; defaults to `/workspace` |

Tool schemas live in `tename.sandbox.schemas.BUILTIN_TOOL_SCHEMAS` as
`ToolDef`s. Adapters (`VanillaAdapter`, `DeepAgentsAdapter`) import
from there so they don't pull docker into adapter-only test runs.

**Adding a built-in** requires three touches:

1. A tool function in `src/tename/sandbox/tools/<name>.py` with
   signature `(container, input, recipe) -> ToolResult`.
2. An entry in `src/tename/sandbox/tools/__init__.py::_BUILTIN_TOOLS`.
3. A `ToolDef` in `src/tename/sandbox/schemas.py::BUILTIN_TOOL_SCHEMAS`.

Nothing in the harness changes. Users who want tools outside this
list use the Tool Proxy (`@proxy_tool`), which runs OUTSIDE the
sandbox and can inject credentials.

## Security boundaries

**The sandbox CAN access:**
- Its own filesystem under the container rootfs
- Its own processes and memory, up to the recipe's CPU/memory/PIDs
  limits
- Network egress (when `network_policy == "open"`, which is the only
  enforced policy in v0.1)
- Files written in via `recipe.files` at provision
- Non-secret environment variables from `recipe.env`

**The sandbox CANNOT access:**
- The host filesystem (proven by
  `tests/sandbox/test_isolation.py`: host tmp files not readable,
  host hostname not visible, host cwd not enumerable)
- Other sandboxes (each is its own Docker container)
- Tename's Python process or its memory
- The session log (it's in Postgres; the sandbox has no credentials
  for it)
- The Vault (the vault file lives on the host; the container has no
  mount for it)
- Credential values passed to proxy tools (proxy execution happens
  outside the sandbox)

**What's deliberately weak in v0.1:**
- Kernel-level isolation is whatever Docker gives you — not
  Firecracker microVMs (deferred as a future backend).
- Network egress isn't restricted by default. Agents can reach
  anything the host can. Tighten via `network_policy` once the
  enforcement lands in a later release, or via a Docker network /
  VPC security group today.
- No runtime monitoring beyond Docker's normal stats.

This is acceptable for local development and OSS use. For production
deployments that need harder guarantees, either wrap Tename in an
outer VM boundary, or implement a custom `SandboxBackend` that
targets a harder-isolated runtime.

Every Tename-provisioned container is labeled `tename.sandbox=1`, so
`docker ps -a -f label=tename.sandbox` lists anything the harness
provisioned. Useful for cleaning up orphans from a crashed run
without running `docker system prune`.

Docker-level hardening applied by the backend:

- `security_opt=["no-new-privileges:true"]`
- `pids_limit=512`
- `nano_cpus = cpu_limit * 1e9` (from recipe)
- `mem_limit = f"{memory_limit_mb}m"` (from recipe)

## Lazy provisioning

Sandboxes are NOT provisioned when sessions start. They're
provisioned when the harness first routes a tool call into the
sandbox.

Flow:

1. Session starts; no sandbox exists.
2. Model emits a `tool_call` for `python` / `bash` / `file_*`.
3. Harness looks up the latest `system_event(type="sandbox_provisioned")`
   in the event log. If none (or its `status()` is not READY/IDLE),
   it calls `sandbox.provision(recipe)`.
4. Harness emits a fresh
   `system_event(type="sandbox_provisioned", sandbox_id=..., runtime=...)`
   with a deterministic uuid5 id keyed on `session_id + sandbox_id`
   (so replay after a mid-provision crash collapses idempotently).
5. Harness calls `sandbox.execute(sandbox_id, tool, input)` and
   emits the `tool_result`.

Subsequent tool calls in the same session reuse the sandbox. When
the session ends the harness's `try/finally` calls
`_destroy_sandboxes` for every id it provisioned; destroy failures
are logged and swallowed so cleanup never blocks session completion.

**Why lazy:** many sessions never call sandbox tools at all. Agents
doing pure research (`web_search`, conversation) shouldn't pay the
provisioning cost.

## Reuse within a session

Once provisioned, a sandbox is reused for all tool calls in the
session. The harness finds the sandbox_id by scanning the event log
for the latest `system_event(type="sandbox_provisioned")`. Filesystem
changes persist within the session (see "State between tool calls"
above).

**What's NOT persisted:**
- Across sessions — each session gets a fresh sandbox.
- Across process restarts — the in-memory `Sandbox._status` tracker
  doesn't survive a crash. The `system_event` in the log is the
  durable record of the sandbox_id; on restart the harness calls
  `status()`, re-provisions if the container is gone, and emits a
  new event.

A future release may add persistent sandbox filesystems for
long-running sessions; v0.1 is single-session-scoped.

## Docker backend specifics

`DockerBackend` wraps the synchronous `docker` Python SDK in
`asyncio.to_thread` so the ABC stays async and the event loop stays
free during blocking Docker calls.

**Base images.** v0.1 uses the official `python:3.12-slim` image by
default. There are NO custom `tename/sandbox-*` images — the S9 call
was to avoid building custom images until there are concrete
common-package needs. Users who want pre-installed packages either:

- Set `recipe.packages` (pip-installed at provision, only when the
  runtime tag starts with `python:`), or
- Override `recipe.runtime` with their own image.

**Tool dispatch.** Every `execute` goes through a per-call
`asyncio.wait_for(timeout=recipe.timeout_seconds)`. On timeout the
backend `container.kill()`s and the service transitions the sandbox
to ERROR.

**Idempotent destroy.** `destroy` does a best-effort stop + remove;
second calls are no-ops. `SandboxNotFoundError` surfaces when a
caller tries to use a sandbox id that was never provisioned (or was
cleaned up).

**Docker SDK threading rule.** The Python Docker SDK is synchronous.
Calling it directly from an async context blocks the event loop for
the duration of the call (often >100ms for `exec_run`). Backend code
MUST go through `await asyncio.to_thread(sync_fn, *args)`. The
shared helpers in `sandbox/tools/_exec.py` (`run_exec`, `put_file`)
encapsulate the demux-decode pattern and run on the worker thread.

## Future backends

The interface is designed so additional backends slot in without
harness changes:

- **Firecracker microVMs** — stronger isolation than Docker; a
  `FirecrackerBackend` implementing the ABC and speaking
  firecracker-containerd.
- **E2B** — specialist sandbox provider; an `E2BBackend` speaking
  their HTTP API.
- **Modal** — serverless backend for bursty workloads.
- **Custom VPC sandboxes** — for enterprise customers running Tename
  inside their own cloud.
- **Local subprocess** — zero-isolation backend for CI or unit tests
  where a full Docker container is overkill.

None ship in v0.1.

## Testing

Shipped coverage in `tests/sandbox/`:

- `test_state_machine.py` — 19 unit tests for valid / invalid
  transitions, self-transition no-ops, DESTROYED terminality.
- `test_types.py` — 8 unit tests for the Pydantic models
  (frozen / `extra="forbid"` / defaults / positive-int validators).
- `test_docker_backend.py` — 14 integration tests (`sandbox` pytest
  marker): provision/destroy roundtrip, idempotent destroy, bash
  echo, python stdout, python-exception-as-tool-error,
  write→read, edit single / ambiguous / replace_all, list, timeout
  kills container, unknown tool, recipe files uploaded at provision,
  64 MB OOM kills a 256 MB allocation, lazy `docker.from_env()`.
- `test_isolation.py` — 3 integration tests proving host isolation
  (see "Security boundaries" above).
- Harness integration: `tests/harness/test_loop_sandbox.py` and
  `test_sandbox_e2e.py` cover the lazy-provision + reuse +
  destroy-on-session-end flow, including a live Opus 4.6 factorial
  computation.

Every integration test self-skips with a clear message when Docker
isn't reachable (`docker.from_env().ping()` fails). Override the
default test image via `TENAME_SANDBOX_TEST_IMAGE`.
