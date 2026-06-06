"""Admin API routes for Audit Logs management."""

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.audit_logs_repository import get_audit_logs_repository
from app.schemas.admin import (
    AuditLogListResponse,
    AuditLogResponse,
)
from app.utils.admin_helpers import batch_get_user_info

router = APIRouter()


# ============================================
# Response Schemas for Stats
# ============================================


class AuditActionCount(BaseModel):
    """Count of audit logs by action."""

    action: str
    count: int


class AuditTargetCount(BaseModel):
    """Count of audit logs by target type."""

    target_type: str
    count: int


class AuditDayCount(BaseModel):
    """Count of audit logs by day."""

    date: str
    count: int


class AuditStatsResponse(BaseModel):
    """Audit log statistics response."""

    by_action: List[AuditActionCount]
    by_target: List[AuditTargetCount]
    by_day: List[AuditDayCount]
    total: int


# ============================================
# Audit Log Endpoints
# ============================================


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    admin_id: Optional[str] = Query(None, description="Filter by admin ID"),
    action: Optional[str] = Query(None, description="Filter by action type"),
    target_type: Optional[str] = Query(None, description="Filter by target type"),
    start_date: Optional[datetime] = Query(
        None, description="Filter by start date (ISO format)"
    ),
    end_date: Optional[datetime] = Query(
        None, description="Filter by end date (ISO format)"
    ),
):
    """
    List audit logs with filtering and pagination.

    - **page**: Page number (starts at 1)
    - **page_size**: Number of items per page (max 100)
    - **admin_id**: Filter by admin who performed the action
    - **action**: Filter by action type (e.g., update_user, ban_user)
    - **target_type**: Filter by target type (e.g., user, team)
    - **start_date**: Filter logs from this date
    - **end_date**: Filter logs until this date
    """
    repo = get_audit_logs_repository()
    rows, total = await repo.list(
        page=page,
        page_size=page_size,
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        start_date=start_date,
        end_date=end_date,
    )

    if not rows:
        return AuditLogListResponse(items=[], total=0, page=page, page_size=page_size)

    # Batch fetch admin info (avoiding N+1 queries)
    admin_ids = list({log["admin_id"] for log in rows if log.get("admin_id")})
    admin_info = await batch_get_user_info(admin_ids)

    items = []
    for log in rows:
        aid = log["admin_id"]
        admin_email, admin_username = admin_info.get(aid, (None, None))
        items.append(
            AuditLogResponse(
                id=str(log["id"]),
                admin_id=aid,
                admin_email=admin_email,
                admin_username=admin_username,
                action=log["action"],
                target_type=log["target_type"],
                target_id=log["target_id"],
                details=log.get("details"),
                ip_address=log.get("ip_address"),
                created_at=log["created_at"],
            )
        )

    return AuditLogListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/actions", response_model=List[str])
async def get_audit_actions(
    auth: AdminAuthDep,
):
    """
    Get unique action types for filter dropdown.

    Returns a list of all unique action types that have been logged.
    """
    repo = get_audit_logs_repository()
    return await repo.list_distinct_actions()


@router.get("/stats", response_model=AuditStatsResponse)
async def get_audit_stats(
    auth: AdminAuthDep,
    days: int = Query(
        30, ge=1, le=365, description="Number of days to include in stats"
    ),
):
    """
    Get audit log statistics for the specified time period.

    - **days**: Number of days to include (default: 30, max: 365)

    Returns statistics grouped by:
    - Action type
    - Target type
    - Day
    """
    repo = get_audit_logs_repository()
    start_date = datetime.utcnow() - timedelta(days=days)
    logs = await repo.list_since(start_date)

    if not logs:
        return AuditStatsResponse(
            by_action=[],
            by_target=[],
            by_day=[],
            total=0,
        )

    total = len(logs)

    # Count by action
    action_counts = {}
    for log in logs:
        action = log.get("action", "unknown")
        action_counts[action] = action_counts.get(action, 0) + 1

    by_action = [
        AuditActionCount(action=action, count=count)
        for action, count in sorted(action_counts.items(), key=lambda x: -x[1])
    ]

    # Count by target type
    target_counts = {}
    for log in logs:
        target_type = log.get("target_type", "unknown")
        target_counts[target_type] = target_counts.get(target_type, 0) + 1

    by_target = [
        AuditTargetCount(target_type=target_type, count=count)
        for target_type, count in sorted(target_counts.items(), key=lambda x: -x[1])
    ]

    # Count by day
    day_counts = {}
    for log in logs:
        created_at = log.get("created_at", "")
        if created_at:
            # Extract date portion (YYYY-MM-DD)
            day = created_at[:10]
            day_counts[day] = day_counts.get(day, 0) + 1

    by_day = [
        AuditDayCount(date=date, count=count)
        for date, count in sorted(day_counts.items())
    ]

    return AuditStatsResponse(
        by_action=by_action,
        by_target=by_target,
        by_day=by_day,
        total=total,
    )
