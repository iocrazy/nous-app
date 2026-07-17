"""Script AI Router — endpoints for AI-powered outline, expansion, and branching."""

import uuid as _uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_guards import verify_script_access
from app.schemas.script import (
    ConvertToStoryboardRequest,
    CreateBranchesRequest,
    ExpandChapterRequest,
    GenerateOutlineRequest,
)
from app.services.infra.unified_task_manager import get_task_manager

router = APIRouter(prefix="/scripts")


@router.post("/generate-outline")
async def generate_outline(
    auth: AuthDep, body: GenerateOutlineRequest
) -> Dict[str, Any]:
    """Dispatch async outline generation. Returns task_id immediately."""
    try:
        await verify_script_access(body.script_id, auth)
        mgr = get_task_manager()
        wf_id = str(_uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_outline_gen",
            title=f"Generate outline ({body.chapter_count} chapters)",
            dbos_workflow_id=wf_id,
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
                "user_id": auth.user_id,
            },
            workflow_id=wf_id,
        )

        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error(f"[ScriptAI] generate_outline failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to generate outline")


@router.post("/expand-chapter")
async def expand_chapter(auth: AuthDep, body: ExpandChapterRequest) -> Dict[str, Any]:
    """Dispatch async chapter expansion. Returns task_id immediately."""
    try:
        await verify_script_access(body.script_id, auth)
        mgr = get_task_manager()
        wf_id = str(_uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_expand_chapter",
            title=f"Expand chapter: {body.title}",
            dbos_workflow_id=wf_id,
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
            workflow_id=wf_id,
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
        await verify_script_access(body.script_id, auth)
        mgr = get_task_manager()
        wf_id = str(_uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_create_branches",
            title=f"Create {body.branch_count} branches: {body.title}",
            dbos_workflow_id=wf_id,
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
            workflow_id=wf_id,
        )

        return {"success": True, "task_id": task_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ScriptAI] create_branches failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create branches")


@router.post("/{script_id}/chapters/{chapter_id}/convert-to-scenes")
async def convert_to_scenes(
    auth: AuthDep, script_id: str, chapter_id: str
) -> Dict[str, Any]:
    """Dispatch async chapter-prose → screenplay-scenes conversion.

    Path-scoped (``/scripts/{script_id}/chapters/{chapter_id}/...``): verifies
    team access to the script, then confirms the chapter actually belongs to
    that script before dispatching. The bigint ids arrive as path strings while
    the chapter's ``script_id`` reads back as a native int — the belongs check
    str-coerces both sides (#1006: an int-vs-str ``!=`` is always true and would
    silently 404 or, worse, let a cross-script chapter through). Returns the
    flat ``{"success", "task_id"}`` envelope immediately.
    """
    try:
        await verify_script_access(script_id, auth)

        from app.services.storyboard.script.script_service import ScriptService

        chapter = await ScriptService().chapter_repo.get_by_id(chapter_id)
        if not chapter or str(chapter.get("script_id")) != str(script_id):
            raise HTTPException(
                status_code=404, detail="Chapter not found in this script"
            )

        mgr = get_task_manager()
        wf_id = str(_uuid.uuid4())
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_scene_convert",
            title="Convert chapter to scenes",
            dbos_workflow_id=wf_id,
        )

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.script_scene_convert import script_scene_convert_workflow

        await start_workflow_routed(
            "script_scene_convert",
            dbos_workflow_callable=script_scene_convert_workflow,
            dbos_workflow_kwargs={
                "script_id": script_id,
                "chapter_id": chapter_id,
                "user_id": auth.user_id,
            },
            workflow_id=wf_id,
        )

        return {"success": True, "task_id": task_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[ScriptAI] convert_to_scenes failed: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Failed to convert chapter to scenes",
        )


@router.post("/convert-to-storyboard")
async def convert_to_storyboard(
    auth: AuthDep, body: ConvertToStoryboardRequest
) -> Dict[str, Any]:
    """Retired (Phase B P4 cutover): 410 Gone.

    This was the bridge that converted a chapter into legacy-workbench storyboard
    nodes (``script_to_storyboard_workflow`` → storyboard_nodes /
    script_storyboard_links). The workbench is retired and its ``/storyboard/*``
    routes are 410-tombstoned; storyboarding now lives in the script editor as
    per-scene shots. The endpoint stays mounted (bookmarks / stale clients) but
    answers 410 rather than writing to the deprecated tables. The workflow it
    used to dispatch (``script_to_storyboard_workflow``) and the storyboard
    tables it wrote were both removed once these 410s were confirmed stable.
    """
    from app.api.sb_gone_router import LEGACY_STORYBOARD_GONE_DETAIL

    raise HTTPException(status_code=410, detail=LEGACY_STORYBOARD_GONE_DETAIL)
