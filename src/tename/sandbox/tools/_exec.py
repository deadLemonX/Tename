"""Shared helpers for built-in sandbox tools.

Keeps the per-tool modules small and consistent: one place that knows
how to run a command with `exec_run`, one place that knows how to write
a file via tar, one place that decodes mixed-bytes output.
"""

from __future__ import annotations

import io
import tarfile
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docker.models.containers import Container


def decode_stream(value: Any) -> str:
    """Coerce docker exec output (bytes / str / None) to a `str`."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return str(value)


def run_exec(
    container: Container,
    cmd: list[str],
    *,
    workdir: str = "/workspace",
    environment: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run `cmd` inside `container` and return (exit_code, stdout, stderr).

    `demux=True` splits stdout / stderr streams. The docker SDK returns
    bytes for each; we decode as UTF-8 with lossy replace so a surprise
    binary byte never crashes the harness.
    """
    exit_code, streams = container.exec_run(
        cmd,
        demux=True,
        workdir=workdir,
        environment=environment or {},
    )
    stdout_raw: Any
    stderr_raw: Any
    if isinstance(streams, tuple):
        stdout_raw, stderr_raw = streams
    else:
        stdout_raw, stderr_raw = streams, None
    return int(exit_code or 0), decode_stream(stdout_raw), decode_stream(stderr_raw)


def put_file(container: Container, path: str, content: str) -> None:
    """Upload a single file into `container` at absolute `path`.

    Creates any missing parent directory by writing into the parent via
    `put_archive`. File mode defaults to 0644; timestamps use wall clock.
    """
    if not path.startswith("/"):
        raise ValueError(f"put_file requires an absolute path, got {path!r}")
    directory, filename = _split_abs(path)
    buf = io.BytesIO()
    data = content.encode("utf-8")
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=filename)
        info.size = len(data)
        info.mode = 0o644
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    # Ensure parent directory exists inside the container.
    mkdir_code, _, _ = run_exec(container, ["mkdir", "-p", directory])
    if mkdir_code != 0:
        raise RuntimeError(f"mkdir -p {directory} failed inside sandbox (exit {mkdir_code})")
    ok = container.put_archive(directory, buf.getvalue())
    if not ok:
        raise RuntimeError(f"put_archive failed for {path}")


def _split_abs(path: str) -> tuple[str, str]:
    """Split `/a/b/c.py` -> ("/a/b", "c.py"). Roots default to "/"."""
    if path.endswith("/"):
        raise ValueError(f"put_file path must name a file, not a directory: {path!r}")
    directory, _, filename = path.rpartition("/")
    if not directory:
        directory = "/"
    return directory, filename


__all__ = ["decode_stream", "put_file", "run_exec"]
