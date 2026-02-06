"""Tests for ticket state machine logic.

TDD: These tests were written FIRST, before the implementation.
Tests are deterministic - no random values or shared mutable state.
"""

import pytest

from stompy_ticketing.service import (
    STATE_MACHINES,
    get_initial_status,
    get_terminal_statuses,
    validate_transition,
    InvalidTransitionError,
)


# --------------------------------------------------------------------------- #
# Initial status tests                                                        #
# --------------------------------------------------------------------------- #


class TestGetInitialStatus:
    def test_task_initial_status(self):
        assert get_initial_status("task") == "backlog"

    def test_bug_initial_status(self):
        assert get_initial_status("bug") == "triage"

    def test_feature_initial_status(self):
        assert get_initial_status("feature") == "proposed"

    def test_decision_initial_status(self):
        assert get_initial_status("decision") == "open"

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Unknown ticket type"):
            get_initial_status("invalid_type")


# --------------------------------------------------------------------------- #
# Terminal status tests                                                       #
# --------------------------------------------------------------------------- #


class TestGetTerminalStatuses:
    def test_task_terminal_statuses(self):
        result = get_terminal_statuses("task")
        assert set(result) == {"done", "cancelled"}

    def test_bug_terminal_statuses(self):
        result = get_terminal_statuses("bug")
        assert set(result) == {"resolved", "wont_fix"}

    def test_feature_terminal_statuses(self):
        result = get_terminal_statuses("feature")
        assert set(result) == {"shipped", "rejected"}

    def test_decision_terminal_statuses(self):
        result = get_terminal_statuses("decision")
        assert set(result) == {"decided", "deferred"}

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Unknown ticket type"):
            get_terminal_statuses("bogus")


# --------------------------------------------------------------------------- #
# Valid transitions                                                           #
# --------------------------------------------------------------------------- #


class TestValidTransitions:
    # --- Task ---
    def test_task_backlog_to_in_progress(self):
        assert validate_transition("task", "backlog", "in_progress") is True

    def test_task_in_progress_to_done(self):
        assert validate_transition("task", "in_progress", "done") is True

    def test_task_in_progress_to_cancelled(self):
        assert validate_transition("task", "in_progress", "cancelled") is True

    def test_task_backlog_to_cancelled(self):
        assert validate_transition("task", "backlog", "cancelled") is True

    # --- Bug ---
    def test_bug_triage_to_confirmed(self):
        assert validate_transition("bug", "triage", "confirmed") is True

    def test_bug_confirmed_to_in_progress(self):
        assert validate_transition("bug", "confirmed", "in_progress") is True

    def test_bug_in_progress_to_resolved(self):
        assert validate_transition("bug", "in_progress", "resolved") is True

    def test_bug_in_progress_to_wont_fix(self):
        assert validate_transition("bug", "in_progress", "wont_fix") is True

    def test_bug_triage_to_wont_fix(self):
        assert validate_transition("bug", "triage", "wont_fix") is True

    # --- Feature ---
    def test_feature_proposed_to_approved(self):
        assert validate_transition("feature", "proposed", "approved") is True

    def test_feature_approved_to_in_progress(self):
        assert validate_transition("feature", "approved", "in_progress") is True

    def test_feature_in_progress_to_shipped(self):
        assert validate_transition("feature", "in_progress", "shipped") is True

    def test_feature_proposed_to_rejected(self):
        assert validate_transition("feature", "proposed", "rejected") is True

    def test_feature_in_progress_to_rejected(self):
        assert validate_transition("feature", "in_progress", "rejected") is True

    # --- Decision ---
    def test_decision_open_to_decided(self):
        assert validate_transition("decision", "open", "decided") is True

    def test_decision_open_to_deferred(self):
        assert validate_transition("decision", "open", "deferred") is True

    def test_decision_deferred_to_open(self):
        """Deferred decisions can be reopened."""
        assert validate_transition("decision", "deferred", "open") is True


# --------------------------------------------------------------------------- #
# Invalid transitions                                                         #
# --------------------------------------------------------------------------- #


class TestInvalidTransitions:
    def test_task_done_to_in_progress(self):
        with pytest.raises(InvalidTransitionError, match="Cannot transition"):
            validate_transition("task", "done", "in_progress", raise_on_invalid=True)

    def test_task_backlog_to_done_skipping(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("task", "backlog", "done", raise_on_invalid=True)

    def test_bug_resolved_to_triage(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("bug", "resolved", "triage", raise_on_invalid=True)

    def test_feature_shipped_to_in_progress(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("feature", "shipped", "in_progress", raise_on_invalid=True)

    def test_decision_decided_to_open(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("decision", "decided", "open", raise_on_invalid=True)

    def test_same_status_is_invalid(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("task", "backlog", "backlog", raise_on_invalid=True)

    def test_invalid_returns_false_without_raise(self):
        result = validate_transition("task", "done", "in_progress", raise_on_invalid=False)
        assert result is False

    def test_invalid_current_status(self):
        with pytest.raises(InvalidTransitionError, match="not a valid status"):
            validate_transition("task", "nonexistent", "done", raise_on_invalid=True)


# --------------------------------------------------------------------------- #
# State machine completeness                                                  #
# --------------------------------------------------------------------------- #


class TestStateMachineCompleteness:
    """Verify every type has a complete, consistent state machine."""

    def test_all_types_have_state_machines(self):
        for ticket_type in ["task", "bug", "feature", "decision"]:
            assert ticket_type in STATE_MACHINES

    def test_all_initial_statuses_exist_in_transitions(self):
        for ticket_type in STATE_MACHINES:
            initial = get_initial_status(ticket_type)
            assert initial in STATE_MACHINES[ticket_type]["transitions"]

    def test_all_terminal_statuses_have_no_outgoing(self):
        """Terminal statuses should have no outgoing transitions (except deferred->open)."""
        for ticket_type in STATE_MACHINES:
            sm = STATE_MACHINES[ticket_type]
            for terminal in sm["terminal"]:
                outgoing = sm["transitions"].get(terminal, [])
                # Decision's deferred is special - can reopen
                if ticket_type == "decision" and terminal == "deferred":
                    assert outgoing == ["open"]
                else:
                    assert outgoing == [], (
                        f"{ticket_type}.{terminal} should have no outgoing transitions, "
                        f"got {outgoing}"
                    )

    def test_all_reachable_statuses_are_defined(self):
        """Every status mentioned in transitions should be a key or listed as initial/terminal."""
        for ticket_type, sm in STATE_MACHINES.items():
            all_statuses = set()
            all_statuses.add(sm["initial"])
            all_statuses.update(sm["terminal"])
            for source, targets in sm["transitions"].items():
                all_statuses.add(source)
                all_statuses.update(targets)

            # Every status should be a key in transitions
            for s in all_statuses:
                assert s in sm["transitions"], (
                    f"Status '{s}' in {ticket_type} is reachable but has no entry "
                    f"in transitions dict"
                )
