"""Notification schemas for API requests and responses."""

from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel


NotificationType = Literal['system', 'team']


class NotificationResponse(BaseModel):
    """Notification response."""
    id: str
    type: NotificationType
    title: str
    content: Optional[str] = None
    team_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    read: bool = False


class NotificationListResponse(BaseModel):
    """List of notifications response."""
    notifications: List[NotificationResponse]
    total: int
    unread_count: int


class MarkReadResponse(BaseModel):
    """Response after marking notification as read."""
    success: bool
    message: str
