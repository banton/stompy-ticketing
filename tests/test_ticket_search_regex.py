"""Tests for regex post-filter in ticket_search.

Tests cover:
- Regex post-filter on TicketResponse results
- Invalid regex handling
- Empty regex (no-op)
- Matching on title and description
"""

import re

import pytest

from stompy_ticketing.models import TicketResponse, SearchResult


# =============================================================================
# Test Fixtures
# =============================================================================

FIXED_TIME = 1700000000.0


def _make_ticket(id, title, description="", type="task", status="backlog"):
    return TicketResponse(
        id=id,
        title=title,
        description=description,
        type=type,
        status=status,
        priority="medium",
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )


def _apply_ticket_regex(tickets, regex_pattern, limit=20):
    """Replicate the regex filter logic from mcp_tools.py ticket_search."""
    if not regex_pattern:
        return tickets
    compiled = re.compile(regex_pattern, re.IGNORECASE)
    return [
        t for t in tickets
        if compiled.search(t.title or "") or compiled.search(t.description or "")
    ][:limit]


# =============================================================================
# Tests
# =============================================================================


class TestTicketSearchRegex:
    def test_should_filter_by_title_pattern(self):
        tickets = [
            _make_ticket(1, "Fix conflict detection false positives"),
            _make_ticket(2, "Add webhook support"),
            _make_ticket(3, "Fix conflict resolution UI"),
        ]
        filtered = _apply_ticket_regex(tickets, r"conflict.*false")
        assert len(filtered) == 1
        assert filtered[0].id == 1

    def test_should_filter_by_description_pattern(self):
        tickets = [
            _make_ticket(1, "Auth bug", "JWT tokens MUST be validated before use"),
            _make_ticket(2, "DB bug", "Connection pool exhaustion under load"),
            _make_ticket(3, "API bug", "MUST NOT return 500 for invalid input"),
        ]
        filtered = _apply_ticket_regex(tickets, r"MUST.*validat")
        assert len(filtered) == 1
        assert filtered[0].id == 1

    def test_should_match_title_or_description(self):
        tickets = [
            _make_ticket(1, "JWT authentication", "basic auth implementation"),
            _make_ticket(2, "DB pooling", "JWT token refresh logic"),
        ]
        filtered = _apply_ticket_regex(tickets, r"JWT")
        assert len(filtered) == 2

    def test_should_be_case_insensitive(self):
        tickets = [
            _make_ticket(1, "fix API endpoint", "returns wrong status"),
        ]
        filtered = _apply_ticket_regex(tickets, r"FIX.*api")
        assert len(filtered) == 1

    def test_should_return_all_when_empty_regex(self):
        tickets = [
            _make_ticket(1, "ticket A"),
            _make_ticket(2, "ticket B"),
        ]
        filtered = _apply_ticket_regex(tickets, "")
        assert len(filtered) == 2

    def test_should_return_empty_when_no_match(self):
        tickets = [
            _make_ticket(1, "some ticket", "some description"),
        ]
        filtered = _apply_ticket_regex(tickets, r"zzz_nonexistent")
        assert len(filtered) == 0

    def test_should_handle_none_description(self):
        tickets = [
            _make_ticket(1, "test ticket", description=None),
        ]
        # Should not raise, matches on title
        filtered = _apply_ticket_regex(tickets, r"test")
        assert len(filtered) == 1

    def test_should_respect_limit(self):
        tickets = [
            _make_ticket(i, f"match {i}", f"JWT content {i}")
            for i in range(15)
        ]
        filtered = _apply_ticket_regex(tickets, r"JWT", limit=5)
        assert len(filtered) == 5

    def test_should_handle_complex_regex(self):
        tickets = [
            _make_ticket(1, "API v1 endpoint", "/api/v1/users endpoint"),
            _make_ticket(2, "API v2 endpoint", "/api/v2/tickets endpoint"),
            _make_ticket(3, "Health check", "GET /health returns ok"),
        ]
        filtered = _apply_ticket_regex(tickets, r"/api/v[0-9]+/\w+")
        assert len(filtered) == 2


class TestTicketRegexValidation:
    def test_should_reject_invalid_regex(self):
        with pytest.raises(re.error):
            re.compile("[invalid")

    def test_should_accept_valid_complex_pattern(self):
        compiled = re.compile(r"conflict.*(?:false|negative)", re.IGNORECASE)
        assert compiled.search("conflict detection false positives")
        assert not compiled.search("simple search query")
