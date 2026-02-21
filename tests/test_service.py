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

    def test_list_default_limit_is_20(self):
        """Default limit should be 20 when no filters provided."""
        rows = [_make_ticket_row(id=i) for i in range(1, 21)]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchone.side_effect = [{"count": 54}]
        cur.fetchall.side_effect = [
            rows,
            [{"status": "backlog", "count": 54}],
            [{"type": "task", "count": 54}],
        ]

        result = self.service.list_tickets(conn, SCHEMA)

        assert result.limit == 20
        assert result.offset == 0
        assert result.has_more is True
        assert result.total == 54
        assert len(result.tickets) == 20

    def test_list_pagination_metadata_with_custom_limit_offset(self):
        """Pagination metadata should reflect custom limit and offset."""
        rows = [_make_ticket_row(id=i) for i in range(21, 41)]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchone.side_effect = [{"count": 54}]
        cur.fetchall.side_effect = [
            rows,
            [{"status": "backlog", "count": 54}],
            [{"type": "task", "count": 54}],
        ]

        filters = TicketListFilters(limit=20, offset=20)
        result = self.service.list_tickets(conn, SCHEMA, filters)

        assert result.limit == 20
        assert result.offset == 20
        assert result.has_more is True
        assert result.total == 54

    def test_list_has_more_false_when_no_more_results(self):
        """has_more should be False when offset + limit >= total."""
        rows = [_make_ticket_row(id=i) for i in range(41, 55)]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchone.side_effect = [{"count": 54}]
        cur.fetchall.side_effect = [
            rows,
            [{"status": "backlog", "count": 54}],
            [{"type": "task", "count": 54}],
        ]

        filters = TicketListFilters(limit=20, offset=40)
        result = self.service.list_tickets(conn, SCHEMA, filters)

        assert result.limit == 20
        assert result.offset == 40
        assert result.has_more is False
        assert result.total == 54
        assert len(result.tickets) == 14

    def test_list_passes_limit_and_offset_to_sql(self):
        """SQL query should use the limit and offset from filters."""
        conn, cur = _mock_conn_and_cursor(rows=[])
        cur.fetchone.side_effect = [{"count": 0}]
        cur.fetchall.side_effect = [[], [], []]

        filters = TicketListFilters(limit=10, offset=30)
        self.service.list_tickets(conn, SCHEMA, filters)

        # The first execute call is the main query with LIMIT/OFFSET
        first_call_params = cur.execute.call_args_list[0][0][1]
        # limit and offset are the last two params
        assert first_call_params[-2] == 10  # limit
        assert first_call_params[-1] == 30  # offset


# --------------------------------------------------------------------------- #
# Link tests                                                                  #
# --------------------------------------------------------------------------- #


