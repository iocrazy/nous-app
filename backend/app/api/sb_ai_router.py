# backend/app/api/sb_ai_router.py

"""
Storyboard AI Router

Endpoints that dispatch DBOS workflows for AI-powered storyboard operations:
image generation, video generation, script splitting, video analysis,
scene detection, and conversational chat.

All generation endpoints return immediately with a task_id so the client
can poll via the unified task manager.
"""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.schemas.storyboard import (
    GenerateImageRequest,
    GenerateVideoRequest,
    SplitScriptRequest,
)
from app.services.dbos_orchestrator import start_workflow_routed
from app.services.storyboard_service import StoryboardService
from app.services.unified_task_manager import get_task_manager
from app.workflows.storyboard import (
    storyboard_image_workflow,
    storyboard_script_split_workflow,
    storyboard_video_analysis_workflow,
    storyboard_video_workflow,
)

router = APIRouter(prefix="/storyboard")


# ---------------------------------------------------------------------------
# Request schemas for endpoints not covered by storyboard.py schemas
# ---------------------------------------------------------------------------


class AnalyzeVideoRequest(BaseModel):
    """Request body for video scene analysis."""

    project_id: str
    video_url: str = Field(..., min_length=1, max_length=2000)


class DetectScenesRequest(BaseModel):
    """Request body for raw scene detection without AI annotation."""

    project_id: str
    video_url: str = Field(..., min_length=1, max_length=2000)
    threshold: float = Field(default=0.3, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# POST /generate/image
# ---------------------------------------------------------------------------


@router.post("/generate/image")
async def generate_image(auth: AuthDep, body: GenerateImageRequest) -> Dict[str, Any]:
    """
    Dispatch an AI image-generation task for a canvas node.

    Returns a task_id immediately; progress is tracked via the unified
    task manager and delivered over the realtime channel.
    """
    try:
        svc = StoryboardService()
        await svc.verify_project_access(body.project_id, auth.user_id)
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="storyboard_image_gen",
            title=f"Generate image for node {body.node_id[:8]}",
            metadata={
                "project_id": body.project_id,
                "node_id": body.node_id,
                "provider": body.provider,
                "model": body.model,
            },
        )

        await start_workflow_routed(
            "storyboard_image_gen",
            dbos_workflow_callable=storyboard_image_workflow,
            dbos_workflow_kwargs={
                "project_id": body.project_id,
                "node_id": body.node_id,
                "prompt": body.prompt,
                "task_id": task_id,
                "model": body.model,
                "provider": body.provider,
                "character_ids": body.character_ids,
                "reference_image_url": body.reference_image_url,
                "aspect_ratio": body.aspect_ratio,
            },
        )

        logger.info(
            "[SBAi] generate_image queued — task=%s project=%s node=%s",
            task_id,
            body.project_id,
            body.node_id,
        )
        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error("[SBAi] generate_image failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to queue image generation: {exc}"
        )


# ---------------------------------------------------------------------------
# POST /generate/video
# ---------------------------------------------------------------------------


@router.post("/generate/video")
async def generate_video(auth: AuthDep, body: GenerateVideoRequest) -> Dict[str, Any]:
    """
    Dispatch an AI video-generation task to animate a canvas node image.

    Returns a task_id immediately.
    """
    try:
        svc = StoryboardService()
        await svc.verify_project_access(body.project_id, auth.user_id)
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="storyboard_video_gen",
            title=f"Generate video for node {body.node_id[:8]}",
            metadata={
                "project_id": body.project_id,
                "node_id": body.node_id,
                "provider": body.provider,
            },
        )

        await start_workflow_routed(
            "storyboard_video_gen",
            dbos_workflow_callable=storyboard_video_workflow,
            dbos_workflow_kwargs={
                "project_id": body.project_id,
                "node_id": body.node_id,
                "source_image_url": body.source_image_url,
                "task_id": task_id,
                "prompt": body.prompt or "",
                "provider": body.provider,
                "duration": int(body.duration_seconds),
                "motion_intensity": (
                    0.5
                    if body.motion_intensity == "medium"
                    else (0.25 if body.motion_intensity == "low" else 0.75)
                ),
            },
        )

        logger.info(
            "[SBAi] generate_video queued — task=%s project=%s node=%s",
            task_id,
            body.project_id,
            body.node_id,
        )
        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error("[SBAi] generate_video failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to queue video generation: {exc}"
        )


