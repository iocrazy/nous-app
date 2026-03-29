"""Script AI Router — endpoints for AI-powered outline, expansion, and branching."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.schemas.script import (
    ConvertToStoryboardRequest,
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


@router.post("/convert-to-storyboard")
async def convert_to_storyboard(
    auth: AuthDep, body: ConvertToStoryboardRequest
) -> Dict[str, Any]:
    """Convert a script chapter into storyboard scenes via AI."""
    try:
        script_svc = ScriptService()

        # 1. Read chapter content/summary
        chapter = await script_svc.chapter_repo.get_by_id(body.chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found")

        # 2. Get script project for style_guide
        project = await script_svc.project_repo.get_by_id(body.script_id)
        style_guide = None
        if project and project.get("settings_json"):
            style_guide = project["settings_json"].get("style_guide")

        # 3. AI: split chapter into visual scenes
        ai_svc = ScriptAIService()
        scenes = await ai_svc.split_chapter_to_scenes(
            title=chapter.get("title", ""),
            summary=chapter.get("summary", ""),
            content=chapter.get("content"),
            style_guide=style_guide,
        )

        # 4. Create storyboard nodes if a target project is specified
        created_nodes = []
        if body.storyboard_project_id:
            from app.repositories.storyboard_repository import StoryboardNodeRepository

            node_repo = StoryboardNodeRepository()
            NODE_Y_SPACING = 300

            for scene in scenes:
                node_data = {
                    "project_id": body.storyboard_project_id,
                    "scene_number": scene["scene_number"],
                    "description": scene["description"],
                    "camera_notes": scene.get("camera_notes", ""),
                    "position_x": 100,
                    "position_y": scene["scene_number"] * NODE_Y_SPACING,
                    "data_json": {"source": "script_conversion"},
                }
                rows = await node_repo.bulk_upsert(
                    body.storyboard_project_id, [node_data]
                )
                if rows:
                    created_nodes.append(rows[0])

            # 5. Record link in script_storyboard_links
            for node in created_nodes:
                await script_svc.create_storyboard_link(
                    {
                        "chapter_id": body.chapter_id,
                        "storyboard_project_id": body.storyboard_project_id,
                        "storyboard_node_id": node.get("id"),
                    }
                )

        return {
            "success": True,
            "data": {
                "scenes": scenes,
                "created_nodes": created_nodes,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[ScriptAI] convert_to_storyboard failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert chapter to storyboard: {exc}",
        )
