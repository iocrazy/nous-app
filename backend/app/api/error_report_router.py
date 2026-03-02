"""Frontend error reporting endpoint."""

from typing import Optional

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from app.core.deps import OptionalAuthDep
from app.db import get_async_supabase_admin

router = APIRouter(prefix="/errors", tags=["Error Reporting"])


class FrontendErrorReport(BaseModel):
    error_type: str  # runtime / network / unhandled_rejection
    message: str
    stack: Optional[str] = None
    url: Optional[str] = None
    component: Optional[str] = None
    session_id: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: Optional[dict] = None


@router.post("/report", status_code=204)
async def report_frontend_error(
    report: FrontendErrorReport,
    auth: OptionalAuthDep,
):
    """Accept frontend error reports. Always returns 204 — error reporting should never fail."""
    try:
        supabase = await get_async_supabase_admin()
        await supabase.table("frontend_error_logs").insert({
            "user_id": auth.user_id if auth else None,
            "session_id": report.session_id,
            "error_type": report.error_type,
            "message": report.message,
            "stack": report.stack,
            "url": report.url,
            "component": report.component,
            "user_agent": report.user_agent,
            "metadata": report.metadata,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to write frontend error log: {e}")
