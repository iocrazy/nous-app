"""SQLAlchemy 2.0 ORM implementation of MonitoringRepository (Phase 2 admin wave).

REST → ORM successor for the admin monitoring dashboard, which reads three log
tables: ``api_request_logs`` / ``application_logs`` / ``frontend_error_logs``.
``MonitoringRepositoryOrm`` subclasses ``MonitoringRepository`` and overrides
every data method; the table/column constants are inherited. Call sites route
through ``get_monitoring_repository()`` (bottom of ``monitoring_repository.py``).

MODELS (all reflected, verified):
  ApiRequestLogs   (api_request_logs)
  ApplicationLogs  (application_logs)
  FrontendErrorLogs(frontend_error_logs)

COLUMN-SUBSET SELECTS (not SELECT *)
====================================
The legacy selects only specific columns per query, so we select the SAME named
ORM attributes and build the dict by hand (not the generic _orm_obj_to_dict
sweep). This keeps the dict keys byte-identical to the legacy projection.

  request_logs_between → path, method, status_code, response_time_ms, timestamp
  app_logs_between     → level, module, message, logged_at
  frontend_error_count → COUNT(*) (returns native int)

★ UUID AUDIT: none of the THREE projections include a uuid column. (The tables
HAVE a uuid user_id, but it is never selected here.) No uuid coercion needed.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  timestamp (api_request_logs, timestamptz) → **.isoformat()** ALWAYS. CONSUMED:
    the monitoring router does ``ts.replace("Z", "+00:00")`` then
    ``datetime.fromisoformat(ts)`` — a native ``datetime`` has no ``.replace(str)``
    with that signature (it would raise / mis-bind), so this MUST be an ISO str.
  logged_at (application_logs, timestamptz) → **.isoformat()** ALWAYS. CONSUMED:
    set on ``RecentErrorEntry.logged_at: str`` and used in the recent-errors list.
  status_code / response_time_ms (int) → native int (the router does numeric
    comparisons / sums; REST returned JSON numbers too).
  path / method / level / module / message (text/varchar) → native str.
  frontend_error_count → native ``int`` (COUNT — the 5.3 trap; the OverviewStats
    field is ``frontend_error_count: int``).

DATE-RANGE FILTER BINDING (v3 rule — this is the LOTS-of-date-windows surface)
-----------------------------------------------------------------------------
Every method filters a timestamptz column with ``>= start`` / ``<= end``. The
legacy bound ``start.isoformat()`` strings (PostgREST auto-coerced); the typed
ORM ``timestamptz`` column compared to a VARCHAR raises in PG. We bind the NATIVE
``datetime`` objects the router already hands us (``get_time_range`` returns
tz-aware UTC datetimes) and defensively make any naive datetime tz-aware UTC.

ORDER + LIMIT are reproduced exactly (request asc by timestamp; app desc by
logged_at; both capped by ``limit``). READS ONLY.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

from sqlalchemy import func, select

from app.db.session import read_scope
from app.models import ApiRequestLogs, ApplicationLogs, FrontendErrorLogs
from app.repositories.admin.monitoring_repository import MonitoringRepository


def _aware(dt: datetime) -> datetime:
    """tz-aware datetime for a timestamptz filter bind (naive → assume UTC)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _iso(dt: Any) -> Any:
    """ISO-string a datetime (NULL/non-datetime pass through) for parity with the
    REST projection, which returned timestamptz as an ISO string."""
    return dt.isoformat() if isinstance(dt, datetime) else dt


class MonitoringRepositoryOrm(MonitoringRepository):
    """ORM-backed MonitoringRepository (admin monitoring dashboard reads)."""

    async def request_logs_between(
        self,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> List[dict[str, Any]]:
        """api_request_logs in [start, end], oldest-first, capped at ``limit``.
        Projection: path / method / status_code / response_time_ms / timestamp
        (timestamp → ISO str)."""
        stmt = (
            select(
                ApiRequestLogs.path,
                ApiRequestLogs.method,
                ApiRequestLogs.status_code,
                ApiRequestLogs.response_time_ms,
                ApiRequestLogs.timestamp,
            )
            .where(ApiRequestLogs.timestamp >= _aware(start))
            .where(ApiRequestLogs.timestamp <= _aware(end))
            .order_by(ApiRequestLogs.timestamp.asc())
            .limit(limit)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [
                {
                    "path": path,
                    "method": method,
                    "status_code": status_code,
                    "response_time_ms": response_time_ms,
                    "timestamp": _iso(timestamp),
                }
                for path, method, status_code, response_time_ms, timestamp in result.all()
            ]

    async def app_logs_between(
        self,
        start: datetime,
        end: datetime,
        limit: int = 5000,
    ) -> List[dict[str, Any]]:
        """application_logs in [start, end], newest-first, capped at ``limit``.
        Projection: level / module / message / logged_at (logged_at → ISO str)."""
        stmt = (
            select(
                ApplicationLogs.level,
                ApplicationLogs.module,
                ApplicationLogs.message,
                ApplicationLogs.logged_at,
            )
            .where(ApplicationLogs.logged_at >= _aware(start))
            .where(ApplicationLogs.logged_at <= _aware(end))
            .order_by(ApplicationLogs.logged_at.desc())
            .limit(limit)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [
                {
                    "level": level,
                    "module": module,
                    "message": message,
                    "logged_at": _iso(logged_at),
                }
                for level, module, message, logged_at in result.all()
            ]

    async def frontend_error_count(
        self,
        start: datetime,
        end: datetime,
    ) -> int:
        """Exact COUNT(*) of frontend_error_logs in [start, end]. Native int."""
        stmt = (
            select(func.count())
            .select_from(FrontendErrorLogs)
            .where(FrontendErrorLogs.created_at >= _aware(start))
            .where(FrontendErrorLogs.created_at <= _aware(end))
        )
        async with read_scope() as session:
            total = await session.scalar(stmt)
        return total or 0


__all__ = ["MonitoringRepositoryOrm"]
