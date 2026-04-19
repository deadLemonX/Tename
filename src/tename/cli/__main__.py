"""Allow `python -m tename.cli` in addition to the installed `tename` script."""

from __future__ import annotations

import sys

from tename.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
