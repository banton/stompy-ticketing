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

import json
from typing import Any, Callable, List, Optional

from stompy_ticketing.models import (
    LinkType,
    Priority,
    TicketCreate,
    TicketLinkCreate,
    TicketListFilters,
    TicketType,
    TicketUpdate,
)
from stompy_ticketing.service import InvalidTransitionError, TicketService


def _safe_json(data: Any) -> str:
    """Serialize to JSON with error wrapping."""
    try:
        if hasattr(data, "model_dump"):
            return json.dumps(data.model_dump(), default=str)
        if hasattr(data, "dict"):
            return json.dumps(data.dict(), default=str)
        return json.dumps(data, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def register_ticketing_tools(
    mcp_instance: Any,
    get_db_func: Callable,
    check_project_func: Callable,
    get_project_func: Callable,
) -> None:
    """Register ticketing MCP tools on the given FastMCP instance.

    Args:
        mcp_instance: FastMCP server to register tools on.
        get_db_func: Function(project=None) -> context-manager DB connection.
        check_project_func: Function(project=None) -> error string or None.
        get_project_func: Function(project=None) -> project name string.
    """
    service = TicketService()

    @mcp_instance.tool()
    async def ticket(
        action: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        type: Optional[str] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
        assignee: Optional[str] = None,
        tags: Optional[str] = None,
        ticket_id: Optional[int] = None,
        project: Optional[str] = None,
    ) -> str:
        """Create, read, update, move, list, or close tickets.

        **REQUIRES ACTIVE PROJECT** - Use project_switch() first or pass project param.

        Actions:
          create  - Create ticket (title required, type defaults to 'task')
          get     - Get ticket by ID (ticket_id required)
          update  - Update ticket fields (ticket_id required)
          move    - Transition status (ticket_id + status required)
          list    - List tickets (optional type/status/priority/assignee filters)
          close   - Close ticket (ticket_id required)

        Args:
            action: create|get|update|move|list|close
            title: Ticket title (create/update)
            description: Ticket description (create/update)
            type: task|bug|feature|decision (create filter, default: task)
            priority: urgent|high|medium|low|none (create/update/filter)
            status: Target status for move, or filter for list
            assignee: Assignee name (create/update/filter)
            tags: Comma-separated tags (create/update)
            ticket_id: Ticket ID (get/update/move/close)
            project: Project name (default: active project)

        Returns: JSON with ticket data or list results

        Examples:
            ticket(action="create", title="Fix login bug", type="bug", priority="high")
            ticket(action="list", type="task", status="in_progress")
            ticket(action="move", ticket_id=1, status="in_progress")
            ticket(action="close", ticket_id=1)
        """
        project_check = check_project_func(project)
        if project_check:
            return project_check

        try:
            project_name = get_project_func(project)
            with get_db_func(project) as conn:
                schema = project_name

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
                    return _safe_json(
                        {"status": "transitioned", "ticket": result.model_dump()}
                    )

                elif action == "list":
                    filters = TicketListFilters(
                        type=TicketType(type) if type else None,
                        status=status,
                        priority=Priority(priority) if priority else None,
                        assignee=assignee,
                    )
                    result = service.list_tickets(conn, schema, filters)
                    return _safe_json(result)

                elif action == "close":
                    if not ticket_id:
                        return json.dumps({"error": "ticket_id is required for close"})
                    result = service.close_ticket(conn, schema, ticket_id)
                    if not result:
                        return json.dumps({"error": f"Ticket {ticket_id} not found"})
                    return _safe_json({"status": "closed", "ticket": result.model_dump()})

                else:
                    return json.dumps(
                        {
                            "error": f"Unknown action: {action}",
                            "valid_actions": ["create", "get", "update", "move", "list", "close"],
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
        action: str,
        ticket_id: Optional[int] = None,
        target_id: Optional[int] = None,
        link_type: Optional[str] = None,
        link_id: Optional[int] = None,
        project: Optional[str] = None,
    ) -> str:
        """Manage relationships between tickets.

        **REQUIRES ACTIVE PROJECT** - Use project_switch() first or pass project param.

        Actions:
          add    - Link two tickets (ticket_id, target_id, link_type required)
          remove - Remove a link (link_id required)
          list   - List links for a ticket (ticket_id required)

        Args:
            action: add|remove|list
            ticket_id: Source ticket ID (add/list)
            target_id: Target ticket ID (add)
            link_type: blocks|parent|related|duplicate (add, default: related)
            link_id: Link ID to remove (remove)
            project: Project name (default: active project)

        Returns: JSON with link data

        Examples:
            ticket_link(action="add", ticket_id=1, target_id=2, link_type="blocks")
            ticket_link(action="list", ticket_id=1)
            ticket_link(action="remove", link_id=5)
        """
        project_check = check_project_func(project)
        if project_check:
            return project_check

        try:
            project_name = get_project_func(project)
            with get_db_func(project) as conn:
                schema = project_name

                if action == "add":
                    if not ticket_id or not target_id:
                        return json.dumps(
                            {"error": "ticket_id and target_id are required for add"}
                        )
                    data = TicketLinkCreate(
                        target_id=target_id,
                        link_type=LinkType(link_type) if link_type else LinkType.related,
                    )
                    result = service.add_link(conn, schema, ticket_id, data)
                    return _safe_json({"status": "linked", "link": result.model_dump()})

                elif action == "remove":
                    if not link_id:
                        return json.dumps({"error": "link_id is required for remove"})
                    removed = service.remove_link(conn, schema, link_id)
                    if not removed:
                        return json.dumps({"error": f"Link {link_id} not found"})
                    return json.dumps({"status": "removed", "link_id": link_id})

                elif action == "list":
                    if not ticket_id:
                        return json.dumps({"error": "ticket_id is required for list"})
                    links = service.list_links(conn, schema, ticket_id)
                    return _safe_json({"ticket_id": ticket_id, "links": [l.model_dump() for l in links]})

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
        view: str = "kanban",
        type: Optional[str] = None,
        status: Optional[str] = None,
        project: Optional[str] = None,
    ) -> str:
        """Get a dashboard view of tickets grouped by status.

        **REQUIRES ACTIVE PROJECT** - Use project_switch() first or pass project param.

        Descriptions are truncated to 200 chars in board views. Use
        ticket(action="get", id=N) to read full descriptions.

        Args:
            view: "summary" (counts only), "kanban" (tickets with truncated descriptions),
                  or "detail" (alias for kanban — same truncated output)
            type: Filter by ticket type (task|bug|feature|decision)
            status: Filter by status (e.g., "triage", "backlog", "in_progress")
            project: Project name (default: active project)

        Returns: JSON board view with columns grouped by status

        Examples:
            ticket_board(view="summary")
            ticket_board(status="triage")
            ticket_board(view="summary", type="task")
        """
        project_check = check_project_func(project)
        if project_check:
            return project_check

        try:
            project_name = get_project_func(project)
            with get_db_func(project) as conn:
                schema = project_name
                result = service.board_view(
                    conn, schema, type_filter=type, view=view, status_filter=status
                )
                return _safe_json(result)

        except Exception as e:
            return json.dumps(
                {"error": f"Board view failed: {str(e)[:200]}", "error_type": e.__class__.__name__}
            )

    @mcp_instance.tool()
    async def ticket_search(
        query: str,
        type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        project: Optional[str] = None,
    ) -> str:
        """Search tickets using full-text search (BM25).

        **REQUIRES ACTIVE PROJECT** - Use project_switch() first or pass project param.

        Args:
            query: Search query string
            type: Filter by ticket type
            status: Filter by status
            limit: Max results (default: 20)
            project: Project name (default: active project)

        Returns: JSON search results ranked by relevance

        Examples:
            ticket_search(query="authentication bug")
            ticket_search(query="deploy", type="task", status="in_progress")
        """
        project_check = check_project_func(project)
        if project_check:
            return project_check

        try:
            project_name = get_project_func(project)
            with get_db_func(project) as conn:
                schema = project_name
                result = service.search_tickets(
                    conn, schema, query, type_filter=type, status_filter=status, limit=limit
                )
                return _safe_json(result)

        except Exception as e:
            return json.dumps(
                {"error": f"Search failed: {str(e)[:200]}", "error_type": e.__class__.__name__}
            )