class TestLinks:
    def setup_method(self):
        self.service = TicketService()

    @patch("stompy_ticketing.service.time")
    def test_add_link(self, mock_time):
        mock_time.time.return_value = FIXED_TIME
        # INSERT RETURNING * only has link columns (no target_title/target_status)
        raw_insert_row = {
            "id": 1,
            "source_id": 1,
            "target_id": 2,
            "link_type": "blocks",
            "created_at": FIXED_TIME,
        }
        # The follow-up SELECT fetches target ticket info
        target_ticket_row = {"title": "Target ticket", "status": "backlog"}
        conn, cur = _mock_conn_and_cursor()
        cur.fetchone.side_effect = [raw_insert_row, target_ticket_row]

        data = TicketLinkCreate(target_id=2, link_type=LinkType.blocks)
        result = self.service.add_link(conn, SCHEMA, 1, data)

        assert result.source_id == 1
        assert result.target_id == 2
        assert result.link_type == "blocks"
        conn.commit.assert_called_once()

    @patch("stompy_ticketing.service.time")
    def test_add_link_populates_target_title_and_status(self, mock_time):
        """Bug fix: add_link must return target_title and target_status like list_links does."""
        mock_time.time.return_value = FIXED_TIME
        # INSERT RETURNING * only has link columns (no target_title/target_status)
        raw_insert_row = {
            "id": 1,
            "source_id": 1,
            "target_id": 2,
            "link_type": "blocks",
            "created_at": FIXED_TIME,
        }
        # The follow-up SELECT fetches target ticket info
        target_ticket_row = {"title": "Implement auth", "status": "in_progress"}
        conn, cur = _mock_conn_and_cursor()
        cur.fetchone.side_effect = [raw_insert_row, target_ticket_row]

        data = TicketLinkCreate(target_id=2, link_type=LinkType.blocks)
        result = self.service.add_link(conn, SCHEMA, 1, data)

        assert result.target_title == "Implement auth"
        assert result.target_status == "in_progress"

    @patch("stompy_ticketing.service.time")
    def test_add_link_response_matches_list_link_format(self, mock_time):
        """The add response should have the same enriched format as list."""
        mock_time.time.return_value = FIXED_TIME
        # INSERT returns raw link row
        raw_insert_row = {
            "id": 5,
            "source_id": 10,
            "target_id": 20,
            "link_type": "related",
            "created_at": FIXED_TIME,
        }
        target_ticket_row = {"title": "Deploy pipeline", "status": "backlog"}
        conn, cur = _mock_conn_and_cursor()
        cur.fetchone.side_effect = [raw_insert_row, target_ticket_row]

        data = TicketLinkCreate(target_id=20, link_type=LinkType.related)
        add_result = self.service.add_link(conn, SCHEMA, 10, data)

        # Verify add response has the same fields list would have
        assert add_result.id == 5
        assert add_result.source_id == 10
        assert add_result.target_id == 20
        assert add_result.link_type == "related"
        assert add_result.target_title == "Deploy pipeline"
        assert add_result.target_status == "backlog"
        assert add_result.created_at == FIXED_TIME

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

    def test_kanban_view_truncates_long_descriptions(self):
        long_desc = "A" * 500
        rows = [
            _make_ticket_row(id=1, status="backlog", description=long_desc),
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.board_view(conn, SCHEMA, view="kanban")

        ticket = result.columns[0].tickets[0]
        assert len(ticket.description) == 203  # 200 chars + "..."
        assert ticket.description.endswith("...")

    def test_kanban_view_preserves_short_descriptions(self):
        short_desc = "Fix the login bug"
        rows = [
            _make_ticket_row(id=1, status="backlog", description=short_desc),
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.board_view(conn, SCHEMA, view="kanban")

        ticket = result.columns[0].tickets[0]
        assert ticket.description == short_desc

    def test_status_filter(self):
        rows = [
            _make_ticket_row(id=1, status="triage"),
            _make_ticket_row(id=2, status="triage"),
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.board_view(conn, SCHEMA, status_filter="triage")

        assert result.total == 2
        # Verify the SQL included the status filter
        executed_sql = cur.execute.call_args[0][0]
        assert "status = %s" in executed_sql

    def test_combined_type_and_status_filter(self):
        rows = [
            _make_ticket_row(id=1, status="triage", type="bug"),
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.board_view(
            conn, SCHEMA, type_filter="bug", status_filter="triage"
        )

        assert result.total == 1
        executed_sql = cur.execute.call_args[0][0]
        assert "type = %s" in executed_sql
        assert "status = %s" in executed_sql

    def test_detail_view_aliases_to_kanban(self):
        rows = [
            _make_ticket_row(id=1, status="backlog", description="A" * 500),
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.board_view(conn, SCHEMA, view="detail")

        # detail is not "summary", so it uses kanban path with truncation
        assert result.total == 1
        ticket = result.columns[0].tickets[0]
        assert len(ticket.description) == 203


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


# --------------------------------------------------------------------------- #
# Search tests                                                                 #
# --------------------------------------------------------------------------- #


class TestSearchTickets:
    """Tests for search_tickets full-text search behavior.

    These tests verify:
    - Multi-word queries use OR logic (partial matches returned)
    - Stemming is applied (e.g. "verification" matches "verify")
    - Results are ranked by relevance via ts_rank
    - Type and status filters are applied correctly
    """

    def setup_method(self):
        self.service = TicketService()

    def test_should_use_or_logic_for_multi_word_queries(self):
        """Multi-word queries should use OR (|) between terms, not AND (&).

        With AND logic, "dogfood test verification" requires all 3 terms.
        With OR logic, documents matching any subset are returned.
        """
        rows = [
            {**_make_ticket_row(id=1, title="Dogfood test results"), "rank": 0.8},
            {**_make_ticket_row(id=2, title="Test plan for release"), "rank": 0.4},
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.search_tickets(conn, SCHEMA, "dogfood test verification")

        assert result.total == 2
        assert len(result.tickets) == 2

        # Verify the SQL uses OR-based tsquery, not plainto_tsquery (which uses AND)
        executed_sql = cur.execute.call_args[0][0]
        assert "plainto_tsquery" not in executed_sql, (
            "plainto_tsquery uses AND logic; should use OR-based tsquery"
        )

    def test_should_apply_english_stemming_config(self):
        """Search should use 'english' text search config for stemming.

        This means "verification" -> stem "verifi" matches "verify" -> stem "verifi".
        """
        rows = [
            {**_make_ticket_row(id=1, title="Verify the deployment"), "rank": 0.6},
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.search_tickets(conn, SCHEMA, "verification")

        # Verify 'english' config is used in the tsquery
        executed_sql = cur.execute.call_args[0][0]
        assert "'english'" in executed_sql

    def test_should_rank_results_by_relevance(self):
        """Results should be ordered by ts_rank descending."""
        rows = [
            {**_make_ticket_row(id=1, title="Best match"), "rank": 0.9},
            {**_make_ticket_row(id=2, title="Partial match"), "rank": 0.3},
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.search_tickets(conn, SCHEMA, "match test")

        assert result.total == 2
        # Verify ORDER BY rank DESC is in the SQL
        executed_sql = cur.execute.call_args[0][0]
        assert "rank DESC" in executed_sql

    def test_should_apply_type_filter_with_search(self):
        """Type filter should be combined with search query."""
        rows = [
            {**_make_ticket_row(id=1, type="bug", title="Bug match"), "rank": 0.5},
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.search_tickets(
            conn, SCHEMA, "match", type_filter="bug"
        )

        assert result.total == 1
        executed_sql = cur.execute.call_args[0][0]
        assert "type = %s" in executed_sql

    def test_should_apply_status_filter_with_search(self):
        """Status filter should be combined with search query."""
        rows = [
            {**_make_ticket_row(id=1, status="backlog", title="Backlog match"), "rank": 0.5},
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.search_tickets(
            conn, SCHEMA, "match", status_filter="backlog"
        )

        assert result.total == 1
        executed_sql = cur.execute.call_args[0][0]
        assert "status = %s" in executed_sql

    def test_should_respect_limit_parameter(self):
        """The limit parameter should be passed to the SQL query."""
        rows = []
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        self.service.search_tickets(conn, SCHEMA, "anything", limit=5)

        # Verify limit is passed as param (last param in the list)
        executed_params = cur.execute.call_args[0][1]
        assert 5 in executed_params

    def test_should_return_empty_results_for_no_matches(self):
        """When no rows match, return empty SearchResult."""
        conn, cur = _mock_conn_and_cursor(rows=[])
        cur.fetchall.return_value = []

        result = self.service.search_tickets(conn, SCHEMA, "nonexistent")

        assert result.total == 0
        assert result.tickets == []
        assert result.query == "nonexistent"

    def test_should_build_or_tsquery_from_multi_word_input(self):
        """Each word in the query should be joined with | (OR) in the tsquery.

        For "dogfood test verification":
        - Should produce something like: to_tsquery('english', 'dogfood | test | verification')
        - NOT: plainto_tsquery('english', 'dogfood test verification') which uses AND
        """
        rows = []
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        self.service.search_tickets(conn, SCHEMA, "dogfood test verification")

        # Verify the query parameter contains OR-joined terms
        executed_params = cur.execute.call_args[0][1]
        # The tsquery string param should contain '|' separators
        tsquery_param = executed_params[0]  # First param is the tsquery string
        assert "|" in tsquery_param, (
            f"Expected OR-joined terms with '|', got: {tsquery_param}"
        )

    def test_should_handle_single_word_query(self):
        """Single-word queries should work without any OR joining."""
        rows = [
            {**_make_ticket_row(id=1, title="Dogfood session"), "rank": 0.7},
        ]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        result = self.service.search_tickets(conn, SCHEMA, "dogfood")

        assert result.total == 1
        assert result.query == "dogfood"

    def test_should_strip_extra_whitespace_from_query_terms(self):
        """Extra whitespace in query should be handled gracefully."""
        rows = []
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchall.return_value = rows

        self.service.search_tickets(conn, SCHEMA, "  dogfood   test  ")

        executed_params = cur.execute.call_args[0][1]
        tsquery_param = executed_params[0]
        # Should not have empty terms or extra spaces in the tsquery
        assert "  " not in tsquery_param
        assert "| |" not in tsquery_param


# --------------------------------------------------------------------------- #
# List tickets search filter tests                                             #
# --------------------------------------------------------------------------- #


class TestListTicketsSearchFilter:
    """Tests for the search filter in list_tickets (uses same OR logic)."""

    def setup_method(self):
        self.service = TicketService()

    def test_should_use_or_logic_for_list_search_filter(self):
        """list_tickets with search filter should also use OR logic."""
        rows = [_make_ticket_row(id=1)]
        conn, cur = _mock_conn_and_cursor(rows=rows)
        cur.fetchone.side_effect = [{"count": 1}]
        cur.fetchall.side_effect = [
            rows,
            [{"status": "backlog", "count": 1}],
            [{"type": "task", "count": 1}],
        ]

        filters = TicketListFilters(search="dogfood test verification")
        result = self.service.list_tickets(conn, SCHEMA, filters)

        # Verify the search clause uses OR-based tsquery, not plainto_tsquery
        first_execute_sql = cur.execute.call_args_list[0][0][0]
        assert "plainto_tsquery" not in first_execute_sql, (
            "list_tickets search should also use OR-based tsquery"
        )


# --------------------------------------------------------------------------- #
# Consistency: history/links always arrays, never None (#16)                   #
# --------------------------------------------------------------------------- #


class TestHistoryLinksConsistency:
    """Verify that history and links are always lists, never None.

    Bug #16: create_ticket and transition_ticket returned history=None
    and links=None, while get_ticket returned history=[] and links=[].
    All actions must return consistent list types.
    """

    def setup_method(self):
        self.service = TicketService()

    @patch("stompy_ticketing.service.time")
    def test_create_ticket_returns_empty_history_list(self, mock_time):
        """create_ticket response must have history=[], not None."""
        mock_time.time.return_value = FIXED_TIME
        row = _make_ticket_row(title="New task", status="backlog")
        conn, cur = _mock_conn_and_cursor(fetchone_value=row)

        data = TicketCreate(title="New task", type=TicketType.task)
        result = self.service.create_ticket(conn, SCHEMA, data)

        assert result.history is not None, "history should not be None"
        assert result.history == []

    @patch("stompy_ticketing.service.time")
    def test_create_ticket_returns_empty_links_list(self, mock_time):
        """create_ticket response must have links=[], not None."""
        mock_time.time.return_value = FIXED_TIME
        row = _make_ticket_row(title="New task", status="backlog")
        conn, cur = _mock_conn_and_cursor(fetchone_value=row)

        data = TicketCreate(title="New task", type=TicketType.task)
        result = self.service.create_ticket(conn, SCHEMA, data)

        assert result.links is not None, "links should not be None"
        assert result.links == []

    @patch("stompy_ticketing.service.time")
    def test_transition_ticket_returns_empty_history_list(self, mock_time):
        """transition_ticket (move) response must have history=[], not None."""
        mock_time.time.return_value = FIXED_TIME
        current = _make_ticket_row(status="backlog", type="task")
        updated = _make_ticket_row(status="in_progress", type="task")
        conn, cur = _mock_conn_and_cursor()
        cur.fetchone.side_effect = [current, updated]

        result = self.service.transition_ticket(conn, SCHEMA, 1, "in_progress")

        assert result.history is not None, "history should not be None"
        assert result.history == []

    @patch("stompy_ticketing.service.time")
    def test_transition_ticket_returns_empty_links_list(self, mock_time):
        """transition_ticket (move) response must have links=[], not None."""
        mock_time.time.return_value = FIXED_TIME
        current = _make_ticket_row(status="backlog", type="task")
        updated = _make_ticket_row(status="in_progress", type="task")
        conn, cur = _mock_conn_and_cursor()
        cur.fetchone.side_effect = [current, updated]

        result = self.service.transition_ticket(conn, SCHEMA, 1, "in_progress")

        assert result.links is not None, "links should not be None"
        assert result.links == []

    @patch("stompy_ticketing.service.time")
    def test_update_ticket_returns_empty_history_list(self, mock_time):
        """update_ticket response must have history=[], not None."""
        mock_time.time.return_value = FIXED_TIME
        current = _make_ticket_row(title="Old title")
        updated = _make_ticket_row(title="New title")
        conn, cur = _mock_conn_and_cursor()
        cur.fetchone.side_effect = [current, updated]

        data = TicketUpdate(title="New title")
        result = self.service.update_ticket(conn, SCHEMA, 1, data, changed_by="user")

        assert result.history is not None, "history should not be None"
        assert result.history == []

    @patch("stompy_ticketing.service.time")
    def test_update_ticket_returns_empty_links_list(self, mock_time):
        """update_ticket response must have links=[], not None."""
        mock_time.time.return_value = FIXED_TIME
        current = _make_ticket_row(title="Old title")
        updated = _make_ticket_row(title="New title")
        conn, cur = _mock_conn_and_cursor()
        cur.fetchone.side_effect = [current, updated]

        data = TicketUpdate(title="New title")
        result = self.service.update_ticket(conn, SCHEMA, 1, data, changed_by="user")

        assert result.links is not None, "links should not be None"
        assert result.links == []

    def test_row_to_response_defaults_history_to_empty_list(self):
        """_row_to_response should produce a TicketResponse with history=[]."""
        row = _make_ticket_row()
        result = self.service._row_to_response(row)

        assert result.history is not None, "history should not be None"
        assert result.history == []

    def test_row_to_response_defaults_links_to_empty_list(self):
        """_row_to_response should produce a TicketResponse with links=[]."""
        row = _make_ticket_row()
        result = self.service._row_to_response(row)

        assert result.links is not None, "links should not be None"
        assert result.links == []

    def test_get_ticket_still_returns_empty_lists(self):
        """get_ticket must continue to return history=[] and links=[] (regression)."""
        row = _make_ticket_row()
        conn, cur = _mock_conn_and_cursor(fetchone_value=row)
        cur.fetchall.return_value = []

        result = self.service.get_ticket(conn, SCHEMA, 1)

        assert result is not None
        assert result.history == []
        assert result.links == []
