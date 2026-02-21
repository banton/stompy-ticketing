"""TicketService - business logic and state machine for stompy-ticketing.

Follows the ContextService pattern from dementia-production:
- Takes a DB connection/adapter as parameter (DI)
- Sync methods with psycopg2 RealDictCursor
- TEXT for JSON strings (tags, metadata), DOUBLE PRECISION timestamps
"""

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from psycopg2 import sql

from stompy_ticketing.models import (
    BoardColumn,
    BoardView,
    Priority,
    SearchResult,
    TicketCreate,
    TicketHistoryEntry,
    TicketLinkCreate,
    TicketLinkResponse,
    TicketListFilters,
    TicketListResponse,
    TicketResponse,
    TicketType,
    TicketUpdate,
)


# =========================================================================== #
# State Machine                                                               #
# =========================================================================== #


class InvalidTransitionError(Exception):
    """Raised when a ticket transition violates the state machine."""

    pass


# Each type maps to: initial status, terminal statuses, and allowed transitions
STATE_MACHINES: Dict[str, Dict[str, Any]] = {
    "task": {
        "initial": "backlog",
        "terminal": ["done", "cancelled"],
        "transitions": {
            "backlog": ["in_progress", "done", "cancelled"],
            "in_progress": ["done", "cancelled"],
            "done": [],
            "cancelled": [],
        },
    },
    "bug": {
        "initial": "triage",
        "terminal": ["resolved", "wont_fix"],
        "transitions": {
            "triage": ["confirmed", "wont_fix"],
            "confirmed": ["in_progress"],
            "in_progress": ["resolved", "wont_fix"],
            "resolved": [],
            "wont_fix": [],
        },
    },
    "feature": {
        "initial": "proposed",
        "terminal": ["shipped", "rejected"],
        "transitions": {
            "proposed": ["approved", "rejected"],
            "approved": ["in_progress"],
            "in_progress": ["shipped", "rejected"],
            "shipped": [],
            "rejected": [],
        },
    },
    "decision": {
        "initial": "open",
        "terminal": ["decided", "deferred"],
        "transitions": {
            "open": ["decided", "deferred"],
            "decided": [],
            "deferred": ["open"],  # Deferred decisions can be reopened
        },
    },
}


def get_initial_status(ticket_type: str) -> str:
    """Get the initial status for a ticket type."""
    if ticket_type not in STATE_MACHINES:
        raise ValueError(f"Unknown ticket type: {ticket_type}")
    return STATE_MACHINES[ticket_type]["initial"]


def get_terminal_statuses(ticket_type: str) -> List[str]:
    """Get terminal (closed) statuses for a ticket type."""
    if ticket_type not in STATE_MACHINES:
        raise ValueError(f"Unknown ticket type: {ticket_type}")
    return STATE_MACHINES[ticket_type]["terminal"]


def get_all_statuses(ticket_type: str) -> List[str]:
    """Get all valid statuses for a ticket type."""
    if ticket_type not in STATE_MACHINES:
        raise ValueError(f"Unknown ticket type: {ticket_type}")
    return list(STATE_MACHINES[ticket_type]["transitions"].keys())


def validate_transition(
    ticket_type: str,
    current_status: str,
    target_status: str,
    raise_on_invalid: bool = True,
) -> bool:
    """Validate a status transition against the state machine.

    Args:
        ticket_type: The ticket type (task, bug, feature, decision).
        current_status: Current status of the ticket.
        target_status: Desired target status.
        raise_on_invalid: If True, raise InvalidTransitionError; otherwise return False.

    Returns:
        True if transition is valid.

    Raises:
        InvalidTransitionError: If transition is invalid and raise_on_invalid=True.
        ValueError: If ticket_type is unknown.
    """
    if ticket_type not in STATE_MACHINES:
        raise ValueError(f"Unknown ticket type: {ticket_type}")

    sm = STATE_MACHINES[ticket_type]
    transitions = sm["transitions"]

    if current_status not in transitions:
        if raise_on_invalid:
            raise InvalidTransitionError(
                f"'{current_status}' is not a valid status for type '{ticket_type}'. "
                f"Valid statuses: {list(transitions.keys())}"
            )
        return False

    allowed = transitions[current_status]
    if target_status not in allowed:
        if raise_on_invalid:
            raise InvalidTransitionError(
                f"Cannot transition {ticket_type} from '{current_status}' to "
                f"'{target_status}'. Allowed: {allowed}"
            )
        return False

    return True


