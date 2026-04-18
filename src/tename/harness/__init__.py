"""Harness Runtime: stateless loop that drives agent turns.

Public API (S6):

    from tename.harness import (
        HarnessRuntime,
        Profile,
        ProfileLoader,
        FrameworkAdapter,
        get_adapter,
        register_adapter,
        VanillaAdapter,
    )

The core loop lands in S7; S6 ships the skeleton: profile loading with
`extends` inheritance and validation, the adapter ABC plus registry,
the vanilla adapter, and a `HarnessRuntime` whose `run_session` raises
`NotImplementedError`. See docs/architecture/harness-runtime.md.
"""

from tename.harness.adapters import (
    FrameworkAdapter,
    PendingEvent,
    UnknownAdapterError,
    VanillaAdapter,
    get_adapter,
    known_adapters,
    register_adapter,
)
from tename.harness.profiles import (
    BUNDLED_PROFILES_PACKAGE,
    CompactionStrategy,
    ContextConfig,
    Profile,
    ProfileError,
    ProfileInheritanceError,
    ProfileLoader,
    ProfileNotFoundError,
    ProfileValidationError,
    Quirk,
    StopConditions,
    ToolFormat,
)
from tename.harness.service import HarnessRuntime

__all__ = [
    "BUNDLED_PROFILES_PACKAGE",
    "CompactionStrategy",
    "ContextConfig",
    "FrameworkAdapter",
    "HarnessRuntime",
    "PendingEvent",
    "Profile",
    "ProfileError",
    "ProfileInheritanceError",
    "ProfileLoader",
    "ProfileNotFoundError",
    "ProfileValidationError",
    "Quirk",
    "StopConditions",
    "ToolFormat",
    "UnknownAdapterError",
    "VanillaAdapter",
    "get_adapter",
    "known_adapters",
    "register_adapter",
]
