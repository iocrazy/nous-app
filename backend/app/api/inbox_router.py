"""API routes for the narrow notification inbox (W3d).

Distinct from ``notifications_router`` (the legacy broadcast/announcement
surface). This router serves ``public.inbox_notifications`` — the per-recipient
action-result feed with the producer kinds listed in ``NOTIFICATION_KINDS``
(``app.services.notifications`` — the single source the schema, the ORM
CheckConstraint, the DB CHECK and the frontend union all derive from).

Every endpoint is scoped to the authenticated user; a row that is absent or
belongs to someone else is a 404 (a foreign id is never confirmed to exist).
"""

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger
from pydantic import ValidationError

from app.core.deps import AuthDep
from app.repositories.inbox_repository import get_inbox_repository
from app.schemas.inbox import (
    InboxListResponse,
    InboxMarkReadResponse,
    InboxNotificationResponse,
)

router = APIRouter(prefix="/inbox", tags=["Inbox"])


@router.get("", response_model=InboxListResponse)
async def list_inbox(
    auth: AuthDep,
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List the current user's inbox notifications (newest first, own rows).

    ``total`` is the number of rows this page actually produced — a row the
    schema cannot parse is logged at ERROR and dropped, never 500s the page.
    ``unread_count`` is unchanged: it is counted in SQL, over every row.
    """
    repo = get_inbox_repository()
    rows = await repo.list_notifications(
        auth.user_id, unread_only=unread_only, limit=limit, offset=offset
    )
    unread_count = await repo.unread_count(auth.user_id)
    items: list[InboxNotificationResponse] = []
    for row in rows:
        try:
            items.append(InboxNotificationResponse(**row))
        except ValidationError as exc:
            # 一行坏数据不该让整个收件箱 500。历史上正是这里：schema 停在三个
            # kind，库里有 workflow_stage / agent_question 行，于是每一次列表请求
            # 都 ValidationError。容纳并**记 ERROR**（不是 except: pass）——
            # 与「分发器要容纳回调异常」同一条纪律。
            logger.error(
                f"[inbox] skipping unparseable row id={row.get('id')} "
                f"kind={row.get('kind')} err={exc}"
            )
    return InboxListResponse(
        notifications=items,
        total=len(items),
        unread_count=unread_count,
    )


@router.post("/{notification_id}/read", response_model=InboxMarkReadResponse)
async def mark_inbox_read(notification_id: str, auth: AuthDep):
    """Mark one notification read. 404 if it isn't the caller's own row."""
    repo = get_inbox_repository()
    owner = await repo.get_owner(notification_id)
    if owner is None or owner != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    await repo.mark_read(notification_id, auth.user_id)
    return InboxMarkReadResponse(success=True, message="Notification marked as read")


@router.post("/read-all", response_model=InboxMarkReadResponse)
async def mark_all_inbox_read(auth: AuthDep):
    """Mark all of the current user's unread notifications read."""
    repo = get_inbox_repository()
    count = await repo.mark_all_read(auth.user_id)
    return InboxMarkReadResponse(
        success=True, message=f"Marked {count} notifications as read"
    )
