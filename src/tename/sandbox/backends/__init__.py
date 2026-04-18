"""Sandbox backend implementations.

v0.1 ships one: `DockerBackend`. Future backends (Firecracker, E2B,
Modal, custom VPC) plug in here.
"""

from tename.sandbox.backends.docker import DockerBackend

__all__ = ["DockerBackend"]
