"""Schemas for the narrow notification inbox (W3d)."""

from typing import List, Literal, Optional

from pydantic import BaseModel

InboxKind = Literal["generation_result", "publish_result", "autopilot_output"]
InboxSeverity = Literal["info", "success", "error"]
InboxLinkKind = Literal["issue", "resource", "publish_batch"]


class InboxNotificationResponse(BaseModel):
    """A single inbox notification (recipient-facing)."""

    id: str
    kind: InboxKind
    title: str
    body: Optional[str] = None
    severity: InboxSeverity = "info"
    link_kind: Optional[InboxLinkKind] = None
    link_id: Optional[str] = None
    team_id: Optional[str] = None
    read: bool = False
    read_at: Optional[str] = None
    created_at: Optional[str] = None


class InboxListResponse(BaseModel):
    """Paginated list of inbox notifications."""

    notifications: List[InboxNotificationResponse]
    total: int
    unread_count: int


class InboxMarkReadResponse(BaseModel):
    """Response after marking one/all notifications read."""

    success: bool
    message: str
