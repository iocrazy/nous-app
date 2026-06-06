"""SQLAlchemy 2.0 ORM implementation of NotificationRepository (Batch L1b).

REST → ORM successor for the notifications / user_notifications / team_members
surface, following the validated ``AgentRepositoryOrm`` pilot template.
``NotificationRepositoryOrm`` subclasses ``NotificationRepository`` and
overrides the data methods. Call sites route through
``get_notification_repository()``.

STRATEGY-C VALUE-TYPE PARITY (per-field, exact REST shape)
==========================================================
Supabase REST renders ``uuid`` → STRING, ``bigint`` → int, ``timestamptz`` →
ISO string. The ORM returns native ``uuid.UUID`` / ``int`` / ``datetime``.

  notifications.id / team_id : bigint → STAY native int (the 5.3 scope-zeroing
    trap). The team-membership filter does ``n.get("team_id") in team_ids`` with
    team_ids built from team_members.team_id (bigint) — BOTH sides int, so
    coercing either to str would silently drop every team notification. The
    read-status join keys on notifications.id (bigint) against
    user_notifications.notification_id (bigint) — same int-both-ways invariant.
    LEAVE INT.
  notifications.created_by : uuid → STR for shape parity (the consumer —
    notifications_router — serializes the dict straight to an HTTP response and
    never reads created_by type-sensitively in Python; coercion is cheap and
    future-proofs any UUID(...) consumer).
  notifications.created_at : timestamptz → ``.isoformat()`` ALWAYS (the
    unconditional template rule; the router passes created_at into the response
    model, where an ISO str matches the REST baseline exactly).
  type / title / content (text) → native str.
  read : a derived bool the repo adds (NOT a column) — unchanged.

Consumer audit (who reads get_user_notifications output):
  - notifications_router (the ONLY consumer): does ``str(n["id"])`` (works on
    int OR str), ``n["created_at"]`` → response model, ``n["read"]`` bool,
    ``n["title"] / n["content"] / n["type"]`` text. NO ``UUID(n[...])`` and NO
    type-sensitive uuid comparison. outbox_dispatcher / inbox_processor were
    checked and do NOT use this repo (they read the separate agent_inbox /
    agent_outbox tables). So no uuid OUTPUT coercion is load-bearing; created_by
    is coerced for shape parity only.

Model-quirk scan: notifications / user_notifications / team_members have NO
renamed columns and NO SQLAlchemy ``Enum`` columns (the type/visibility CHECKs
are DB-side, not PG enums). ``_plain`` is not load-bearing; we route reads
through ``_name_to_attr`` + ``_orm_obj_to_dict`` for mechanical parity /
rename-safety.

Write-input audit: mark_as_read / mark_all_as_read upsert user_notifications
with a str user_id (binds via the Uuid type processor) and a notification_id
(coerced to int for the BigInteger PK). No raw ``values()`` type hazard.

LEGACY QUIRK PRESERVED — delete_notification:
  The REST impl upserts a ``dismissed_at`` column that DOES NOT EXIST on
  user_notifications (verified against information_schema). The supabase-py call
  errors, the surrounding try/except catches it, and the method returns False —
  so its OBSERVABLE contract today is "graceful no-op returning False". The ORM
  override reproduces that EXACT contract (it cannot persist a phantom column
  either) rather than silently inventing new behavior. Fixing the dismiss
  feature is a logic change out of scope for a mechanical migration.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson). The
mark-as-read upserts are idempotent (ON CONFLICT (user_id, notification_id)).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import Notifications, TeamMembers, UserNotifications
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.notification_repository import NotificationRepository

# notifications DB-column-name → mapped-attribute-name (built once).
_NOTIFICATIONS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(Notifications)


def _notification_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``notifications`` ORM row with strategy-C
    parity: bigint id/team_id stay native int, created_by uuid → str,
    created_at → ISO str. NULLs pass through unchanged."""
    out = _orm_obj_to_dict(obj, _NOTIFICATIONS_NAME_TO_ATTR)
    val = out.get("created_by")
    if val is not None:
        out["created_by"] = str(val)
    # timestamptz → ISO (explicit + a generic datetime guard for future cols).
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


