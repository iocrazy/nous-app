"""SQLAlchemy 2.0 ORM impl of the three admin-console log repositories
(Phase 2 admin wave): api_request_logs / frontend_error_logs / application_logs.

REST → ORM successors for the three log views in the admin console. Each Orm class
subclasses its legacy counterpart and overrides every data method; the ``TABLE`` /
``NOISE_MODULES`` constants are inherited. Call sites route through
``get_request_logs_repository()`` / ``get_frontend_error_logs_repository()`` /
``get_app_logs_repository()`` (bottom of ``request_logs_repository.py``).

MODELS (all reflected, verified):
  ApiRequestLogs    (api_request_logs)
  FrontendErrorLogs (frontend_error_logs)  — has a renamed ``metadata`` → ``metadata_``
  ApplicationLogs   (application_logs)

SELECT * SHAPE
==============
All three ``list_with_filters`` methods return the FULL row (the legacy did
``select("*")``), built via ``_orm_obj_to_dict`` + the precomputed ``_name_to_attr``
map per model so that:
  - the renamed FrontendErrorLogs ``metadata_`` attribute is keyed back as the DB
    column name ``metadata`` (the canonical rename trap — reading getattr(obj,
    "metadata") would hand back the SQLAlchemy MetaData registry);
  - there are NO SQLAlchemy Enum columns on any of the three models, so no
    ``_plain`` unwrap is load-bearing (``_orm_obj_to_dict`` applies it harmlessly).

★ UUID AUDIT (admin reads-across-all-users; service_role scope) ★
=================================================================
Each table HAS a uuid ``user_id`` column (api_request_logs.user_id,
frontend_error_logs.user_id) — and SELECT * returns it. It is NOT an authz ``==``
guard and NOT a dict key in any of these three routers (the routers read ``id`` /
``timestamp`` / ``logged_at`` / ``created_at`` / ``message`` / etc., never
``user_id`` as a key). But for byte-exact REST parity (REST returned uuid as a JSON
string) we str() every uuid value in ``_row``. The ``id`` columns are BIGINT (NOT
uuid) → native int; the routers do ``str(log["id"])`` themselves.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  timestamp (api_request_logs) / created_at (frontend_error_logs) / logged_at
    (application_logs), all timestamptz → **.isoformat()** ALWAYS. CONSUMED: the
    routers set these on ``*.timestamp/created_at/logged_at: str`` Pydantic fields
    (and the request-stats endpoint reads ``log.get("timestamp")`` for bucketing).
  id (BigInteger) → native int (router str()s it). status_code / response_time_ms
    / line (int) → native int. query_params / request_body / extra / metadata
    (jsonb) → native dict. uuid (user_id) → str (see audit above).
  method / path / level / module / message / error_type / exception / etc.
    (text/varchar) → native str.

FILTERS (reproduced exactly)
----------------------------
  RequestLogs.list_with_filters: method.upper() eq; path ILIKE %..%; status_group
    2xx/4xx/5xx → status_code range; user_id eq; min_response_time >=; request_id
    eq; timestamp date-range. Order timestamp DESC, paginated.
  FrontendErrorLogs.list_with_filters: error_type eq; created_at date-range. Order
    created_at DESC, paginated.
  AppLogs.list_with_filters: NOISE_MODULES exclusion when ``module`` is unset
    (``module != noise`` for each); level.upper() eq; module ILIKE %..%; message
    ILIKE %..%; has_exception True → ``exception IS NOT NULL`` / False → ``exception
    IS NULL`` (REPRODUCING the legacy ``.neq("exception", None)`` /
    ``.is_("exception","null")``); logged_at date-range. Order logged_at DESC.

DATE-RANGE FILTER BINDING (v3 rule — many date windows here)
------------------------------------------------------------
Every ``start_date`` / ``end_date`` filter binds a NATIVE tz-aware ``datetime``
(the routers hand ``datetime`` objects; naive → assumed UTC), never an ISO string,
so the typed timestamptz column comparison does not raise the timestamptz<VARCHAR
error. READS ONLY — none of the three repos write.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.db.session import read_scope
from app.models import ApiRequestLogs, ApplicationLogs, FrontendErrorLogs
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.admin.request_logs_repository import (
    AppLogsRepository,
    FrontendErrorLogsRepository,
    RequestLogsRepository,
)

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


class RequestLogsRepositoryOrm(RequestLogsRepository):
    """ORM-backed RequestLogsRepository (api_request_logs admin view)."""

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


class FrontendErrorLogsRepositoryOrm(FrontendErrorLogsRepository):
    """ORM-backed FrontendErrorLogsRepository (frontend_error_logs admin view)."""

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


class AppLogsRepositoryOrm(AppLogsRepository):
    """ORM-backed AppLogsRepository (application_logs admin view)."""

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


__all__ = [
    "RequestLogsRepositoryOrm",
    "FrontendErrorLogsRepositoryOrm",
    "AppLogsRepositoryOrm",
]
