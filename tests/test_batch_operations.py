"""Tests for batch_transition and batch_close operations.

TDD: Verifies batch move/close logic with mocked database.
All test data is fixed and deterministic.
"""

import json
from unittest.mock import MagicMock, call

import pytest

from stompy_ticketing.models import BatchItemResult, BatchOperationResult
from stompy_ticketing.service import TicketService


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #

FIXED_TIME = 1700000000.0
SCHEMA = "test_project"


def _make_ticket_row(
    id=1,
    title="Test ticket",
    description="Test description",
    type="task",
    status="backlog",
    priority="medium",
    assignee=None,
    tags=None,
    metadata=None,
    session_id="sess_123",
    created_at=FIXED_TIME,
    updated_at=FIXED_TIME,
    closed_at=None,
    content_hash="abc123",
    content_tsvector=None,
    archived_at=None,
):
    return {
        "id": id,
        "title": title,
        "description": description,
        "type": type,
        "status": status,
        "priority": priority,
        "assignee": assignee,
        "tags": tags,
        "metadata": metadata,
        "session_id": session_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "closed_at": closed_at,
        "content_hash": content_hash,
        "content_tsvector": content_tsvector,
        "archived_at": archived_at,
    }


def _mock_conn_and_cursor():
    """Create a mock connection and cursor."""
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# --------------------------------------------------------------------------- #
# batch_transition tests                                                       #
# --------------------------------------------------------------------------- #


