"""Schemas for the narrow notification inbox (W3d)."""

from typing import List, Literal, Optional

from pydantic import BaseModel

from app.services.notifications import NOTIFICATION_KINDS

#: 派生自写入侧的单一来源。此前这里手写三个值，而 mig 387 / 399 各扩过一次 DB
#: CHECK —— 库里一行 agent_question 就让 /api/v1/inbox 整表 500。
InboxKind = Literal[*NOTIFICATION_KINDS]
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
