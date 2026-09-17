"""Schemas for the narrow notification inbox (W3d)."""

from typing import List, Literal, Optional

from pydantic import BaseModel

from app.schemas.inbox_kinds import NOTIFICATION_KINDS

#: 派生自单一来源。此前这里手写三个值，而 mig 387 / 399 各扩过一次 DB CHECK
#: —— 库里一行 agent_question 就让 /api/v1/inbox 整表 500。
#: 常量放在零依赖的 ``inbox_kinds`` 而不是写入侧的 ``services.notifications``：
#: 后者会拉起 SQLAlchemy 与 settings，而本模块只是一组 Pydantic 响应模型。
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
