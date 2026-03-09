"""Tests for schema DDL functions.

Verifies that DDL generation functions produce correct SQL.
"""

from psycopg2 import sql

from stompy_ticketing.schema import get_all_ticket_tables_sql


SCHEMA = "test_schema"


def _sql_to_string(composed) -> str:
    """Render a sql.Composed to a plain string.

    psycopg2 sql objects need a real connection for as_string().
    We walk the composed tree and extract raw strings plus identifier
    names to build a representative output.
    """
    parts = []
    for item in composed:
        if isinstance(item, sql.SQL):
            parts.append(item.string)
        elif isinstance(item, sql.Identifier):
            parts.append(".".join(item._wrapped))
        elif isinstance(item, sql.Composed):
            parts.append(_sql_to_string(item))
        else:
            parts.append(str(item))
    return "".join(parts)


def test_get_all_ticket_tables_sql_includes_context_links():
    """Verify get_all_ticket_tables_sql includes context links table and indexes."""
    result = get_all_ticket_tables_sql(SCHEMA)
    sql_str = _sql_to_string(result)
    assert "ticket_context_links" in sql_str
    assert f"idx_{SCHEMA}_ticket_context_links" in sql_str


def test_get_all_ticket_tables_sql_includes_core_tables():
    """Verify get_all_ticket_tables_sql includes all core tables."""
    result = get_all_ticket_tables_sql(SCHEMA)
    sql_str = _sql_to_string(result)
    assert "CREATE TABLE IF NOT EXISTS" in sql_str
    assert ".tickets" in sql_str
    assert ".ticket_history" in sql_str
    assert ".ticket_links" in sql_str
    assert ".ticket_context_links" in sql_str
