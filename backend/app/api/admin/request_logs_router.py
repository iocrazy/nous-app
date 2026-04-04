"""Admin API routes for Request Logs and Frontend Error Logs."""

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin
from app.utils.admin_helpers import batch_get_user_info

router = APIRouter()


# ============================================
# Response Schemas
# ============================================


class RequestLogItem(BaseModel):
    id: str
    request_id: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    auth_type: str
    method: str
    path: str
    query_params: Optional[dict] = None
    request_body: Optional[dict] = None
    status_code: Optional[int] = None
    response_time_ms: Optional[int] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    error_detail: Optional[str] = None
    timestamp: str


class RequestLogListResponse(BaseModel):
    data: List[RequestLogItem]
    total: int


class MethodCount(BaseModel):
    method: str
    count: int


class StatusCount(BaseModel):
    status_group: str
    count: int


class TopPath(BaseModel):
    path: str
    count: int
    avg_response_time_ms: Optional[int] = None


class HourCount(BaseModel):
    hour: str
    count: int


class RequestLogStats(BaseModel):
    by_method: List[MethodCount]
    by_status: List[StatusCount]
    top_paths: List[TopPath]
    by_hour: List[HourCount]
    total: int


class AppLogItem(BaseModel):
    id: str
    level: str
    message: str
    module: Optional[str] = None
    function: Optional[str] = None
    line: Optional[int] = None
    file_path: Optional[str] = None
    exception: Optional[str] = None
    extra: Optional[dict] = None
    logged_at: str


class AppLogListResponse(BaseModel):
    data: List[AppLogItem]
    total: int


class FrontendErrorItem(BaseModel):
    id: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    session_id: Optional[str] = None
    error_type: str
    message: Optional[str] = None
    stack: Optional[str] = None
    url: Optional[str] = None
    component: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: Optional[dict] = None
    created_at: str


class FrontendErrorListResponse(BaseModel):
    data: List[FrontendErrorItem]
    total: int


# ============================================
# Endpoints
# ============================================


@router.get("", response_model=RequestLogListResponse)
async def list_request_logs(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100, alias="pageSize"),
    method: Optional[str] = Query(None, description="Filter by HTTP method"),
    path: Optional[str] = Query(None, description="Filter by path (ilike)"),
    status_group: Optional[str] = Query(None, description="Filter by status group: 2xx, 4xx, 5xx"),
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    min_response_time: Optional[int] = Query(None, description="Min response time (ms)"),
    request_id: Optional[str] = Query(None, description="Filter by request ID"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
):
    """List API request logs with filtering and pagination."""
    supabase = await get_async_supabase_admin()

    query = supabase.table("api_request_logs").select("*", count="exact")

    if method:
        query = query.eq("method", method.upper())
    if path:
        query = query.ilike("path", f"%{path}%")
    if status_group:
        if status_group == "2xx":
            query = query.gte("status_code", 200).lt("status_code", 300)
        elif status_group == "4xx":
            query = query.gte("status_code", 400).lt("status_code", 500)
        elif status_group == "5xx":
            query = query.gte("status_code", 500).lt("status_code", 600)
    if user_id:
        query = query.eq("user_id", user_id)
    if min_response_time:
        query = query.gte("response_time_ms", min_response_time)
    if request_id:
        query = query.eq("request_id", request_id)
    if start_date:
        query = query.gte("timestamp", start_date.isoformat())
    if end_date:
        query = query.lte("timestamp", end_date.isoformat())

    query = query.order("timestamp", desc=True)

    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = await query.execute()

    if not result.data:
        return RequestLogListResponse(data=[], total=0)

    # Batch fetch user emails
    user_ids = list(set(
        log["user_id"] for log in result.data
        if log.get("user_id")
    ))
    user_info = await batch_get_user_info(user_ids)

    data = []
    for log in result.data:
        uid = log.get("user_id")
        email = None
        if uid and uid in user_info:
            email = user_info[uid][0]  # (email, username)

        data.append(RequestLogItem(
            id=str(log["id"]),
            request_id=log["request_id"],
            user_id=uid,
            user_email=email,
            auth_type=log.get("auth_type", "anonymous"),
            method=log["method"],
            path=log["path"],
            query_params=log.get("query_params"),
            request_body=log.get("request_body"),
            status_code=log.get("status_code"),
            response_time_ms=log.get("response_time_ms"),
            ip_address=log.get("ip_address"),
            user_agent=log.get("user_agent"),
            error_detail=log.get("error_detail"),
            timestamp=log["timestamp"],
        ))

    return RequestLogListResponse(data=data, total=result.count or len(data))


