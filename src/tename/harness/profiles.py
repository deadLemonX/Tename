"""Profile models and loader for the Harness Runtime.

A `Profile` is a YAML-described, model-specific configuration bundle: which
provider to hit, how much context to use, how to cache, which tools are
supported, when to stop, which quirks to work around, and pricing for usage
reports. The loader reads YAML from a search path, resolves `extends`
inheritance with key-by-key override semantics, and validates the result
against every rule in `docs/harness/profile-format.md`.

The router-facing subset (`ModelConfig`, `CachingConfig`, `Sampling`,
`ErrorHandling`, `Pricing`) is reused verbatim from `tename.router.types`;
S6-only fields (`ContextConfig`, `StopConditions`, `Quirk`, `tool_format`)
live here. `Profile.to_router_profile()` returns the narrow projection the
Model Router already consumes.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tename.router.types import (
    CachingConfig,
    ErrorHandling,
    ModelConfig,
    Pricing,
    RouterProfile,
    Sampling,
)

BUNDLED_PROFILES_PACKAGE = "tename.profiles"
MAX_EXTENDS_DEPTH = 16

ToolFormat = Literal["anthropic_tool_use", "openai_function_calling", "json_schema"]
CompactionStrategy = Literal["truncate", "summarize", "file_offload"]
SUPPORTED_COMPACTION_STRATEGIES: frozenset[str] = frozenset({"truncate"})


class ProfileError(ValueError):
    """Base class for profile-loading errors."""


class ProfileNotFoundError(ProfileError):
    """Raised when a named profile cannot be found on any search path."""


class ProfileInheritanceError(ProfileError):
    """Raised when `extends` produces a cycle or exceeds the depth cap."""


class ProfileValidationError(ProfileError):
    """Raised when a profile fails schema or cross-field validation."""


class ContextConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_tokens: int = Field(gt=0)
    effective_budget: int = Field(gt=0)
    compaction_threshold: int | None = Field(default=None, gt=0)
    compaction_strategy: CompactionStrategy = "truncate"
    keep_last_n_events: int = Field(default=10, gt=0)

    @property
    def resolved_compaction_threshold(self) -> int:
        """Compaction threshold with the 80%-of-effective-budget default applied."""
        if self.compaction_threshold is not None:
            return self.compaction_threshold
        return int(self.effective_budget * 0.8)


class StopConditions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    no_tool_calls_for: int = Field(default=1, ge=1)
    max_turns: int = Field(default=50, ge=1)
    max_duration_seconds: int | None = Field(default=3600, gt=0)


class Quirk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    added: date
    review_date: date
    description: str = Field(min_length=1)
    mitigation: str = Field(min_length=1)
    enabled: bool = True
    notes: str | None = None

    @model_validator(mode="after")
    def _review_after_added(self) -> Quirk:
        if self.review_date <= self.added:
            raise ValueError(
                f"quirk '{self.name}': review_date ({self.review_date.isoformat()}) "
                f"must be after added ({self.added.isoformat()})"
            )
        return self


class Profile(BaseModel):
    """Fully-validated Tename profile. Produced by `ProfileLoader.load`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: ModelConfig
    context: ContextConfig
    caching: CachingConfig = Field(default_factory=CachingConfig)
    tool_format: ToolFormat
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
    error_handling: ErrorHandling = Field(default_factory=ErrorHandling)
    sampling: Sampling = Field(default_factory=Sampling)
    quirks: list[Quirk] = Field(default_factory=list)
    pricing: Pricing | None = None

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Profile:
        ctx = self.context
        if ctx.effective_budget > ctx.max_tokens:
            raise ValueError(
                "context.effective_budget "
                f"({ctx.effective_budget}) must be <= context.max_tokens "
                f"({ctx.max_tokens})"
            )

        threshold = ctx.resolved_compaction_threshold
        if threshold >= ctx.effective_budget:
            raise ValueError(
                "context.compaction_threshold "
                f"({threshold}) must be < context.effective_budget "
                f"({ctx.effective_budget})"
            )

        if ctx.compaction_strategy not in SUPPORTED_COMPACTION_STRATEGIES:
            raise ValueError(
                f"context.compaction_strategy '{ctx.compaction_strategy}' is not "
                f"supported in v0.1; only "
                f"{sorted(SUPPORTED_COMPACTION_STRATEGIES)} are implemented"
            )
        return self

    def to_router_profile(self) -> RouterProfile:
        """Project down to the Model Router's narrower view of a profile."""
        return RouterProfile(
            model=self.model,
            caching=self.caching,
            sampling=self.sampling,
            error_handling=self.error_handling,
            pricing=self.pricing,
        )


