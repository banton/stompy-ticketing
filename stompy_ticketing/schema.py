"""Database DDL functions for stompy-ticketing.

Follows the pattern from dementia-production/src/schema_definitions.py:
- Functions returning psycopg2.sql.Composed objects with sql.Identifier for schema
- TEXT for JSON strings (tags, metadata), DOUBLE PRECISION timestamps
- SERIAL primary keys, session_id FK
"""

from psycopg2 import sql


def get_tickets_table_sql(schema: str) -> sql.Composed:
    """Tickets table DDL for the given schema."""
    sch = sql.Identifier(schema)
    return sql.SQL("""
        CREATE TABLE IF NOT EXISTS {}.tickets (
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
            archived_at DOUBLE PRECISION,
            content_hash TEXT,
            content_tsvector tsvector,
            FOREIGN KEY (session_id) REFERENCES {}.sessions(id)
        );
    """).format(sch, sch)


def get_ticket_history_table_sql(schema: str) -> sql.Composed:
    """Ticket history (audit trail) table DDL for the given schema."""
    sch = sql.Identifier(schema)
    return sql.SQL("""
        CREATE TABLE IF NOT EXISTS {}.ticket_history (
            id SERIAL PRIMARY KEY,
            ticket_id INTEGER NOT NULL,
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_by TEXT,
            changed_at DOUBLE PRECISION,
            FOREIGN KEY (ticket_id) REFERENCES {}.tickets(id) ON DELETE CASCADE
        );
    """).format(sch, sch)


def get_ticket_links_table_sql(schema: str) -> sql.Composed:
    """Ticket links (relationships) table DDL for the given schema."""
    sch = sql.Identifier(schema)
    return sql.SQL("""
        CREATE TABLE IF NOT EXISTS {}.ticket_links (
            id SERIAL PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            link_type TEXT NOT NULL,
            created_at DOUBLE PRECISION,
            FOREIGN KEY (source_id) REFERENCES {}.tickets(id) ON DELETE CASCADE,
            FOREIGN KEY (target_id) REFERENCES {}.tickets(id) ON DELETE CASCADE,
            UNIQUE(source_id, target_id, link_type)
        );
    """).format(sch, sch, sch)


def get_ticket_context_links_table_sql(schema: str) -> sql.Composed:
    """Ticket↔context links table DDL for the given schema."""
    sch = sql.Identifier(schema)
    return sql.SQL("""
        CREATE TABLE IF NOT EXISTS {}.ticket_context_links (
            id SERIAL PRIMARY KEY,
            ticket_id INTEGER NOT NULL,
            context_label TEXT NOT NULL,
            context_version TEXT NOT NULL DEFAULT 'latest',
            link_type TEXT NOT NULL DEFAULT 'related',
            created_at DOUBLE PRECISION,
            FOREIGN KEY (ticket_id) REFERENCES {}.tickets(id) ON DELETE CASCADE,
            UNIQUE(ticket_id, context_label, context_version)
        );
    """).format(sch, sch)


def get_ticket_context_links_indexes_sql(schema: str) -> sql.Composed:
    """Indexes for ticket_context_links in the given schema."""
    sch = sql.Identifier(schema)
    return sql.SQL("""
        CREATE INDEX IF NOT EXISTS {idx_context}
            ON {sch}.ticket_context_links(context_label);
        CREATE INDEX IF NOT EXISTS {idx_ticket}
            ON {sch}.ticket_context_links(ticket_id);
    """).format(
        sch=sch,
        idx_context=sql.Identifier(f"idx_{schema}_ticket_context_links_context"),
        idx_ticket=sql.Identifier(f"idx_{schema}_ticket_context_links_ticket"),
    )


def get_tickets_indexes_sql(schema: str) -> sql.Composed:
    """Indexes for ticket tables in the given schema."""
    sch = sql.Identifier(schema)
    return sql.SQL("""
        CREATE INDEX IF NOT EXISTS {idx_type}
            ON {sch}.tickets(type);
        CREATE INDEX IF NOT EXISTS {idx_status}
            ON {sch}.tickets(status);
        CREATE INDEX IF NOT EXISTS {idx_priority}
            ON {sch}.tickets(priority);
        CREATE INDEX IF NOT EXISTS {idx_assignee}
            ON {sch}.tickets(assignee)
            WHERE assignee IS NOT NULL;
        CREATE INDEX IF NOT EXISTS {idx_session}
            ON {sch}.tickets(session_id);
        CREATE INDEX IF NOT EXISTS {idx_tsvector}
            ON {sch}.tickets
            USING gin (content_tsvector)
            WHERE content_tsvector IS NOT NULL;
        CREATE INDEX IF NOT EXISTS {idx_history}
            ON {sch}.ticket_history(ticket_id);
        CREATE INDEX IF NOT EXISTS {idx_links_source}
            ON {sch}.ticket_links(source_id);
        CREATE INDEX IF NOT EXISTS {idx_links_target}
            ON {sch}.ticket_links(target_id);
    """).format(
        sch=sch,
        idx_type=sql.Identifier(f"idx_{schema}_tickets_type"),
        idx_status=sql.Identifier(f"idx_{schema}_tickets_status"),
        idx_priority=sql.Identifier(f"idx_{schema}_tickets_priority"),
        idx_assignee=sql.Identifier(f"idx_{schema}_tickets_assignee"),
        idx_session=sql.Identifier(f"idx_{schema}_tickets_session"),
        idx_tsvector=sql.Identifier(f"idx_{schema}_tickets_tsvector"),
        idx_history=sql.Identifier(f"idx_{schema}_ticket_history_ticket"),
        idx_links_source=sql.Identifier(f"idx_{schema}_ticket_links_source"),
        idx_links_target=sql.Identifier(f"idx_{schema}_ticket_links_target"),
    )


def get_tickets_tsvector_trigger_sql(schema: str) -> sql.Composed:
    """Trigger DDL to auto-populate content_tsvector on tickets.

    Creates a BEFORE INSERT OR UPDATE trigger that sets
    content_tsvector = to_tsvector('english', title || ' ' || coalesce(description, '')).
    """
    sch = sql.Identifier(schema)
    func_name = sql.Identifier(schema, "update_tickets_tsvector")
    return sql.SQL("""
        CREATE OR REPLACE FUNCTION {}()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.content_tsvector := to_tsvector(
                'english',
                coalesce(NEW.title, '') || ' ' || coalesce(NEW.description, '')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS tickets_tsvector_update ON {}.tickets;
        CREATE TRIGGER tickets_tsvector_update
            BEFORE INSERT OR UPDATE OF title, description ON {}.tickets
            FOR EACH ROW EXECUTE FUNCTION {}();
    """).format(func_name, sch, sch, func_name)


def get_all_ticket_tables_sql(schema: str) -> sql.Composed:
    """All ticket tables for the given schema."""
    return (
        get_tickets_table_sql(schema)
        + get_ticket_history_table_sql(schema)
        + get_ticket_links_table_sql(schema)
        + get_tickets_indexes_sql(schema)
        + get_tickets_tsvector_trigger_sql(schema)
    )
