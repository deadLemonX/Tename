"""Sandbox isolation integration tests (v0.1 exit criterion #4).

Proves that code running inside a Tename sandbox cannot read
host-specific files unless they were explicitly mounted. The sandbox's
filesystem comes from the Docker image (`python:3.12-slim`), not the
host.

These tests complement the resource-limit tests in
`test_docker_backend.py`; together they validate commitment #1
(brain/hands/session decoupling — "hands" run in an isolated box).
"""

from __future__ import annotations

import socket
import uuid
from pathlib import Path

import pytest

from tename.sandbox import Sandbox

pytestmark = pytest.mark.sandbox


async def test_host_file_not_visible_in_sandbox(
    provisioned_sandbox: tuple[Sandbox, str],
    tmp_path: Path,
) -> None:
    """Write a file to the host, try to read it from inside the sandbox.

    The sandbox's `/tmp` is distinct from the host's `tmp_path`, so the
    exact host path should not resolve to the host file's contents.
    """
    sandbox, sandbox_id = provisioned_sandbox

    secret = f"host-secret-{uuid.uuid4()}"
    host_file = tmp_path / f"tename-isolation-{uuid.uuid4().hex}.txt"
    host_file.write_text(secret)

    result = await sandbox.execute(
        sandbox_id,
        "bash",
        {"command": f"cat {host_file} 2>&1 || echo MISSING"},
    )

    # Two acceptable outcomes prove isolation:
    #   - the bash `cat` prints "No such file or directory" then MISSING
    #   - the sandbox tmp happens to allow the path but returns different content
    # What must NOT happen: the host secret appears in stdout.
    assert secret not in result.stdout, f"host secret leaked into sandbox stdout: {result.stdout!r}"
    assert secret not in result.content, (
        f"host secret leaked into tool result content: {result.content!r}"
    )


async def test_sandbox_hostname_is_not_host_hostname(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    """`hostname` inside the container should NOT match the host's.

    Docker assigns a random short-id hostname to each container unless
    overridden. A matching hostname would signal the container is
    running in the host's namespace (not our configuration).
    """
    sandbox, sandbox_id = provisioned_sandbox
    host_hostname = socket.gethostname()

    result = await sandbox.execute(
        sandbox_id,
        "bash",
        {"command": "hostname"},
    )

    assert not result.is_error
    container_hostname = result.stdout.strip()
    assert container_hostname, "sandbox produced no hostname output"
    assert container_hostname != host_hostname, (
        "sandbox reports the host's hostname — container is not isolated"
    )


async def test_sandbox_cannot_enumerate_host_home(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    """`/Users/<real-user>` or `/home/<real-user>` should not resolve.

    The sandbox image has its own skeletal `/home`; it must not see the
    host's user directory at the same absolute path. We check that a
    directory the host has (the CWD of these tests) is NOT reachable in
    the container at the same absolute path.
    """
    sandbox, sandbox_id = provisioned_sandbox
    host_cwd = str(Path.cwd().resolve())

    result = await sandbox.execute(
        sandbox_id,
        "bash",
        {"command": f"ls -la {host_cwd} 2>&1 | head -20 || echo MISSING"},
    )

    # The host CWD contains tename-specific files (pyproject.toml,
    # Makefile, docker-compose.yml, ...). If the sandbox could see the
    # host filesystem at that path, those names would show up.
    forbidden_markers = ["pyproject.toml", "docker-compose.yml", "Makefile"]
    combined = (result.stdout + result.content).lower()
    for marker in forbidden_markers:
        assert marker.lower() not in combined, (
            f"sandbox saw host-only file {marker!r} at {host_cwd} (stdout+content: {combined!r})"
        )
