"""Docker backend integration tests.

Gated by the `sandbox` marker and the `skip_if_no_docker` fixture. These
spin up real containers against the local Docker daemon. Each test
cleans up after itself so a failure doesn't leak containers into the
dev environment.

Runtime budget: each test should finish in a few seconds against a
warm python:3.12-slim cache. First run on a machine without the image
pays a one-time pull cost.
"""

from __future__ import annotations

import pytest

from tename.sandbox import DockerBackend, Sandbox, SandboxRecipe, SandboxStatus

from .conftest import SANDBOX_TEST_IMAGE

pytestmark = pytest.mark.sandbox


async def test_provision_and_destroy_roundtrip(
    sandbox: Sandbox,
) -> None:
    recipe = SandboxRecipe(runtime=SANDBOX_TEST_IMAGE, timeout_seconds=30)
    sandbox_id = await sandbox.provision(recipe)
    assert sandbox_id  # non-empty short id
    status = await sandbox.status(sandbox_id)
    assert status in {SandboxStatus.READY, SandboxStatus.IDLE}

    await sandbox.destroy(sandbox_id)
    final = await sandbox.status(sandbox_id)
    assert final == SandboxStatus.DESTROYED


async def test_destroy_is_idempotent(sandbox: Sandbox) -> None:
    recipe = SandboxRecipe(runtime=SANDBOX_TEST_IMAGE, timeout_seconds=30)
    sandbox_id = await sandbox.provision(recipe)
    await sandbox.destroy(sandbox_id)
    # Second destroy must not raise.
    await sandbox.destroy(sandbox_id)