# =========================================================================== #
# Database Protocol                                                           #
# =========================================================================== #


class DBConnection(Protocol):
    """Protocol for database connections used by TicketService.

    This matches the psycopg2 connection interface.
    The caller is responsible for connection lifecycle.
    """

    def cursor(self, **kwargs) -> Any: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


# =========================================================================== #
# TicketService                                                               #
# =========================================================================== #


class TicketService:
    """Business logic for ticket operations.

    Takes a database connection and schema as parameters (dependency injection).
    The service does NOT manage connection lifecycle - the caller does.
    """

    def create_ticket(
        self,
        conn: DBConnection,
        schema: str,
        data: TicketCreate,
        changed_by: Optional[str] = None,
    ) -> TicketResponse:
        """Create a new ticket.

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name (project).
            data: Ticket creation data.
            changed_by: Who created the ticket.

        Returns:
            Created ticket response.
        """
        ticket_type = data.type.value
        initial_status = get_initial_status(ticket_type)
        now = time.time()

        tags_json = json.dumps(data.tags) if data.tags else None
        metadata_json = json.dumps(data.metadata) if data.metadata else None

        # Content hash for deduplication
        content = f"{data.title}|{data.description or ''}"
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        cur = conn.cursor()
        try:
            cur.execute(
                sql.SQL("""
                INSERT INTO {}.tickets
                    (title, description, type, status, priority, assignee,
                     tags, metadata, session_id, content_hash, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """).format(sql.Identifier(schema)),
                (
                    data.title,
                    data.description,
                    ticket_type,
                    initial_status,
                    data.priority.value,
                    data.assignee,
                    tags_json,
                    metadata_json,
                    data.session_id,
                    content_hash,
                    now,
                    now,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return self._row_to_response(row)
        except Exception:
            conn.rollback()
            raise

    def get_ticket(
        self,
        conn: DBConnection,
        schema: str,
        ticket_id: int,
        include_history: bool = True,
        include_links: bool = True,
    ) -> Optional[TicketResponse]:
        """Get a ticket by ID with optional history and links.

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name.
            ticket_id: Ticket ID.
            include_history: Include change history.
            include_links: Include ticket links.

        Returns:
            Ticket response or None if not found.
        """
        cur = conn.cursor()
        cur.execute(
            sql.SQL("SELECT * FROM {}.tickets WHERE id = %s").format(
                sql.Identifier(schema)
            ),
            (ticket_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        response = self._row_to_response(row)

        if include_history:
            cur.execute(
                sql.SQL("""
                SELECT * FROM {}.ticket_history
                WHERE ticket_id = %s ORDER BY changed_at DESC
                """).format(sql.Identifier(schema)),
                (ticket_id,),
            )
            response.history = [
                TicketHistoryEntry(
                    id=h["id"],
                    field_name=h["field_name"],
                    old_value=h["old_value"],
                    new_value=h["new_value"],
                    changed_by=h["changed_by"],
                    changed_at=h["changed_at"],
                )
                for h in cur.fetchall()
            ]

        if include_links:
            response.links = self._get_links_for_ticket(cur, schema, ticket_id)

        return response

    def update_ticket(
        self,
        conn: DBConnection,
        schema: str,
        ticket_id: int,
        data: TicketUpdate,
        changed_by: Optional[str] = None,
    ) -> Optional[TicketResponse]:
        """Update ticket fields (not status - use transition_ticket for that).

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name.
            ticket_id: Ticket ID.
            data: Fields to update.
            changed_by: Who made the change.

        Returns:
            Updated ticket or None if not found.
        """
        cur = conn.cursor()
        try:
            # Get current ticket
            cur.execute(
                sql.SQL("SELECT * FROM {}.tickets WHERE id = %s").format(
                    sql.Identifier(schema)
                ),
                (ticket_id,),
            )
            current = cur.fetchone()
            if not current:
                return None

            now = time.time()
            updates = {}
            history_entries = []

            # Check each updatable field
            if data.title is not None and data.title != current["title"]:
                updates["title"] = data.title
                history_entries.append(("title", current["title"], data.title))

            if data.description is not None and data.description != current.get("description"):
                updates["description"] = data.description
                history_entries.append(("description", current.get("description"), data.description))

            if data.priority is not None and data.priority.value != current["priority"]:
                updates["priority"] = data.priority.value
                history_entries.append(("priority", current["priority"], data.priority.value))

            if data.assignee is not None and data.assignee != current.get("assignee"):
                updates["assignee"] = data.assignee
                history_entries.append(("assignee", current.get("assignee"), data.assignee))

            if data.tags is not None:
                new_tags = json.dumps(data.tags)
                if new_tags != current.get("tags"):
                    updates["tags"] = new_tags
                    history_entries.append(("tags", current.get("tags"), new_tags))

            if data.metadata is not None:
                new_metadata = json.dumps(data.metadata)
                if new_metadata != current.get("metadata"):
                    updates["metadata"] = new_metadata
                    history_entries.append(("metadata", current.get("metadata"), new_metadata))

            if not updates:
                return self._row_to_response(current)

            # Build UPDATE query
            set_clauses = [f"{col} = %s" for col in updates]
            set_clauses.append("updated_at = %s")
            values = list(updates.values()) + [now, ticket_id]

            cur.execute(
                sql.SQL("""
                UPDATE {}.tickets
                SET {}
                WHERE id = %s
                RETURNING *
                """).format(
                    sql.Identifier(schema),
                    sql.SQL(', ').join(sql.SQL(c) for c in set_clauses),
                ),
                values,
            )
            updated_row = cur.fetchone()

            # Record history
            for field_name, old_val, new_val in history_entries:
                cur.execute(
                    sql.SQL("""
                    INSERT INTO {}.ticket_history
                        (ticket_id, field_name, old_value, new_value, changed_by, changed_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """).format(sql.Identifier(schema)),
                    (ticket_id, field_name, old_val, new_val, changed_by, now),
                )

            conn.commit()
            return self._row_to_response(updated_row)
        except Exception:
            conn.rollback()
            raise

    def transition_ticket(
        self,
        conn: DBConnection,
        schema: str,
        ticket_id: int,
        target_status: str,
        changed_by: Optional[str] = None,
    ) -> Optional[TicketResponse]:
        """Transition a ticket to a new status via the state machine.

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name.
            ticket_id: Ticket ID.
            target_status: Target status.
            changed_by: Who made the change.

        Returns:
            Updated ticket or None if not found.

        Raises:
            InvalidTransitionError: If transition is not allowed.
        """
        cur = conn.cursor()
        try:
            cur.execute(
                sql.SQL("SELECT * FROM {}.tickets WHERE id = %s").format(
                    sql.Identifier(schema)
                ),
                (ticket_id,),
            )
            current = cur.fetchone()
            if not current:
                return None

            ticket_type = current["type"]
            current_status = current["status"]

            # Validate transition
            validate_transition(ticket_type, current_status, target_status)

            now = time.time()
            closed_at = now if target_status in get_terminal_statuses(ticket_type) else None

            cur.execute(
                sql.SQL("""
                UPDATE {}.tickets
                SET status = %s, updated_at = %s, closed_at = %s
                WHERE id = %s
                RETURNING *
                """).format(sql.Identifier(schema)),
                (target_status, now, closed_at, ticket_id),
            )
            updated_row = cur.fetchone()

            # Record history
            cur.execute(
                sql.SQL("""
                INSERT INTO {}.ticket_history
                    (ticket_id, field_name, old_value, new_value, changed_by, changed_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """).format(sql.Identifier(schema)),
                (ticket_id, "status", current_status, target_status, changed_by, now),
            )

            conn.commit()
            return self._row_to_response(updated_row)
        except Exception:
            conn.rollback()
            raise

    def close_ticket(
        self,
        conn: DBConnection,
        schema: str,
        ticket_id: int,
        changed_by: Optional[str] = None,
    ) -> Optional[TicketResponse]:
        """Close a ticket by moving it to its first terminal status.

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name.
            ticket_id: Ticket ID.
            changed_by: Who closed it.

        Returns:
            Updated ticket or None if not found.
        """
        cur = conn.cursor()
        cur.execute(
            sql.SQL("SELECT type, status FROM {}.tickets WHERE id = %s").format(
                sql.Identifier(schema)
            ),
            (ticket_id,),
        )
        current = cur.fetchone()
        if not current:
            return None

        ticket_type = current["type"]
        current_status = current["status"]

        # Already closed?
        terminals = get_terminal_statuses(ticket_type)
        if current_status in terminals:
            return self.get_ticket(conn, schema, ticket_id, include_history=False, include_links=False)

        # Find the first reachable terminal status
        sm = STATE_MACHINES[ticket_type]
        allowed = sm["transitions"].get(current_status, [])
        for target in allowed:
            if target in terminals:
                return self.transition_ticket(conn, schema, ticket_id, target, changed_by)

        # Not directly reachable - try the default terminal
        raise InvalidTransitionError(
            f"Cannot close {ticket_type} from '{current_status}'. "
            f"No terminal status is directly reachable. "
            f"Allowed transitions: {allowed}"
        )

    def list_tickets(
        self,
        conn: DBConnection,
        schema: str,
        filters: Optional[TicketListFilters] = None,
    ) -> TicketListResponse:
        """List tickets with optional filters.

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name.
            filters: Filter/pagination options.

        Returns:
            List of tickets with counts.
        """
        if filters is None:
            filters = TicketListFilters()

        cur = conn.cursor()
        where_clauses: List[str] = []
        params: List[Any] = []

        if filters.type:
            where_clauses.append("type = %s")
            params.append(filters.type.value)

        if filters.status:
            where_clauses.append("status = %s")
            params.append(filters.status)

        if filters.priority:
            where_clauses.append("priority = %s")
            params.append(filters.priority.value)

        if filters.assignee:
            where_clauses.append("assignee = %s")
            params.append(filters.assignee)

        if filters.search:
            tsquery_param = self._build_or_tsquery_param(filters.search)
            where_clauses.append(
                "content_tsvector @@ to_tsquery('english', %s)"
            )
            params.append(tsquery_param)

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        # Get filtered tickets
        cur.execute(
            sql.SQL("""
            SELECT * FROM {}.tickets
            {}
            ORDER BY
                CASE priority
                    WHEN 'urgent' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                    ELSE 4
                END,
                updated_at DESC
            LIMIT %s OFFSET %s
            """).format(sql.Identifier(schema), sql.SQL(where_sql)),
            params + [filters.limit, filters.offset],
        )
        rows = cur.fetchall()

        # Get total count
        cur.execute(
            sql.SQL("SELECT COUNT(*) as count FROM {}.tickets {}").format(
                sql.Identifier(schema), sql.SQL(where_sql)
            ),
            params,
        )
        total = cur.fetchone()["count"]

        # Get counts by status
        cur.execute(
            sql.SQL(
                "SELECT status, COUNT(*) as count FROM {}.tickets {} GROUP BY status"
            ).format(sql.Identifier(schema), sql.SQL(where_sql)),
            params,
        )
        by_status = {r["status"]: r["count"] for r in cur.fetchall()}

        # Get counts by type
        cur.execute(
            sql.SQL(
                "SELECT type, COUNT(*) as count FROM {}.tickets {} GROUP BY type"
            ).format(sql.Identifier(schema), sql.SQL(where_sql)),
            params,
        )
        by_type = {r["type"]: r["count"] for r in cur.fetchall()}

        return TicketListResponse(
            tickets=[self._row_to_response(r) for r in rows],
            total=total,
            limit=filters.limit,
            offset=filters.offset,
            has_more=(filters.offset + filters.limit) < total,
            by_status=by_status,
            by_type=by_type,
        )

    @staticmethod
    def _build_or_tsquery_param(query: str) -> str:
        """Build an OR-joined tsquery parameter string from a free-text query.

        Splits the query into words, strips whitespace, removes empty tokens,
        and joins with ' | ' for OR semantics in to_tsquery('english', ...).

        This enables partial matching: "dogfood test verification" becomes
        "dogfood | test | verification", so documents matching ANY term are
        returned (ranked by how many terms match via ts_rank).

        Stemming is handled by to_tsquery('english', ...) at query time,
        e.g. "verification" -> stem "verifi" matches stored "verify" -> "verifi".

        Args:
            query: Raw search query string.

        Returns:
            OR-joined term string suitable for to_tsquery('english', ...).
        """
        # Split on whitespace, filter empty tokens
        terms = [t.strip() for t in query.split() if t.strip()]
        if not terms:
            return ""
        return " | ".join(terms)

    def search_tickets(
        self,
        conn: DBConnection,
        schema: str,
        query: str,
        type_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        limit: int = 20,
    ) -> SearchResult:
        """Full-text search tickets using tsvector with OR-based ranking.

        Uses OR logic between query terms so that partial matches are returned,
        ranked by ts_rank (documents matching more terms rank higher).
        Stemming is applied via the 'english' text search configuration.

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name.
            query: Search query string.
            type_filter: Filter by type.
            status_filter: Filter by status.
            limit: Max results.

        Returns:
            Search results ranked by relevance.
        """
        cur = conn.cursor()
        tsquery_param = self._build_or_tsquery_param(query)

        where_clauses = ["content_tsvector @@ to_tsquery('english', %s)"]
        params: List[Any] = [tsquery_param]

        if type_filter:
            where_clauses.append("type = %s")
            params.append(type_filter)

        if status_filter:
            where_clauses.append("status = %s")
            params.append(status_filter)

        where_sql = "WHERE " + " AND ".join(where_clauses)

        cur.execute(
            sql.SQL("""
            SELECT *, ts_rank(content_tsvector, to_tsquery('english', %s)) as rank
            FROM {}.tickets
            {}
            ORDER BY rank DESC
            LIMIT %s
            """).format(sql.Identifier(schema), sql.SQL(where_sql)),
            [tsquery_param] + params + [limit],
        )
        rows = cur.fetchall()

        return SearchResult(
            tickets=[self._row_to_response(r) for r in rows],
            total=len(rows),
            query=query,
        )

    # Maximum description length in board view responses (chars).
    # Full descriptions are only returned via individual ticket reads.
    BOARD_DESC_MAX_LENGTH = 200

    def board_view(
        self,
        conn: DBConnection,
        schema: str,
        type_filter: Optional[str] = None,
        view: str = "kanban",
        status_filter: Optional[str] = None,
    ) -> BoardView:
        """Get a kanban board view of tickets grouped by status.

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name.
            type_filter: Filter by ticket type.
            view: "kanban" (full tickets), "summary" (counts only),
                  or "detail" (like kanban but with truncated descriptions).
            status_filter: Filter by status (e.g., "triage", "backlog").

        Returns:
            Board view with columns.
        """
        cur = conn.cursor()
        conditions: List[str] = []
        params: List[Any] = []

        if type_filter:
            conditions.append("type = %s")
            params.append(type_filter)

        if status_filter:
            conditions.append("status = %s")
            params.append(status_filter)

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        if view == "summary":
            cur.execute(
                sql.SQL("""
                SELECT status, COUNT(*) as count
                FROM {}.tickets {}
                GROUP BY status ORDER BY status
                """).format(sql.Identifier(schema), sql.SQL(where_sql)),
                params,
            )
            rows = cur.fetchall()
            columns = [
                BoardColumn(status=r["status"], count=r["count"], tickets=[])
                for r in rows
            ]
            total = sum(r["count"] for r in rows)
        else:
            # Kanban / detail - get tickets grouped by status
            cur.execute(
                sql.SQL("""
                SELECT * FROM {}.tickets {}
                ORDER BY
                    CASE priority
                        WHEN 'urgent' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 3
                        ELSE 4
                    END,
                    updated_at DESC
                """).format(sql.Identifier(schema), sql.SQL(where_sql)),
                params,
            )
            rows = cur.fetchall()

            # Group by status, truncating descriptions to keep response lean
            status_groups: Dict[str, List] = {}
            for r in rows:
                status = r["status"]
                if status not in status_groups:
                    status_groups[status] = []
                ticket = self._row_to_response(r)
                if ticket.description and len(ticket.description) > self.BOARD_DESC_MAX_LENGTH:
                    ticket.description = ticket.description[: self.BOARD_DESC_MAX_LENGTH] + "..."
                status_groups[status].append(ticket)

            # Build ordered columns based on type's state machine
            if type_filter and type_filter in STATE_MACHINES:
                ordered_statuses = list(STATE_MACHINES[type_filter]["transitions"].keys())
            else:
                ordered_statuses = sorted(status_groups.keys())

            columns = []
            for status in ordered_statuses:
                tickets = status_groups.get(status, [])
                columns.append(
                    BoardColumn(status=status, count=len(tickets), tickets=tickets)
                )
            # Add any statuses not in the ordered list
            for status in status_groups:
                if status not in ordered_statuses:
                    tickets = status_groups[status]
                    columns.append(
                        BoardColumn(status=status, count=len(tickets), tickets=tickets)
                    )

            total = len(rows)

        return BoardView(
            columns=columns,
            total=total,
            type_filter=type_filter,
        )

    # --- Link operations --- #

    def add_link(
        self,
        conn: DBConnection,
        schema: str,
        source_id: int,
        data: TicketLinkCreate,
    ) -> TicketLinkResponse:
        """Add a link between two tickets.

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name.
            source_id: Source ticket ID.
            data: Link creation data.

        Returns:
            Created link response.
        """
        cur = conn.cursor()
        try:
            now = time.time()
            cur.execute(
                sql.SQL("""
                INSERT INTO {}.ticket_links
                    (source_id, target_id, link_type, created_at)
                VALUES (%s, %s, %s, %s)
                RETURNING *
                """).format(sql.Identifier(schema)),
                (source_id, data.target_id, data.link_type.value, now),
            )
            row = cur.fetchone()

            # Enrich with target ticket title/status so the response
            # matches the format returned by list_links / _get_links_for_ticket
            cur.execute(
                sql.SQL("SELECT title, status FROM {}.tickets WHERE id = %s").format(
                    sql.Identifier(schema)
                ),
                (data.target_id,),
            )
            target = cur.fetchone()
            if target:
                row["target_title"] = target["title"]
                row["target_status"] = target["status"]

            conn.commit()
            return self._link_row_to_response(row)
        except Exception:
            conn.rollback()
            raise

    def remove_link(
        self,
        conn: DBConnection,
        schema: str,
        link_id: int,
    ) -> bool:
        """Remove a ticket link.

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name.
            link_id: Link ID.

        Returns:
            True if deleted, False if not found.
        """
        cur = conn.cursor()
        try:
            cur.execute(
                sql.SQL("DELETE FROM {}.ticket_links WHERE id = %s RETURNING id").format(
                    sql.Identifier(schema)
                ),
                (link_id,),
            )
            result = cur.fetchone()
            conn.commit()
            return result is not None
        except Exception:
            conn.rollback()
            raise

    def list_links(
        self,
        conn: DBConnection,
        schema: str,
        ticket_id: int,
    ) -> List[TicketLinkResponse]:
        """List all links for a ticket (both directions).

        Args:
            conn: Database connection.
            schema: PostgreSQL schema name.
            ticket_id: Ticket ID.

        Returns:
            List of link responses.
        """
        cur = conn.cursor()
        return self._get_links_for_ticket(cur, schema, ticket_id)

    # --- Helpers --- #

    def _get_links_for_ticket(
        self, cur: Any, schema: str, ticket_id: int
    ) -> List[TicketLinkResponse]:
        """Get all links for a ticket (both as source and target)."""
        sch = sql.Identifier(schema)
        cur.execute(
            sql.SQL("""
            SELECT tl.*, t.title as target_title, t.status as target_status
            FROM {}.ticket_links tl
            JOIN {}.tickets t ON t.id = tl.target_id
            WHERE tl.source_id = %s
            UNION ALL
            SELECT tl.*, t.title as target_title, t.status as target_status
            FROM {}.ticket_links tl
            JOIN {}.tickets t ON t.id = tl.source_id
            WHERE tl.target_id = %s
            """).format(sch, sch, sch, sch),
            (ticket_id, ticket_id),
        )
        return [self._link_row_to_response(r) for r in cur.fetchall()]

    def _row_to_response(self, row: Dict[str, Any]) -> TicketResponse:
        """Convert a database row to a TicketResponse."""
        tags = None
        if row.get("tags"):
            try:
                tags = json.loads(row["tags"])
            except (json.JSONDecodeError, TypeError):
                tags = None

        metadata = None
        if row.get("metadata"):
            try:
                metadata = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError):
                metadata = None

        return TicketResponse(
            id=row["id"],
            title=row["title"],
            description=row.get("description"),
            type=row["type"],
            status=row["status"],
            priority=row["priority"],
            assignee=row.get("assignee"),
            tags=tags,
            metadata=metadata,
            session_id=row.get("session_id"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            closed_at=row.get("closed_at"),
        )

    def _link_row_to_response(self, row: Dict[str, Any]) -> TicketLinkResponse:
        """Convert a link database row to a TicketLinkResponse."""
        return TicketLinkResponse(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            link_type=row["link_type"],
            created_at=row.get("created_at"),
            target_title=row.get("target_title"),
            target_status=row.get("target_status"),
        )
