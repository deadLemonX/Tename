"""Platform CLI: `tename` command entry point.

The pyproject `[project.scripts]` table wires `tename` to
`tename.cli.main:main`. This package only re-exports the entry point so
`python -m tename.cli` also works for ad-hoc use during development.
"""

from tename.cli.main import main

__all__ = ["main"]
