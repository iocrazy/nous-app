"""Script screenplay-import router — POST /scripts/import-screenplay.

Creates a NEW script from imported text (Fountain or prose) and dispatches the
async ``script_import`` workflow. Distinct from the pre-existing
``POST /scripts/import`` (``script_import_router.py``), which synchronously
extracts a document's text into a chapter — this endpoint produces a fully
scened script asynchronously and returns a task to poll.
"""

import uuid as _uuid

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep, require_team_id
from app.schemas.script import ScriptImportRequest
from app.services.infra.unified_task_manager import get_task_manager
from app.services.script.fountain_parser import MAX_FOUNTAIN_CHARS

router = APIRouter(prefix="/scripts", tags=["Script Import"])


@router.post("/import-screenplay")
async def import_screenplay(auth: AuthDep, body: ScriptImportRequest) -> dict:
    """Create a script from imported text and dispatch the import workflow.

    Guard口径 mirrors ``create_script_project``: ``require_team_id`` scopes the
    new script to the caller's team. Returns ``{success, script_id, task_id}``
    immediately; the client polls ``task_id`` and opens the editor on the new
    ``script_id`` once the workflow completes.
    """
    if len(body.content) > MAX_FOUNTAIN_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"Content too large (max {MAX_FOUNTAIN_CHARS} characters)",
        )

    team_id = await require_team_id(auth.user_id)

    try:
        from app.services.storyboard.script.script_service import ScriptService

        script = await ScriptService().create_project(
            team_id=team_id,
            user_id=auth.user_id,
            project_id=body.project_id,
            name=body.name,
        )
        script_id = str(script["id"])

        mgr = get_task_manager()
        wf_id = str(_uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_import",
            title="Import script",
            dbos_workflow_id=wf_id,
        )

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.script_import import script_import_workflow

        await start_workflow_routed(
            "script_import",
            dbos_workflow_callable=script_import_workflow,
            dbos_workflow_kwargs={
                "script_id": script_id,
                "mode": body.mode,
                "content": body.content,
                "user_id": auth.user_id,
            },
            workflow_id=wf_id,
        )

        return {"success": True, "script_id": script_id, "task_id": task_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ScriptImport] import_screenplay failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to import script")
