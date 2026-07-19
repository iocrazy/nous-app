"""API routes for the narrow notification inbox (W3d).

Distinct from ``notifications_router`` (the legacy broadcast/announcement
surface). This router serves ``public.inbox_notifications`` — the per-recipient
action-result feed with exactly three producer kinds. Every endpoint is scoped
to the authenticated user; a row that is absent or belongs to someone else is a
404 (a foreign id is never confirmed to exist).
"""

from fastapi import APIRouter, HTTPException, Query, status

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
    """List the current user's inbox notifications (newest first, own rows)."""
    repo = get_inbox_repository()
    rows = await repo.list_notifications(
        auth.user_id, unread_only=unread_only, limit=limit, offset=offset
    )
    unread_count = await repo.unread_count(auth.user_id)
    return InboxListResponse(
        notifications=[InboxNotificationResponse(**r) for r in rows],
        total=len(rows),
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
