"""SQLAlchemy 2.0 ORM implementation of LogsRepository (Batch L2).

REST → ORM successor for the user-facing ``user_logs`` viewer/export surface,
following the validated ``AgentRepositoryOrm`` pilot template.
``LogsRepositoryOrm`` subclasses ``LogsRepository`` and overrides the data
methods; the ``TABLE_NAME`` constant is inherited. Call sites route through
``get_logs_repository()``.

NOTE — TWO REPOS, ONE TABLE: ``user_logs`` is served by BOTH ``LogsRepository``
(this file's parent — the user-facing logs viewer / CSV export, methods
get_logs / get_logs_for_export / create_log / delete_logs) AND
``UserLogsRepository`` (the append-only write path + get_recent / get_paginated /
get_by_aweme_id). They are GENUINELY DISTINCT (disjoint method sets, different
purposes), both LIVE, so each gets its own Orm subclass over the same model.

PHANTOM-COLUMN PRE-FLIGHT (template-v2 mandatory first step): every write path
here (create_log) writes only user_id / action / message / status / aweme_id /
details — all present on the ``UserLogs`` model and the real schema (verified
against information_schema). No phantom column. No try/except-wrapped dead write.

STRATEGY-C VALUE-TYPE PARITY (per-field, exact REST shape)
==========================================================
  user_logs.id : bigint → STAYS native int (the 5.3 trap; the LogEntry response
    model declares ``id: int``).
  user_logs.user_id : uuid → STR for shape parity. Consumer audit: logs_router's
    LogEntry response model does NOT include user_id, and no consumer does
    ``UUID(log["user_id"])`` or a ``log["user_id"] == ...`` compare — user_id is
    input-only on the read side. Coercion is shape-parity only (cheap, matches
    the REST SELECT * which returned a str).
  user_logs.created_at : timestamptz → ``.isoformat()`` ALWAYS. CONSUMED: the
    CSV export does ``str(log["created_at"])`` (idempotent on an ISO str, but a
    native datetime's ``str()`` uses a SPACE separator ≠ REST's ISO ``T``); the
    LogEntry model takes ``created_at: datetime`` which parses an ISO str fine.
  status / action / message / aweme_id (text) → native str; details (JSONB) →
    native dict.

Model-quirk scan: ``UserLogs`` has no renamed column and no SQLAlchemy ``Enum``
column (status is a plain String with a server default). ``_plain`` is not
load-bearing; reads route through ``_name_to_attr`` + ``_orm_obj_to_dict``.

Write-input audit: create_log binds a str user_id (Uuid type processor) +
text/JSONB columns — no raw ``values()`` type hazard.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import delete, func, insert, select

from app.db.session import read_scope, write_scope
from app.models import UserLogs
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.logs_repository import LogsRepository

_USER_LOGS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(UserLogs)


def _log_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``user_logs`` ORM row with strategy-C parity:
    bigint id stays native int, user_id uuid → str, created_at → ISO str. NULLs
    pass through unchanged."""
    out = _orm_obj_to_dict(obj, _USER_LOGS_NAME_TO_ATTR)
    val = out.get("user_id")
    if val is not None:
        out["user_id"] = str(val)
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


def _apply_filters(
    stmt,
    user_id: str,
    levels: Optional[List[str]],
    start_date: Optional[date],
    end_date: Optional[date],
    search: Optional[str],
):
    """Apply the shared user/level/date/search filters to a SELECT, mirroring
    the REST query construction exactly (status IN levels, created_at range with
    the end-of-day end bound, ilike message search).

    The date bounds are bound as tz-aware ``datetime`` objects (NOT
    ``.isoformat()`` strings): comparing the ``timestamptz`` column against a
    bare VARCHAR makes PG raise ``operator does not exist: timestamp with time
    zone < character varying`` (PostgREST auto-coerced the string; the typed ORM
    column does not). The bounds carry an explicit ``tzinfo=timezone.utc`` to
    match the canonical sibling (``resources_repository_orm`` created_after/
    created_before) and remove the latent "engine session is UTC" dependency."""
    stmt = stmt.where(UserLogs.user_id == user_id)
    if levels:
        stmt = stmt.where(UserLogs.status.in_(levels))
    if start_date:
        stmt = stmt.where(
            UserLogs.created_at
            >= datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        )
    if end_date:
        end_dt = datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)
        stmt = stmt.where(UserLogs.created_at <= end_dt)
    if search:
        stmt = stmt.where(UserLogs.message.ilike(f"%{search}%"))
    return stmt


class LogsRepositoryOrm(LogsRepository):
    """ORM-backed LogsRepository (user-facing user_logs viewer / export)."""

    async def get_logs(
        self,
        user_id: str,
        levels: Optional[List[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[dict], int]:
        """Filtered + paginated logs for a user, newest-first, with an exact
        total count. Returns (logs, total) — same tuple shape as REST."""
        base = _apply_filters(
            select(UserLogs), user_id, levels, start_date, end_date, search
        )
        offset = (page - 1) * page_size
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count()).select_from(base.subquery())
            )
            result = await session.execute(
                base.order_by(UserLogs.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            logs = [_log_to_dict(r) for r in result.scalars().all()]
        return logs, (total or 0)

    async def get_logs_for_export(
        self,
        user_id: str,
        levels: Optional[List[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        search: Optional[str] = None,
        limit: int = 10000,
    ) -> List[dict]:
        """All logs matching the filters (no pagination), newest-first, capped
        at ``limit`` — for CSV/JSON export."""
        base = _apply_filters(
            select(UserLogs), user_id, levels, start_date, end_date, search
        )
        async with read_scope() as session:
            result = await session.execute(
                base.order_by(UserLogs.created_at.desc()).limit(limit)
            )
            return [_log_to_dict(r) for r in result.scalars().all()]

    async def create_log(
        self,
        user_id: str,
        action: str,
        message: str,
        status: str = "info",
        aweme_id: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> Optional[dict]:
        """Insert a log entry; returns the inserted row dict (or None if no row
        came back — REST-contract parity). Committing."""
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
        logger.info(f"Created log for user {user_id}: {action} - {message}")
        return out

    async def delete_logs(
        self, user_id: str, before_date: Optional[date] = None
    ) -> int:
        """Delete a user's logs (optionally only those before ``before_date``).
        Returns the number deleted. Committing."""
        stmt = delete(UserLogs).where(UserLogs.user_id == user_id)
        if before_date:
            # Bind the bound as a tz-aware datetime, not an ISO string — comparing
            # the timestamptz column to a VARCHAR raises in PG (see _apply_filters).
            stmt = stmt.where(
                UserLogs.created_at
                < datetime.combine(
                    before_date, datetime.min.time(), tzinfo=timezone.utc
                )
            )
        async with write_scope() as session:
            result = await session.execute(stmt)
            deleted_count = result.rowcount or 0
        logger.info(f"Deleted {deleted_count} logs for user {user_id}")
        return deleted_count


__all__ = ["LogsRepositoryOrm"]
