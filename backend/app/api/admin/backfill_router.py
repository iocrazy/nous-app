"""Admin API endpoint: dispatch data-backfill DBOS workflows.

THE BACKFILL PARADIGM (see ``app/workflows/backfill_issue_scope.py`` module
docstring): backfills run as DBOS workflows — Task Center visible, metadata
audited, retry-safe — never as hand-run SQL. This router is the dispatch
half of the template; add new backfills to ``_BACKFILLS`` (one workflow per
name) instead of opening a psql shell.

Defaults are deliberately conservative: ``dry_run=True`` — an admin must
explicitly opt into a live run, and a dry run's report (in the task's
metadata) is the natural thing to review first.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel, Field

from app.core.admin_deps import AdminAuthDep
from app.utils.admin_helpers import create_audit_log
from app.workflows.backfill_issue_scope import backfill_issue_scope_workflow
from app.workflows.backfill_normalize_personal_project_team_ids import (
    backfill_normalize_personal_project_team_ids_workflow,
)
from app.workflows.backfill_project_stage_issue_team_ids import (
    backfill_project_stage_issue_team_ids_workflow,
)

router = APIRouter()

# name → workflow callable. Imported, not duplicated, so the endpoint can
# never drift from what actually exists.
_BACKFILLS: dict[str, Callable[..., Any]] = {
    "issue_scope": backfill_issue_scope_workflow,
    "project_stage_issue_team_ids": backfill_project_stage_issue_team_ids_workflow,
    "normalize_personal_project_team_ids": (
        backfill_normalize_personal_project_team_ids_workflow
    ),
}


class BackfillRequest(BaseModel):
    """Body for POST /admin/backfill."""

    name: str
    limit: int = Field(default=500, ge=1, le=5000)
    dry_run: bool = True


class BackfillResponse(BaseModel):
    workflow_id: str
    name: str
    dry_run: bool
    limit: int


@router.post("", response_model=BackfillResponse)
async def dispatch_backfill(
    body: BackfillRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """Dispatch one backfill workflow run. Outcomes land in task_tracking."""
    if body.name not in _BACKFILLS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unknown backfill: {body.name!r}. "
                f"Valid backfills: {sorted(_BACKFILLS)}"
            ),
        )

    from app.services.infra.dbos_orchestrator import start_workflow_routed

    result = await start_workflow_routed(
        "backfill",
        dbos_workflow_callable=_BACKFILLS[body.name],
        dbos_workflow_kwargs={"dry_run": body.dry_run, "limit": body.limit},
    )
    workflow_id = result["dbos_workflow_id"]

    # Audit log (non-blocking — failures inside create_audit_log never
    # affect the dispatch). Mirrors storage_migration_router.
    await create_audit_log(
        admin_id=auth.user_id,
        action="backfill_dispatch",
        target_type="backfill",
        target_id=body.name,
        details={
            "name": body.name,
            "limit": body.limit,
            "dry_run": body.dry_run,
            "workflow_id": workflow_id,
        },
        ip_address=request.client.host if request.client else None,
    )

    logger.info(
        f"[Admin] backfill dispatched: name={body.name} limit={body.limit} "
        f"dry_run={body.dry_run} workflow_id={workflow_id} by admin={auth.user_id}"
    )

    return BackfillResponse(
        workflow_id=workflow_id,
        name=body.name,
        dry_run=body.dry_run,
        limit=body.limit,
    )
