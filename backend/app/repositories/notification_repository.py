"""Notification repository for database operations.

ORM 2.0 migration (Batch L1b): ``NotificationRepository`` is the legacy
supabase-py REST implementation; ``NotificationRepositoryOrm`` (in
``notification_repository_orm.py``) is the SQLAlchemy 2.0 ORM successor. Call
sites go through ``get_notification_repository()`` (bottom of this file) which
picks the ORM subclass when ``USE_ORM_NOTIFICATIONS`` is on AND the engine is
configured.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Union

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

if TYPE_CHECKING:
    from app.repositories.notification_repository_orm import (
        NotificationRepositoryOrm,
    )


class NotificationRepository:
    """Repository for notification database operations."""

    async def get_user_notifications(
        self, user_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get notifications for a user with read status."""
        client = await get_async_supabase_admin()

        # Get team IDs user is member of
        memberships = (
            await client.table("team_members")
            .select("team_id")
            .eq("user_id", user_id)
            .execute()
        )
        team_ids = [m["team_id"] for m in (memberships.data or [])]

        # Get notifications (system + team)
        if team_ids:
            # Get all notifications and filter
            result = (
                await client.table("notifications")
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )

            notifications = [
                n
                for n in (result.data or [])
                if n["type"] == "system" or n.get("team_id") in team_ids
            ]
        else:
            # Only system notifications
            result = (
                await client.table("notifications")
                .select("*")
                .eq("type", "system")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            notifications = result.data or []

        if not notifications:
            return []

        # Get read status for these notifications
        notification_ids = [n["id"] for n in notifications]
        read_status = (
            await client.table("user_notifications")
            .select("notification_id, read_at")
            .eq("user_id", user_id)
            .in_("notification_id", notification_ids)
            .execute()
        )

        read_map = {
            r["notification_id"]: bool(r.get("read_at"))
            for r in (read_status.data or [])
        }

        # Combine notifications with read status
        return [{**n, "read": read_map.get(n["id"], False)} for n in notifications]

    async def mark_as_read(self, notification_id: str, user_id: str) -> bool:
        """Mark a notification as read."""
        client = await get_async_supabase_admin()

        try:
            await client.table("user_notifications").upsert(
                {
                    "user_id": user_id,
                    "notification_id": notification_id,
                    "read_at": datetime.utcnow().isoformat(),
                }
            ).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to mark notification as read: {e}")
            return False

    async def mark_all_as_read(self, user_id: str) -> int:
        """Mark all notifications as read. Returns count of marked."""
        client = await get_async_supabase_admin()

        # Get all unread notifications for user
        notifications = await self.get_user_notifications(user_id)
        unread = [n for n in notifications if not n["read"]]

        if not unread:
            return 0

        # Create upsert data
        upserts = [
            {
                "user_id": user_id,
                "notification_id": n["id"],
                "read_at": datetime.utcnow().isoformat(),
            }
            for n in unread
        ]

        try:
            await client.table("user_notifications").upsert(upserts).execute()
            return len(upserts)
        except Exception as e:
            logger.error(f"Failed to mark all notifications as read: {e}")
            return 0

    async def get_unread_count(self, user_id: str) -> int:
        """Get count of unread notifications."""
        notifications = await self.get_user_notifications(user_id)
        return sum(1 for n in notifications if not n["read"])

    async def delete_notification(self, notification_id: str, user_id: str) -> bool:
        """Delete a user's read status for a notification (hide it for them)."""
        client = await get_async_supabase_admin()

        # We don't actually delete the notification, just mark it as dismissed
        # by setting a dismissed_at timestamp in user_notifications
        try:
            await client.table("user_notifications").upsert(
                {
                    "user_id": user_id,
                    "notification_id": notification_id,
                    "dismissed_at": datetime.utcnow().isoformat(),
                }
            ).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to delete notification: {e}")
            return False


def get_notification_repository() -> (
    Union["NotificationRepository", "NotificationRepositoryOrm"]
):
    """Return the right NotificationRepository implementation per env.

    ORM when ``USE_ORM_NOTIFICATIONS`` is set AND the SQLAlchemy engine is
    configured; otherwise the legacy supabase-py REST path. A flag-on but
    engine-missing deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_NOTIFICATIONS:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.notification_repository_orm import (
                NotificationRepositoryOrm,
            )

            return NotificationRepositoryOrm()
        logger.warning(
            "USE_ORM_NOTIFICATIONS=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return NotificationRepository()
