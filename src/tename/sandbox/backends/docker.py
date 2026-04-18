"""Docker backend for the Sandbox module.

Uses the synchronous `docker` Python SDK; every blocking call is
dispatched to a worker thread via `asyncio.to_thread` so the backend
satisfies the async `SandboxBackend` contract without blocking the
event loop.

Lifecycle pattern:

- `provision(recipe)`: pull the base image if absent, create a
  detached container with `tail -f /dev/null` as its command (so it
  stays alive between tool calls), apply CPU / memory / pids limits,
  upload `recipe.files`, install `recipe.packages` via pip for
  python runtimes.
- `execute(sandbox_id, tool_name, input)`: dispatch to the registered
  built-in tool. Per-call timeout = `recipe.timeout_seconds`; on
  timeout we `kill()` the container (which aborts the exec) and
  surface a timeout error as `ToolResult(is_error=True, error=...)`.
- `destroy(sandbox_id)`: stop then remove. Idempotent.
- `status(sandbox_id)`: map docker container state → `SandboxStatus`.

v0.1 does NOT implement network policies beyond the default bridge;
`recipe.network_policy` is accepted but only `"open"` is enforced.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from tename.sandbox.base import SandboxBackend
from tename.sandbox.tools import get_tool, is_builtin
from tename.sandbox.types import SandboxRecipe, SandboxStatus, ToolResult

if TYPE_CHECKING:
    from docker import DockerClient
    from docker.models.containers import Container

logger = logging.getLogger(__name__)


CONTAINER_LABEL = "tename.sandbox"
"""Label applied to every container created by Tename. Makes orphan
cleanup in dev environments a one-liner: `docker ps -f label=tename.sandbox`."""


class SandboxNotFoundError(LookupError):
    """Raised when a sandbox_id isn't tracked by this backend."""


class DockerBackend(SandboxBackend):
    """Docker-based sandbox backend.

    Args:
        client: Optional pre-built `docker.DockerClient`. When omitted
            the backend lazily builds one via `docker.from_env()` on
            first use. Tests inject a fake client here.
    """

    def __init__(self, client: DockerClient | None = None) -> None:
        self._client = client
        self._containers: dict[str, Container] = {}
        self._recipes: dict[str, SandboxRecipe] = {}

    # ---- Public contract ---------------------------------------------------

    async def provision(self, recipe: SandboxRecipe) -> str:
        container = await asyncio.to_thread(self._provision_sync, recipe)
        sandbox_id = _short_id(container.id)
        self._containers[sandbox_id] = container
        self._recipes[sandbox_id] = recipe
        logger.info(
            "sandbox.docker.provisioned",
            extra={"sandbox_id": sandbox_id, "runtime": recipe.runtime},
        )
        return sandbox_id

    async def execute(self, sandbox_id: str, tool_name: str, input: dict[str, Any]) -> ToolResult:
        if not is_builtin(tool_name):
            return ToolResult(
                is_error=True,
                error=f"sandbox: unknown tool '{tool_name}'",
                content=f"sandbox: unknown tool '{tool_name}'",
            )
        try:
            container = self._containers[sandbox_id]
        except KeyError as exc:
            raise SandboxNotFoundError(f"unknown sandbox_id '{sandbox_id}'") from exc
        recipe = self._recipes[sandbox_id]
        tool_fn = get_tool(tool_name)

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(tool_fn, container, input, recipe),
                timeout=recipe.timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "sandbox.docker.timeout",
                extra={
                    "sandbox_id": sandbox_id,
                    "tool": tool_name,
                    "timeout_s": recipe.timeout_seconds,
                },
            )
            # Kill the container so the stuck exec dies. Follow-up tool
            # calls will see the sandbox as DESTROYED / ERROR via status().
            await asyncio.to_thread(_safe_kill, container)
            return ToolResult(
                is_error=True,
                error=f"tool '{tool_name}' exceeded {recipe.timeout_seconds}s timeout",
                content=f"tool '{tool_name}' exceeded {recipe.timeout_seconds}s timeout",
            )

    async def destroy(self, sandbox_id: str) -> None:
        container = self._containers.pop(sandbox_id, None)
        self._recipes.pop(sandbox_id, None)
        if container is None:
            return
        await asyncio.to_thread(_safe_destroy, container)
        logger.info("sandbox.docker.destroyed", extra={"sandbox_id": sandbox_id})

    async def status(self, sandbox_id: str) -> SandboxStatus:
        container = self._containers.get(sandbox_id)
        if container is None:
            return SandboxStatus.DESTROYED
        return await asyncio.to_thread(_status_from_container, container)

    # ---- Internal ----------------------------------------------------------

    def _docker_client(self) -> DockerClient:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def _provision_sync(self, recipe: SandboxRecipe) -> Container:
        client = self._docker_client()
        image = recipe.runtime
        try:
            client.images.get(image)
        except Exception:
            logger.info("sandbox.docker.pulling", extra={"image": image})
            client.images.pull(image)

        nano_cpus = int(recipe.cpu_limit * 1_000_000_000)
        mem_limit = f"{recipe.memory_limit_mb}m"
        # `tail -f /dev/null` keeps the container alive between tool calls
        # without consuming resources.
        container = client.containers.run(
            image,
            command=["tail", "-f", "/dev/null"],
            detach=True,
            tty=False,
            nano_cpus=nano_cpus,
            mem_limit=mem_limit,
            pids_limit=512,
            working_dir="/workspace",
            environment=dict(recipe.env),
            labels={CONTAINER_LABEL: "1"},
            security_opt=["no-new-privileges:true"],
            network_mode="bridge",
        )

        # Create the workspace directory and upload any recipe files.
        from tename.sandbox.tools._exec import put_file, run_exec

        run_exec(container, ["mkdir", "-p", "/workspace"])
        for path, content in recipe.files.items():
            put_file(container, path, content)

        if recipe.packages and image.startswith("python:"):
            pip_cmd = ["pip", "install", "--no-cache-dir", "--quiet", *recipe.packages]
            pip_code, _, pip_err = run_exec(container, pip_cmd)
            if pip_code != 0:
                logger.warning(
                    "sandbox.docker.pip_install_failed",
                    extra={"exit": pip_code, "stderr": pip_err},
                )
        return container


def _safe_destroy(container: Container) -> None:
    """Best-effort stop + remove. Swallows docker.errors.NotFound."""
    with contextlib.suppress(Exception):
        container.stop(timeout=5)
    with contextlib.suppress(Exception):
        container.remove(force=True)


def _safe_kill(container: Container) -> None:
    with contextlib.suppress(Exception):
        container.kill()


def _status_from_container(container: Container) -> SandboxStatus:
    try:
        container.reload()
    except Exception:
        return SandboxStatus.DESTROYED
    state = getattr(container, "status", "unknown")
    if state == "running":
        return SandboxStatus.IDLE
    if state in {"created"}:
        return SandboxStatus.PROVISIONING
    if state in {"exited", "dead", "removing"}:
        return SandboxStatus.DESTROYED
    if state == "paused":
        return SandboxStatus.ERROR
    return SandboxStatus.ERROR


def _short_id(full_id: str | None) -> str:
    if not full_id:
        raise ValueError("docker returned a container without an id")
    return full_id[:12]


__all__ = ["CONTAINER_LABEL", "DockerBackend", "SandboxNotFoundError"]