class ProfileLoader:
    """Reads, resolves, and validates profile YAML files.

    Search order (first hit wins):
      1. User-supplied search paths, in order.
      2. The bundled `tename.profiles` package (ships inside the wheel).

    `extends` is resolved recursively; nested dicts merge key-by-key, lists
    replace entirely (documented choice — children that set a list are
    stating intent, not appending).
    """

    def __init__(self, search_paths: list[Path] | None = None) -> None:
        self._search_paths: list[Path] = list(search_paths or [])

    def load(self, name: str) -> Profile:
        raw = self._resolve(name, visited=[])
        try:
            return Profile.model_validate(raw)
        except ValueError as exc:
            raise ProfileValidationError(f"profile '{name}' failed validation: {exc}") from exc

    def _resolve(self, name: str, *, visited: list[str]) -> dict[str, Any]:
        if name in visited:
            chain = " -> ".join([*visited, name])
            raise ProfileInheritanceError(f"profile extends cycle: {chain}")
        if len(visited) >= MAX_EXTENDS_DEPTH:
            raise ProfileInheritanceError(
                f"profile '{name}': extends depth exceeds {MAX_EXTENDS_DEPTH}"
            )

        data = self._read(name)
        parent_name = data.pop("extends", None)
        if parent_name is None:
            return data
        if not isinstance(parent_name, str):
            raise ProfileValidationError(
                f"profile '{name}': 'extends' must be a string, got {type(parent_name).__name__}"
            )
        parent = self._resolve(parent_name, visited=[*visited, name])
        return _deep_merge(parent, data)

    def _read(self, name: str) -> dict[str, Any]:
        for path in self._search_paths:
            candidate = path / f"{name}.yaml"
            if candidate.is_file():
                return _parse_yaml(candidate.read_text(), source=str(candidate))

        bundled = _read_bundled(name)
        if bundled is not None:
            return bundled

        searched = ", ".join(str(p) for p in self._search_paths) or "(none)"
        raise ProfileNotFoundError(
            f"profile '{name}' not found. search paths: {searched}; "
            f"bundled package: {BUNDLED_PROFILES_PACKAGE}"
        )


def _parse_yaml(text: str, *, source: str) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileValidationError(f"{source}: invalid YAML: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ProfileValidationError(
            f"{source}: top-level YAML must be a mapping, got {type(parsed).__name__}"
        )
    return parsed


@lru_cache(maxsize=64)
def _bundled_text(name: str) -> str | None:
    try:
        resource = resources.files(BUNDLED_PROFILES_PACKAGE).joinpath(f"{name}.yaml")
    except ModuleNotFoundError:
        return None
    if not resource.is_file():
        return None
    return resource.read_text()


def _read_bundled(name: str) -> dict[str, Any] | None:
    text = _bundled_text(name)
    if text is None:
        return None
    return _parse_yaml(text, source=f"<bundled:{name}>")


def _deep_merge(parent: Mapping[str, Any], child: Mapping[str, Any]) -> dict[str, Any]:
    """Key-by-key merge; nested dicts merge recursively; lists replace."""
    merged: dict[str, Any] = dict(parent)
    for key, child_value in child.items():
        parent_value = merged.get(key)
        if isinstance(parent_value, Mapping) and isinstance(child_value, Mapping):
            merged[key] = _deep_merge(parent_value, child_value)
        else:
            merged[key] = child_value
    return merged


__all__ = [
    "BUNDLED_PROFILES_PACKAGE",
    "CompactionStrategy",
    "ContextConfig",
    "Profile",
    "ProfileError",
    "ProfileInheritanceError",
    "ProfileLoader",
    "ProfileNotFoundError",
    "ProfileValidationError",
    "Quirk",
    "StopConditions",
    "ToolFormat",
]
