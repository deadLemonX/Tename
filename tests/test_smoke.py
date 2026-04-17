"""Smoke tests: the package imports and the version string is sane."""

from __future__ import annotations

import tename


def test_import() -> None:
    assert tename is not None


def test_version_is_string() -> None:
    assert isinstance(tename.__version__, str)
    assert len(tename.__version__) > 0