# ---------------------------------------------------------------------------
# POST /split-script
# ---------------------------------------------------------------------------


@router.post("/split-script")
async def split_script(auth: AuthDep, body: SplitScriptRequest) -> Dict[str, Any]:
    """
    Dispatch a script-splitting task that creates one canvas node per scene.

    Returns a task_id immediately.
    """
    try:
        svc = StoryboardService()
        await svc.verify_project_access(body.project_id, auth.user_id)
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="storyboard_script_split",
            title="Split script into storyboard scenes",
            metadata={"project_id": body.project_id},
        )

        await start_workflow_routed(
            "storyboard_script_split",
            dbos_workflow_callable=storyboard_script_split_workflow,
            dbos_workflow_kwargs={
                "project_id": body.project_id,
                "script_text": body.script_text,
                "task_id": task_id,
                "style_guide": body.style_guide or "",
            },
        )

        logger.info(
            "[SBAi] split_script queued — task=%s project=%s",
            task_id,
            body.project_id,
        )
        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error("[SBAi] split_script failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to queue script split: {exc}"
        )


# ---------------------------------------------------------------------------
# POST /analyze-video
# ---------------------------------------------------------------------------


@router.post("/analyze-video")
async def analyze_video(auth: AuthDep, body: AnalyzeVideoRequest) -> Dict[str, Any]:
    """
    Dispatch a video analysis task that annotates keyframes via LLM.

    Returns a task_id immediately.
    """
    try:
        svc = StoryboardService()
        await svc.verify_project_access(body.project_id, auth.user_id)
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="storyboard_video_analysis",
            title="Analyze video for storyboard scenes",
            metadata={"project_id": body.project_id, "video_url": body.video_url},
        )

        await start_workflow_routed(
            "storyboard_video_analysis",
            dbos_workflow_callable=storyboard_video_analysis_workflow,
            dbos_workflow_kwargs={
                "project_id": body.project_id,
                "video_path": body.video_url,
                "task_id": task_id,
            },
        )

        logger.info(
            "[SBAi] analyze_video queued — task=%s project=%s",
            task_id,
            body.project_id,
        )
        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error("[SBAi] analyze_video failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to queue video analysis: {exc}"
        )


# ---------------------------------------------------------------------------
# POST /detect-scenes
# ---------------------------------------------------------------------------


@router.post("/detect-scenes")
async def detect_scenes(auth: AuthDep, body: DetectScenesRequest) -> Dict[str, Any]:
    """
    Dispatch a scene-detection task (no LLM annotation).

    Uses the image service's detect_scenes() method directly via a Celery
    worker. Returns a task_id immediately.
    """
    try:
        svc = StoryboardService()
        await svc.verify_project_access(body.project_id, auth.user_id)
        mgr = get_task_manager()
        task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="storyboard_scene_detect",
            title="Detect scenes in video",
            metadata={
                "project_id": body.project_id,
                "video_url": body.video_url,
                "threshold": body.threshold,
            },
        )

        # Reuse storyboard_video_analysis_workflow — wraps detect_scenes
        # internally. Threshold is stored in metadata for observability;
        # the workflow uses StoryboardImageService defaults.
        await start_workflow_routed(
            "storyboard_scene_detect",
            dbos_workflow_callable=storyboard_video_analysis_workflow,
            dbos_workflow_kwargs={
                "project_id": body.project_id,
                "video_path": body.video_url,
                "task_id": task_id,
            },
        )

        logger.info(
            "[SBAi] detect_scenes queued — task=%s project=%s",
            task_id,
            body.project_id,
        )
        return {"success": True, "task_id": task_id}
    except Exception as exc:
        logger.error("[SBAi] detect_scenes failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to queue scene detection: {exc}"
        )


# Chat moved to /api/v1/ai-library/sessions/* (AgentRunner pipeline). The
# legacy POST /chat endpoint + StoryboardAIService.chat() were deleted in
# the AIChatDrawer migration — Storyboard now uses the same agent runner
# as ScriptEditor, picking up Skill / Delegate / agent_runs telemetry for
# free.