class NotificationRepositoryOrm(NotificationRepository):
    """ORM-backed NotificationRepository."""

    async def get_user_notifications(
        self, user_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get notifications for a user with read status.

        Mirrors the REST logic exactly: resolve the user's team memberships,
        then (system + team) notifications ordered newest-first, then join the
        per-user read status. bigint ids stay int so the in-Python
        ``n['team_id'] in team_ids`` filter and the id→read_status join keep
        matching."""
        try:
            async with read_scope() as session:
                # Team IDs the user is a member of (bigint → int).
                mem_result = await session.execute(
                    select(TeamMembers.team_id).where(TeamMembers.user_id == user_id)
                )
                team_ids = [int(t) for t in mem_result.scalars().all()]

                if team_ids:
                    # All notifications newest-first, then filter system|team in
                    # Python (matches the REST impl's post-filter on team_id).
                    result = await session.execute(
                        select(Notifications)
                        .order_by(Notifications.created_at.desc())
                        .limit(limit)
                    )
                    rows = [_notification_to_dict(r) for r in result.scalars().all()]
                    notifications = [
                        n
                        for n in rows
                        if n["type"] == "system" or n.get("team_id") in team_ids
                    ]
                else:
                    result = await session.execute(
                        select(Notifications)
                        .where(Notifications.type == "system")
                        .order_by(Notifications.created_at.desc())
                        .limit(limit)
                    )
                    notifications = [
                        _notification_to_dict(r) for r in result.scalars().all()
                    ]

                if not notifications:
                    return []

                # Per-user read status for these notifications (bigint ids).
                notification_ids = [n["id"] for n in notifications]
                rs_result = await session.execute(
                    select(
                        UserNotifications.notification_id,
                        UserNotifications.read_at,
                    )
                    .where(UserNotifications.user_id == user_id)
                    .where(UserNotifications.notification_id.in_(notification_ids))
                )
                read_map = {
                    int(row.notification_id): bool(row.read_at)
                    for row in rs_result.all()
                }

            return [{**n, "read": read_map.get(n["id"], False)} for n in notifications]
        except Exception as e:
            logger.error(f"Failed to get user notifications for {user_id}: {e}")
            return []

    async def mark_as_read(self, notification_id: str, user_id: str) -> bool:
        """Mark a notification as read (upsert user_notifications). Committing +
        idempotent (ON CONFLICT (user_id, notification_id))."""
        try:
            stmt = (
                pg_insert(UserNotifications)
                .values(
                    user_id=user_id,
                    notification_id=int(notification_id),
                    read_at=datetime.utcnow(),
                )
                .on_conflict_do_update(
                    index_elements=[
                        UserNotifications.user_id,
                        UserNotifications.notification_id,
                    ],
                    set_={"read_at": datetime.utcnow()},
                )
            )
            async with write_scope() as session:
                await session.execute(stmt)
            return True
        except Exception as e:
            logger.error(f"Failed to mark notification as read: {e}")
            return False

    async def mark_all_as_read(self, user_id: str) -> int:
        """Mark all of the user's unread notifications as read. Returns the
        count marked. Committing + idempotent."""
        try:
            notifications = await self.get_user_notifications(user_id)
            unread = [n for n in notifications if not n["read"]]
            if not unread:
                return 0

            now = datetime.utcnow()
            rows = [
                {
                    "user_id": user_id,
                    "notification_id": int(n["id"]),
                    "read_at": now,
                }
                for n in unread
            ]
            stmt = pg_insert(UserNotifications).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    UserNotifications.user_id,
                    UserNotifications.notification_id,
                ],
                set_={"read_at": stmt.excluded.read_at},
            )
            async with write_scope() as session:
                await session.execute(stmt)
            return len(rows)
        except Exception as e:
            logger.error(f"Failed to mark all notifications as read: {e}")
            return 0

    async def get_unread_count(self, user_id: str) -> int:
        """Count the user's unread notifications."""
        notifications = await self.get_user_notifications(user_id)
        return sum(1 for n in notifications if not n["read"])

    async def delete_notification(self, notification_id: str, user_id: str) -> bool:
        """LEGACY QUIRK PRESERVED: the REST impl upserts a ``dismissed_at``
        column that does not exist on user_notifications, so the call fails and
        the method returns False (a graceful no-op). The ORM cannot persist a
        phantom column either, so we reproduce the EXACT observable contract —
        return False without mutating anything. Fixing the dismiss feature is a
        logic change out of scope for this mechanical migration."""
        logger.warning(
            "delete_notification is a legacy no-op (user_notifications has no "
            "dismissed_at column); returning False for notification %s",
            notification_id,
        )
        return False


__all__ = ["NotificationRepositoryOrm"]
