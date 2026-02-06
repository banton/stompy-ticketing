"""Stompy Ticketing - A ticketing plugin for Stompy AI Memory."""

from stompy_ticketing.models import (
    TicketCreate,
    TicketUpdate,
    TicketResponse,
    TicketListResponse,
    TicketLinkCreate,
    TicketLinkResponse,
    BoardView,
    SearchResult,
)
from stompy_ticketing.service import TicketService
from stompy_ticketing.schema import (
    get_tickets_table_sql,
    get_ticket_history_table_sql,
    get_ticket_links_table_sql,
)

__version__ = "0.1.0"

__all__ = [
    "TicketService",
    "TicketCreate",
    "TicketUpdate",
    "TicketResponse",
    "TicketListResponse",
    "TicketLinkCreate",
    "TicketLinkResponse",
    "BoardView",
    "SearchResult",
    "get_tickets_table_sql",
    "get_ticket_history_table_sql",
    "get_ticket_links_table_sql",
]
