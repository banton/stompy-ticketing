"""Pydantic request/response models for stompy-ticketing."""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enums                                                                       #
# --------------------------------------------------------------------------- #


class TicketType(str, Enum):
    task = "task"
    bug = "bug"
    feature = "feature"
    decision = "decision"


class Priority(str, Enum):
    urgent = "urgent"
    high = "high"
    medium = "medium"
    low = "low"
    none = "none"


class LinkType(str, Enum):
    blocks = "blocks"
    parent = "parent"
    related = "related"
    duplicate = "duplicate"


# --------------------------------------------------------------------------- #
# Request models                                                              #
# --------------------------------------------------------------------------- #


class TicketCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    type: TicketType = TicketType.task
    priority: Priority = Priority.medium
    assignee: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None


class TicketUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = None
    priority: Optional[Priority] = None
    assignee: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class TicketTransition(BaseModel):
    status: str


class TicketLinkCreate(BaseModel):
    target_id: int
    link_type: LinkType = LinkType.related


class TicketListFilters(BaseModel):
    type: Optional[TicketType] = None
    status: Optional[str] = None
    priority: Optional[Priority] = None
    assignee: Optional[str] = None
    search: Optional[str] = None
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


# --------------------------------------------------------------------------- #
# Response models                                                             #
# --------------------------------------------------------------------------- #


class TicketHistoryEntry(BaseModel):
    id: int
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_by: Optional[str] = None
    changed_at: Optional[float] = None


class TicketLinkResponse(BaseModel):
    id: int
    source_id: int
    target_id: int
    link_type: str
    created_at: Optional[float] = None
    # Denormalized target info for display
    target_title: Optional[str] = None
    target_status: Optional[str] = None


class TicketResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    type: str
    status: str
    priority: str
    assignee: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    closed_at: Optional[float] = None
    history: Optional[List[TicketHistoryEntry]] = None
    links: Optional[List[TicketLinkResponse]] = None


class TicketListResponse(BaseModel):
    tickets: List[TicketResponse]
    total: int
    by_status: Optional[Dict[str, int]] = None
    by_type: Optional[Dict[str, int]] = None


class BoardColumn(BaseModel):
    status: str
    count: int
    tickets: List[TicketResponse]


class BoardView(BaseModel):
    columns: List[BoardColumn]
    total: int
    type_filter: Optional[str] = None


class SearchResult(BaseModel):
    tickets: List[TicketResponse]
    total: int
    query: str
