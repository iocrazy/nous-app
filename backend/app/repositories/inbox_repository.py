"""Inbox repository — read + mark-read access for ``inbox_notifications`` (W3d).

The producer WRITE path lives in ``app.services.notifications.notify``; this
repository serves the recipient-facing router (list / mark-read / mark-all).
Every method scopes to ``user_id`` — a caller can only ever see or mutate their
own rows (the router turns a foreign/absent row into a 404).

Value shapes at the dict boundary: ``id`` and ``link_id`` → str (snowflake
JS-precision safety — the router serializes straight to JSON), ``team_id`` →
str or None, timestamps → ISO strings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, select, update

from app.db.session import read_scope, write_scope
from app.models import InboxNotifications


def _to_dict(obj: InboxNotifications) -> Dict[str, Any]:
    """SELECT *-shaped dict with string ids and ISO timestamps."""
    return {
        "id": str(obj.id),
        "user_id": str(obj.user_id),
        "kind": obj.kind,
        "title": obj.title,
        "body": obj.body,
        "severity": obj.severity,
        "link_kind": obj.link_kind,
        "link_id": obj.link_id,
        "team_id": str(obj.team_id) if obj.team_id is not None else None,
        "read": obj.read_at is not None,
        "read_at": obj.read_at.isoformat() if obj.read_at else None,
        "created_at": obj.created_at.isoformat() if obj.created_at else None,
    }


class InboxRepository:
    """Data access for the narrow notification inbox (SQLAlchemy 2.0 ORM)."""

    async def list_notifications(
        self,
        user_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Recipient's notifications, newest first. Own rows only."""
        try:
            t = InboxNotifications
            stmt = select(t).where(t.user_id == user_id)
            if unread_only:
                stmt = stmt.where(t.read_at.is_(None))
            stmt = (
                stmt.order_by(t.created_at.desc())
                .limit(max(1, min(limit, 200)))
                .offset(max(0, offset))
            )
            async with read_scope() as session:
                rows = (await session.execute(stmt)).scalars().all()
            return [_to_dict(r) for r in rows]
        except Exception as exc:
            logger.error("list_notifications failed for {}: {}", user_id, exc)
            return []

    async def unread_count(self, user_id: str) -> int:
        """Count the recipient's unread notifications."""
        try:
            t = InboxNotifications
            async with read_scope() as session:
                result = await session.execute(
                    select(func.count())
                    .select_from(t)
                    .where(t.user_id == user_id)
                    .where(t.read_at.is_(None))
                )
            return int(result.scalar() or 0)
        except Exception as exc:
            logger.error("unread_count failed for {}: {}", user_id, exc)
            return 0

    async def get_owner(self, notification_id: str) -> Optional[str]:
        """Return the recipient user_id of a row, or None if it doesn't exist.

        Lets the router distinguish 'not found' from 'not yours' — both surface
        as 404 so a foreign id is never confirmed to exist."""
        try:
            t = InboxNotifications
            async with read_scope() as session:
                result = await session.execute(
                    select(t.user_id).where(t.id == int(notification_id))
                )
            owner = result.scalar()
            return str(owner) if owner is not None else None
        except Exception as exc:
            logger.error("get_owner failed for {}: {}", notification_id, exc)
            return None

    async def mark_read(self, notification_id: str, user_id: str) -> bool:
        """Mark one own row read (idempotent). Returns True if a matching row
        was found for this user (already-read counts as success), False if no
        such row belongs to the user (router → 404)."""
        try:
            t = InboxNotifications
            now = datetime.now(timezone.utc)
            async with write_scope() as session:
                result = await session.execute(
                    update(t)
                    .where(t.id == int(notification_id))
                    .where(t.user_id == user_id)
                    .where(t.read_at.is_(None))
                    .values(read_at=now)
                )
                if result.rowcount and result.rowcount > 0:
                    return True
                # No unread row updated — confirm the row exists & is ours
                # (already-read is still a success for an idempotent endpoint).
                owner = await session.execute(
                    select(t.id)
                    .where(t.id == int(notification_id))
                    .where(t.user_id == user_id)
                )
                return owner.first() is not None
        except Exception as exc:
            logger.error("mark_read failed for {}: {}", notification_id, exc)
            return False

    async def mark_all_read(self, user_id: str) -> int:
        """Mark all of the recipient's unread rows read. Returns count marked."""
        try:
            t = InboxNotifications
            now = datetime.now(timezone.utc)
            async with write_scope() as session:
                result = await session.execute(
                    update(t)
                    .where(t.user_id == user_id)
                    .where(t.read_at.is_(None))
                    .values(read_at=now)
                )
            return int(result.rowcount or 0)
        except Exception as exc:
            logger.error("mark_all_read failed for {}: {}", user_id, exc)
            return 0


_inbox_repository: Optional[InboxRepository] = None


def get_inbox_repository() -> InboxRepository:
    """Return the shared InboxRepository singleton."""
    global _inbox_repository
    if _inbox_repository is None:
        _inbox_repository = InboxRepository()
    return _inbox_repository
