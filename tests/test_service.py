"""Tests for TicketService with mocked database.

TDD: These tests use a mock DB to verify service logic without PostgreSQL.
All test data is fixed and deterministic.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from stompy_ticketing.models import (
    Priority,
    TicketCreate,
    TicketLinkCreate,
    TicketListFilters,
    TicketType,
    TicketUpdate,
    LinkType,
)
from stompy_ticketing.service import (
    InvalidTransitionError,
    TicketService,
)


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
):
    """Create a mock ticket row dict."""
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
    }


def _make_history_row(
    id=1,
    ticket_id=1,
    field_name="status",
    old_value="backlog",
    new_value="in_progress",
    changed_by="agent",
    changed_at=FIXED_TIME,
):
    return {
        "id": id,
        "ticket_id": ticket_id,
        "field_name": field_name,
        "old_value": old_value,
        "new_value": new_value,
        "changed_by": changed_by,
        "changed_at": changed_at,
    }


def _make_link_row(
    id=1,
    source_id=1,
    target_id=2,
    link_type="blocks",
    created_at=FIXED_TIME,
    target_title="Target ticket",
    target_status="backlog",
):
    return {
        "id": id,
        "source_id": source_id,
        "target_id": target_id,
        "link_type": link_type,
        "created_at": created_at,
        "target_title": target_title,
        "target_status": target_status,
    }


def _mock_conn_and_cursor(rows=None, fetchone_value=None):
    """Create a mock connection and cursor.

    Args:
        rows: List of rows for fetchall to return.
        fetchone_value: Value for fetchone to return (if None, returns first of rows).
    """
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur

    if rows is not None:
        cur.fetchall.return_value = rows

    if fetchone_value is not None:
        cur.fetchone.return_value = fetchone_value
    elif rows:
        cur.fetchone.return_value = rows[0]
    else:
        cur.fetchone.return_value = None

    return conn, cur


# --------------------------------------------------------------------------- #
# Create tests                                                                #
# --------------------------------------------------------------------------- #


class TestCreateTicket:
    def setup_method(self):
        self.service = TicketService()

    @patch("stompy_ticketing.service.time")
    def test_create_task_sets_initial_status(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        row = _make_ticket_row(title="New task", status="backlog")
        conn, cur = _mock_conn_and_cursor(fetchone_value=row)

        data = TicketCreate(title="New task", type=TicketType.task)
        result = self.service.create_ticket(conn, SCHEMA, data)

        assert result.status == "backlog"
        assert result.type == "task"
        assert result.title == "New task"
        conn.commit.assert_called_once()

    @patch("stompy_ticketing.service.time")
    def test_create_bug_sets_triage_status(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        row = _make_ticket_row(status="triage", type="bug")
        conn, cur = _mock_conn_and_cursor(fetchone_value=row)

        data = TicketCreate(title="Bug report", type=TicketType.bug)
        result = self.service.create_ticket(conn, SCHEMA, data)

        assert result.status == "triage"
        assert result.type == "bug"

    @patch("stompy_ticketing.service.time")
    def test_create_with_tags_and_metadata(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        row = _make_ticket_row(
            tags=json.dumps(["backend", "urgent"]),
            metadata=json.dumps({"sprint": 5}),
        )
        conn, cur = _mock_conn_and_cursor(fetchone_value=row)

        data = TicketCreate(
            title="Task with meta",
            tags=["backend", "urgent"],
            metadata={"sprint": 5},
        )
        result = self.service.create_ticket(conn, SCHEMA, data)

        assert result.tags == ["backend", "urgent"]
        assert result.metadata == {"sprint": 5}

    @patch("stompy_ticketing.service.time")
    def test_create_rollback_on_error(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        conn, cur = _mock_conn_and_cursor()
        cur.execute.side_effect = Exception("DB error")

        data = TicketCreate(title="Will fail")
        with pytest.raises(Exception, match="DB error"):
            self.service.create_ticket(conn, SCHEMA, data)

        conn.rollback.assert_called_once()


# --------------------------------------------------------------------------- #
# Get tests                                                                   #
# --------------------------------------------------------------------------- #


class TestGetTicket:
    def setup_method(self):
        self.service = TicketService()

    def test_get_existing_ticket(self):
        row = _make_ticket_row()
        conn, cur = _mock_conn_and_cursor(fetchone_value=row)
        # History and links queries return empty
        cur.fetchall.return_value = []

        result = self.service.get_ticket(conn, SCHEMA, 1)

        assert result is not None
        assert result.id == 1
        assert result.title == "Test ticket"
        assert result.history == []
        assert result.links == []

    def test_get_nonexistent_ticket_returns_none(self):
        conn, cur = _mock_conn_and_cursor(fetchone_value=None)

        result = self.service.get_ticket(conn, SCHEMA, 999)

        assert result is None

    def test_get_ticket_with_history(self):
        row = _make_ticket_row()
        history = [_make_history_row()]
        links = []
        conn, cur = _mock_conn_and_cursor(fetchone_value=row)
        # First fetchall is history, second is links
        cur.fetchall.side_effect = [history, links]

        result = self.service.get_ticket(conn, SCHEMA, 1)

        assert result is not None
        assert len(result.history) == 1
        assert result.history[0].field_name == "status"
        assert result.links == []


# --------------------------------------------------------------------------- #
# Update tests                                                                #
# --------------------------------------------------------------------------- #


class TestUpdateTicket:
    def setup_method(self):
        self.service = TicketService()

    @patch("stompy_ticketing.service.time")
    def test_update_title(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        current = _make_ticket_row(title="Old title")
        updated = _make_ticket_row(title="New title")
        conn, cur = _mock_conn_and_cursor()
        # First fetchone: get current, second: after UPDATE RETURNING
        cur.fetchone.side_effect = [current, updated]

        data = TicketUpdate(title="New title")
        result = self.service.update_ticket(conn, SCHEMA, 1, data, changed_by="user")

        assert result.title == "New title"
        conn.commit.assert_called_once()
        # Should have recorded history
        assert cur.execute.call_count >= 2  # SELECT + UPDATE + INSERT history

    @patch("stompy_ticketing.service.time")
    def test_update_no_changes_returns_current(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        current = _make_ticket_row(title="Same title")
        conn, cur = _mock_conn_and_cursor(fetchone_value=current)

        data = TicketUpdate(title="Same title")
        result = self.service.update_ticket(conn, SCHEMA, 1, data)

        assert result.title == "Same title"
        conn.commit.assert_not_called()

    def test_update_nonexistent_returns_none(self):
        conn, cur = _mock_conn_and_cursor(fetchone_value=None)

        data = TicketUpdate(title="Anything")
        result = self.service.update_ticket(conn, SCHEMA, 999, data)

        assert result is None


# --------------------------------------------------------------------------- #
# Transition tests                                                            #
# --------------------------------------------------------------------------- #


class TestTransitionTicket:
    def setup_method(self):
        self.service = TicketService()

    @patch("stompy_ticketing.service.time")
    def test_valid_transition(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        current = _make_ticket_row(status="backlog", type="task")
        updated = _make_ticket_row(status="in_progress", type="task")
        conn, cur = _mock_conn_and_cursor()
        cur.fetchone.side_effect = [current, updated]

        result = self.service.transition_ticket(conn, SCHEMA, 1, "in_progress")

        assert result.status == "in_progress"
        conn.commit.assert_called_once()

    @patch("stompy_ticketing.service.time")
    def test_transition_to_terminal_sets_closed_at(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        current = _make_ticket_row(status="in_progress", type="task")
        updated = _make_ticket_row(status="done", type="task", closed_at=FIXED_TIME)
        conn, cur = _mock_conn_and_cursor()
        cur.fetchone.side_effect = [current, updated]

        result = self.service.transition_ticket(conn, SCHEMA, 1, "done")

        assert result.status == "done"
        assert result.closed_at == FIXED_TIME

    def test_invalid_transition_raises(self):
        current = _make_ticket_row(status="backlog", type="task")
        conn, cur = _mock_conn_and_cursor(fetchone_value=current)

        with pytest.raises(InvalidTransitionError, match="Cannot transition"):
            self.service.transition_ticket(conn, SCHEMA, 1, "done")

    def test_transition_nonexistent_returns_none(self):
        conn, cur = _mock_conn_and_cursor(fetchone_value=None)

        result = self.service.transition_ticket(conn, SCHEMA, 999, "in_progress")

        assert result is None


# --------------------------------------------------------------------------- #
# Close tests                                                                 #
# --------------------------------------------------------------------------- #


class TestCloseTicket:
    def setup_method(self):
        self.service = TicketService()

    def test_close_already_closed_returns_ticket(self):
        """Closing an already-closed ticket is a no-op."""
        row = _make_ticket_row(status="done", type="task")
        conn, cur = _mock_conn_and_cursor(fetchone_value=row)
        # get_ticket fetches history/links
        cur.fetchall.return_value = []

        result = self.service.close_ticket(conn, SCHEMA, 1)

        assert result is not None
        assert result.status == "done"

    def test_close_nonexistent_returns_none(self):
        conn, cur = _mock_conn_and_cursor(fetchone_value=None)

        result = self.service.close_ticket(conn, SCHEMA, 999)

        assert result is None

    @patch("stompy_ticketing.service.time")
    def test_close_in_progress_task_transitions_to_done(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        # close_ticket fetches type/status first
        type_row = {"type": "task", "status": "in_progress"}
        # transition_ticket fetches full row
        full_row = _make_ticket_row(status="in_progress", type="task")
        updated_row = _make_ticket_row(status="done", type="task", closed_at=FIXED_TIME)
        conn, cur = _mock_conn_and_cursor()
        cur.fetchone.side_effect = [type_row, full_row, updated_row]

        result = self.service.close_ticket(conn, SCHEMA, 1)

        assert result.status == "done"


# --------------------------------------------------------------------------- #
# List tests                                                                  #
# --------------------------------------------------------------------------- #


class TestListTickets:
    def setup_method(self):
        self.service = TicketService()

    def test_list_returns_tickets(self):
        rows = [_make_ticket_row(id=1), _make_ticket_row(id=2)]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        # Total count, by_status, by_type
        cur.fetchone.side_effect = [{"count": 2}]
        cur.fetchall.side_effect = [
            rows,
            [{"status": "backlog", "count": 2}],
            [{"type": "task", "count": 2}],
        ]

        result = self.service.list_tickets(conn, SCHEMA)

        assert result.total == 2
        assert len(result.tickets) == 2

    def test_list_with_type_filter(self):
        rows = [_make_ticket_row(type="bug")]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchone.side_effect = [{"count": 1}]
        cur.fetchall.side_effect = [
            rows,
            [{"status": "triage", "count": 1}],
            [{"type": "bug", "count": 1}],
        ]

        filters = TicketListFilters(type=TicketType.bug)
        result = self.service.list_tickets(conn, SCHEMA, filters)

        assert len(result.tickets) == 1
        assert result.by_type == {"bug": 1}

    def test_list_empty(self):
        conn, cur = _mock_conn_and_cursor(rows=[])
        cur.fetchone.side_effect = [{"count": 0}]
        cur.fetchall.side_effect = [[], [], []]

        result = self.service.list_tickets(conn, SCHEMA)

        assert result.total == 0
        assert result.tickets == []


# --------------------------------------------------------------------------- #
# Link tests                                                                  #
# --------------------------------------------------------------------------- #


class TestLinks:
    def setup_method(self):
        self.service = TicketService()

    @patch("stompy_ticketing.service.time")
    def test_add_link(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        link_row = _make_link_row()
        conn, cur = _mock_conn_and_cursor(fetchone_value=link_row)

        data = TicketLinkCreate(target_id=2, link_type=LinkType.blocks)
        result = self.service.add_link(conn, SCHEMA, 1, data)

        assert result.source_id == 1
        assert result.target_id == 2
        assert result.link_type == "blocks"
        conn.commit.assert_called_once()

    def test_remove_link_found(self):
        conn, cur = _mock_conn_and_cursor(fetchone_value={"id": 1})

        result = self.service.remove_link(conn, SCHEMA, 1)

        assert result is True
        conn.commit.assert_called_once()

    def test_remove_link_not_found(self):
        conn, cur = _mock_conn_and_cursor(fetchone_value=None)

        result = self.service.remove_link(conn, SCHEMA, 999)

        assert result is False

    def test_list_links(self):
        links = [_make_link_row(id=1), _make_link_row(id=2, link_type="related")]
        conn, cur = _mock_conn_and_cursor(rows=links)
        cur.fetchall.return_value = links

        result = self.service.list_links(conn, SCHEMA, 1)

        assert len(result) == 2


# --------------------------------------------------------------------------- #
# Board view tests                                                            #
# --------------------------------------------------------------------------- #


class TestBoardView:
    def setup_method(self):
        self.service = TicketService()

    def test_summary_view(self):
        conn, cur = _mock_conn_and_cursor()
        cur.fetchall.return_value = [
            {"status": "backlog", "count": 3},
            {"status": "in_progress", "count": 1},
        ]

        result = self.service.board_view(conn, SCHEMA, view="summary")

        assert result.total == 4
        assert len(result.columns) == 2

    def test_kanban_view_with_type_filter(self):
        rows = [
            _make_ticket_row(id=1, status="backlog"),
            _make_ticket_row(id=2, status="in_progress"),
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.board_view(conn, SCHEMA, type_filter="task", view="kanban")

        assert result.total == 2
        assert result.type_filter == "task"
        # Should have columns for all task statuses
        status_names = [c.status for c in result.columns]
        assert "backlog" in status_names
        assert "in_progress" in status_names


# --------------------------------------------------------------------------- #
# Row conversion tests                                                        #
# --------------------------------------------------------------------------- #


class TestRowConversion:
    def setup_method(self):
        self.service = TicketService()

    def test_row_with_json_tags(self):
        row = _make_ticket_row(tags=json.dumps(["a", "b"]))
        result = self.service._row_to_response(row)
        assert result.tags == ["a", "b"]

    def test_row_with_null_tags(self):
        row = _make_ticket_row(tags=None)
        result = self.service._row_to_response(row)
        assert result.tags is None

    def test_row_with_invalid_json_tags(self):
        row = _make_ticket_row(tags="not-json")
        result = self.service._row_to_response(row)
        assert result.tags is None

    def test_row_with_json_metadata(self):
        row = _make_ticket_row(metadata=json.dumps({"key": "value"}))
        result = self.service._row_to_response(row)
        assert result.metadata == {"key": "value"}