async def test_bash_echo(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    sandbox, sandbox_id = provisioned_sandbox
    result = await sandbox.execute(sandbox_id, "bash", {"command": "echo hello"})
    assert result.is_error is False
    assert "hello" in result.stdout
    assert result.exit_code == 0


async def test_python_stdout(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    sandbox, sandbox_id = provisioned_sandbox
    result = await sandbox.execute(
        sandbox_id,
        "python",
        {"code": "print('pi', 3.14)\n"},
    )
    assert result.is_error is False
    assert "pi 3.14" in result.stdout
    assert result.exit_code == 0


async def test_python_exception_is_tool_error(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    sandbox, sandbox_id = provisioned_sandbox
    result = await sandbox.execute(
        sandbox_id,
        "python",
        {"code": "raise RuntimeError('boom')\n"},
    )
    assert result.is_error is True
    assert "RuntimeError" in result.stderr or "RuntimeError" in result.content


async def test_file_write_then_read(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    sandbox, sandbox_id = provisioned_sandbox
    write = await sandbox.execute(
        sandbox_id,
        "file_write",
        {"path": "/workspace/hello.txt", "content": "greetings\n"},
    )
    assert write.is_error is False

    read = await sandbox.execute(
        sandbox_id,
        "file_read",
        {"path": "/workspace/hello.txt"},
    )
    assert read.is_error is False
    assert read.content == "greetings\n"


async def test_file_edit_replace_single(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    sandbox, sandbox_id = provisioned_sandbox
    await sandbox.execute(
        sandbox_id,
        "file_write",
        {"path": "/workspace/a.txt", "content": "foo bar baz"},
    )
    edit = await sandbox.execute(
        sandbox_id,
        "file_edit",
        {"path": "/workspace/a.txt", "old_str": "bar", "new_str": "qux"},
    )
    assert edit.is_error is False
    read = await sandbox.execute(sandbox_id, "file_read", {"path": "/workspace/a.txt"})
    assert read.content == "foo qux baz"


async def test_file_edit_ambiguous_requires_replace_all(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    sandbox, sandbox_id = provisioned_sandbox
    await sandbox.execute(
        sandbox_id,
        "file_write",
        {"path": "/workspace/b.txt", "content": "one one one"},
    )
    bad = await sandbox.execute(
        sandbox_id,
        "file_edit",
        {"path": "/workspace/b.txt", "old_str": "one", "new_str": "two"},
    )
    assert bad.is_error is True
    assert "replace_all" in bad.error.lower() if bad.error else False

    good = await sandbox.execute(
        sandbox_id,
        "file_edit",
        {
            "path": "/workspace/b.txt",
            "old_str": "one",
            "new_str": "two",
            "replace_all": True,
        },
    )
    assert good.is_error is False
    read = await sandbox.execute(sandbox_id, "file_read", {"path": "/workspace/b.txt"})
    assert read.content == "two two two"


async def test_file_list(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    sandbox, sandbox_id = provisioned_sandbox
    await sandbox.execute(
        sandbox_id,
        "file_write",
        {"path": "/workspace/listed.txt", "content": "x"},
    )
    result = await sandbox.execute(sandbox_id, "file_list", {"path": "/workspace"})
    assert result.is_error is False
    assert "listed.txt" in result.stdout


async def test_timeout_kills_container(sandbox: Sandbox) -> None:
    recipe = SandboxRecipe(runtime=SANDBOX_TEST_IMAGE, timeout_seconds=2)
    sandbox_id = await sandbox.provision(recipe)
    try:
        result = await sandbox.execute(
            sandbox_id,
            "bash",
            {"command": "sleep 10"},
        )
        assert result.is_error is True
        assert result.error is not None
        assert "timeout" in result.error.lower()
        # Sandbox should now be unusable.
        status = await sandbox.status(sandbox_id)
        assert status in {SandboxStatus.ERROR, SandboxStatus.DESTROYED}
    finally:
        await sandbox.destroy(sandbox_id)


async def test_unknown_tool_returns_error(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    sandbox, sandbox_id = provisioned_sandbox
    result = await sandbox.execute(sandbox_id, "not_a_tool", {})
    assert result.is_error is True
    assert result.error is not None
    assert "unknown tool" in result.error


async def test_recipe_files_uploaded_at_provision(sandbox: Sandbox) -> None:
    recipe = SandboxRecipe(
        runtime=SANDBOX_TEST_IMAGE,
        timeout_seconds=30,
        files={"/workspace/seeded.txt": "seeded-content"},
    )
    sandbox_id = await sandbox.provision(recipe)
    try:
        result = await sandbox.execute(
            sandbox_id,
            "file_read",
            {"path": "/workspace/seeded.txt"},
        )
        assert result.is_error is False
        assert result.content == "seeded-content"
    finally:
        await sandbox.destroy(sandbox_id)


async def test_memory_limit_is_applied(sandbox: Sandbox) -> None:
    """Smoke test: 64MB cap kills an obviously-too-big allocation.

    Python OOM inside a cgroup-limited container exits nonzero (SIGKILL
    from the OOM killer). The exact error text varies; we assert on
    is_error + a nonzero / negative exit code.
    """
    recipe = SandboxRecipe(
        runtime=SANDBOX_TEST_IMAGE,
        timeout_seconds=30,
        memory_limit_mb=64,
    )
    sandbox_id = await sandbox.provision(recipe)
    try:
        # Allocate ~256MB of bytes; will be OOM-killed.
        result = await sandbox.execute(
            sandbox_id,
            "python",
            {"code": "x = bytearray(256 * 1024 * 1024); print('should not print', len(x))"},
        )
        assert result.is_error is True
    finally:
        await sandbox.destroy(sandbox_id)


async def test_lazy_client_builds_on_first_use() -> None:
    """Backend without a pre-built client lazily calls docker.from_env()."""
    backend = DockerBackend()
    # Verify it works; nothing to assert on internals.
    recipe = SandboxRecipe(runtime=SANDBOX_TEST_IMAGE, timeout_seconds=30)
    sandbox_id = await backend.provision(recipe)
    try:
        result = await backend.execute(sandbox_id, "bash", {"command": "echo ok"})
        assert result.is_error is False
    finally:
        await backend.destroy(sandbox_id)
