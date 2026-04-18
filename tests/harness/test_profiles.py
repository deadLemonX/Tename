"""Profile loader and validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tename.harness.profiles import (
    Profile,
    ProfileInheritanceError,
    ProfileLoader,
    ProfileNotFoundError,
    ProfileValidationError,
)
from tename.router.types import RouterProfile

MINIMAL_VALID = """\
model:
  provider: anthropic
  model_id: test-model
context:
  max_tokens: 200000
  effective_budget: 160000
tool_format: anthropic_tool_use
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(body)
    return path


# ---- Bundled profile -------------------------------------------------------


def test_load_opus_profile_from_bundled_package() -> None:
    loader = ProfileLoader()
    profile = loader.load("claude-opus-4-6")

    assert isinstance(profile, Profile)
    assert profile.model.provider == "anthropic"
    assert profile.model.model_id == "claude-opus-4-6"
    assert profile.context.max_tokens == 200000
    assert profile.context.effective_budget == 160000
    assert profile.context.compaction_threshold == 128000
    assert profile.context.keep_last_n_events == 20
    assert profile.caching.provider_strategy == "explicit_breakpoints"
    assert len(profile.caching.breakpoints) == 2
    assert profile.tool_format == "anthropic_tool_use"
    assert profile.stop_conditions.max_turns == 50
    assert profile.sampling.temperature == 0.7
    assert profile.sampling.max_tokens == 8192
    assert profile.quirks == []
    assert profile.pricing is not None
    assert profile.pricing.input_per_million == 15.0


def test_to_router_profile_projection() -> None:
    loader = ProfileLoader()
    profile = loader.load("claude-opus-4-6")

    router_profile = profile.to_router_profile()
    assert isinstance(router_profile, RouterProfile)
    assert router_profile.model == profile.model
    assert router_profile.caching == profile.caching
    assert router_profile.sampling == profile.sampling
    assert router_profile.error_handling == profile.error_handling
    assert router_profile.pricing == profile.pricing


# ---- Extends ---------------------------------------------------------------


def test_extends_merges_scalar_overrides(tmp_path: Path) -> None:
    _write(tmp_path, "parent", MINIMAL_VALID)
    _write(
        tmp_path,
        "child",
        """\
extends: parent
sampling:
  temperature: 0.3
  max_tokens: 16384
""",
    )

    loader = ProfileLoader(search_paths=[tmp_path])
    child = loader.load("child")

    assert child.sampling.temperature == 0.3
    assert child.sampling.max_tokens == 16384
    # unchanged from parent defaults
    assert child.sampling.top_p == 1.0
    assert child.model.model_id == "test-model"


def test_extends_replaces_list_fields(tmp_path: Path) -> None:
    """Per the design decision documented in ProfileLoader: child lists
    replace parent lists; they do not append."""
    _write(
        tmp_path,
        "parent",
        MINIMAL_VALID
        + """\
caching:
  provider_strategy: explicit_breakpoints
  breakpoints:
    - after: system_prompt
    - after: compaction_summary
""",
    )
    _write(
        tmp_path,
        "child",
        """\
extends: parent
caching:
  breakpoints:
    - after: system_prompt
""",
    )

    loader = ProfileLoader(search_paths=[tmp_path])
    child = loader.load("child")
    assert [bp.after for bp in child.caching.breakpoints] == ["system_prompt"]
    # scalar sibling inherited from parent
    assert child.caching.provider_strategy == "explicit_breakpoints"


def test_extends_cycle_detected(tmp_path: Path) -> None:
    _write(tmp_path, "a", "extends: b\n" + MINIMAL_VALID)
    _write(tmp_path, "b", "extends: a\n" + MINIMAL_VALID)

    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileInheritanceError, match="cycle"):
        loader.load("a")


def test_missing_profile_raises_profile_not_found(tmp_path: Path) -> None:
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileNotFoundError):
        loader.load("does-not-exist")


def test_search_paths_precede_bundled(tmp_path: Path) -> None:
    """A search-path override wins over the bundled profile of the same name."""
    overridden = MINIMAL_VALID.replace("test-model", "overridden-id")
    _write(tmp_path, "claude-opus-4-6", overridden)

    loader = ProfileLoader(search_paths=[tmp_path])
    assert loader.load("claude-opus-4-6").model.model_id == "overridden-id"


# ---- Validation: negative cases --------------------------------------------


def test_effective_budget_exceeds_max_tokens_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad",
        """\
model:
  provider: anthropic
  model_id: x
context:
  max_tokens: 1000
  effective_budget: 2000
tool_format: anthropic_tool_use
""",
    )
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileValidationError, match="effective_budget"):
        loader.load("bad")


def test_compaction_threshold_not_less_than_effective_budget_rejected(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "bad",
        """\
model:
  provider: anthropic
  model_id: x
context:
  max_tokens: 200000
  effective_budget: 160000
  compaction_threshold: 160000
tool_format: anthropic_tool_use
""",
    )
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileValidationError, match="compaction_threshold"):
        loader.load("bad")


def test_default_compaction_threshold_is_80_percent_of_effective_budget(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "p", MINIMAL_VALID)
    loader = ProfileLoader(search_paths=[tmp_path])
    profile = loader.load("p")
    # effective_budget 160000 → default threshold 128000
    assert profile.context.resolved_compaction_threshold == 128000


def test_quirk_missing_review_date_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad",
        MINIMAL_VALID
        + """\
quirks:
  - name: some_quirk
    added: 2026-01-15
    description: "something"
    mitigation: some_func
""",
    )
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileValidationError, match="review_date"):
        loader.load("bad")


def test_quirk_review_date_before_added_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad",
        MINIMAL_VALID
        + """\
quirks:
  - name: some_quirk
    added: 2026-07-01
    review_date: 2026-01-01
    description: "something"
    mitigation: some_func
""",
    )
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileValidationError, match="review_date"):
        loader.load("bad")


def test_unknown_provider_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad",
        """\
model:
  provider: not-a-provider
  model_id: x
context:
  max_tokens: 100
  effective_budget: 80
tool_format: anthropic_tool_use
""",
    )
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileValidationError):
        loader.load("bad")


def test_unknown_tool_format_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad",
        """\
model:
  provider: anthropic
  model_id: x
context:
  max_tokens: 100
  effective_budget: 80
tool_format: not-a-format
""",
    )
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileValidationError):
        loader.load("bad")


def test_unsupported_compaction_strategy_rejected_in_v0_1(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad",
        """\
model:
  provider: anthropic
  model_id: x
context:
  max_tokens: 200000
  effective_budget: 160000
  compaction_strategy: summarize
tool_format: anthropic_tool_use
""",
    )
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileValidationError, match="compaction_strategy"):
        loader.load("bad")


def test_temperature_out_of_range_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad",
        MINIMAL_VALID
        + """\
sampling:
  temperature: 3.0
""",
    )
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileValidationError):
        loader.load("bad")


def test_extra_field_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad",
        MINIMAL_VALID
        + """\
mystery_field: hello
""",
    )
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileValidationError):
        loader.load("bad")


def test_extends_must_be_string(tmp_path: Path) -> None:
    _write(tmp_path, "bad", "extends:\n  - a\n  - b\n" + MINIMAL_VALID)
    loader = ProfileLoader(search_paths=[tmp_path])
    with pytest.raises(ProfileValidationError, match="extends"):
        loader.load("bad")
