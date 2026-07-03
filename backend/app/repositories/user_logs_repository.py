# backend/app/repositories/user_logs_repository.py

"""用户日志仓库

提供用户操作日志的存储和查询功能。

ORM 2.0 (post-rollout, Batch L2 collapsed): ``UserLogsRepository`` is the
SQLAlchemy 2.0 ORM implementation of the append-only ``user_logs`` write path
plus the get_recent / get_paginated / get_by_aweme_id read surface. The former
supabase-py REST bodies and the ``USE_ORM_USER_LOGS`` routing flag are retired;
``get_user_logs_repository()`` (and the module-level ``log_user_action`` helper)
unconditionally return this class. Writes commit via ``write_scope()``.

TWO REPOS, ONE TABLE: the ``user_logs`` table is ALSO served by
``LogsRepository`` (the user-facing viewer/export) — disjoint method sets, both
live; each keeps its own repo over the same ``UserLogs`` model.

PRESERVED LEGACY GUARD — create() skips on a missing user_id: Celery retry paths
(scheduled_tasks.retry_failed_downloads) can pull rows with a NULL user_id from
legacy/system downloads; ``create(None, ...)`` used to raise a 23502 NOT-NULL
violation and spam ERROR logs once per orphan. The guard soft-skips (returns
None) when user_id is missing, BEFORE opening any session.

STRATEGY-C VALUE-TYPE PARITY (per-field, exact legacy REST shape)
=================================================================
  user_logs.id : bigint → STAYS native int (the 5.3 trap).
  user_logs.user_id : uuid → STR for shape parity. Consumer audit: the callers
    (log_user_action fire-and-forget; media/auth routers; downloader) treat
    create()'s result as fire-and-forget (return value ignored) and the read
    methods feed dicts to HTTP/UI; none does ``UUID(log["user_id"])`` or a
    ``log["user_id"] == ...`` compare. Coercion is shape-parity only.
  user_logs.created_at : timestamptz → ``.isoformat()`` ALWAYS (the template
    rule; get_paginated/get_recent surface created_at to the UI as an ISO
    string in the REST baseline).
  status / action / message / aweme_id (text) → native str; details (JSONB) →
    native dict.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, insert, select

from app.db.session import read_scope, write_scope
from app.models import UserLogs
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

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


class UserLogsRepository:
    """用户日志仓库 (异步, ORM-backed — append-only writer + reads)"""

    TABLE_NAME = "user_logs"

    def __init__(self):
        pass

    async def create(
        self,
        user_id: str,
        action: str,
        message: str,
        status: str = "info",
        aweme_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict]:
        """
        创建日志记录

        Args:
            user_id: 用户 ID
            action: 操作类型 (fetch, download, delete, retry, update, login, logout)
            message: 日志消息
            status: 状态 (success, error, warning, info, pending)
            aweme_id: 关联的视频 ID（可选）
            details: 额外详情（可选）

        Returns:
            创建的日志记录（parity dict：id int, user_id str, created_at ISO str）

        Skips writes when ``user_id`` is missing — Celery retry paths
        (scheduled_tasks.retry_failed_downloads) can pull rows with a
        NULL ``user_id`` from legacy/system-initiated downloads, and
        calling create(None, ...) used to fail loudly with 23502 NOT
        NULL violation, spamming ERROR logs once per orphan download.
        Soft-skip is correct: if there's no user, there's no per-user
        log to create. (PRESERVED LEGACY GUARD — see module docstring;
        the check runs BEFORE opening any session.)
        """
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
        """
        获取最近的日志记录（newest-first, limit honored, optional action filter）

        Args:
            user_id: 用户 ID
            limit: 返回数量
            action: 筛选特定操作类型（可选）

        Returns:
            日志记录列表
        """
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
        """
        Get paginated and filtered logs.

        Returns:
            Dict with 'logs', 'total', 'page', 'page_size', 'total_pages'; on
            failure returns the empty envelope (parity with the legacy except
            path).
        """
        try:
            base = select(UserLogs).where(UserLogs.user_id == user_id)

            if level and level != "all":
                base = base.where(UserLogs.status == level)

            # date_from / end_date are ISO STRINGS at the API boundary; comparing
            # the timestamptz column against a bare VARCHAR raises in PG (the
            # typed ORM column does not auto-coerce like PostgREST did). Parse to
            # tz-aware UTC datetimes before binding — explicit tzinfo matches the
            # canonical sibling (resources_repository) and drops the latent
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
        """
        获取特定视频的日志记录

        Args:
            user_id: 用户 ID
            aweme_id: 视频 ID
            limit: 返回数量

        Returns:
            日志记录列表
        """
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


def get_user_logs_repository() -> "UserLogsRepository":
    """Return the ORM-backed UserLogsRepository (per-domain rollout flag retired
    — prod runs 100% ORM)."""
    return UserLogsRepository()


# 便捷的日志记录函数
async def log_user_action(
    user_id: str,
    action: str,
    message: str,
    status: str = "info",
    aweme_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """便捷的日志记录函数"""
    repo = get_user_logs_repository()
    await repo.create(
        user_id=user_id,
        action=action,
        message=message,
        status=status,
        aweme_id=aweme_id,
        details=details,
    )
