"""storyboard DBOS workflows — port of all 8 user-triggered tasks
from `tasks.storyboard_tasks`.

The legacy 764-LOC file is structured as 8 independent @shared_task
functions, NOT a 6-task chain as the original `_DEFERRED_TASKS.md`
ledger entry suggested. Each task is fired by a separate UI control
(generate image, generate video, split script, analyze video, export,
etc.) and delegates to one of:

    - StoryboardAIService    (image gen, video gen, script split,
                              scene detect)
    - StoryboardExportService (PDF / PNG / ZIP renderers)
    - StoryboardImageService  (sprite-sheet split — pure CPU)

unified_task lifecycle calls (start / progress / complete / fail) are
dropped here — DBOS owns workflow status. Best-effort `task_id`
parameter is kept on the DBOS workflow signatures only for caller
parity during D4 wiring; not used downstream. Drop after D4.

Each workflow is independently routable via `dbos_workflow_routing`
(task_types: storyboard_image_gen, storyboard_video_gen,
storyboard_script_split, storyboard_video_analysis,
storyboard_scene_detect, storyboard_export — already seeded in
migration 169). Two new task_types not in migration 169 are added by
this port: `storyboard_image_batch` and `storyboard_annotation`. Add
their routing rows in a follow-up migration if/when needed.
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.tasks.utils import run_async

_GRID_COLS = 4
_GRID_NODE_WIDTH = 320
_GRID_NODE_HEIGHT = 220
_GRID_GAP_X = 60
_GRID_GAP_Y = 60
_GRID_ORIGIN_X = 100
_GRID_ORIGIN_Y = 100

_EXPORT_FORMATS = frozenset({"png", "pdf", "zip"})


def _grid_position(index: int) -> dict[str, int]:
    col = index % _GRID_COLS
    row = index // _GRID_COLS
    return {
        "x": _GRID_ORIGIN_X + col * (_GRID_NODE_WIDTH + _GRID_GAP_X),
        "y": _GRID_ORIGIN_Y + row * (_GRID_NODE_HEIGHT + _GRID_GAP_Y),
    }


# ---------------------------------------------------------------------------
# 1. generate_storyboard_image
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=3)
def generate_image_step(
    *,
    project_id: str,
    node_id: str,
    prompt: str,
    model: str,
    provider: str,
    character_ids: list,
    reference_image_url: Optional[str],
    aspect_ratio: str,
) -> dict[str, Any]:
    from app.services.storyboard.storyboard_ai_service import StoryboardAIService

    async def _do() -> dict[str, Any]:
        svc = StoryboardAIService()
        return await svc.generate_image(
            project_id=project_id,
            node_id=node_id,
            prompt=prompt,
            model=model,
            provider=provider,
            character_ids=character_ids,
            reference_image_url=reference_image_url,
            aspect_ratio=aspect_ratio,
        )

    return run_async(_do())


@DBOS.workflow()
def storyboard_image_workflow(
    project_id: str,
    node_id: str,
    prompt: str,
    *,
    task_id: Optional[
        str
    ] = None,  # legacy unified_task id; ignored, kept for D4 parity
    model: str = "dall-e-3",
    provider: str = "openai",
    character_ids: Optional[list] = None,
    reference_image_url: Optional[str] = None,
    aspect_ratio: str = "16:9",
) -> dict[str, Any]:
    result = generate_image_step(
        project_id=project_id,
        node_id=node_id,
        prompt=prompt,
        model=model,
        provider=provider,
        character_ids=character_ids or [],
        reference_image_url=reference_image_url,
        aspect_ratio=aspect_ratio,
    )
    return {"status": "success", "node_id": node_id, "result": result}


# ---------------------------------------------------------------------------
# 2. generate_storyboard_image_batch
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=3)
def generate_image_batch_step(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.services.storyboard.storyboard_ai_service import StoryboardAIService

    async def _do() -> list[dict[str, Any]]:
        svc = StoryboardAIService()
        return await svc.generate_image_batch(requests=requests)

    return run_async(_do())


@DBOS.workflow()
def storyboard_image_batch_workflow(
    project_id: str,
    requests: list[dict[str, Any]],
    *,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    results = generate_image_batch_step(requests)
    succeeded = [r for r in results if r.get("success") is True]
    failed = [r for r in results if r.get("success") is not True]
    if failed:
        logger.warning(
            f"[storyboard.image_batch] partial — succeeded={len(succeeded)} "
            f"failed={len(failed)}"
        )
    return {
        "status": "success" if not failed else "partial",
        "project_id": project_id,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "results": results,
    }


# ---------------------------------------------------------------------------
# 3. generate_storyboard_video
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=3)
def generate_video_step(
    *,
    project_id: str,
    node_id: str,
    source_image_url: str,
    prompt: str,
    provider: str,
    duration: int,
    motion_intensity: float,
) -> dict[str, Any]:
    from app.services.storyboard.storyboard_ai_service import StoryboardAIService

    async def _do() -> dict[str, Any]:
        svc = StoryboardAIService()
        return await svc.generate_video(
            project_id=project_id,
            node_id=node_id,
            source_image_url=source_image_url,
            prompt=prompt,
            provider=provider,
            duration=duration,
            motion_intensity=motion_intensity,
        )

    return run_async(_do())


@DBOS.workflow()
def storyboard_video_workflow(
    project_id: str,
    node_id: str,
    source_image_url: str,
    *,
    task_id: Optional[str] = None,
    prompt: str = "",
    provider: str = "runway",
    duration: int = 4,
    motion_intensity: float = 0.5,
) -> dict[str, Any]:
    result = generate_video_step(
        project_id=project_id,
        node_id=node_id,
        source_image_url=source_image_url,
        prompt=prompt,
        provider=provider,
        duration=duration,
        motion_intensity=motion_intensity,
    )
    return {"status": "success", "node_id": node_id, "result": result}


# ---------------------------------------------------------------------------
# 4. split_script_to_storyboard
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=2)
def split_script_step(script_text: str, style_guide: str) -> list[dict[str, Any]]:
    from app.services.storyboard.storyboard_ai_service import StoryboardAIService

    async def _do() -> list[dict[str, Any]]:
        svc = StoryboardAIService()
        return await svc.split_script(script_text=script_text, style_guide=style_guide)

    return run_async(_do())


@DBOS.step()
def persist_split_scenes_step(
    project_id: str, scenes: list[dict[str, Any]]
) -> list[str]:
    from app.repositories.storyboard_repository import (
        get_storyboard_frame_repository,
        get_storyboard_node_repository,
    )

    async def _do() -> list[str]:
        node_repo = get_storyboard_node_repository()
        frame_repo = get_storyboard_frame_repository()
        created_nodes: list[str] = []
        for idx, scene in enumerate(scenes):
            position = _grid_position(idx)
            node_data = {
                "project_id": project_id,
                "node_type": "storyboard_split",
                "position_x": position["x"],
                "position_y": position["y"],
                "width": _GRID_NODE_WIDTH,
                "height": _GRID_NODE_HEIGHT,
                "data_json": {
                    "scene_number": idx + 1,
                    "title": scene.get("title", f"Scene {idx + 1}"),
                    "description": scene.get("description", ""),
                    "dialogue": scene.get("dialogue", ""),
                    "action": scene.get("action", ""),
                },
            }
            node = await node_repo.bulk_upsert(project_id, [node_data])
            node_id = node[0]["id"] if node else None
            if node_id:
                # Map to real storyboard_frames columns (the old order_index /
                # prompt / notes / status keys are not columns): order_index →
                # frame_index, notes → note, prompt / status → annotations_json
                # jsonb. project_id + node_id are required (NOT NULL) FKs.
                frame_data = {
                    "project_id": project_id,
                    "node_id": node_id,
                    "frame_index": 0,
                    "note": scene.get("notes", ""),
                    "annotations_json": {
                        "prompt": scene.get(
                            "image_prompt", scene.get("description", "")
                        ),
                        "status": "pending",
                    },
                }
                await frame_repo.bulk_upsert(node_id, [frame_data])
                created_nodes.append(node_id)
        return created_nodes

    return run_async(_do())


@DBOS.workflow()
def storyboard_script_split_workflow(
    project_id: str,
    script_text: str,
    *,
    task_id: Optional[str] = None,
    style_guide: str = "",
) -> dict[str, Any]:
    scenes = split_script_step(script_text, style_guide)
    node_ids = persist_split_scenes_step(project_id, scenes)
    return {
        "status": "success",
        "project_id": project_id,
        "scene_count": len(node_ids),
        "node_ids": node_ids,
    }


# ---------------------------------------------------------------------------
# 5. analyze_video_scenes
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=2)
def analyze_video_step(video_path: str) -> list[dict[str, Any]]:
    from app.services.storyboard.storyboard_ai_service import StoryboardAIService

    async def _do() -> list[dict[str, Any]]:
        svc = StoryboardAIService()
        return await svc.analyze_video(video_path=video_path)

    return run_async(_do())


@DBOS.step()
def persist_video_scenes_step(
    project_id: str, scenes: list[dict[str, Any]]
) -> list[str]:
    from app.repositories.storyboard_repository import (
        get_storyboard_frame_repository,
        get_storyboard_node_repository,
    )

    async def _do() -> list[str]:
        node_repo = get_storyboard_node_repository()
        frame_repo = get_storyboard_frame_repository()
        created_nodes: list[str] = []
        for idx, scene in enumerate(scenes):
            position = _grid_position(idx)
            node_data = {
                "project_id": project_id,
                "node_type": "storyboard_split",
                "position_x": position["x"],
                "position_y": position["y"],
                "width": _GRID_NODE_WIDTH,
                "height": _GRID_NODE_HEIGHT,
                "data_json": {
                    "scene_number": idx + 1,
                    "timestamp": scene.get("time", idx),
                    "description": scene.get("description", ""),
                },
            }
            node = await node_repo.bulk_upsert(project_id, [node_data])
            node_id = node[0]["id"] if node else None
            if node_id:
                # Map to real storyboard_frames columns (order_index / prompt /
                # status / source_image_path are not columns): order_index →
                # frame_index, source_image_path → image_url, prompt / status →
                # annotations_json jsonb. project_id + node_id are required FKs.
                frame_data = {
                    "project_id": project_id,
                    "node_id": node_id,
                    "frame_index": 0,
                    "image_url": scene.get("image_path", ""),
                    "annotations_json": {
                        "prompt": scene.get("description", ""),
                        "status": (
                            "completed" if scene.get("image_path") else "pending"
                        ),
                    },
                }
                await frame_repo.bulk_upsert(node_id, [frame_data])
                created_nodes.append(node_id)
        return created_nodes

    return run_async(_do())


@DBOS.workflow()
def storyboard_video_analysis_workflow(
    project_id: str,
    video_path: str,
    *,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    scenes = analyze_video_step(video_path)
    node_ids = persist_video_scenes_step(project_id, scenes)
    return {
        "status": "success",
        "project_id": project_id,
        "scene_count": len(node_ids),
        "node_ids": node_ids,
    }


# ---------------------------------------------------------------------------
# 6. export_storyboard
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=2)
def export_storyboard_step(
    *, project_id: str, format: str, options: dict[str, Any]
) -> dict[str, Any]:
    from app.services.storyboard.storyboard_export_service import (
        StoryboardExportService,
    )

    async def _do() -> dict[str, Any]:
        svc = StoryboardExportService()
        if format == "pdf":
            return await svc.export_pdf(project_id=project_id, options=options)
        if format == "zip":
            return await svc.export_zip(project_id=project_id)
        return await svc.export_png(project_id=project_id, options=options)

    return run_async(_do())


@DBOS.workflow()
def storyboard_export_workflow(
    project_id: str,
    *,
    task_id: Optional[str] = None,
    format: str = "pdf",
    options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if format not in _EXPORT_FORMATS:
        return {
            "status": "failed",
            "error": (
                f"Unsupported export format '{format}'. "
                f"Must be one of: {sorted(_EXPORT_FORMATS)}"
            ),
        }
    result = export_storyboard_step(
        project_id=project_id, format=format, options=options or {}
    )
    return {
        "status": "success",
        "project_id": project_id,
        "format": format,
        "result": result,
    }


# ---------------------------------------------------------------------------
# 7. split_image_grid
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=2)
def split_image_grid_step(*, image_path: str, rows: int, cols: int) -> list[str]:
    """CPU-bound — sync service call wrapped in a step for retry."""
    from app.services.storyboard.storyboard_image_service import StoryboardImageService

    svc = StoryboardImageService()
    return svc.split_image(image_path=image_path, rows=rows, cols=cols)


@DBOS.workflow()
def storyboard_image_grid_split_workflow(
    project_id: str,
    image_path: str,
    rows: int,
    cols: int,
    *,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    if rows < 1 or cols < 1:
        return {
            "status": "failed",
            "error": f"rows and cols must be ≥ 1, got rows={rows} cols={cols}",
        }
    frame_paths = split_image_grid_step(image_path=image_path, rows=rows, cols=cols)
    return {
        "status": "success",
        "project_id": project_id,
        "frame_count": len(frame_paths),
        "frame_paths": frame_paths,
    }


# ---------------------------------------------------------------------------
# 8. process_annotation
# ---------------------------------------------------------------------------


@DBOS.step()
def process_annotation_step(node_id: str, annotation_data: dict[str, Any]) -> str:
    """Currently a no-op placeholder per the legacy task — TODO when the
    storyboard_annotations schema lands. Returns the annotation_type for
    the workflow result payload."""
    if not isinstance(annotation_data, dict):
        raise ValueError(
            f"annotation_data must be a dict, got {type(annotation_data).__name__}"
        )
    annotation_type = annotation_data.get("type", "unknown")
    logger.info(
        f"[storyboard.annotation] node={node_id} type={annotation_type} "
        f"keys={list(annotation_data.keys())}"
    )
    return annotation_type


@DBOS.workflow()
def storyboard_annotation_workflow(
    node_id: str,
    annotation_data: dict[str, Any],
    *,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    annotation_type = process_annotation_step(node_id, annotation_data)
    return {
        "status": "success",
        "node_id": node_id,
        "annotation_type": annotation_type,
    }
