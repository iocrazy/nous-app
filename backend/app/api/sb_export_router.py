# backend/app/api/sb_export_router.py

"""
Storyboard Export Router

Endpoint for exporting a storyboard project to a downloadable file.
Dispatches a Celery task and returns a task_id immediately so the client
can poll for completion via the unified task manager.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.services.storyboard_service import StoryboardService
from app.services.unified_task_manager import get_task_manager

router = APIRouter(prefix="/storyboard")

_VALID_FORMATS = frozenset({"png", "pdf", "zip"})


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    """Request body for exporting a storyboard project."""

    format: str = Field(..., pattern="^(png|pdf|zip)$")
    options: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/export
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/export")
async def export_project(
    auth: AuthDep,
    project_id: str,
    body: ExportRequest,
) -> Dict[str, Any]:
    """
    Dispatch an export task for a storyboard project.

    Supported formats: ``png``, ``pdf``, ``zip``.

    Returns a ``task_id`` immediately; the caller should poll the unified
    task manager endpoint for status and the resulting download URL.
    """
    if body.format not in _VALID_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported format '{body.format}'. Must be one of: {sorted(_VALID_FORMATS)}",
        )

    try:
        import uuid as _uuid

        svc = StoryboardService()
        await svc.verify_project_access(project_id, auth.user_id)
        mgr = get_task_manager()
        wf_id = str(_uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="storyboard_export",
            title=f"Export storyboard as {body.format.upper()}",
            celery_task_id=wf_id,
            metadata={
                "project_id": project_id,
                "format": body.format,
                "options": body.options or {},
            },
        )

        from app.services.dbos_orchestrator import start_workflow_routed
        from app.workflows.storyboard import storyboard_export_workflow

        await start_workflow_routed(
            "storyboard_export",
            dbos_workflow_callable=storyboard_export_workflow,
            dbos_workflow_kwargs={
                "project_id": project_id,
                "task_id": task_id,
                "format": body.format,
                "options": body.options or {},
            },
            workflow_id=wf_id,
        )

        logger.info(
            "[SBExport] export_project queued — task=%s project=%s format=%s",
            task_id,
            project_id,
            body.format,
        )
        return {"success": True, "task_id": task_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[SBExport] export_project project=%s failed: %s", project_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to queue export: {exc}")
