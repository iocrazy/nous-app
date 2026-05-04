"""API routes for user logs management."""

import csv
import io
import json
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.repositories.logs_repository import LogsRepository

router = APIRouter(prefix="/logs", tags=["Logs"])

# Repository instance
logs_repo = LogsRepository()


# ============================================
# Response Models
# ============================================


class LogEntry(BaseModel):
    """Log entry model."""

    id: int
    action: str
    message: str
    status: str
    aweme_id: Optional[str] = None
    details: Optional[dict] = None
    created_at: datetime


class LogsResponse(BaseModel):
    """Response for logs list."""

    success: bool = True
    logs: List[LogEntry]
    total: int
    page: int
    page_size: int
    total_pages: int


# ============================================
# Helper Functions
# ============================================


def parse_levels(level_str: Optional[str]) -> Optional[List[str]]:
    """Parse comma-separated level string into list."""
    if not level_str:
        return None
    levels = [entry.strip().lower() for entry in level_str.split(",") if entry.strip()]
    # Map display levels to database status values
    level_mapping = {
        "info": ["info"],
        "success": ["success"],
        "warn": ["warning"],
        "warning": ["warning"],
        "error": ["error"],
        "pending": ["pending"],
        "debug": ["debug"],
    }
    result = []
    for level in levels:
        if level in level_mapping:
            result.extend(level_mapping[level])
        else:
            result.append(level)
    return list(set(result)) if result else None


def parse_date_range(
    date_range: Optional[str],
) -> tuple[Optional[date], Optional[date]]:
    """Parse date range string into start and end dates."""
    if not date_range:
        return None, None

    today = date.today()

    if date_range == "today":
        return today, today
    elif date_range == "7days":
        return today - timedelta(days=7), today
    elif date_range == "30days":
        return today - timedelta(days=30), today

    return None, None


# ============================================
# API Endpoints
# ============================================


@router.get("", response_model=LogsResponse)
async def get_logs(
    current_user: dict = Depends(get_current_user),
    level: Optional[str] = Query(
        None,
        description="Filter by levels (comma-separated: info,success,warn,error,pending,debug)",
    ),
    date_range: Optional[str] = Query(
        None, description="Date range: today, 7days, 30days"
    ),
    start_date: Optional[date] = Query(
        None, description="Custom start date (YYYY-MM-DD)"
    ),
    end_date: Optional[date] = Query(None, description="Custom end date (YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Search in log messages"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, description="Items per page (50, 100, 200)"),
):
    """
    Get user activity logs with filtering and pagination.

    Supports filtering by:
    - Level: info, success, warn, error, pending, debug (comma-separated)
    - Date range: today, 7days, 30days, or custom start/end dates
    - Search: keyword search in log messages

    Pagination: page and page_size (50, 100, or 200)
    """
    try:
        # current_user can be User object or dict
        user_id = (
            getattr(current_user, "id", None)
            if hasattr(current_user, "id")
            else current_user.get("id")
        )
        if not user_id:
            raise HTTPException(status_code=401, detail="User ID not found")

        # Validate page_size
        if page_size not in [50, 100, 200]:
            page_size = 50

        # Parse filters
        levels = parse_levels(level)

        # Use date_range if no custom dates provided
        if not start_date and not end_date and date_range:
            start_date, end_date = parse_date_range(date_range)

        # Query logs
        logs, total = await logs_repo.get_logs(
            user_id=user_id,
            levels=levels,
            start_date=start_date,
            end_date=end_date,
            search=search,
            page=page,
            page_size=page_size,
        )

        total_pages = (total + page_size - 1) // page_size if total > 0 else 1

        return LogsResponse(
            success=True,
            logs=logs,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get logs: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve logs")


@router.get("/export")
async def export_logs(
    current_user: dict = Depends(get_current_user),
    format: str = Query("json", description="Export format: json or csv"),
    level: Optional[str] = Query(None, description="Filter by levels"),
    date_range: Optional[str] = Query(
        None, description="Date range: today, 7days, 30days"
    ),
    start_date: Optional[date] = Query(None, description="Custom start date"),
    end_date: Optional[date] = Query(None, description="Custom end date"),
    search: Optional[str] = Query(None, description="Search in log messages"),
):
    """
    Export user logs as JSON or CSV file.

    Supports the same filters as the logs list endpoint.
    Maximum 10,000 logs per export.
    """
    try:
        # current_user can be User object or dict
        user_id = (
            getattr(current_user, "id", None)
            if hasattr(current_user, "id")
            else current_user.get("id")
        )
        if not user_id:
            raise HTTPException(status_code=401, detail="User ID not found")

        # Validate format
        if format not in ["json", "csv"]:
            format = "json"

        # Parse filters
        levels = parse_levels(level)

        if not start_date and not end_date and date_range:
            start_date, end_date = parse_date_range(date_range)

        # Get logs for export
        logs = await logs_repo.get_logs_for_export(
            user_id=user_id,
            levels=levels,
            start_date=start_date,
            end_date=end_date,
            search=search,
        )

        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"logs_export_{timestamp}.{format}"

        if format == "json":
            # Export as JSON
            content = json.dumps(logs, indent=2, default=str, ensure_ascii=False)
            return StreamingResponse(
                io.BytesIO(content.encode("utf-8")),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        else:
            # Export as CSV
            output = io.StringIO()
            if logs:
                fieldnames = [
                    "id",
                    "action",
                    "message",
                    "status",
                    "aweme_id",
                    "created_at",
                ]
                writer = csv.DictWriter(
                    output, fieldnames=fieldnames, extrasaction="ignore"
                )
                writer.writeheader()
                for log in logs:
                    # Convert datetime to string
                    if "created_at" in log and log["created_at"]:
                        log["created_at"] = str(log["created_at"])
                    writer.writerow(log)

            content = output.getvalue()
            return StreamingResponse(
                io.BytesIO(content.encode("utf-8")),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to export logs: {e}")
        raise HTTPException(status_code=500, detail="Failed to export logs")