@router.get("/stats", response_model=RequestLogStats)
async def get_request_log_stats(
    auth: AdminAuthDep,
    hours: int = Query(24, ge=1, le=168, description="Number of hours to include"),
):
    """Get aggregated request log statistics."""
    supabase = await get_async_supabase_admin()

    from datetime import timedelta
    start_time = datetime.utcnow() - timedelta(hours=hours)

    result = await supabase.table("api_request_logs").select(
        "method, status_code, path, response_time_ms, timestamp"
    ).gte("timestamp", start_time.isoformat()).execute()

    if not result.data:
        return RequestLogStats(by_method=[], by_status=[], top_paths=[], by_hour=[], total=0)

    logs = result.data
    total = len(logs)

    # By method
    method_counts: dict[str, int] = {}
    for log in logs:
        m = log.get("method", "UNKNOWN")
        method_counts[m] = method_counts.get(m, 0) + 1
    by_method = [MethodCount(method=m, count=c) for m, c in sorted(method_counts.items(), key=lambda x: -x[1])]

    # By status group
    status_counts: dict[str, int] = {}
    for log in logs:
        sc = log.get("status_code")
        if sc is not None:
            group = f"{sc // 100}xx"
            status_counts[group] = status_counts.get(group, 0) + 1
    by_status = [StatusCount(status_group=s, count=c) for s, c in sorted(status_counts.items())]

    # Top paths
    path_data: dict[str, list] = {}
    for log in logs:
        p = log.get("path", "")
        if p not in path_data:
            path_data[p] = []
        path_data[p].append(log.get("response_time_ms", 0))

    top_paths_list = sorted(path_data.items(), key=lambda x: -len(x[1]))[:20]
    top_paths = [
        TopPath(
            path=p,
            count=len(times),
            avg_response_time_ms=int(sum(t for t in times if t) / max(len([t for t in times if t]), 1)) if times else None,
        )
        for p, times in top_paths_list
    ]

    # By hour
    hour_counts: dict[str, int] = {}
    for log in logs:
        ts = log.get("timestamp", "")
        if ts and len(ts) >= 13:
            hour = ts[:13]  # "2024-01-01T12"
            hour_counts[hour] = hour_counts.get(hour, 0) + 1
    by_hour = [HourCount(hour=h, count=c) for h, c in sorted(hour_counts.items())]

    return RequestLogStats(
        by_method=by_method,
        by_status=by_status,
        top_paths=top_paths,
        by_hour=by_hour,
        total=total,
    )


@router.get("/frontend-errors", response_model=FrontendErrorListResponse)
async def list_frontend_errors(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100, alias="pageSize"),
    error_type: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
):
    """List frontend error reports."""
    supabase = await get_async_supabase_admin()

    query = supabase.table("frontend_error_logs").select("*", count="exact")

    if error_type:
        query = query.eq("error_type", error_type)
    if start_date:
        query = query.gte("created_at", start_date.isoformat())
    if end_date:
        query = query.lte("created_at", end_date.isoformat())

    query = query.order("created_at", desc=True)

    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = await query.execute()

    if not result.data:
        return FrontendErrorListResponse(data=[], total=0)

    # Batch fetch user emails
    user_ids = list(set(
        log["user_id"] for log in result.data
        if log.get("user_id")
    ))
    user_info = await batch_get_user_info(user_ids)

    data = []
    for log in result.data:
        uid = log.get("user_id")
        email = None
        if uid and uid in user_info:
            email = user_info[uid][0]

        data.append(FrontendErrorItem(
            id=str(log["id"]),
            user_id=uid,
            user_email=email,
            session_id=log.get("session_id"),
            error_type=log["error_type"],
            message=log.get("message"),
            stack=log.get("stack"),
            url=log.get("url"),
            component=log.get("component"),
            user_agent=log.get("user_agent"),
            metadata=log.get("metadata"),
            created_at=log["created_at"],
        ))

    return FrontendErrorListResponse(data=data, total=result.count or len(data))


@router.get("/app-logs", response_model=AppLogListResponse)
async def list_app_logs(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100, alias="pageSize"),
    level: Optional[str] = Query(None, description="Filter by log level"),
    module: Optional[str] = Query(None, description="Filter by module (ilike)"),
    message: Optional[str] = Query(None, description="Filter by message (ilike)"),
    has_exception: Optional[bool] = Query(None, description="Filter logs with exceptions"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
):
    """List application logs (loguru) with filtering and pagination.

    Excludes infrastructure noise (httpx, uvicorn.access, celery.beat)
    by default to show only business logic logs.
    """
    supabase = await get_async_supabase_admin()

    # Noise modules to exclude from Application Logs view
    NOISE_MODULES = ("httpx", "uvicorn.access", "uvicorn.error", "celery.beat")

    query = supabase.table("application_logs").select("*", count="exact")

    # Exclude noise unless user explicitly filters by module
    if not module:
        for noise in NOISE_MODULES:
            query = query.neq("module", noise)

    if level:
        query = query.eq("level", level.upper())
    if module:
        query = query.ilike("module", f"%{module}%")
    if message:
        query = query.ilike("message", f"%{message}%")
    if has_exception is True:
        query = query.neq("exception", None)
    elif has_exception is False:
        query = query.is_("exception", "null")
    if start_date:
        query = query.gte("logged_at", start_date.isoformat())
    if end_date:
        query = query.lte("logged_at", end_date.isoformat())

    query = query.order("logged_at", desc=True)

    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = await query.execute()

    if not result.data:
        return AppLogListResponse(data=[], total=0)

    data = [
        AppLogItem(
            id=str(log["id"]),
            level=log["level"],
            message=log["message"],
            module=log.get("module"),
            function=log.get("function"),
            line=log.get("line"),
            file_path=log.get("file_path"),
            exception=log.get("exception"),
            extra=log.get("extra"),
            logged_at=log["logged_at"],
        )
        for log in result.data
    ]

    return AppLogListResponse(data=data, total=result.count or len(data))
