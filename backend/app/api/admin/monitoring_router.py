"""Admin API routes for system monitoring statistics."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin

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


def _bucket_key(dt: datetime, bucket_minutes: int) -> str:
    """Truncate a datetime to its bucket key string."""
    minutes = (dt.hour * 60 + dt.minute) // bucket_minutes * bucket_minutes
    if bucket_minutes >= 1440:
        return dt.strftime("%Y-%m-%dT00:00")
    elif bucket_minutes >= 60:
        bucket_hour = minutes // 60
        return f"{dt.strftime('%Y-%m-%d')}T{bucket_hour:02d}:00"
    else:
        bucket_hour = minutes // 60
        bucket_min = minutes % 60
        return f"{dt.strftime('%Y-%m-%d')}T{bucket_hour:02d}:{bucket_min:02d}"


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
    """Get aggregated monitoring statistics for the admin dashboard."""
    supabase = await get_async_supabase_admin()
    start, end, bucket_minutes = get_time_range(period, start_date, end_date)
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    # ---- Fetch request logs ----
    req_result = await (
        supabase.table("api_request_logs")
        .select("path,method,status_code,response_time_ms,timestamp")
        .gte("timestamp", start_iso)
        .lte("timestamp", end_iso)
        .order("timestamp", desc=False)
        .limit(10000)
        .execute()
    )
    req_logs = req_result.data or []

    # ---- Fetch app logs (level counts + recent errors) ----
    app_result = await (
        supabase.table("application_logs")
        .select("level,module,message,logged_at")
        .gte("logged_at", start_iso)
        .lte("logged_at", end_iso)
        .order("logged_at", desc=True)
        .limit(5000)
        .execute()
    )
    app_logs = app_result.data or []

    # ---- Fetch frontend error count ----
    fe_result = await (
        supabase.table("frontend_error_logs")
        .select("id", count="exact")
        .gte("created_at", start_iso)
        .lte("created_at", end_iso)
        .execute()
    )
    fe_error_count = fe_result.count or 0

    # ---- Compute overview ----
    total_requests = len(req_logs)
    error_requests = sum(1 for r in req_logs if (r.get("status_code") or 0) >= 400)
    error_rate = round(
        (error_requests / total_requests * 100) if total_requests > 0 else 0, 2
    )
    avg_ms = round(
        sum(r.get("response_time_ms") or 0 for r in req_logs) / total_requests
        if total_requests > 0
        else 0,
        1,
    )
    app_error_count = sum(
        1 for a in app_logs if a.get("level") in ("ERROR", "CRITICAL")
    )

    overview = OverviewStats(
        total_requests=total_requests,
        error_rate=error_rate,
        avg_response_ms=avg_ms,
        app_error_count=app_error_count,
        frontend_error_count=fe_error_count,
    )

    # ---- Compute request trend (bucketed) ----
    buckets: dict[str, dict] = defaultdict(lambda: {"requests": 0, "errors": 0})
    for r in req_logs:
        ts = r.get("timestamp", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        key = _bucket_key(dt, bucket_minutes)
        buckets[key]["requests"] += 1
        if (r.get("status_code") or 0) >= 400:
            buckets[key]["errors"] += 1

    request_trend = [
        TrendPoint(time=k, requests=v["requests"], errors=v["errors"])
        for k, v in sorted(buckets.items())
    ]

    # ---- Compute top slow APIs ----
    path_stats: dict[str, list[int]] = defaultdict(list)
    for r in req_logs:
        ms = r.get("response_time_ms")
        path = r.get("path", "")
        if ms is not None and path:
            path_stats[path].append(ms)

    top_slow = []
    for path, times in path_stats.items():
        times_sorted = sorted(times)
        count = len(times_sorted)
        avg = round(sum(times_sorted) / count, 1)
        p95_idx = min(int(count * 0.95), count - 1)
        p95 = times_sorted[p95_idx]
        top_slow.append(
            SlowApiEntry(path=path, avg_ms=avg, p95_ms=p95, count=count)
        )

    top_slow.sort(key=lambda x: x.avg_ms, reverse=True)
    top_slow_apis = top_slow[:10]

    # ---- Compute top error endpoints ----
    error_paths: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "last_status": None}
    )
    for r in req_logs:
        sc = r.get("status_code") or 0
        if sc >= 400:
            path = r.get("path", "")
            error_paths[path]["count"] += 1
            error_paths[path]["last_status"] = sc

    top_error_endpoints = sorted(
        [
            ErrorEndpointEntry(
                path=p, error_count=v["count"], last_status=v["last_status"]
            )
            for p, v in error_paths.items()
        ],
        key=lambda x: x.error_count,
        reverse=True,
    )[:10]

    # ---- Compute log level distribution ----
    level_dist: dict[str, int] = defaultdict(int)
    for a in app_logs:
        level_dist[a.get("level", "UNKNOWN")] += 1

    # ---- Compute top error modules ----
    module_errors: dict[str, int] = defaultdict(int)
    for a in app_logs:
        if a.get("level") in ("ERROR", "CRITICAL", "WARNING"):
            mod = a.get("module") or "unknown"
            short = mod.split(".")[-1] if "." in mod else mod
            module_errors[short] += 1

    top_error_modules = sorted(
        [ErrorModuleEntry(module=m, count=c) for m, c in module_errors.items()],
        key=lambda x: x.count,
        reverse=True,
    )[:10]

    # ---- Recent errors (last 5 ERROR/CRITICAL) ----
    recent_errors = [
        RecentErrorEntry(
            level=a["level"],
            module=(a.get("module") or "").split(".")[-1] or None,
            message=a.get("message", ""),
            logged_at=a.get("logged_at", ""),
        )
        for a in app_logs
        if a.get("level") in ("ERROR", "CRITICAL")
    ][:5]

    return MonitoringStatsResponse(
        overview=overview,
        request_trend=request_trend,
        top_slow_apis=top_slow_apis,
        top_error_endpoints=top_error_endpoints,
        log_level_distribution=dict(level_dist),
        top_error_modules=top_error_modules,
        recent_errors=recent_errors,
    )
