"""SQLAlchemy 2.0 ORM implementation of UserLogsRepository (Batch L2).

REST → ORM successor for the append-only ``user_logs`` write path + the
get_recent / get_paginated / get_by_aweme_id read surface, following the
validated ``AgentRepositoryOrm`` pilot template. ``UserLogsRepositoryOrm``
subclasses ``UserLogsRepository`` and overrides the data methods; the
``TABLE_NAME`` constant is inherited. Call sites route through
``get_user_logs_repository()`` (and the module-level ``log_user_action`` helper,
which now uses the factory).

TWO REPOS, ONE TABLE: see ``logs_repository_orm`` — ``LogsRepository`` (viewer/
export) and ``UserLogsRepository`` (this file's parent — append-only writer +
distinct reads) are genuinely different live surfaces over ``user_logs``; each
gets its own Orm subclass over the same ``UserLogs`` model.

PHANTOM-COLUMN PRE-FLIGHT (template-v2 mandatory): the only write path
(``create``) writes user_id / action / message / status / aweme_id / details —
all present on the model + the real schema (verified). No phantom column.

PRESERVED LEGACY GUARD — create() skips on a missing user_id: Celery retry paths
(scheduled_tasks.retry_failed_downloads) can pull rows with a NULL user_id from
legacy/system downloads; ``create(None, ...)`` used to raise a 23502 NOT-NULL
violation and spam ERROR logs once per orphan. The REST impl soft-skips (returns
None) when user_id is missing — this Orm override keeps that EXACT guard, BEFORE
opening any session.

STRATEGY-C VALUE-TYPE PARITY (per-field, exact REST shape)
==========================================================
  user_logs.id : bigint → STAYS native int (the 5.3 trap).
  user_logs.user_id : uuid → STR for shape parity. Consumer audit: the callers
    (log_user_action fire-and-forget; media/auth routers; downloader) treat
    create()'s result as fire-and-forget (return value ignored) and the read
    methods feed dicts to HTTP/UI; none does ``UUID(log["user_id"])`` or a
    ``log["user_id"] == ...`` compare. Coercion is shape-parity only.
  user_logs.created_at : timestamptz → ``.isoformat()`` ALWAYS (the template
    rule; the get_paginated/get_recent dicts surface created_at to the UI as an
    ISO string in the REST baseline).
  status / action / message / aweme_id (text) → native str; details (JSONB) →
    native dict.

Model-quirk scan: ``UserLogs`` has no renamed column and no SQLAlchemy ``Enum``
column. ``_plain`` not load-bearing; reads route through ``_orm_obj_to_dict``.

Write-input audit: create binds a str user_id (Uuid processor) + text/JSONB —
no raw ``values()`` type hazard. Writes commit via ``write_scope()``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, insert, select

from app.db.session import read_scope, write_scope
from app.models import UserLogs
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.user_logs_repository import UserLogsRepository

_USER_LOGS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(UserLogs)


def _as_utc(dt: datetime) -> datetime:
    """Attach UTC tzinfo to a naive ISO-parsed datetime (a tz-aware one passes
    through). Binding a tz-aware bound against the timestamptz column matches the
    canonical sibling and avoids relying on the engine session's UTC setting."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _log_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``user_logs`` ORM row with strategy-C parity:
    bigint id stays native int, user_id uuid → str, created_at → ISO str."""
    out = _orm_obj_to_dict(obj, _USER_LOGS_NAME_TO_ATTR)
    val = out.get("user_id")
    if val is not None:
        out["user_id"] = str(val)
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


class UserLogsRepositoryOrm(UserLogsRepository):
    """ORM-backed UserLogsRepository (append-only writer + reads)."""

    async def create(
        self,
        user_id: str,
        action: str,
        message: str,
        status: str = "info",
        aweme_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict]:
        """Insert a log row; returns the inserted dict (or None). Soft-skips when
        user_id is missing (PRESERVED legacy guard — see module docstring).
        Committing; swallows failures into a None return to match REST."""
        if not user_id or str(user_id).lower() in ("none", "null"):
            logger.debug(
                f"[user-logs] skipping create with missing user_id "
                f"(action={action}, status={status})"
            )
            return None
        try:
            data: Dict[str, Any] = {
                "user_id": user_id,
                "action": action,
                "message": message,
                "status": status,
            }
            if aweme_id:
                data["aweme_id"] = aweme_id
            if details:
                data["details"] = details

            async with write_scope() as session:
                result = await session.execute(
                    insert(UserLogs).values(**data).returning(UserLogs)
                )
                row = result.scalars().first()
                out = _log_to_dict(row) if row else None
            if out:
                logger.debug(f"日志记录创建成功: {action} - {message}")
            return out
        except Exception as e:
            logger.error(f"创建日志记录失败: {e}")
            return None

    async def get_recent(
        self, user_id: str, limit: int = 20, action: Optional[str] = None
    ) -> List[Dict]:
        """Most-recent logs for a user (optionally filtered by action)."""
        try:
            stmt = (
                select(UserLogs)
                .where(UserLogs.user_id == user_id)
                .order_by(UserLogs.created_at.desc())
                .limit(limit)
            )
            if action:
                stmt = stmt.where(UserLogs.action == action)
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_log_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"获取日志记录失败: {e}")
            return []

    async def get_paginated(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 50,
        level: Optional[str] = None,
        date_range: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated + filtered logs. Returns the REST envelope
        {logs, total, page, page_size, total_pages}; on failure returns the
        empty envelope (parity with the REST except path)."""
        try:
            base = select(UserLogs).where(UserLogs.user_id == user_id)

            if level and level != "all":
                base = base.where(UserLogs.status == level)

            # date_from / end_date are ISO STRINGS at the API boundary; comparing
            # the timestamptz column against a bare VARCHAR raises in PG (the
            # typed ORM column does not auto-coerce like PostgREST did). Parse to
            # tz-aware UTC datetimes before binding — explicit tzinfo matches the
            # canonical sibling (resources_repository_orm) and drops the latent
            # "engine session is UTC" dependency.
            date_from = None
            if date_range and date_range != "custom":
                days_map = {"24h": 1, "7days": 7, "30days": 30, "90days": 90}
                days = days_map.get(date_range)
                if days:
                    date_from = datetime.now(timezone.utc) - timedelta(days=days)
            elif start_date:
                date_from = _as_utc(datetime.fromisoformat(start_date))

            if date_from:
                base = base.where(UserLogs.created_at >= date_from)
            if end_date:
                base = base.where(
                    UserLogs.created_at <= _as_utc(datetime.fromisoformat(end_date))
                )
            if search:
                base = base.where(UserLogs.message.ilike(f"%{search}%"))

            offset = (page - 1) * page_size
            async with read_scope() as session:
                total = await session.scalar(
                    select(func.count()).select_from(base.subquery())
                )
                total = total or 0
                result = await session.execute(
                    base.order_by(UserLogs.created_at.desc())
                    .offset(offset)
                    .limit(page_size)
                )
                logs = [_log_to_dict(r) for r in result.scalars().all()]

            total_pages = max(1, (total + page_size - 1) // page_size)
            return {
                "logs": logs,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            }
        except Exception as e:
            logger.error(f"获取分页日志失败: {e}")
            return {
                "logs": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
            }

    async def get_by_aweme_id(
        self, user_id: str, aweme_id: str, limit: int = 10
    ) -> List[Dict]:
        """Recent logs for a specific video (aweme_id) for a user."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(UserLogs)
                    .where(UserLogs.user_id == user_id)
                    .where(UserLogs.aweme_id == aweme_id)
                    .order_by(UserLogs.created_at.desc())
                    .limit(limit)
                )
                return [_log_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"获取视频日志失败: {e}")
            return []


__all__ = ["UserLogsRepositoryOrm"]
