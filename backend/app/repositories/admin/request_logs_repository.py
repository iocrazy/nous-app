"""Repositories for the three log tables surfaced in the admin console:
api_request_logs, frontend_error_logs, application_logs.

The admin router previously built each of these filter chains inline,
which made adding a new filter mean touching three places. Centralizing
them here keeps the column lists, noise-exclusion policies, and status-
group bucketing in one module.

ORM 2.0 (post-rollout collapse). The three ``list_with_filters`` reads plus
``RequestLogsRepository.stats_since`` are the SQLAlchemy 2.0 implementation: they
go through ``read_scope()`` with ``select`` statements and build SELECT *-shaped
dicts via ``_orm_obj_to_dict`` + a precomputed ``_name_to_attr`` map per model, so
that FrontendErrorLogs' renamed ``metadata`` → ``metadata_`` attribute is keyed
back as the DB column name ``metadata``.

STRATEGY-C VALUE-TYPE PARITY (per-field, matches the byte-exact REST shape)
  timestamp (api_request_logs) / created_at (frontend_error_logs) / logged_at
    (application_logs), all timestamptz → ``.isoformat()`` ALWAYS (the routers set
    these on ``str`` Pydantic fields; the request-stats endpoint slices the ISO
    string).
  id (BIGINT) → native int (routers ``str()`` it). status_code / response_time_ms
    / line (int) → native int. query_params / request_body / extra / metadata
    (jsonb) → native dict. user_id (uuid) → str (in SELECT * but never a dict key
    in these routers).
  method / path / level / module / message / error_type / exception (text) →
    native str.

DATE-RANGE FILTER BINDING (v3 rule — many date windows here): every
``start_date`` / ``end_date`` binds a NATIVE tz-aware ``datetime`` (naive →
assumed UTC), never an ISO string, so the typed timestamptz comparison does not
raise the timestamptz<VARCHAR error. All three repos READ ONLY.

``RequestLogsRepository.request_log_stats`` stays on the supabase-py client: it is
the ``rpc_request_log_stats`` server-side aggregation (migration 270) which has no
ORM successor, so ``_client()`` and the ``get_async_supabase_admin`` import are
retained for that one method (exactly prod behavior today).
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.db import get_async_supabase_admin
from app.db.session import read_scope
from app.models import ApiRequestLogs, ApplicationLogs, FrontendErrorLogs
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_REQUEST_N2A: Dict[str, str] = _name_to_attr(ApiRequestLogs)
_FRONTEND_N2A: Dict[str, str] = _name_to_attr(FrontendErrorLogs)
_APP_N2A: Dict[str, str] = _name_to_attr(ApplicationLogs)


def _aware(dt: datetime) -> datetime:
    """tz-aware datetime for a timestamptz filter bind (naive → assume UTC)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _row(obj: Any, name_to_attr: Dict[str, str]) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict: uuid → str, datetime → ISO str.
    NULLs pass through. Built via the renamed-column-safe attribute map."""
    out = _orm_obj_to_dict(obj, name_to_attr)
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


async def _paginate(base, order_col, page: int, page_size: int):
    """Run an exact COUNT over ``base`` then the ordered, paginated page. Returns
    (rows_objs, total). Shared by all three list_with_filters."""
    offset = (page - 1) * page_size
    async with read_scope() as session:
        total = await session.scalar(select(func.count()).select_from(base.subquery()))
        result = await session.execute(
            base.order_by(order_col.desc()).offset(offset).limit(page_size)
        )
        objs = result.scalars().all()
    return objs, (total or 0)


class RequestLogsRepository:
    async def _client(self):
        return await get_async_supabase_admin()

    async def list_with_filters(
        self,
        *,
        page: int,
        page_size: int,
        method: Optional[str] = None,
        path: Optional[str] = None,
        status_group: Optional[str] = None,
        user_id: Optional[str] = None,
        min_response_time: Optional[int] = None,
        request_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        base = select(ApiRequestLogs)
        if method:
            base = base.where(ApiRequestLogs.method == method.upper())
        if path:
            base = base.where(ApiRequestLogs.path.ilike(f"%{path}%"))
        if status_group == "2xx":
            base = base.where(ApiRequestLogs.status_code >= 200).where(
                ApiRequestLogs.status_code < 300
            )
        elif status_group == "4xx":
            base = base.where(ApiRequestLogs.status_code >= 400).where(
                ApiRequestLogs.status_code < 500
            )
        elif status_group == "5xx":
            base = base.where(ApiRequestLogs.status_code >= 500).where(
                ApiRequestLogs.status_code < 600
            )
        if user_id:
            base = base.where(ApiRequestLogs.user_id == user_id)
        if min_response_time:
            base = base.where(ApiRequestLogs.response_time_ms >= min_response_time)
        if request_id:
            base = base.where(ApiRequestLogs.request_id == request_id)
        if start_date:
            base = base.where(ApiRequestLogs.timestamp >= _aware(start_date))
        if end_date:
            base = base.where(ApiRequestLogs.timestamp <= _aware(end_date))

        objs, total = await _paginate(base, ApiRequestLogs.timestamp, page, page_size)
        return [_row(o, _REQUEST_N2A) for o in objs], total

    async def stats_since(self, start_time: datetime) -> List[dict[str, Any]]:
        """{method, status_code, path, response_time_ms, timestamp} since
        ``start_time`` (column-subset; timestamp → ISO str)."""
        stmt = select(
            ApiRequestLogs.method,
            ApiRequestLogs.status_code,
            ApiRequestLogs.path,
            ApiRequestLogs.response_time_ms,
            ApiRequestLogs.timestamp,
        ).where(ApiRequestLogs.timestamp >= _aware(start_time))
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [
                {
                    "method": method,
                    "status_code": status_code,
                    "path": path,
                    "response_time_ms": response_time_ms,
                    "timestamp": (
                        timestamp.isoformat()
                        if isinstance(timestamp, datetime)
                        else timestamp
                    ),
                }
                for method, status_code, path, response_time_ms, timestamp in (
                    result.all()
                )
            ]

    async def request_log_stats(self, start_time: datetime) -> dict[str, Any]:
        """Server-side aggregated request-log stats (``rpc_request_log_stats``,
        migration 270).

        Replaces the old ``stats_since`` fetch-all + Python aggregation, which
        was capped at 1000 rows by PostgREST on the REST path (stats computed
        from a tiny truncated sample) and an unbounded full-window fetch on the
        ORM path. The RPC does the GROUP BY in SQL and returns only the small
        aggregated payload — correct at any scale, one round-trip, no row dump.
        Kept on the supabase-py client: the RPC has no ORM successor, so this is
        exactly the prod behavior today.

        Returns a dict: ``{total, by_method, by_status, top_paths, by_hour}``.
        """
        client = await self._client()
        result = await client.rpc(
            "rpc_request_log_stats", {"p_since": start_time.isoformat()}
        ).execute()
        return result.data or {}


class FrontendErrorLogsRepository:
    async def list_with_filters(
        self,
        *,
        page: int,
        page_size: int,
        error_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        base = select(FrontendErrorLogs)
        if error_type:
            base = base.where(FrontendErrorLogs.error_type == error_type)
        if start_date:
            base = base.where(FrontendErrorLogs.created_at >= _aware(start_date))
        if end_date:
            base = base.where(FrontendErrorLogs.created_at <= _aware(end_date))

        objs, total = await _paginate(
            base, FrontendErrorLogs.created_at, page, page_size
        )
        return [_row(o, _FRONTEND_N2A) for o in objs], total


class AppLogsRepository:
    # Infrastructure noise — hidden from the Application Logs view unless the
    # admin explicitly filters by module.
    NOISE_MODULES = ("httpx", "uvicorn.access", "uvicorn.error", "celery.beat")

    async def list_with_filters(
        self,
        *,
        page: int,
        page_size: int,
        level: Optional[str] = None,
        module: Optional[str] = None,
        message: Optional[str] = None,
        has_exception: Optional[bool] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        base = select(ApplicationLogs)

        # Hide infrastructure noise unless an explicit module filter is set —
        # reproduces the legacy per-module .neq() chain exactly.
        if not module:
            for noise in self.NOISE_MODULES:
                base = base.where(ApplicationLogs.module != noise)

        if level:
            base = base.where(ApplicationLogs.level == level.upper())
        if module:
            base = base.where(ApplicationLogs.module.ilike(f"%{module}%"))
        if message:
            base = base.where(ApplicationLogs.message.ilike(f"%{message}%"))
        if has_exception is True:
            base = base.where(ApplicationLogs.exception.isnot(None))
        elif has_exception is False:
            base = base.where(ApplicationLogs.exception.is_(None))
        if start_date:
            base = base.where(ApplicationLogs.logged_at >= _aware(start_date))
        if end_date:
            base = base.where(ApplicationLogs.logged_at <= _aware(end_date))

        objs, total = await _paginate(base, ApplicationLogs.logged_at, page, page_size)
        return [_row(o, _APP_N2A) for o in objs], total


def get_request_logs_repository() -> "RequestLogsRepository":
    """Return the RequestLogsRepository (ORM-only post-rollout)."""
    return RequestLogsRepository()


def get_frontend_error_logs_repository() -> "FrontendErrorLogsRepository":
    """Return the FrontendErrorLogsRepository (ORM-only post-rollout)."""
    return FrontendErrorLogsRepository()


def get_app_logs_repository() -> "AppLogsRepository":
    """Return the AppLogsRepository (ORM-only post-rollout)."""
    return AppLogsRepository()
