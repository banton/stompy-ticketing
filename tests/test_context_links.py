"""Tests for ticket↔context linking feature.

TDD: These tests use a mock DB to verify service logic without PostgreSQL.
All test data is fixed and deterministic.
"""

import json
from unittest.mock import MagicMock, call, patch

import pytest

from stompy_ticketing.models import (
    ContextLinkCreate,
    ContextLinkType,
)
from stompy_ticketing.service import TicketService


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #

FIXED_TIME = 1700000000.0
SCHEMA = "test_project"


def _mock_conn_and_cursor():
    """Return (conn, cursor) mocks wired together."""
    cursor = MagicMock()
    cursor.__iter__ = MagicMock(return_value=iter([]))
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _make_context_link_row(
    id=1,
    ticket_id=1,
    context_label="auth_rules",
    context_version="latest",
    link_type="implements",
    created_at=FIXED_TIME,
):
    """Create a mock ticket_context_links row dict."""
    return {
        "id": id,
        "ticket_id": ticket_id,
        "context_label": context_label,
        "context_version": context_version,
        "link_type": link_type,
        "created_at": created_at,
    }


def _make_ticket_row(id=1, title="Fix auth", status="in_progress", type="task"):
    """Minimal ticket row for JOIN results."""
    return {"id": id, "title": title, "status": status, "type": type}


# --------------------------------------------------------------------------- #
# add_context_link                                                             #
# --------------------------------------------------------------------------- #


class TestAddContextLink:
    def test_should_insert_row_and_return_response(self):
        conn, cursor = _mock_conn_and_cursor()
        link_row = _make_context_link_row()
        ticket_row = _make_ticket_row()
        cursor.fetchone.side_effect = [link_row, ticket_row]

        svc = TicketService()
        data = ContextLinkCreate(
            context_label="auth_rules",
            context_version="latest",
            link_type=ContextLinkType.implements,
        )
        result = svc.add_context_link(conn, SCHEMA, ticket_id=1, data=data)

        assert result.id == 1
        assert result.ticket_id == 1
        assert result.context_label == "auth_rules"
        assert result.context_version == "latest"
        assert result.link_type == "implements"
        assert result.created_at == FIXED_TIME
        assert result.ticket_title == "Fix auth"
        assert result.ticket_status == "in_progress"
        conn.commit.assert_called_once()

    def test_should_rollback_on_error(self):
        conn, cursor = _mock_conn_and_cursor()
        cursor.execute.side_effect = Exception("DB error")

        svc = TicketService()
        data = ContextLinkCreate(context_label="auth_rules")

        with pytest.raises(Exception, match="DB error"):
            svc.add_context_link(conn, SCHEMA, ticket_id=1, data=data)

        conn.rollback.assert_called_once()

    def test_should_use_related_as_default_link_type(self):
        conn, cursor = _mock_conn_and_cursor()
        link_row = _make_context_link_row(link_type="related")
        ticket_row = _make_ticket_row()
        cursor.fetchone.side_effect = [link_row, ticket_row]

        svc = TicketService()
        data = ContextLinkCreate(context_label="api_spec")
        result = svc.add_context_link(conn, SCHEMA, ticket_id=2, data=data)

        assert result.link_type == "related"

    def test_should_handle_missing_ticket_gracefully(self):
        conn, cursor = _mock_conn_and_cursor()
        link_row = _make_context_link_row()
        cursor.fetchone.side_effect = [link_row, None]

        svc = TicketService()
        data = ContextLinkCreate(context_label="auth_rules")
        result = svc.add_context_link(conn, SCHEMA, ticket_id=1, data=data)

        assert result.ticket_title is None
        assert result.ticket_status is None


# --------------------------------------------------------------------------- #
# remove_context_link                                                          #
# --------------------------------------------------------------------------- #


class TestRemoveContextLink:
    def test_should_return_true_when_link_exists(self):
        conn, cursor = _mock_conn_and_cursor()
        cursor.fetchone.return_value = {"id": 5}

        svc = TicketService()
        result = svc.remove_context_link(conn, SCHEMA, link_id=5)

        assert result is True
        conn.commit.assert_called_once()

    def test_should_return_false_when_link_not_found(self):
        conn, cursor = _mock_conn_and_cursor()
        cursor.fetchone.return_value = None

        svc = TicketService()
        result = svc.remove_context_link(conn, SCHEMA, link_id=999)

        assert result is False
        conn.commit.assert_called_once()

    def test_should_rollback_on_error(self):
        conn, cursor = _mock_conn_and_cursor()
        cursor.execute.side_effect = Exception("DB error")

        svc = TicketService()
        with pytest.raises(Exception, match="DB error"):
            svc.remove_context_link(conn, SCHEMA, link_id=1)

        conn.rollback.assert_called_once()


