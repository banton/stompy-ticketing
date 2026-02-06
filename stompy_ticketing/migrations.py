"""Migration definitions for stompy-ticketing.

These follow the exact format from dementia-production/src/migrations/definitions.py.
When integrated into Stompy, these are appended to the MIGRATIONS list.

Migration type: CREATE_TABLE is used because the existing CUSTOM handler
skips if the table doesn't exist (line 409 of migration_runner.py).
For V1, we use "custom" type with raw SQL operations.
"""

from typing import Any, Dict, List

# Migration type constants (matching dementia-production)
ADD_COLUMN = "add_column"
ADD_INDEX = "add_index"
CUSTOM = "custom"

# Schema type constants
PROJECT_SCHEMA = "project"


def get_ticket_migrations(start_id: int = 26) -> List[Dict[str, Any]]:
    """Get migration definitions for ticket tables.

    Args:
        start_id: Starting migration ID. Default 26 (after last Stompy migration 25).

    Returns:
        List of migration dictionaries.
    """
    return [
        # Migration N: Create tickets table
        {
            "id": start_id,
            "description": "create_tickets_table",
            "type": CUSTOM,
            "table": "tickets",
            "schema": PROJECT_SCHEMA,
            "spec": {
                "create_if_not_exists": True,
                "sql": """
                    CREATE TABLE IF NOT EXISTS {schema}.tickets (
                        id SERIAL PRIMARY KEY,
                        session_id TEXT,
                        title TEXT NOT NULL,
                        description TEXT,
                        type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        priority TEXT DEFAULT 'medium',
                        assignee TEXT,
                        tags TEXT,
                        metadata TEXT,
                        created_at DOUBLE PRECISION,
                        updated_at DOUBLE PRECISION,
                        closed_at DOUBLE PRECISION,
                        content_hash TEXT,
                        content_tsvector tsvector
                    )
                """,
            },
        },
        # Migration N+1: Create ticket_history table
        {
            "id": start_id + 1,
            "description": "create_ticket_history_table",
            "type": CUSTOM,
            "table": "ticket_history",
            "schema": PROJECT_SCHEMA,
            "spec": {
                "create_if_not_exists": True,
                "sql": """
                    CREATE TABLE IF NOT EXISTS {schema}.ticket_history (
                        id SERIAL PRIMARY KEY,
                        ticket_id INTEGER NOT NULL,
                        field_name TEXT NOT NULL,
                        old_value TEXT,
                        new_value TEXT,
                        changed_by TEXT,
                        changed_at DOUBLE PRECISION,
                        FOREIGN KEY (ticket_id) REFERENCES {schema}.tickets(id) ON DELETE CASCADE
                    )
                """,
            },
        },
        # Migration N+2: Create ticket_links table
        {
            "id": start_id + 2,
            "description": "create_ticket_links_table",
            "type": CUSTOM,
            "table": "ticket_links",
            "schema": PROJECT_SCHEMA,
            "spec": {
                "create_if_not_exists": True,
                "sql": """
                    CREATE TABLE IF NOT EXISTS {schema}.ticket_links (
                        id SERIAL PRIMARY KEY,
                        source_id INTEGER NOT NULL,
                        target_id INTEGER NOT NULL,
                        link_type TEXT NOT NULL,
                        created_at DOUBLE PRECISION,
                        FOREIGN KEY (source_id) REFERENCES {schema}.tickets(id) ON DELETE CASCADE,
                        FOREIGN KEY (target_id) REFERENCES {schema}.tickets(id) ON DELETE CASCADE,
                        UNIQUE(source_id, target_id, link_type)
                    )
                """,
            },
        },
        # Migration N+3: Add indexes for tickets
        {
            "id": start_id + 3,
            "description": "add_tickets_indexes",
            "type": CUSTOM,
            "table": "tickets",
            "schema": PROJECT_SCHEMA,
            "spec": {
                "operations": [
                    {
                        "type": ADD_INDEX,
                        "index_name": "idx_tickets_type",
                        "columns": ["type"],
                    },
                    {
                        "type": ADD_INDEX,
                        "index_name": "idx_tickets_status",
                        "columns": ["status"],
                    },
                    {
                        "type": ADD_INDEX,
                        "index_name": "idx_tickets_priority",
                        "columns": ["priority"],
                    },
                    {
                        "type": ADD_INDEX,
                        "index_name": "idx_tickets_tsvector",
                        "index_type": "gin",
                        "columns": ["content_tsvector"],
                        "where": "content_tsvector IS NOT NULL",
                    },
                ],
            },
        },
        # Migration N+4: Add tsvector trigger for tickets
        {
            "id": start_id + 4,
            "description": "add_tickets_tsvector_trigger",
            "type": CUSTOM,
            "table": "tickets",
            "schema": PROJECT_SCHEMA,
            "spec": {
                "sql": """
                    CREATE OR REPLACE FUNCTION {schema}.update_tickets_tsvector()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        NEW.content_tsvector := to_tsvector(
                            'english',
                            coalesce(NEW.title, '') || ' ' || coalesce(NEW.description, '')
                        );
                        RETURN NEW;
                    END;
                    $$ LANGUAGE plpgsql;

                    DROP TRIGGER IF EXISTS tickets_tsvector_update ON {schema}.tickets;
                    CREATE TRIGGER tickets_tsvector_update
                        BEFORE INSERT OR UPDATE OF title, description ON {schema}.tickets
                        FOR EACH ROW EXECUTE FUNCTION {schema}.update_tickets_tsvector();
                """,
            },
        },
    ]
