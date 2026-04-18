"""Framework adapters for the Harness Runtime.

Adapters translate between a specific agent framework's message and tool
formats and Tename's event log. The core harness loop (S7) never imports a
framework directly — it calls into an adapter selected by
`agent.framework`.

Importing this package registers the built-in adapters (currently only
`vanilla`). Third-party adapters can auto-register by importing them.
"""

from tename.harness.adapters.base import (
    FrameworkAdapter,
    PendingEvent,
    UnknownAdapterError,
    get_adapter,
    known_adapters,
    register_adapter,
)
from tename.harness.adapters.vanilla import VanillaAdapter

__all__ = [
    "FrameworkAdapter",
    "PendingEvent",
    "UnknownAdapterError",
    "VanillaAdapter",
    "get_adapter",
    "known_adapters",
    "register_adapter",
]