class TestBatchTransition:
    """Tests for TicketService.batch_transition()."""

    def test_preview_mode_does_not_execute(self):
        """Default (confirm=False) should preview without modifying tickets."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        # Ticket 1: backlog task, Ticket 2: backlog task
        cur.fetchone.side_effect = [
            {"type": "task", "status": "backlog"},
            {"type": "task", "status": "backlog"},
        ]

        result = svc.batch_transition(conn, SCHEMA, [1, 2], "in_progress")

        assert result.dry_run is True
        assert result.total == 2
        assert result.succeeded == 2
        assert result.failed == 0
        assert len(result.results) == 2
        assert result.results[0].old_status == "backlog"
        assert result.results[0].new_status == "in_progress"
        # No commit should have been called (preview mode)
        conn.commit.assert_not_called()

    def test_confirm_mode_executes_transitions(self):
        """confirm=True should actually transition each ticket."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        # For batch_transition: cursor returns type/status for each ticket
        # Then transition_ticket internally does its own SELECT + UPDATE
        ticket_row_1 = _make_ticket_row(id=1, type="task", status="backlog")
        ticket_row_2 = _make_ticket_row(id=2, type="task", status="backlog")
        transitioned_row_1 = _make_ticket_row(id=1, type="task", status="in_progress")
        transitioned_row_2 = _make_ticket_row(id=2, type="task", status="in_progress")

        cur.fetchone.side_effect = [
            # batch_transition SELECT for ticket 1
            {"type": "task", "status": "backlog"},
            # transition_ticket SELECT for ticket 1
            ticket_row_1,
            # transition_ticket UPDATE RETURNING for ticket 1
            transitioned_row_1,
            # batch_transition SELECT for ticket 2
            {"type": "task", "status": "backlog"},
            # transition_ticket SELECT for ticket 2
            ticket_row_2,
            # transition_ticket UPDATE RETURNING for ticket 2
            transitioned_row_2,
        ]
        cur.fetchall.return_value = []  # history queries

        result = svc.batch_transition(conn, SCHEMA, [1, 2], "in_progress", confirm=True)

        assert result.dry_run is False
        assert result.succeeded == 2
        assert result.failed == 0

    def test_invalid_transition_reported_per_ticket(self):
        """Invalid transitions should be reported per-ticket, not fail the whole batch."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        cur.fetchone.side_effect = [
            # Ticket 1: backlog -> in_progress (valid)
            {"type": "task", "status": "backlog"},
            # Ticket 2: done -> in_progress (invalid - done is terminal)
            {"type": "task", "status": "done"},
        ]

        result = svc.batch_transition(conn, SCHEMA, [1, 2], "in_progress")

        assert result.total == 2
        assert result.succeeded == 1
        assert result.failed == 1
        assert result.results[0].success is True
        assert result.results[1].success is False
        assert "Cannot transition" in result.results[1].error

    def test_missing_ticket_reported(self):
        """Missing tickets should be reported as failed."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        cur.fetchone.side_effect = [None]  # Ticket not found

        result = svc.batch_transition(conn, SCHEMA, [999], "in_progress")

        assert result.total == 1
        assert result.failed == 1
        assert result.results[0].error == "Ticket not found"

    def test_batch_max_exceeded(self):
        """Should reject batches exceeding BATCH_MAX (50)."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        ids = list(range(1, 52))  # 51 IDs
        result = svc.batch_transition(conn, SCHEMA, ids, "in_progress")

        assert result.failed == 51
        assert result.succeeded == 0
        assert "exceeds max 50" in result.results[0].error

    def test_empty_batch_returns_zero(self):
        """Empty batch should return zero counts."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        result = svc.batch_transition(conn, SCHEMA, [], "in_progress")

        assert result.total == 0
        assert result.succeeded == 0
        assert result.failed == 0

    def test_mixed_ticket_types(self):
        """Should handle different ticket types in the same batch."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        cur.fetchone.side_effect = [
            {"type": "task", "status": "backlog"},      # valid: backlog -> in_progress
            {"type": "bug", "status": "triage"},         # invalid: triage -> in_progress
            {"type": "feature", "status": "approved"},   # valid: approved -> in_progress
        ]

        result = svc.batch_transition(conn, SCHEMA, [1, 2, 3], "in_progress")

        assert result.succeeded == 2
        assert result.failed == 1
        assert result.results[1].success is False  # bug triage -> in_progress invalid


# --------------------------------------------------------------------------- #
# batch_close tests                                                            #
# --------------------------------------------------------------------------- #


class TestBatchClose:
    """Tests for TicketService.batch_close()."""

    def test_preview_mode_shows_close_path(self):
        """Preview should show what terminal status each ticket would reach."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        cur.fetchone.side_effect = [
            {"type": "task", "status": "in_progress"},  # -> done (direct)
            {"type": "bug", "status": "confirmed"},      # -> in_progress -> resolved
        ]

        result = svc.batch_close(conn, SCHEMA, [1, 2])

        assert result.dry_run is True
        assert result.total == 2
        assert result.succeeded == 2
        # Task in_progress -> done (direct terminal)
        assert result.results[0].new_status == "done"
        assert result.results[0].old_status == "in_progress"
        # Bug confirmed -> in_progress -> resolved (BFS finds shortest path)
        assert result.results[1].new_status == "resolved"
        assert result.results[1].old_status == "confirmed"

    def test_already_closed_tickets_succeed(self):
        """Tickets already in terminal status should succeed immediately."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        cur.fetchone.side_effect = [
            {"type": "task", "status": "done"},  # already terminal
        ]

        result = svc.batch_close(conn, SCHEMA, [1])

        assert result.succeeded == 1
        assert result.results[0].old_status == "done"
        assert result.results[0].new_status == "done"

    def test_confirm_mode_walks_intermediate_states(self):
        """confirm=True should transition through intermediate states to reach positive terminal.

        Bug at triage: BFS now prefers positive terminal (resolved) over wont_fix (#163).
        Path: triage -> confirmed -> in_progress -> resolved (3 steps).
        """
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        # Bug at triage -> confirmed -> in_progress -> resolved (positive path)
        cur.fetchone.side_effect = [
            # batch_close SELECT
            {"type": "bug", "status": "triage"},
            # transition triage -> confirmed
            _make_ticket_row(id=1, type="bug", status="triage"),
            _make_ticket_row(id=1, type="bug", status="confirmed"),
            # transition confirmed -> in_progress
            _make_ticket_row(id=1, type="bug", status="confirmed"),
            _make_ticket_row(id=1, type="bug", status="in_progress"),
            # transition in_progress -> resolved
            _make_ticket_row(id=1, type="bug", status="in_progress"),
            _make_ticket_row(id=1, type="bug", status="resolved", closed_at=FIXED_TIME),
        ]
        cur.fetchall.return_value = []  # history queries

        result = svc.batch_close(conn, SCHEMA, [1], confirm=True)

        assert result.dry_run is False
        assert result.succeeded == 1
        assert result.results[0].new_status == "resolved"

    def test_confirm_mode_with_negative_resolution(self):
        """confirm=True with resolution=wont_fix should use the shortest path to wont_fix."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        # Bug at triage -> wont_fix (direct, 1 step) via explicit resolution
        cur.fetchone.side_effect = [
            # batch_close SELECT
            {"type": "bug", "status": "triage"},
            # transition triage -> wont_fix
            _make_ticket_row(id=1, type="bug", status="triage"),
            _make_ticket_row(id=1, type="bug", status="wont_fix", closed_at=FIXED_TIME),
        ]
        cur.fetchall.return_value = []

        result = svc.batch_close(conn, SCHEMA, [1], confirm=True, resolution="wont_fix")

        assert result.dry_run is False
        assert result.succeeded == 1
        assert result.results[0].new_status == "wont_fix"

    def test_confirm_mode_multi_step_close(self):
        """confirm=True with a ticket requiring multiple transitions."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        # Bug at confirmed: confirmed -> in_progress -> resolved (2 steps)
        cur.fetchone.side_effect = [
            # batch_close SELECT
            {"type": "bug", "status": "confirmed"},
            # transition_ticket for confirmed -> in_progress
            _make_ticket_row(id=1, type="bug", status="confirmed"),
            _make_ticket_row(id=1, type="bug", status="in_progress"),
            # transition_ticket for in_progress -> resolved
            _make_ticket_row(id=1, type="bug", status="in_progress"),
            _make_ticket_row(id=1, type="bug", status="resolved", closed_at=FIXED_TIME),
        ]
        cur.fetchall.return_value = []

        result = svc.batch_close(conn, SCHEMA, [1], confirm=True)

        assert result.dry_run is False
        assert result.succeeded == 1
        assert result.results[0].old_status == "confirmed"
        assert result.results[0].new_status == "resolved"

    def test_missing_ticket_in_batch_close(self):
        """Missing tickets should be reported as failed."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        cur.fetchone.side_effect = [None]

        result = svc.batch_close(conn, SCHEMA, [999])

        assert result.failed == 1
        assert result.results[0].error == "Ticket not found"

    def test_batch_close_max_exceeded(self):
        """Should reject batches exceeding BATCH_MAX."""
        conn, cur = _mock_conn_and_cursor()
        svc = TicketService()

        ids = list(range(1, 52))
        result = svc.batch_close(conn, SCHEMA, ids)

        assert result.failed == 51
        assert "exceeds max 50" in result.results[0].error


# --------------------------------------------------------------------------- #
# _find_close_path tests                                                       #
# --------------------------------------------------------------------------- #


class TestFindClosePath:
    """Tests for TicketService._find_close_path() BFS logic."""

    def test_task_backlog_to_done(self):
        """task: backlog -> done (preferred positive terminal)."""
        path = TicketService._find_close_path("task", "backlog")
        assert path is not None
        assert path[-1] == "done"  # positive terminal preferred (#163)

    def test_task_in_progress_to_done(self):
        """task: in_progress -> done (preferred positive terminal)."""
        path = TicketService._find_close_path("task", "in_progress")
        assert path == ["done"]  # positive terminal preferred (#163)

    def test_bug_triage_to_resolved(self):
        """bug: triage -> confirmed -> in_progress -> resolved (positive)."""
        path = TicketService._find_close_path("bug", "triage")
        assert path is not None
        assert path[-1] == "resolved"  # positive terminal preferred (#163)

    def test_bug_triage_to_wont_fix_explicit(self):
        """bug: triage -> wont_fix (explicit negative target)."""
        path = TicketService._find_close_path("bug", "triage", "wont_fix")
        assert path == ["wont_fix"]

    def test_bug_in_progress_to_resolved(self):
        """bug: in_progress -> resolved (positive terminal preferred)."""
        path = TicketService._find_close_path("bug", "in_progress")
        assert path is not None
        assert path[-1] == "resolved"  # positive terminal preferred (#163)

    def test_feature_proposed_to_shipped(self):
        """feature: proposed -> approved -> in_progress -> shipped (positive)."""
        path = TicketService._find_close_path("feature", "proposed")
        assert path is not None
        assert path[-1] == "shipped"  # positive terminal preferred (#163)

    def test_feature_proposed_to_rejected_explicit(self):
        """feature: proposed -> rejected (explicit negative target)."""
        path = TicketService._find_close_path("feature", "proposed", "rejected")
        assert path == ["rejected"]

    def test_decision_open_to_decided(self):
        """decision: open -> decided (positive terminal preferred)."""
        path = TicketService._find_close_path("decision", "open")
        assert path is not None
        assert path[-1] == "decided"  # positive terminal preferred (#163)

    def test_already_terminal_returns_none(self):
        """Terminal status has no onward path."""
        # done is terminal for task, no transitions from it
        path = TicketService._find_close_path("task", "done")
        assert path is None

    def test_unknown_type_returns_none(self):
        """Unknown ticket type should return None."""
        path = TicketService._find_close_path("unknown_type", "backlog")
        assert path is None


# --------------------------------------------------------------------------- #
# BatchOperationResult model tests                                             #
# --------------------------------------------------------------------------- #


class TestBatchOperationResultModel:
    """Tests for the BatchOperationResult Pydantic model."""

    def test_default_values(self):
        result = BatchOperationResult(
            action="batch_move", total=0, succeeded=0, failed=0
        )
        assert result.dry_run is True
        assert result.results == []

    def test_serialization(self):
        result = BatchOperationResult(
            action="batch_close",
            total=2,
            succeeded=1,
            failed=1,
            results=[
                BatchItemResult(ticket_id=1, success=True, old_status="backlog", new_status="done"),
                BatchItemResult(ticket_id=2, success=False, error="Not found"),
            ],
            dry_run=False,
        )
        data = result.model_dump()
        assert data["action"] == "batch_close"
        assert data["total"] == 2
        assert len(data["results"]) == 2
        assert data["results"][0]["success"] is True
        assert data["results"][1]["error"] == "Not found"
