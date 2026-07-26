"""Narrow notification inbox — the ``notify()`` producer helper (W3d).

This is the ONLY write path into ``public.inbox_notifications``. It is called
best-effort from exactly three producer sites (a notification failure must never
break the producing flow), so every insert is wrapped: any exception is logged
loudly and swallowed — the caller's success/failure is never affected.

NARROWNESS (the design): the inbox carries exactly three ``kind`` values —
``generation_result`` / ``publish_result`` / ``autopilot_output``. If you find
yourself tempted to add a fourth producer, don't — Realtime already covers
immediacy elsewhere, and comment/@mention notifications are deliberately out of
scope. Note the temptation in a PR instead.

DEDUPE: producers can double-fire across retry/mirror seams (e.g. a DBOS step
replay). Before inserting, an identical (user_id, kind, link_kind, link_id) row
inside a 10-minute window short-circuits the insert, so a recipient never sees
twins for the same event.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from loguru import logger
from sqlalchemy import and_, insert, select

from app.db.session import read_scope, write_scope
from app.models import InboxNotifications

NotificationKind = Literal[
    "generation_result", "publish_result", "autopilot_output", "workflow_stage"
]
NotificationSeverity = Literal["info", "success", "error"]
NotificationLinkKind = Literal["issue", "resource", "publish_batch"]

# Window inside which an identical (user_id, kind, link_kind, link_id) row
# suppresses a re-insert. Guards double-fire seams without hiding genuinely
# distinct later events.
_DEDUPE_WINDOW = timedelta(minutes=10)


async def _is_duplicate(
    user_id: str,
    kind: str,
    link_kind: Optional[str],
    link_id: Optional[str],
) -> bool:
    """True if an identical notification was created inside the dedupe window.

    NULL link_kind/link_id are matched with IS NULL (not ``= NULL``) so a
    link-less notification also dedupes correctly."""
    cutoff = datetime.now(timezone.utc) - _DEDUPE_WINDOW
    t = InboxNotifications
    conditions = [
        t.user_id == user_id,
        t.kind == kind,
        t.created_at >= cutoff,
        (t.link_kind == link_kind) if link_kind is not None else t.link_kind.is_(None),
        (t.link_id == link_id) if link_id is not None else t.link_id.is_(None),
    ]
    async with read_scope() as session:
        existing = await session.execute(select(t.id).where(and_(*conditions)).limit(1))
        return existing.first() is not None


async def notify(
    user_id: str,
    kind: NotificationKind,
    title: str,
    *,
    body: Optional[str] = None,
    severity: NotificationSeverity = "info",
    link_kind: Optional[NotificationLinkKind] = None,
    link_id: Optional[str] = None,
    team_id: Optional[int] = None,
) -> Optional[int]:
    """Best-effort insert of a single inbox notification for one recipient.

    Returns the new row id, ``None`` if the insert was deduped, and ``None``
    (with a loud log) on any failure. NEVER raises — producers call this on
    their success/failure paths and must not be affected by inbox trouble.
    """
    try:
        if not user_id:
            logger.warning("notify() skipped — empty user_id (kind={})", kind)
            return None

        if await _is_duplicate(user_id, kind, link_kind, link_id):
            logger.debug(
                "notify() deduped — {} {}/{} for {} within {}m",
                kind,
                link_kind,
                link_id,
                str(user_id)[:8],
                int(_DEDUPE_WINDOW.total_seconds() // 60),
            )
            return None

        tbl = InboxNotifications.__table__
        async with write_scope() as session:
            new_id = (
                await session.execute(
                    insert(tbl)
                    .values(
                        user_id=user_id,
                        kind=kind,
                        title=title,
                        body=body,
                        severity=severity,
                        link_kind=link_kind,
                        link_id=link_id,
                        team_id=team_id,
                    )
                    .returning(tbl.c.id)
                )
            ).scalar()
        return int(new_id) if new_id is not None else None
    except Exception as exc:  # best-effort: never break the producing flow
        logger.error(
            "notify() failed (non-fatal) — kind={} user={} link={}/{}: {}",
            kind,
            str(user_id)[:8],
            link_kind,
            link_id,
            exc,
        )
        return None
