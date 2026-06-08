"""Script AI Router — endpoints for AI-powered outline, expansion, and branching."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep, get_team_id_for_user
from app.schemas.script import (
    ConvertToStoryboardRequest,
    CreateBranchesRequest,
    ExpandChapterRequest,
    GenerateOutlineRequest,
)
from app.services.infra.unified_task_manager import get_task_manager
from app.services.storyboard.script.script_service import ScriptService

router = APIRouter(prefix="/scripts")


async def _verify_script_access(script_id: str, user_id: str) -> None:
    """Verify the authenticated user has access to this script (via team membership)."""
    svc = ScriptService()
    project = await svc.project_repo.get_by_id(script_id)
    if not project:
        raise HTTPException(status_code=404, detail="Script project not found")
    user_team = await get_team_id_for_user(user_id)
    if str(user_team) != str(project.get("team_id")):
        raise HTTPException(status_code=403, detail="Access denied")


@router.post("/generate-outline")
async def generate_outline(
    auth: AuthDep, body: GenerateOutlineRequest
) -> Dict[str, Any]:
    """Dispatch async outline generation. Returns task_id immediately."""
    try:
        await _verify_script_access(body.script_id, auth.user_id)
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_outline_gen",
            title=f"Generate outline ({body.chapter_count} chapters)",
        )

        # PR-D7 phase 3: routes through DBOS only (legacy script_tasks
        # Celery wrapper has been deleted). DBOS owns workflow status
        # via its own dbos_workflow_id; task_id is still returned for
        # the legacy task_tracking UI bridge during the frontend
        # migration window.
        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.script_outline import script_outline_workflow

        await start_workflow_routed(
            "script_outline_gen",
            dbos_workflow_callable=script_outline_workflow,
            dbos_workflow_kwargs={
                "script_id": body.script_id,
                "premise": body.premise,
                "chapter_count": body.chapter_count,
                "style_guide": body.style_guide,
            },
        )

        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error(f"[ScriptAI] generate_outline failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to generate outline")


@router.post("/expand-chapter")
async def expand_chapter(auth: AuthDep, body: ExpandChapterRequest) -> Dict[str, Any]:
    """Dispatch async chapter expansion. Returns task_id immediately."""
    try:
        await _verify_script_access(body.script_id, auth.user_id)
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_expand_chapter",
            title=f"Expand chapter: {body.title}",
        )

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.script_ai_workflows import script_expand_chapter_workflow

        await start_workflow_routed(
            "script_expand_chapter",
            dbos_workflow_callable=script_expand_chapter_workflow,
            dbos_workflow_kwargs={
                "script_id": body.script_id,
                "chapter_id": body.chapter_id,
                "title": body.title,
                "summary": body.summary,
                "context": body.context,
                "user_id": auth.user_id,
            },
        )

        return {"success": True, "task_id": task_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ScriptAI] expand_chapter failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to expand chapter")


@router.post("/create-branches")
async def create_branches(auth: AuthDep, body: CreateBranchesRequest) -> Dict[str, Any]:
    """Dispatch async story branching. Returns task_id immediately."""
    try:
        await _verify_script_access(body.script_id, auth.user_id)
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_create_branches",
            title=f"Create {body.branch_count} branches: {body.title}",
        )

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.script_ai_workflows import script_create_branches_workflow

        await start_workflow_routed(
            "script_create_branches",
            dbos_workflow_callable=script_create_branches_workflow,
            dbos_workflow_kwargs={
                "script_id": body.script_id,
                "chapter_id": body.chapter_id,
                "title": body.title,
                "summary": body.summary,
                "branch_count": body.branch_count,
                "branch_type": body.branch_type,
                "context": body.context,
                "user_id": auth.user_id,
            },
        )

        return {"success": True, "task_id": task_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ScriptAI] create_branches failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create branches")


@router.post("/convert-to-storyboard")
async def convert_to_storyboard(
    auth: AuthDep, body: ConvertToStoryboardRequest
) -> Dict[str, Any]:
    """Dispatch async chapter→storyboard conversion. Returns task_id.

    The chapter + project style-guide reads now happen INSIDE the workflow
    (``script_ai_scenes_step``); the endpoint only verifies access and
    dispatches chapter_id + storyboard_project_id.
    """
    try:
        await _verify_script_access(body.script_id, auth.user_id)
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_to_storyboard",
            title="Convert chapter to storyboard",
        )

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.script_ai_workflows import script_to_storyboard_workflow

        await start_workflow_routed(
            "script_to_storyboard",
            dbos_workflow_callable=script_to_storyboard_workflow,
            dbos_workflow_kwargs={
                "script_id": body.script_id,
                "chapter_id": body.chapter_id,
                "storyboard_project_id": body.storyboard_project_id,
                "user_id": auth.user_id,
            },
        )

        return {"success": True, "task_id": task_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ScriptAI] convert_to_storyboard failed: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Failed to convert chapter to storyboard",
        )
