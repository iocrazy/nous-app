"""API routes for Notifications management."""

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.notification_repository import NotificationRepository
from app.schemas.notification import (
    MarkReadResponse,
    NotificationListResponse,
    NotificationResponse,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationListResponse)
async def list_notifications(auth: AuthDep):
    """List all notifications for the current user."""
    repo = NotificationRepository()
    notifications = await repo.get_user_notifications(auth.user_id)
    unread_count = sum(1 for n in notifications if not n["read"])

    return NotificationListResponse(
        notifications=[
            NotificationResponse(
                id=str(n["id"]),
                type=n["type"],
                title=n["title"],
                content=n.get("content"),
                team_id=n.get("team_id"),
                created_by=n.get("created_by"),
                created_at=n["created_at"],
                read=n.get("read", False),
            )
            for n in notifications
        ],
        total=len(notifications),
        unread_count=unread_count,
    )


@router.put("/{notification_id}/read", response_model=MarkReadResponse)
async def mark_as_read(notification_id: str, auth: AuthDep):
    """Mark a notification as read."""
    repo = NotificationRepository()
    success = await repo.mark_as_read(notification_id, auth.user_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to mark notification as read",
        )

    return MarkReadResponse(success=True, message="Notification marked as read")


@router.put("/read-all", response_model=MarkReadResponse)
async def mark_all_as_read(auth: AuthDep):
    """Mark all notifications as read."""
    repo = NotificationRepository()
    count = await repo.mark_all_as_read(auth.user_id)

    return MarkReadResponse(
        success=True, message=f"Marked {count} notifications as read"
    )


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(notification_id: str, auth: AuthDep):
    """Delete (dismiss) a notification."""
    repo = NotificationRepository()
    deleted = await repo.delete_notification(notification_id, auth.user_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete notification",
        )


@router.get("/unread-count")
async def get_unread_count(auth: AuthDep):
    """Get count of unread notifications."""
    repo = NotificationRepository()
    count = await repo.get_unread_count(auth.user_id)

    return {"unread_count": count}
