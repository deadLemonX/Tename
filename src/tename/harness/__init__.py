"""Harness Runtime: stateless loop that drives agent turns.

Public API:

    from tename.harness import (
        HarnessRuntime,
        Profile,
        ProfileLoader,
        FrameworkAdapter,
        get_adapter,
        register_adapter,
        VanillaAdapter,
    )

S6 shipped the skeleton (profile loading, adapter registry, vanilla
adapter). S7 fills in `HarnessRuntime.run_session`: the stateless core
loop that wakes a session, streams a model completion per turn, emits
incremental + consolidated events, stubs tool execution, truncates via
compaction when context grows too large, and marks the session complete
when stop conditions fire. See `docs/architecture/harness-runtime.md`.
"""

from tename.harness.adapters import (
    BUILTIN_TOOLS,
    DeepAgentsAdapter,
    FrameworkAdapter,
    PendingEvent,
    UnknownAdapterError,
    VanillaAdapter,
    get_adapter,
    known_adapters,
    register_adapter,
)
from tename.harness.compaction import (
    CompactionDecision,
    apply_compaction_view,
    estimate_event_tokens,
    plan_truncate,
    should_compact,
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
from tename.harness.service import (
    SANDBOX_PROVISIONED_UUID_NAMESPACE,
    SYSTEM_EVENT_SANDBOX_PROVISIONED,
    SYSTEM_PROMPT_UUID_NAMESPACE,
    HarnessRuntime,
)

__all__ = [
    "BUILTIN_TOOLS",
    "BUNDLED_PROFILES_PACKAGE",
    "SANDBOX_PROVISIONED_UUID_NAMESPACE",
    "SYSTEM_EVENT_SANDBOX_PROVISIONED",
    "SYSTEM_PROMPT_UUID_NAMESPACE",
    "CompactionDecision",
    "CompactionStrategy",
    "ContextConfig",
    "DeepAgentsAdapter",
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
    "apply_compaction_view",
    "estimate_event_tokens",
    "get_adapter",
    "known_adapters",
    "plan_truncate",
    "register_adapter",
    "should_compact",
]
