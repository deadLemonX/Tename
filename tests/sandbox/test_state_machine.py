"""Unit tests for the Sandbox lifecycle state machine.

Pure — no Docker required. Exercises both valid transitions (silent,
logged) and invalid transitions (raise `InvalidTransitionError` with a
descriptive message). DESTROYED is terminal; every other state has a
well-defined successor set.
"""

from __future__ import annotations

import pytest

from tename.sandbox import InvalidTransitionError, SandboxStatus, assert_transition
from tename.sandbox.state_machine import ALLOWED_TRANSITIONS


class TestValidTransitions:
    def test_provisioning_to_ready(self) -> None:
        assert_transition(SandboxStatus.PROVISIONING, SandboxStatus.READY)

    def test_provisioning_to_error(self) -> None:
        assert_transition(SandboxStatus.PROVISIONING, SandboxStatus.ERROR)

    def test_ready_to_running(self) -> None:
        assert_transition(SandboxStatus.READY, SandboxStatus.RUNNING)

    def test_running_to_idle(self) -> None:
        assert_transition(SandboxStatus.RUNNING, SandboxStatus.IDLE)

    def test_idle_to_running(self) -> None:
        assert_transition(SandboxStatus.IDLE, SandboxStatus.RUNNING)

    def test_ready_to_destroyed(self) -> None:
        assert_transition(SandboxStatus.READY, SandboxStatus.DESTROYED)

    def test_idle_to_destroyed(self) -> None:
        assert_transition(SandboxStatus.IDLE, SandboxStatus.DESTROYED)

    def test_running_to_error(self) -> None:
        assert_transition(SandboxStatus.RUNNING, SandboxStatus.ERROR)

    def test_error_to_destroyed(self) -> None:
        assert_transition(SandboxStatus.ERROR, SandboxStatus.DESTROYED)

    def test_noop_self_transition_is_silent(self) -> None:
        # Polling the current state shouldn't raise or log as a change.
        for state in SandboxStatus:
            assert_transition(state, state)


class TestInvalidTransitions:
    def test_ready_to_idle_rejected(self) -> None:
        # Must go READY -> RUNNING -> IDLE; skipping RUNNING is not allowed.
        with pytest.raises(InvalidTransitionError, match="ready -> idle"):
            assert_transition(SandboxStatus.READY, SandboxStatus.IDLE)

    def test_provisioning_to_running_rejected(self) -> None:
        with pytest.raises(InvalidTransitionError):
            assert_transition(SandboxStatus.PROVISIONING, SandboxStatus.RUNNING)

    def test_destroyed_is_terminal(self) -> None:
        # Nothing is allowed out of DESTROYED.
        for nxt in SandboxStatus:
            if nxt == SandboxStatus.DESTROYED:
                continue
            with pytest.raises(InvalidTransitionError):
                assert_transition(SandboxStatus.DESTROYED, nxt)

    def test_error_cannot_go_back_to_ready(self) -> None:
        with pytest.raises(InvalidTransitionError):
            assert_transition(SandboxStatus.ERROR, SandboxStatus.READY)

    def test_error_message_mentions_sandbox_id(self) -> None:
        with pytest.raises(InvalidTransitionError, match=r"sandbox abc123"):
            assert_transition(
                SandboxStatus.ERROR,
                SandboxStatus.READY,
                sandbox_id="abc123",
            )

    def test_error_message_lists_permitted_transitions(self) -> None:
        with pytest.raises(InvalidTransitionError, match=r"allowed:.*destroyed"):
            assert_transition(SandboxStatus.ERROR, SandboxStatus.RUNNING)


class TestTable:
    def test_every_state_present(self) -> None:
        # Every enum member appears as a key, even DESTROYED (empty set).
        for state in SandboxStatus:
            assert state in ALLOWED_TRANSITIONS

    def test_destroyed_permits_nothing(self) -> None:
        assert ALLOWED_TRANSITIONS[SandboxStatus.DESTROYED] == frozenset()

    def test_permitted_sets_are_frozen(self) -> None:
        # Guard against accidental mutation of the transition table.
        for permitted in ALLOWED_TRANSITIONS.values():
            assert isinstance(permitted, frozenset)
