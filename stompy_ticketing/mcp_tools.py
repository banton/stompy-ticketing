"""MCP tool definitions for stompy-ticketing.

Provides 4 tools that replace ~33 Linear tools (~800 tokens vs ~5000):
- ticket: Primary CRUD + transitions
- ticket_link: Relationship management
- ticket_board: Dashboard view
- ticket_search: Full-text search

Registration pattern: register_ticketing_tools() takes the FastMCP instance
and helper functions from the host (Stompy), decoupling this plugin from
Stompy internals.
"""

import fnmatch
import json
from typing import Annotated, Any, Callable, List, Literal, Optional

from stompy_ticketing.models import (
    ContextLinkCreate,
    ContextLinkType,
    LinkType,
    Priority,
    TicketCreate,
    TicketLinkCreate,
    TicketListFilters,
    TicketType,
    TicketUpdate,
)
from stompy_ticketing.service import InvalidTransitionError, TicketService


def _toon_encode(data):
    """Encode as TOON (Token-Oriented Object Notation), JSON fallback."""
    try:
        from toon import encode
        return encode(data)
    except Exception:
        return json.dumps(data, default=str)


def _safe_json(data: Any) -> str:
    """Serialize data as TOON for token-efficient MCP responses."""
    try:
        if hasattr(data, "model_dump"):
            return _toon_encode(data.model_dump())
        if hasattr(data, "dict"):
            return _toon_encode(data.dict())
        return _toon_encode(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


def register_ticketing_tools(
    mcp_instance: Any,
    get_db_func: Callable,
    check_project_func: Callable,
    get_project_func: Callable,
    resolve_schema_func: Optional[Callable] = None,
    notify_resolution_func: Optional[Callable] = None,
) -> None:
    """Register ticketing MCP tools on the given FastMCP instance.

    Args:
        mcp_instance: FastMCP server to register tools on.
        get_db_func: Function(project=None) -> context-manager DB connection.
        check_project_func: Function(project=None) -> error string or None.
        get_project_func: Function(project=None) -> project name string.
        resolve_schema_func: Optional function(name) -> schema name. If None,
            uses the project name directly as the schema.
        notify_resolution_func: Optional callback(report, new_status) for bug
            resolution emails on mcp_global tickets.
    """
    service = TicketService()

    def _get_schema(project_name: str) -> str:
        """Resolve project name to PostgreSQL schema name."""
        return resolve_schema_func(project_name) if resolve_schema_func else project_name

    @mcp_instance.tool()
    async def ticket(
        action: Annotated[
            Literal["create", "get", "update", "move", "list", "close", "archive", "batch_move", "batch_close"],
            "Operation to perform",
        ],
        title: Annotated[Optional[str], "Ticket title (create/update)"] = None,
        description: Annotated[Optional[str], "Ticket description (create/update)"] = None,
        type: Annotated[
            Optional[Literal["task", "bug", "feature", "decision"]],
            "Ticket type (default: task)",
        ] = None,
        priority: Annotated[
            Optional[Literal["urgent", "high", "medium", "low", "none"]],
            "Ticket priority (default: medium)",
        ] = None,
        status: Annotated[Optional[str], "Target status for move/batch_move, or filter for list"] = None,
        assignee: Annotated[Optional[str], "Assignee name"] = None,
        tags: Annotated[Optional[str], "Comma-separated tags"] = None,
        ticket_id: Annotated[Optional[int], "Ticket ID (get/update/move/close)"] = None,
        ticket_ids: Annotated[Optional[str], "Comma-separated IDs (batch_move/batch_close)"] = None,
        confirm: Annotated[bool, "Execute batch operation (default: preview only)"] = False,
        resolution: Annotated[Optional[str], "Terminal status for close (e.g. 'resolved', 'wont_fix')"] = None,
        limit: Annotated[Optional[int], "Max tickets for list (default 20, max 200)"] = None,
        offset: Annotated[Optional[int], "Skip N tickets for list pagination"] = None,
        include_archived: Annotated[bool, "Include archived tickets in list"] = False,
        project: Annotated[Optional[str], "Project name"] = None,
        grep: Annotated[Optional[str], "Filter list results by title (fnmatch glob, e.g. 'auth*', '*bug*')"] = None,
    ) -> str:
        """CRUD + lifecycle for tickets. Pass project= on every call.

        action → required params:
          create      → title (type defaults to task)
          get         → ticket_id
          update      → ticket_id + fields to change
          move        → ticket_id + status
          list        → optional filters (type/status/priority/assignee)
          close       → ticket_id
          archive     → (none)
          batch_move  → ticket_ids + status; confirm=True to execute
          batch_close → ticket_ids; confirm=True to execute"""
        project_check = check_project_func(project)
        if project_check:
            return project_check

        try:
            project_name = get_project_func(project)
            with get_db_func(project) as conn:
                schema = _get_schema(project_name)

                if action == "create":
                    if not title:
                        return json.dumps({"error": "title is required for create"})
                    tag_list = [t.strip() for t in tags.split(",")] if tags else None
                    data = TicketCreate(
                        title=title,
                        description=description,
                        type=TicketType(type) if type else TicketType.task,
                        priority=Priority(priority) if priority else Priority.medium,
                        assignee=assignee,
                        tags=tag_list,
                    )
                    result = service.create_ticket(conn, schema, data)
                    return _safe_json({"status": "created", "ticket": result.model_dump()})

                elif action == "get":
                    if not ticket_id:
                        return json.dumps({"error": "ticket_id is required for get"})
                    result = service.get_ticket(conn, schema, ticket_id)
                    if not result:
                        return json.dumps({"error": f"Ticket {ticket_id} not found"})
                    return _safe_json(result)

                elif action == "update":
                    if not ticket_id:
                        return json.dumps({"error": "ticket_id is required for update"})
                    tag_list = [t.strip() for t in tags.split(",")] if tags else None
                    data = TicketUpdate(
                        title=title,
                        description=description,
                        priority=Priority(priority) if priority else None,
                        assignee=assignee,
                        tags=tag_list,
                    )
                    result = service.update_ticket(conn, schema, ticket_id, data)
                    if not result:
                        return json.dumps({"error": f"Ticket {ticket_id} not found"})
                    return _safe_json({"status": "updated", "ticket": result.model_dump()})

                elif action == "move":
                    if not ticket_id or not status:
                        return json.dumps(
                            {"error": "ticket_id and status are required for move"}
                        )
                    result = service.transition_ticket(conn, schema, ticket_id, status)
                    if not result:
                        return json.dumps({"error": f"Ticket {ticket_id} not found"})

                    # Email notification for bug ticket resolutions in mcp_global
                    if (
                        notify_resolution_func
                        and schema == "mcp_global"
                        and result.type == "bug"
                        and status in ("resolved", "wont_fix", "closed")
                    ):
                        try:
                            meta = result.metadata or {}
                            reporter_email = meta.get("reporter_email")
                            if reporter_email:
                                notify_resolution_func(
                                    report={
                                        "id": result.id,
                                        "title": result.title,
                                        "user_email": reporter_email,
                                    },
                                    new_status=status,
                                )
                        except Exception:
                            pass  # Email failure should not break the transition

                    return _safe_json(
                        {"status": "transitioned", "ticket": result.model_dump()}
                    )

                elif action == "list":
                    effective_limit = min(limit, 200) if limit is not None else 20
                    effective_offset = offset if offset is not None else 0
                    filters = TicketListFilters(
                        type=TicketType(type) if type else None,
                        status=status,
                        priority=Priority(priority) if priority else None,
                        assignee=assignee,
                        limit=effective_limit,
                        offset=effective_offset,
                        include_archived=include_archived,
                    )
                    result = service.list_tickets(conn, schema, filters)
                    if grep and hasattr(result, "tickets"):
                        result.tickets = [
                            t for t in result.tickets
                            if fnmatch.fnmatch(t.title if hasattr(t, "title") else t.get("title", ""), grep)
                        ]
                        result.total = len(result.tickets)
                    return _safe_json(result)

                elif action == "archive":
                    count = service.archive_stale_tickets(conn, schema)
                    return json.dumps({
                        "status": "archived",
                        "count": count,
                        "message": f"Archived {count} stale ticket(s)",
                    })

                elif action == "close":
                    if not ticket_id:
                        return json.dumps({"error": "ticket_id is required for close"})
                    result = service.close_ticket(
                        conn, schema, ticket_id, resolution=resolution,
                    )
                    if not result:
                        return json.dumps({"error": f"Ticket {ticket_id} not found"})
                    return _safe_json({"status": "closed", "ticket": result.model_dump()})

                elif action == "batch_move":
                    if not ticket_ids or not status:
                        return json.dumps(
                            {"error": "ticket_ids and status are required for batch_move"}
                        )
                    try:
                        parsed_ids = [int(x.strip()) for x in ticket_ids.split(",")]
                    except ValueError:
                        return json.dumps({"error": "ticket_ids must be comma-separated integers"})
                    result = service.batch_transition(
                        conn, schema, parsed_ids, status,
                        confirm=confirm,
                    )
                    return _safe_json(result)

                elif action == "batch_close":
                    if not ticket_ids:
                        return json.dumps(
                            {"error": "ticket_ids is required for batch_close"}
                        )
                    try:
                        parsed_ids = [int(x.strip()) for x in ticket_ids.split(",")]
                    except ValueError:
                        return json.dumps({"error": "ticket_ids must be comma-separated integers"})
                    result = service.batch_close(
                        conn, schema, parsed_ids,
                        confirm=confirm,
                        resolution=resolution,
                    )
                    return _safe_json(result)

                else:
                    return json.dumps(
                        {
                            "error": f"Unknown action: {action}",
                            "valid_actions": [
                                "create", "get", "update", "move", "list",
                                "close", "archive", "batch_move", "batch_close",
                            ],
                        }
                    )

        except InvalidTransitionError as e:
            return json.dumps({"error": str(e), "error_type": "InvalidTransition"})
        except ValueError as e:
            return json.dumps({"error": str(e), "error_type": "ValueError"})
        except Exception as e:
            return json.dumps(
                {"error": f"Ticket operation failed: {str(e)[:200]}", "error_type": e.__class__.__name__}
            )

    @mcp_instance.tool()
    async def ticket_link(
        action: Annotated[
            Literal["add", "remove", "list"],
            "Operation to perform",
        ],
        ticket_id: Annotated[Optional[int], "Source ticket ID (add/list)"] = None,
        target_id: Annotated[Optional[int], "Target ticket ID for ticket-to-ticket links (add)"] = None,
        link_type: Annotated[
            Optional[Literal["blocks", "parent", "related", "duplicate", "implements", "references", "updates"]],
            "Relationship type (default: related)",
        ] = None,
        link_id: Annotated[Optional[int], "Link ID to remove (remove)"] = None,
        project: Annotated[Optional[str], "Project name"] = None,
        context_label: Annotated[Optional[str], "Context label for ticket↔context links (add/list)"] = None,
        context_version: Annotated[Optional[str], "Context version (default: latest)"] = "latest",
    ) -> str:
        """Manage relationships between tickets and/or contexts. Pass project= on every call.

        Ticket-to-ticket links (target_id present):
          add    → ticket_id + target_id (+ optional link_type: blocks/parent/related/duplicate)
          remove → link_id
          list   → ticket_id (returns both ticket and context links)

        Ticket-to-context links (context_label present):
          add    → ticket_id + context_label (+ optional link_type: implements/references/updates/related)
          remove → link_id
          list   → ticket_id"""
        project_check = check_project_func(project)
        if project_check:
            return project_check

        try:
            project_name = get_project_func(project)
            with get_db_func(project) as conn:
                schema = _get_schema(project_name)

                if action == "add":
                    if context_label:
                        # Ticket-to-context link
                        if not ticket_id:
                            return json.dumps(
                                {"error": "ticket_id is required for context link add"}
                            )
                        # Validate link_type for context links
                        ctx_link_types = {e.value for e in ContextLinkType}
                        effective_link_type = link_type or "related"
                        if effective_link_type not in ctx_link_types:
                            effective_link_type = "related"
                        data = ContextLinkCreate(
                            context_label=context_label,
                            context_version=context_version or "latest",
                            link_type=ContextLinkType(effective_link_type),
                        )
                        result = service.add_context_link(conn, schema, ticket_id, data)
                        return _safe_json({"status": "linked", "context_link": result.model_dump()})
                    else:
                        # Ticket-to-ticket link
                        if not ticket_id or not target_id:
                            return json.dumps(
                                {"error": "ticket_id and target_id are required for ticket link add"}
                            )
                        # Validate link_type for ticket links
                        ticket_link_types = {e.value for e in LinkType}
                        effective_link_type = link_type or "related"
                        if effective_link_type not in ticket_link_types:
                            effective_link_type = "related"
                        data = TicketLinkCreate(
                            target_id=target_id,
                            link_type=LinkType(effective_link_type),
                        )
                        result = service.add_link(conn, schema, ticket_id, data)
                        return _safe_json({"status": "linked", "link": result.model_dump()})

                elif action == "remove":
                    if not link_id:
                        return json.dumps({"error": "link_id is required for remove"})
                    # Try ticket-to-ticket link first, then context link
                    removed = service.remove_link(conn, schema, link_id)
                    if not removed:
                        removed = service.remove_context_link(conn, schema, link_id)
                    if not removed:
                        return json.dumps({"error": f"Link {link_id} not found"})
                    return json.dumps({"status": "removed", "link_id": link_id})

                elif action == "list":
                    if not ticket_id:
                        return json.dumps({"error": "ticket_id is required for list"})
                    ticket_links = service.list_links(conn, schema, ticket_id)
                    context_links = service.list_context_links_for_ticket(conn, schema, ticket_id)
                    return _safe_json({
                        "ticket_id": ticket_id,
                        "ticket_links": [l.model_dump() for l in ticket_links],
                        "context_links": [l.model_dump() for l in context_links],
                    })

                else:
                    return json.dumps(
                        {"error": f"Unknown action: {action}", "valid_actions": ["add", "remove", "list"]}
                    )

        except Exception as e:
            return json.dumps(
                {"error": f"Link operation failed: {str(e)[:200]}", "error_type": e.__class__.__name__}
            )

    @mcp_instance.tool()
    async def ticket_board(
        view: Annotated[
            Literal["kanban", "summary", "compact"],
            "kanban=full tickets; summary=counts only; compact=id+title+priority",
        ] = "kanban",
        type: Annotated[
            Optional[Literal["task", "bug", "feature", "decision"]],
            "Filter by ticket type",
        ] = None,
        status: Annotated[Optional[str], "Filter by specific status"] = None,
        include_terminal: Annotated[bool, "Include terminal statuses (done, resolved, etc.)"] = False,
        include_archived: Annotated[bool, "Include archived tickets"] = False,
        limit: Annotated[Optional[int], "Max tickets per column (default 10, 0=all)"] = None,
        project: Annotated[Optional[str], "Project name"] = None,
    ) -> str:
        """Ticket board grouped by status. Active statuses only by default."""
        project_check = check_project_func(project)
        if project_check:
            return project_check

        try:
            project_name = get_project_func(project)
            with get_db_func(project) as conn:
                schema = _get_schema(project_name)
                result = service.board_view(
                    conn, schema,
                    type_filter=type,
                    view=view,
                    status_filter=status,
                    include_terminal=include_terminal,
                    include_archived=include_archived,
                    limit=limit,
                )
                return _safe_json(result)

        except Exception as e:
            return json.dumps(
                {"error": f"Board view failed: {str(e)[:200]}", "error_type": e.__class__.__name__}
            )

    @mcp_instance.tool()
    async def ticket_search(
        query: Annotated[str, "Search query string"],
        type: Annotated[
            Optional[Literal["task", "bug", "feature", "decision"]],
            "Filter by ticket type",
        ] = None,
        status: Annotated[Optional[str], "Filter by status"] = None,
        limit: Annotated[int, "Max results"] = 20,
        include_archived: Annotated[bool, "Include archived tickets"] = False,
        project: Annotated[Optional[str], "Project name"] = None,
    ) -> str:
        """Full-text search (BM25) over tickets. Excludes archived by default."""
        project_check = check_project_func(project)
        if project_check:
            return project_check

        try:
            project_name = get_project_func(project)
            with get_db_func(project) as conn:
                schema = _get_schema(project_name)
                result = service.search_tickets(
                    conn, schema, query,
                    type_filter=type,
                    status_filter=status,
                    limit=limit,
                    include_archived=include_archived,
                )
                return _safe_json(result)

        except Exception as e:
            return json.dumps(
                {"error": f"Search failed: {str(e)[:200]}", "error_type": e.__class__.__name__}
            )
