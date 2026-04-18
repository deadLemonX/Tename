"""Shared fixtures for Sandbox tests.

Every integration test in this tree is marked `sandbox`. The
`docker_available` fixture pings the daemon once per session; if Docker
isn't running the test skips cleanly instead of failing noisily.

Real provision/execute/destroy rides on `python:3.12-slim`. Most dev
machines either have it cached or pull it in under 30s.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from tename.sandbox import DockerBackend, Sandbox, SandboxRecipe

SANDBOX_TEST_IMAGE = os.getenv("TENAME_SANDBOX_TEST_IMAGE", "python:3.12-slim")


def _docker_ping() -> bool:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def docker_available() -> bool:
    return _docker_ping()


@pytest.fixture
def skip_if_no_docker(docker_available: bool) -> None:
    if not docker_available:
        pytest.skip("Docker daemon not reachable; skipping sandbox integration test")


@pytest.fixture
def docker_backend(skip_if_no_docker: None) -> Iterator[DockerBackend]:
    backend = DockerBackend()
    yield backend


@pytest_asyncio.fixture
async def sandbox(docker_backend: DockerBackend) -> AsyncIterator[Sandbox]:
    svc = Sandbox(docker_backend)
    yield svc


@pytest_asyncio.fixture
async def provisioned_sandbox(sandbox: Sandbox) -> AsyncIterator[tuple[Sandbox, str]]:
    """A freshly-provisioned sandbox plus its id; torn down after the test."""
    recipe = SandboxRecipe(runtime=SANDBOX_TEST_IMAGE, timeout_seconds=30)
    sandbox_id = await sandbox.provision(recipe)
    try:
        yield sandbox, sandbox_id
    finally:
        await sandbox.destroy(sandbox_id)


__all__ = [
    "SANDBOX_TEST_IMAGE",
    "docker_available",
    "docker_backend",
    "provisioned_sandbox",
    "sandbox",
    "skip_if_no_docker",
]
