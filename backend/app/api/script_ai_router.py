"""Script AI Router — async AI dispatch for chapter expansion, branching and
chapter → scenes conversion.

Every route verifies the caller may write the script, and every route that
names a chapter checks that chapter belongs to that script before dispatching
(``_chapter_of_script``): the workflows write through the chapter id, so a
chapter id from someone else's script must never ride in under a script the
caller owns.

Each answers the FLAT ``{"success", "task_id"}`` envelope (#1019).
"""

import uuid as _uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.api.script_chapter_guard import require_chapter_of_script as _chapter_of_script
from app.core.deps import AuthDep
from app.core.scope_guards import verify_script_access
from app.schemas.script import CreateBranchesRequest, ExpandChapterRequest
from app.schemas.script_ai_responses import ScriptAiTaskDispatch
from app.services.infra.unified_task_manager import get_task_manager

router = APIRouter(prefix="/scripts")


@router.post("/expand-chapter", response_model=ScriptAiTaskDispatch)
async def expand_chapter(auth: AuthDep, body: ExpandChapterRequest) -> Dict[str, Any]:
    """Dispatch async chapter expansion. Returns task_id immediately."""
    try:
        await verify_script_access(body.script_id, auth)
        # The workflow overwrites this chapter's content: it must be a chapter
        # of the script the caller was just authorized on.
        await _chapter_of_script(body.chapter_id, body.script_id)
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


@router.post("/create-branches", response_model=ScriptAiTaskDispatch)
async def create_branches(auth: AuthDep, body: CreateBranchesRequest) -> Dict[str, Any]:
    """Dispatch async story branching. Returns task_id immediately."""
    try:
        await verify_script_access(body.script_id, auth)
        # The branches hang off (and read the position of) this chapter.
        await _chapter_of_script(body.chapter_id, body.script_id)
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


@router.post(
    "/{script_id}/chapters/{chapter_id}/convert-to-scenes",
    response_model=ScriptAiTaskDispatch,
)
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
        await _chapter_of_script(chapter_id, script_id)

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
