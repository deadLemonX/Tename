# Sandbox

## Purpose

Executes LLM-generated code in isolation. Untrusted by design. Bounded lifetime and resources.

## Design

In v0.1, the sandbox is Docker containers provisioned on demand. The interface is designed to support multiple backends later, but Docker is the only implementation for now.

## API

```python
class Sandbox:
    async def provision(recipe: SandboxRecipe) -> str:
        """Create a sandbox, return sandbox_id. Blocks until ready."""
    
    async def execute(sandbox_id: str, tool: str, input: dict) -> ToolResult:
        """Run a tool in an existing sandbox. Returns result or error."""
    
    async def destroy(sandbox_id: str) -> None:
        """Tear down the sandbox. Reclaims resources."""
    
    async def status(sandbox_id: str) -> SandboxStatus:
        """Get current state of a sandbox."""
```

## Sandbox recipes

A recipe defines what the sandbox should look like:

```python
@dataclass
class SandboxRecipe:
    runtime: str  # "python:3.12", "node:20", "bash"
    packages: List[str]  # ["pandas", "numpy"]
    files: Dict[str, str]  # {"/app/data.csv": "..."}
    env: Dict[str, str]  # Non-secret environment
    cpu_limit: int = 2
    memory_limit_mb: int = 4096
    timeout_seconds: int = 600
    network_policy: str = "open"  # or "isolated", "allowlist"
```

Credentials are NOT part of the recipe. They're injected via the Tool Proxy, never by the sandbox directly.

## Lifecycle state machine

```
    provisioning -> ready -> running -> idle -> destroyed
                       \                  \
                        \                  -> destroyed (after timeout)
                         -> error
```

- **provisioning:** Docker is pulling images, creating container
- **ready:** Container is running but no tool is executing
- **running:** A tool is actively executing
- **idle:** Tool completed, awaiting next call
- **destroyed:** Container stopped and removed
- **error:** Something went wrong; destroyed after logging

Transitions are logged. Invalid transitions raise errors.

## Built-in tools

v0.1 ships with these sandbox tools:

**`bash`:** Execute a bash command. Input: `{command: string}`. Output: stdout, stderr, exit_code.

**`python`:** Execute Python code. Input: `{code: string}`. Output: stdout, stderr, exception (if raised).

**`file_read`:** Read a file from the sandbox filesystem. Input: `{path: string}`. Output: content or error.

**`file_write`:** Write a file to the sandbox filesystem. Input: `{path: string, content: string}`. Output: success or error.

**`file_edit`:** Edit a file (str_replace style). Input: `{path: string, old_str: string, new_str: string}`. Output: success, diff, or error.

**`file_list`:** List files in a directory. Input: `{path: string}`. Output: list of files with metadata.

Custom tools can be added by users — they're Python functions decorated with `@sandbox_tool` that the sandbox exposes.

## Security boundaries

**What the sandbox CAN'T access:**
- The host filesystem outside the sandbox container
- Other sandboxes
- Tename credentials or configuration
- The session log (can't modify event history)
- The vault
- Network egress restrictions if configured (default: open in v0.1 since it's local)

**What the sandbox CAN access:**
- Its own filesystem (bounded by container limits)
- Its own processes and memory
- Network egress (unless restricted)
- Files explicitly added via the recipe

**What's deliberately weak in v0.1:**
- Kernel-level isolation is whatever Docker provides (not Firecracker microVMs — that's a commercial-grade choice)
- Network egress isn't restricted by default (local development context)
- No sophisticated runtime monitoring

This is acceptable for local development and open-source use. For production enterprise deployments, users should provide additional isolation at the infrastructure layer (dedicated VMs, microVMs, network policies). The sandbox interface is designed so better backends can be swapped in.

## Provisioning lazily

Sandboxes are NOT provisioned when sessions start. They're provisioned when the harness first calls `execute` for a sandbox tool.

**Why:** Many sessions never call sandbox tools. Agents that only do research (web searches, analysis) don't need sandboxes. Provisioning them up-front wastes resources and adds latency.

**Implementation:**
1. Session starts; no sandbox exists
2. Model calls `python` tool
3. Harness sees no sandbox_id in session events
4. Harness calls `sandbox.provision(recipe)` — blocks 2-5 seconds for Docker
5. Harness emits `system_event` with type=sandbox_provisioned, sandbox_id=X
6. Harness calls `sandbox.execute(X, "python", ...)`

On session end, the sandbox is destroyed.

## Reuse within a session

Once provisioned, a sandbox is reused for all tool calls in the session. The harness finds the sandbox_id from session events and reuses it. Filesystem changes persist within the session.

**What's NOT persisted:**
- Across sessions (each session gets its own fresh sandbox)
- Across process restarts mid-session (sandbox is destroyed if it becomes orphaned)

A future commercial version might add persistent sandbox filesystems for long-running sessions, but v0.1 is single-session-scoped.

## Docker backend specifics

**Base images:** Published by us, based on official language images with common packages pre-installed. Users can override with custom images.

```
Runtime: python:3.12
Base image: tename/sandbox-python:3.12
Pre-installed: numpy, pandas, requests, pydantic

Runtime: node:20
Base image: tename/sandbox-node:20
Pre-installed: axios, lodash

Runtime: bash
Base image: tename/sandbox-bash:latest
Pre-installed: curl, wget, jq, git
```

**Resource limits:** Docker flags `--cpus`, `--memory`, and `--pids-limit` are set from the recipe.

**Timeout:** A timeout watchdog kills the container if it exceeds the recipe timeout.

**Networking:** Default is bridge mode. Network policies (isolated, allowlist) require custom Docker networks that users set up.

## Future backends

The Sandbox interface is designed so other backends can be added:

- **Firecracker microVMs** (via a library like firecracker-containerd) — stronger isolation
- **E2B** — specialist sandbox provider (commercial backend)
- **Modal** — serverless sandbox backend (commercial backend)
- **Custom VPC sandboxes** — for enterprise deployments in customer cloud
- **Local subprocess** — no isolation, for development only

These don't ship in v0.1. The interface just doesn't preclude them.

## Testing requirements

- Unit tests for state machine transitions
- Integration tests that actually provision, execute, and destroy Docker containers
- Tool tests for each built-in tool (bash, python, file_*, etc.)
- Timeout tests (long-running commands are killed)
- Resource limit tests (high-memory commands are killed)
- Concurrent sandbox tests (20 sandboxes active, no interference)
