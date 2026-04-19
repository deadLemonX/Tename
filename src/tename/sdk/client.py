"""Top-level Tename SDK client.

Two shapes, same surface:

- `Tename` — synchronous. Runs the Session Service / Model Router /
  Harness on a dedicated background event loop and exposes blocking
  methods plus a sync iterator for `session.send(...)`. This is the
  shape the README code-snippet uses, because it's what most developers
  reach for first.

- `AsyncTename` — asynchronous. Same methods, `async def`. For
  long-lived services where the user already owns an event loop.

Both share the same internal services, so switching between them does
not re-initialize connections or reload profiles.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tename.harness import HarnessRuntime, ProfileLoader
from tename.proxy import ToolProxy
from tename.router.service import ModelRouter
from tename.sandbox import DockerBackend, Sandbox, SandboxRecipe
from tename.sdk._config import ResolvedConfig, resolve_config
from tename.sdk.agents import (
    CreateAgentInput,
    create_agent,
    delete_agent,
    fetch_agent,
    list_agents,
)
from tename.sdk.errors import ConfigurationError
from tename.sdk.runtime import BackgroundLoop
from tename.sdk.sessions import AsyncSessionHandle, SessionHandle
from tename.sessions import SessionService
from tename.sessions.models import Agent, Event, EventType, Session
from tename.vault import Vault

if TYPE_CHECKING:
    pass


class AsyncTename:
    """Async Tename client.

    The SDK owns construction of every internal service: Session
    Service, Model Router, Sandbox (optional), Tool Proxy, Harness
    Runtime. Callers configure the client once and use the three
    sub-clients (`agents`, `sessions`, `vault`).

    Args:
        database_url: SQLAlchemy URL, or env `TENAME_DATABASE_URL`.
        anthropic_api_key: Forwarded to `ANTHROPIC_API_KEY` env so the
            Anthropic provider picks it up. Optional — defaults to env.
        profiles_dir: Additional directory containing YAML profiles.
            Searched before the bundled `tename.profiles` package.
        vault_path: Override `~/.tename/vault.json.enc`.
        vault_passphrase: Passphrase for vault key derivation. Defaults
            to env `TENAME_VAULT_PASSPHRASE`.
        enable_sandbox: Wire a `DockerBackend` sandbox. Defaults to
            True; set False for unit tests or environments without
            Docker.
    """

    def __init__(
        self,
        database_url: str | None = None,
        *,
        anthropic_api_key: str | None = None,
        profiles_dir: str | None = None,
        vault_path: str | None = None,
        vault_passphrase: str | None = None,
        enable_sandbox: bool = True,
    ) -> None:
        self._config: ResolvedConfig = resolve_config(
            database_url=database_url,
            anthropic_api_key=anthropic_api_key,
            profiles_dir=profiles_dir,
            vault_path=vault_path,
            vault_passphrase=vault_passphrase,
        )

        # Surface the Anthropic key through env; the provider reads it
        # from env by default. We don't inject explicitly to keep the
        # provider wiring one-path (and preserve the "env wins" rule the
        # rest of the codebase respects).
        if self._config.anthropic_api_key is not None:
            os.environ.setdefault("ANTHROPIC_API_KEY", self._config.anthropic_api_key)

        self._engine: AsyncEngine = create_async_engine(self._config.database_url, future=True)
        self._session_service: SessionService = SessionService(self._config.database_url)
        self._model_router: ModelRouter = ModelRouter()

        search_paths: list[Path] | None = (
            [Path(self._config.profiles_dir)] if self._config.profiles_dir else None
        )
        self._profile_loader: ProfileLoader = ProfileLoader(search_paths=search_paths)

        self._sandbox: Sandbox | None = Sandbox(DockerBackend()) if enable_sandbox else None
        self._vault: Vault = Vault(
            path=self._config.vault_path,
            passphrase=self._config.vault_passphrase,
        )
        self._tool_proxy: ToolProxy = ToolProxy(vault=self._vault)

        self._harness: HarnessRuntime = HarnessRuntime(
            session_service=self._session_service,
            model_router=self._model_router,
            sandbox=self._sandbox,
            tool_proxy=self._tool_proxy,
            profile_loader=self._profile_loader,
        )

        self.agents: AsyncAgentsClient = AsyncAgentsClient(self._engine)
        self.sessions: AsyncSessionsClient = AsyncSessionsClient(
            service=self._session_service, harness=self._harness
        )
        self.vault: AsyncVaultClient = AsyncVaultClient(self._vault)

    async def close(self) -> None:
        """Release the database connection pool.

        The Session Service and the SDK each hold their own engine.
        Safe to call more than once.
        """
        await self._session_service.close()
        await self._engine.dispose()

    async def __aenter__(self) -> AsyncTename:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    def install_test_model_router(self, router: object) -> None:
        """Swap in a fake `ModelRouter`-shaped object for tests.

        The SDK builds its own harness + model router internally; tests
        that want to drive the client with scripted chunks need to
        replace both pieces. This is the blessed entry point — no other
        production code should ever call it.

        The replacement router must satisfy the structural contract
        `async complete(profile, messages, tools) -> AsyncIterator[ModelChunk]`.
        """
        self._model_router = router  # type: ignore[assignment]
        self._harness = HarnessRuntime(
            session_service=self._session_service,
            model_router=self._model_router,
            sandbox=self._sandbox,
            tool_proxy=self._tool_proxy,
            profile_loader=self._profile_loader,
        )
        self.sessions = AsyncSessionsClient(service=self._session_service, harness=self._harness)


class AsyncAgentsClient:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self,
        *,
        name: str,
        model: str,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        framework: str = "vanilla",
        sandbox_recipe: SandboxRecipe | None = None,
    ) -> Agent:
        spec = CreateAgentInput(
            name=name,
            model=model,
            system_prompt=system_prompt,
            tools=tuple(tools or ()),
            framework=framework,
            sandbox_recipe=sandbox_recipe,
        )
        return await create_agent(self._engine, spec)

    async def get(self, agent_id: UUID) -> Agent:
        return await fetch_agent(self._engine, agent_id)

    async def list(self) -> list[Agent]:
        return await list_agents(self._engine)

    async def delete(self, agent_id: UUID) -> None:
        await delete_agent(self._engine, agent_id)


class AsyncSessionsClient:
    def __init__(self, *, service: SessionService, harness: HarnessRuntime) -> None:
        self._service = service
        self._harness = harness

    async def create(
        self,
        *,
        agent_id: UUID,
        metadata: dict[str, object] | None = None,
    ) -> AsyncSessionHandle:
        session = await self._service.create_session(agent_id, metadata=metadata)
        return AsyncSessionHandle(session=session, service=self._service, harness=self._harness)

    async def get(self, session_id: UUID) -> AsyncSessionHandle:
        session = await self._service.wake(session_id)
        return AsyncSessionHandle(session=session, service=self._service, harness=self._harness)


class AsyncVaultClient:
    def __init__(self, vault: Vault) -> None:
        self._vault = vault

    def set(self, name: str, value: str) -> None:
        self._vault.store(name, value)

    def get(self, name: str) -> str:
        return self._vault.retrieve(name)

    def remove(self, name: str) -> bool:
        return self._vault.revoke(name)

    def list(self) -> list[str]:
        return self._vault.list()


class Tename:
    """Synchronous Tename client.

    Runs a dedicated asyncio event loop on a background thread and
    bridges every call to the `AsyncTename` surface via
    `asyncio.run_coroutine_threadsafe`. That makes `session.send(...)`
    a regular sync iterator — what the README snippet shows.

    Always `close()` (or use the context manager) to dispose the engine
    and stop the background loop.
    """

    def __init__(
        self,
        database_url: str | None = None,
        *,
        anthropic_api_key: str | None = None,
        profiles_dir: str | None = None,
        vault_path: str | None = None,
        vault_passphrase: str | None = None,
        enable_sandbox: bool = True,
    ) -> None:
        self._loop = BackgroundLoop()
        self._loop.start()
        try:
            self._async: AsyncTename = self._loop.run(
                _build_async(
                    database_url=database_url,
                    anthropic_api_key=anthropic_api_key,
                    profiles_dir=profiles_dir,
                    vault_path=vault_path,
                    vault_passphrase=vault_passphrase,
                    enable_sandbox=enable_sandbox,
                )
            )
        except ConfigurationError:
            self._loop.stop()
            raise

        self.agents: SyncAgentsClient = SyncAgentsClient(self._async.agents, self._loop)
        self.sessions: SyncSessionsClient = SyncSessionsClient(self._async.sessions, self._loop)
        self.vault: AsyncVaultClient = self._async.vault

    def install_test_model_router(self, router: object) -> None:
        """Swap in a scripted model router for tests. Mirrors `AsyncTename`."""
        self._loop.run(_install_test_router_async(self._async, router))
        self.sessions = SyncSessionsClient(self._async.sessions, self._loop)

    def close(self) -> None:
        """Dispose the DB engine and stop the background loop."""
        try:
            self._loop.run(self._async.close())
        finally:
            self._loop.stop()

    def __enter__(self) -> Tename:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


async def _install_test_router_async(client: AsyncTename, router: object) -> None:
    client.install_test_model_router(router)


async def _build_async(
    *,
    database_url: str | None,
    anthropic_api_key: str | None,
    profiles_dir: str | None,
    vault_path: str | None,
    vault_passphrase: str | None,
    enable_sandbox: bool,
) -> AsyncTename:
    """Build an AsyncTename on the background event loop.

    Construction itself is synchronous but the caller runs it through
    `BackgroundLoop.run`; that guarantees the SQLAlchemy engine's
    initialization happens on the same loop it will later be used from.
    """
    return AsyncTename(
        database_url=database_url,
        anthropic_api_key=anthropic_api_key,
        profiles_dir=profiles_dir,
        vault_path=vault_path,
        vault_passphrase=vault_passphrase,
        enable_sandbox=enable_sandbox,
    )


class SyncAgentsClient:
    def __init__(self, inner: AsyncAgentsClient, loop: BackgroundLoop) -> None:
        self._inner = inner
        self._loop = loop

    def create(
        self,
        *,
        name: str,
        model: str,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        framework: str = "vanilla",
        sandbox_recipe: SandboxRecipe | None = None,
    ) -> Agent:
        return self._loop.run(
            self._inner.create(
                name=name,
                model=model,
                system_prompt=system_prompt,
                tools=tools,
                framework=framework,
                sandbox_recipe=sandbox_recipe,
            )
        )

    def get(self, agent_id: UUID) -> Agent:
        return self._loop.run(self._inner.get(agent_id))

    def list(self) -> list[Agent]:
        return self._loop.run(self._inner.list())

    def delete(self, agent_id: UUID) -> None:
        self._loop.run(self._inner.delete(agent_id))


class SyncSessionsClient:
    def __init__(self, inner: AsyncSessionsClient, loop: BackgroundLoop) -> None:
        self._inner = inner
        self._loop = loop

    def create(
        self,
        *,
        agent_id: UUID,
        metadata: dict[str, object] | None = None,
    ) -> SessionHandle:
        async_handle = self._loop.run(self._inner.create(agent_id=agent_id, metadata=metadata))
        return SessionHandle(async_handle=async_handle, loop=self._loop)

    def get(self, session_id: UUID) -> SessionHandle:
        async_handle = self._loop.run(self._inner.get(session_id))
        return SessionHandle(async_handle=async_handle, loop=self._loop)


__all__ = [
    "Agent",
    "AsyncAgentsClient",
    "AsyncSessionsClient",
    "AsyncTename",
    "AsyncVaultClient",
    "Event",
    "EventType",
    "Session",
    "SyncAgentsClient",
    "SyncSessionsClient",
    "Tename",
]
