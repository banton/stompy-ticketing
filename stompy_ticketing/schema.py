"""Database DDL functions for stompy-ticketing.

Follows the pattern from dementia-production/src/schema_definitions.py:
- Functions returning SQL strings with {schema} placeholders
- TEXT for JSON strings (tags, metadata), DOUBLE PRECISION timestamps
- SERIAL primary keys, session_id FK
"""


def get_tickets_table_sql(schema: str) -> str:
    """Tickets table DDL for the given schema."""
    return f"""
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
            content_tsvector tsvector,
            FOREIGN KEY (session_id) REFERENCES {schema}.sessions(id)
        );
    """


def get_ticket_history_table_sql(schema: str) -> str:
    """Ticket history (audit trail) table DDL for the given schema."""
    return f"""
        CREATE TABLE IF NOT EXISTS {schema}.ticket_history (
            id SERIAL PRIMARY KEY,
            ticket_id INTEGER NOT NULL,
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_by TEXT,
            changed_at DOUBLE PRECISION,
            FOREIGN KEY (ticket_id) REFERENCES {schema}.tickets(id) ON DELETE CASCADE
        );
    """


def get_ticket_links_table_sql(schema: str) -> str:
    """Ticket links (relationships) table DDL for the given schema."""
    return f"""
        CREATE TABLE IF NOT EXISTS {schema}.ticket_links (
            id SERIAL PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            link_type TEXT NOT NULL,
            created_at DOUBLE PRECISION,
            FOREIGN KEY (source_id) REFERENCES {schema}.tickets(id) ON DELETE CASCADE,
            FOREIGN KEY (target_id) REFERENCES {schema}.tickets(id) ON DELETE CASCADE,
            UNIQUE(source_id, target_id, link_type)
        );
    """


def get_tickets_indexes_sql(schema: str) -> str:
    """Indexes for ticket tables in the given schema."""
    return f"""
        CREATE INDEX IF NOT EXISTS idx_{schema}_tickets_type
            ON {schema}.tickets(type);
        CREATE INDEX IF NOT EXISTS idx_{schema}_tickets_status
            ON {schema}.tickets(status);
        CREATE INDEX IF NOT EXISTS idx_{schema}_tickets_priority
            ON {schema}.tickets(priority);
        CREATE INDEX IF NOT EXISTS idx_{schema}_tickets_assignee
            ON {schema}.tickets(assignee)
            WHERE assignee IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_{schema}_tickets_session
            ON {schema}.tickets(session_id);
        CREATE INDEX IF NOT EXISTS idx_{schema}_tickets_tsvector
            ON {schema}.tickets
            USING gin (content_tsvector)
            WHERE content_tsvector IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_{schema}_ticket_history_ticket
            ON {schema}.ticket_history(ticket_id);
        CREATE INDEX IF NOT EXISTS idx_{schema}_ticket_links_source
            ON {schema}.ticket_links(source_id);
        CREATE INDEX IF NOT EXISTS idx_{schema}_ticket_links_target
            ON {schema}.ticket_links(target_id);
    """


def get_tickets_tsvector_trigger_sql(schema: str) -> str:
    """Trigger DDL to auto-populate content_tsvector on tickets.

    Creates a BEFORE INSERT OR UPDATE trigger that sets
    content_tsvector = to_tsvector('english', title || ' ' || coalesce(description, '')).
    """
    return f"""
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
    """


def get_all_ticket_tables_sql(schema: str) -> str:
    """All ticket tables for the given schema."""
    return (
        get_tickets_table_sql(schema)
        + get_ticket_history_table_sql(schema)
        + get_ticket_links_table_sql(schema)
        + get_tickets_indexes_sql(schema)
        + get_tickets_tsvector_trigger_sql(schema)
    )
