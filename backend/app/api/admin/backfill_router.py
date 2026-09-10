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
from app.workflows.backfill_agent_runs_issue_id import (
    backfill_agent_runs_issue_id_workflow,
)
from app.workflows.backfill_canvas_upload_roles import (
    backfill_canvas_upload_roles_workflow,
)
from app.workflows.backfill_generated_inbox import backfill_generated_inbox
from app.workflows.backfill_issue_scope import backfill_issue_scope_workflow
from app.workflows.backfill_normalize_personal_project_team_ids import (
    backfill_normalize_personal_project_team_ids_workflow,
)
from app.workflows.backfill_project_stage_issue_team_ids import (
    backfill_project_stage_issue_team_ids_workflow,
)
from app.workflows.backfill_publish_task_team_ids import (
    backfill_publish_task_team_ids_workflow,
)
from app.workflows.backfill_resource_gen_params import (
    backfill_resource_gen_params_workflow,
)
from app.workflows.backfill_resource_prompt_origin import (
    backfill_resource_prompt_origin_workflow,
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
    "publish_task_team_ids": backfill_publish_task_team_ids_workflow,
    "resource_gen_params": backfill_resource_gen_params_workflow,
    "resource_prompt_origin": backfill_resource_prompt_origin_workflow,
    "canvas_upload_roles": backfill_canvas_upload_roles_workflow,
    "generated_inbox": backfill_generated_inbox,
    "agent_runs_issue_id": backfill_agent_runs_issue_id_workflow,
}


def workflow_kwargs(
    name: str, body: "BackfillRequest", admin_user_id: str
) -> dict[str, Any]:
    """Build the workflow kwargs from the parameters the target actually takes.

    ``run_user_id``: pass the admin's id to workflows that accept it so their
    task_tracking row has a real auth.users owner (the all-zero system id
    violates the FK and the row is never created).

    ``limit``: not every backfill can honour one — a planner that groups rows
    globally would emit a wrong plan from a partial scan — so it is passed only
    where it exists rather than blowing up the dispatch with a TypeError."""
    import inspect

    params = inspect.signature(inspect.unwrap(_BACKFILLS[name])).parameters
    kwargs: dict[str, Any] = {"dry_run": body.dry_run}
    if "limit" in params:
        kwargs["limit"] = body.limit
    if "run_user_id" in params:
        kwargs["run_user_id"] = admin_user_id
    return kwargs


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
        dbos_workflow_kwargs=workflow_kwargs(body.name, body, auth.user_id),
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
