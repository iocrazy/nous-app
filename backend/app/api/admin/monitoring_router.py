"""Admin API routes for system monitoring statistics."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.monitoring_repository import get_monitoring_repository

router = APIRouter()


# ============================================
# Response Schemas
# ============================================


class OverviewStats(BaseModel):
    total_requests: int
    error_rate: float
    avg_response_ms: float
    app_error_count: int
    frontend_error_count: int


class TrendPoint(BaseModel):
    time: str
    requests: int
    errors: int


class SlowApiEntry(BaseModel):
    path: str
    avg_ms: float
    p95_ms: float
    count: int


class ErrorEndpointEntry(BaseModel):
    path: str
    error_count: int
    last_status: Optional[int] = None


class ErrorModuleEntry(BaseModel):
    module: str
    count: int


class RecentErrorEntry(BaseModel):
    level: str
    module: Optional[str] = None
    message: str
    logged_at: str


class MonitoringStatsResponse(BaseModel):
    overview: OverviewStats
    request_trend: List[TrendPoint]
    top_slow_apis: List[SlowApiEntry]
    top_error_endpoints: List[ErrorEndpointEntry]
    log_level_distribution: dict
    top_error_modules: List[ErrorModuleEntry]
    recent_errors: List[RecentErrorEntry]


# ============================================
# Helper: compute time range and granularity
# ============================================

PERIOD_MAP = {
    "1h": (1, 5),  # 1 hour, 5-min buckets
    "6h": (6, 5),  # 6 hours, 5-min buckets
    "24h": (24, 60),  # 24 hours, 1-hour buckets
    "7d": (168, 360),  # 7 days, 6-hour buckets
    "30d": (720, 1440),  # 30 days, 1-day buckets
}


def get_time_range(
    period: Optional[str],
    start_date: Optional[datetime],
    end_date: Optional[datetime],
) -> tuple[datetime, datetime, int]:
    """Returns (start, end, bucket_minutes)."""
    now = datetime.now(timezone.utc)
    if start_date and end_date:
        delta_hours = (end_date - start_date).total_seconds() / 3600
        if delta_hours <= 6:
            bucket = 5
        elif delta_hours <= 24:
            bucket = 60
        elif delta_hours <= 168:
            bucket = 360
        else:
            bucket = 1440
        return start_date, end_date, bucket

    hours, bucket = PERIOD_MAP.get(period or "24h", (24, 60))
    return now - timedelta(hours=hours), now, bucket


# ============================================
# Main Endpoint
# ============================================


@router.get("/stats", response_model=MonitoringStatsResponse)
async def get_monitoring_stats(
    auth: AdminAuthDep,
    period: Optional[str] = Query("24h", pattern="^(1h|6h|24h|7d|30d)$"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
):
    """Get aggregated monitoring statistics for the admin dashboard.

    All 7 sections are aggregated server-side in SQL (``rpc_monitoring_stats``,
    mig 271) over the full window — correct at any log volume. The previous path
    fetched every api_request_logs (capped 10000) + application_logs (capped
    5000) row and aggregated in Python (an undercount on the capped REST path,
    a heavy full-window fetch on the ORM path).
    """
    repo = get_monitoring_repository()
    start, end, bucket_minutes = get_time_range(period, start_date, end_date)

    stats = await repo.monitoring_stats(start, end, bucket_minutes)
    ov = stats.get("overview", {})

    return MonitoringStatsResponse(
        overview=OverviewStats(
            total_requests=ov.get("total_requests", 0),
            error_rate=ov.get("error_rate", 0),
            avg_response_ms=ov.get("avg_response_ms", 0),
            app_error_count=ov.get("app_error_count", 0),
            frontend_error_count=ov.get("frontend_error_count", 0),
        ),
        request_trend=[TrendPoint(**t) for t in stats.get("request_trend", [])],
        top_slow_apis=[SlowApiEntry(**s) for s in stats.get("top_slow_apis", [])],
        top_error_endpoints=[
            ErrorEndpointEntry(**e) for e in stats.get("top_error_endpoints", [])
        ],
        log_level_distribution=stats.get("log_level_distribution", {}),
        top_error_modules=[
            ErrorModuleEntry(**m) for m in stats.get("top_error_modules", [])
        ],
        recent_errors=[RecentErrorEntry(**r) for r in stats.get("recent_errors", [])],
    )


# ============================================
# Database connection pressure
# ============================================


class ConnectionGroupEntry(BaseModel):
    application_name: str
    usename: str
    state: str
    count: int
    oldest_state_seconds: int


class ConnectionsResponse(BaseModel):
    """Live Postgres connection-slot usage.

    ``status`` is ``ok`` / ``warning`` (>=80%) / ``critical`` (>=95%) /
    ``unknown``. ``unknown`` means the reading could not be taken — the UI
    renders that as "no reading", never as healthy. Thresholds come from
    ``app.services.infra.pg_connection_monitor`` so this panel and the
    scheduled WARNING line can never disagree about what "high" means.
    """

    status: str
    used: Optional[int] = None
    max_connections: Optional[int] = None
    percent: Optional[float] = None
    idle_in_transaction: Optional[int] = None
    oldest_idle_in_transaction_seconds: Optional[int] = None
    warn_pct: float
    critical_pct: float
    reason: Optional[str] = None
    groups: List[ConnectionGroupEntry] = []


@router.get("/connections", response_model=ConnectionsResponse)
async def get_db_connections(auth: AdminAuthDep) -> ConnectionsResponse:
    """Live connection-slot snapshot + per-client breakdown.

    Queried on demand rather than read from the sampler's cache: an operator
    opening this panel is usually reacting to something, and a reading up to
    5 minutes stale is the wrong answer during an incident. The scheduled
    sampler and this endpoint share the same query helpers, so the summary
    numbers agree by construction.
    """
    from app.services.infra.pg_connection_monitor import (
        CRITICAL_PCT,
        WARN_PCT,
        fetch_connection_breakdown,
        sample_connection_usage,
    )

    summary = await sample_connection_usage()
    # Skip the GROUP BY entirely when the cheap summary already failed —
    # it would fail the same way, and one log line about it is enough.
    groups = (
        [] if summary.get("status") == "unknown" else await fetch_connection_breakdown()
    )

    return ConnectionsResponse(
        status=summary.get("status", "unknown"),
        used=summary.get("used"),
        max_connections=summary.get("max_connections"),
        percent=summary.get("percent"),
        idle_in_transaction=summary.get("idle_in_transaction"),
        oldest_idle_in_transaction_seconds=summary.get(
            "oldest_idle_in_transaction_seconds"
        ),
        warn_pct=WARN_PCT,
        critical_pct=CRITICAL_PCT,
        reason=summary.get("reason"),
        groups=[ConnectionGroupEntry(**g) for g in groups],
    )
