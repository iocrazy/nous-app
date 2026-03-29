"""Script AI Router — endpoints for AI-powered outline, expansion, and branching."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.schemas.script import (
    CreateBranchesRequest,
    ExpandChapterRequest,
    GenerateOutlineRequest,
)
from app.services.script_ai_service import ScriptAIService
from app.services.script_service import ScriptService
from app.services.unified_task_manager import get_task_manager

router = APIRouter(prefix="/scripts")


@router.post("/generate-outline")
async def generate_outline(auth: AuthDep, body: GenerateOutlineRequest) -> Dict[str, Any]:
    """Dispatch async outline generation. Returns task_id immediately."""
    try:
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="script_outline_gen",
            title=f"Generate outline ({body.chapter_count} chapters)",
        )

        from app.tasks.script_tasks import generate_script_outline

        generate_script_outline.delay(
            task_id,
            body.script_id,
            body.premise,
            body.chapter_count,
            body.style_guide,
        )

        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error("[ScriptAI] generate_outline failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to generate outline: {exc}")


@router.post("/expand-chapter")
async def expand_chapter(auth: AuthDep, body: ExpandChapterRequest) -> Dict[str, Any]:
    """Synchronously expand a chapter summary into full prose."""
    try:
        ai_svc = ScriptAIService()
        content = await ai_svc.expand_chapter(
            title=body.title,
            summary=body.summary,
            context=body.context,
        )

        # Update the chapter with expanded content
        script_svc = ScriptService()
        updated = await script_svc.update_chapter(
            body.chapter_id, {"content": content}
        )

        return {"success": True, "data": {"content": content, "chapter": updated}}
    except Exception as exc:
        logger.error("[ScriptAI] expand_chapter failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to expand chapter: {exc}")


@router.post("/create-branches")
async def create_branches(auth: AuthDep, body: CreateBranchesRequest) -> Dict[str, Any]:
    """Synchronously generate story branches and create chapter nodes."""
    try:
        ai_svc = ScriptAIService()
        branches = await ai_svc.create_branches(
            title=body.title,
            summary=body.summary,
            branch_count=body.branch_count,
            branch_type=body.branch_type,
            context=body.context,
        )

        # Get parent chapter position to offset branches
        script_svc = ScriptService()
        parent = await script_svc.chapter_repo.get_by_id(body.chapter_id)
        parent_x = parent.get("position_x", 400) if parent else 400
        parent_y = parent.get("position_y", 100) if parent else 100

        BRANCH_X_OFFSET = 350
        BRANCH_Y_OFFSET = 250

        created = []
        for i, branch in enumerate(branches):
            x_offset = (i - len(branches) / 2 + 0.5) * BRANCH_X_OFFSET
            chapter_data = {
                "script_id": body.script_id,
                "parent_chapter_id": body.chapter_id,
                "title": branch["title"],
                "summary": branch["summary"],
                "branch_label": branch["branch_label"],
                "branch_type": body.branch_type,
                "position_x": parent_x + x_offset,
                "position_y": parent_y + BRANCH_Y_OFFSET,
            }
            result = await script_svc.create_chapter(body.script_id, chapter_data)
            created.append(result)

        return {"success": True, "data": {"branches": created}}
    except Exception as exc:
        logger.error("[ScriptAI] create_branches failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to create branches: {exc}")
