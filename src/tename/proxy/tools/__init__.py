"""Built-in proxy tools ship here.

Each tool self-registers at import time via `@proxy_tool`. The
`__init__.py` imports them so a simple `import tename.proxy` pulls the
full built-in set into the registry.
"""

from tename.proxy.tools import web_search as _web_search  # pyright: ignore[reportUnusedImport]

__all__: list[str] = []