# --------------------------------------------------------------------------- #
# list_context_links_for_ticket                                               #
# --------------------------------------------------------------------------- #


class TestListContextLinksForTicket:
    def test_should_return_empty_list_when_no_links(self):
        conn, cursor = _mock_conn_and_cursor()
        cursor.fetchall.return_value = []

        svc = TicketService()
        result = svc.list_context_links_for_ticket(conn, SCHEMA, ticket_id=1)

        assert result == []

    def test_should_return_all_links_for_ticket(self):
        conn, cursor = _mock_conn_and_cursor()
        cursor.fetchall.return_value = [
            {**_make_context_link_row(id=1, context_label="auth_rules", link_type="implements"),
             "ticket_title": "Fix auth", "ticket_status": "in_progress"},
            {**_make_context_link_row(id=2, context_label="api_spec", link_type="references"),
             "ticket_title": "Fix auth", "ticket_status": "in_progress"},
        ]

        svc = TicketService()
        result = svc.list_context_links_for_ticket(conn, SCHEMA, ticket_id=1)

        assert len(result) == 2
        assert result[0].context_label == "auth_rules"
        assert result[0].link_type == "implements"
        assert result[1].context_label == "api_spec"
        assert result[1].link_type == "references"

    def test_should_populate_ticket_title_and_status(self):
        conn, cursor = _mock_conn_and_cursor()
        cursor.fetchall.return_value = [
            {**_make_context_link_row(), "ticket_title": "Fix auth bug", "ticket_status": "confirmed"},
        ]

        svc = TicketService()
        result = svc.list_context_links_for_ticket(conn, SCHEMA, ticket_id=1)

        assert result[0].ticket_title == "Fix auth bug"
        assert result[0].ticket_status == "confirmed"


# --------------------------------------------------------------------------- #
# list_tickets_for_context                                                     #
# --------------------------------------------------------------------------- #


class TestListTicketsForContext:
    def test_should_return_empty_list_when_no_tickets(self):
        conn, cursor = _mock_conn_and_cursor()
        cursor.fetchall.return_value = []

        svc = TicketService()
        result = svc.list_tickets_for_context(conn, SCHEMA, context_label="auth_rules")

        assert result == []

    def test_should_return_tickets_linked_to_context(self):
        conn, cursor = _mock_conn_and_cursor()
        cursor.fetchall.return_value = [
            {**_make_context_link_row(id=1, ticket_id=5, link_type="implements"),
             "ticket_title": "Add JWT", "ticket_status": "in_progress"},
            {**_make_context_link_row(id=2, ticket_id=8, link_type="updates"),
             "ticket_title": "Rotate keys", "ticket_status": "backlog"},
        ]

        svc = TicketService()
        result = svc.list_tickets_for_context(conn, SCHEMA, context_label="auth_rules")

        assert len(result) == 2
        assert result[0].ticket_id == 5
        assert result[0].link_type == "implements"
        assert result[0].ticket_title == "Add JWT"
        assert result[1].ticket_id == 8
        assert result[1].link_type == "updates"


# --------------------------------------------------------------------------- #
# Model validation                                                             #
# --------------------------------------------------------------------------- #


class TestContextLinkModels:
    def test_context_link_type_values(self):
        assert ContextLinkType.implements == "implements"
        assert ContextLinkType.references == "references"
        assert ContextLinkType.updates == "updates"
        assert ContextLinkType.related == "related"

    def test_context_link_create_defaults(self):
        data = ContextLinkCreate(context_label="auth_rules")
        assert data.context_version == "latest"
        assert data.link_type == ContextLinkType.related

    def test_context_link_create_custom_values(self):
        data = ContextLinkCreate(
            context_label="auth_rules",
            context_version="1.2",
            link_type=ContextLinkType.implements,
        )
        assert data.context_label == "auth_rules"
        assert data.context_version == "1.2"
        assert data.link_type == ContextLinkType.implements
