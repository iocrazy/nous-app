# backend/app/tasks/storyboard_tasks.py

"""
Storyboard Workbench Celery Tasks.

Async tasks for AI image/video generation, script splitting, video scene
analysis, export, image grid splitting, and annotation processing.
All tasks report progress through the UnifiedTaskManager.
"""

import logging

from celery import shared_task

from app.tasks.utils import run_async

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers — unified task lifecycle (best-effort, never raises)
# ---------------------------------------------------------------------------

def _start_unified(task_id: str) -> None:
    """Transition a unified task to PROCESSING."""
    if not task_id:
        return
    try:
        from app.services.unified_task_manager import get_task_manager
        run_async(get_task_manager().start(task_id))
    except Exception as exc:
        logger.debug("[Storyboard] _start_unified failed: %s", exc)


def _complete_unified(task_id: str, result_data: dict | None = None) -> None:
    """Transition a unified task to COMPLETED, optionally persisting result_data."""
    if not task_id:
        return
    try:
        from app.services.unified_task_manager import get_task_manager
        mgr = get_task_manager()
        if result_data:
            run_async(mgr.update_progress(task_id, progress=100, subtitle="Completed"))
        run_async(mgr.complete(task_id))
    except Exception as exc:
        logger.debug("[Storyboard] _complete_unified failed: %s", exc)


def _fail_unified(task_id: str, error_msg: str) -> None:
    """Transition a unified task to FAILED with an error message."""
    if not task_id:
        return
    try:
        from app.services.unified_task_manager import get_task_manager
        run_async(get_task_manager().fail(task_id, error_msg[:500]))
    except Exception as exc:
        logger.debug("[Storyboard] _fail_unified failed: %s", exc)


def _update_unified_progress(task_id: str, progress: int, subtitle: str = "") -> None:
    """Update progress percentage on a unified task."""
    if not task_id:
        return
    try:
        from app.services.unified_task_manager import get_task_manager
        run_async(get_task_manager().update_progress(task_id, progress=progress, subtitle=subtitle))
    except Exception as exc:
        logger.debug("[Storyboard] _update_unified_progress failed: %s", exc)


# ---------------------------------------------------------------------------
# Task 1: generate_storyboard_image
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def generate_storyboard_image(
    self,
    task_id: str,
    project_id: str,
    node_id: str,
    prompt: str,
    model: str = "dall-e-3",
    provider: str = "openai",
    character_ids: list | None = None,
    reference_image_url: str | None = None,
    aspect_ratio: str = "16:9",
):
    """Generate a single storyboard frame image via the AI provider.

    Args:
        task_id: Unified task row UUID for progress tracking.
        project_id: Storyboard project UUID.
        node_id: Canvas node UUID that will receive the generated image.
        prompt: Image generation prompt text.
        model: AI model identifier.
        provider: AI provider key (e.g. 'openai', 'replicate').
        character_ids: Optional list of character UUIDs for reference images.
        reference_image_url: Optional URL to an existing image for img2img.
        aspect_ratio: Output aspect ratio string (e.g. '16:9', '1:1').
    """
    logger.info(
        "[Storyboard] generate_storyboard_image start — task=%s node=%s",
        task_id, node_id,
    )
    _start_unified(task_id)
    _update_unified_progress(task_id, 5, "Generating image…")

    try:
        from app.services.storyboard_ai_service import StoryboardAIService

        service = StoryboardAIService()
        result = run_async(
            service.generate_image(
                project_id=project_id,
                node_id=node_id,
                prompt=prompt,
                model=model,
                provider=provider,
                character_ids=character_ids or [],
                reference_image_url=reference_image_url,
                aspect_ratio=aspect_ratio,
            )
        )

        logger.info(
            "[Storyboard] generate_storyboard_image done — task=%s node=%s",
            task_id, node_id,
        )
        _complete_unified(task_id, result_data=result)
        return {"status": "success", "node_id": node_id, "result": result}

    except Exception as exc:
        logger.error(
            "[Storyboard] generate_storyboard_image failed — task=%s: %s",
            task_id, exc,
        )
        _fail_unified(task_id, f"Image generation failed: {exc}")
        raise self.retry(exc=exc, countdown=30)


# ---------------------------------------------------------------------------
# Task 2: generate_storyboard_image_batch
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def generate_storyboard_image_batch(
    self,
    task_id: str,
    project_id: str,
    requests: list,
):
    """Generate multiple storyboard frame images in one batch call.

    Each item in *requests* is a dict accepted by
    ``StoryboardAIService.generate_image()``.  Partial failures are reported
    individually; the overall task succeeds as long as at least one image was
    generated.

    Args:
        task_id: Unified task row UUID for progress tracking.
        project_id: Storyboard project UUID.
        requests: List of image-generation parameter dicts.
    """
    logger.info(
        "[Storyboard] generate_storyboard_image_batch start — task=%s count=%d",
        task_id, len(requests),
    )
    _start_unified(task_id)
    _update_unified_progress(task_id, 5, "Starting batch image generation…")

    try:
        from app.services.storyboard_ai_service import StoryboardAIService

        service = StoryboardAIService()
        results = run_async(
            service.generate_image_batch(
                requests=requests,
            )
        )

        # Separate successes from partial failures
        succeeded = [r for r in results if r.get("success") is True]
        failed = [r for r in results if r.get("success") is not True]

        if failed:
            logger.warning(
                "[Storyboard] generate_storyboard_image_batch partial failure — "
                "task=%s succeeded=%d failed=%d",
                task_id, len(succeeded), len(failed),
            )

        _complete_unified(
            task_id,
            result_data={"succeeded": len(succeeded), "failed": len(failed)},
        )
        return {
            "status": "success" if not failed else "partial",
            "project_id": project_id,
            "succeeded": len(succeeded),
            "failed": len(failed),
            "results": results,
        }

    except Exception as exc:
        logger.error(
            "[Storyboard] generate_storyboard_image_batch failed — task=%s: %s",
            task_id, exc,
        )
        _fail_unified(task_id, f"Batch image generation failed: {exc}")
        raise self.retry(exc=exc, countdown=30)


# ---------------------------------------------------------------------------
# Task 3: generate_storyboard_video
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_storyboard_video(
    self,
    task_id: str,
    project_id: str,
    node_id: str,
    source_image_url: str,
    prompt: str = "",
    provider: str = "runway",
    duration: int = 4,
    motion_intensity: float = 0.5,
):
    """Animate a storyboard frame image into a short video clip.

    Args:
        task_id: Unified task row UUID for progress tracking.
        project_id: Storyboard project UUID.
        node_id: Canvas node UUID whose image will be animated.
        source_image_url: URL of the source still image.
        prompt: Optional motion/style prompt.
        provider: Video generation provider key.
        duration: Desired clip length in seconds.
        motion_intensity: Provider-specific motion strength (0.0–1.0).
    """
    logger.info(
        "[Storyboard] generate_storyboard_video start — task=%s node=%s",
        task_id, node_id,
    )
    _start_unified(task_id)
    _update_unified_progress(task_id, 5, "Generating video…")

    try:
        from app.services.storyboard_ai_service import StoryboardAIService

        service = StoryboardAIService()
        result = run_async(
            service.generate_video(
                project_id=project_id,
                node_id=node_id,
                source_image_url=source_image_url,
                prompt=prompt,
                provider=provider,
                duration=duration,
                motion_intensity=motion_intensity,
            )
        )

        logger.info(
            "[Storyboard] generate_storyboard_video done — task=%s node=%s",
            task_id, node_id,
        )
        _complete_unified(task_id, result_data=result)
        return {"status": "success", "node_id": node_id, "result": result}

    except Exception as exc:
        logger.error(
            "[Storyboard] generate_storyboard_video failed — task=%s: %s",
            task_id, exc,
        )
        _fail_unified(task_id, f"Video generation failed: {exc}")
        raise self.retry(exc=exc, countdown=60)


# ---------------------------------------------------------------------------
# Task 4: split_script_to_storyboard
# ---------------------------------------------------------------------------

_GRID_COLS = 4
_GRID_NODE_WIDTH = 320
_GRID_NODE_HEIGHT = 220
_GRID_GAP_X = 60
_GRID_GAP_Y = 60
_GRID_ORIGIN_X = 100
_GRID_ORIGIN_Y = 100


def _grid_position(index: int) -> dict:
    """Return {x, y} canvas position for a 0-based scene index in a grid layout."""
    col = index % _GRID_COLS
    row = index // _GRID_COLS
    return {
        "x": _GRID_ORIGIN_X + col * (_GRID_NODE_WIDTH + _GRID_GAP_X),
        "y": _GRID_ORIGIN_Y + row * (_GRID_NODE_HEIGHT + _GRID_GAP_Y),
    }


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def split_script_to_storyboard(
    self,
    task_id: str,
    project_id: str,
    script_text: str,
    style_guide: str = "",
):
    """Parse a screenplay / script and create one canvas node per scene.

    The AI service splits *script_text* into scenes, then a
    ``storyboard_node`` and ``storyboard_frame`` row are created for each
    scene.  Nodes are arranged in a left-to-right grid layout.

    Args:
        task_id: Unified task row UUID for progress tracking.
        project_id: Storyboard project UUID.
        script_text: Raw screenplay or scene description text.
        style_guide: Optional visual style instructions injected into the AI prompt.
    """
    logger.info(
        "[Storyboard] split_script_to_storyboard start — task=%s project=%s",
        task_id, project_id,
    )
    _start_unified(task_id)
    _update_unified_progress(task_id, 5, "Analysing script…")

    try:
        from app.repositories.storyboard_repository import (
            StoryboardNodeRepository,
            StoryboardFrameRepository,
        )
        from app.services.storyboard_ai_service import StoryboardAIService

        service = StoryboardAIService()
        scenes = run_async(
            service.split_script(
                script_text=script_text,
                style_guide=style_guide,
            )
        )

        _update_unified_progress(task_id, 40, f"Creating {len(scenes)} scene nodes…")

        node_repo = StoryboardNodeRepository()
        frame_repo = StoryboardFrameRepository()

        created_nodes = []
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
            node = run_async(node_repo.bulk_upsert(project_id, [node_data]))
            node_id = node[0]["id"] if node else None

            if node_id:
                frame_data = {
                    "order_index": 0,
                    "prompt": scene.get("image_prompt", scene.get("description", "")),
                    "notes": scene.get("notes", ""),
                    "status": "pending",
                }
                run_async(frame_repo.bulk_upsert(node_id, [frame_data]))
                created_nodes.append(node_id)

        logger.info(
            "[Storyboard] split_script_to_storyboard done — task=%s scenes=%d",
            task_id, len(created_nodes),
        )
        _complete_unified(
            task_id,
            result_data={"scene_count": len(created_nodes), "node_ids": created_nodes},
        )
        return {
            "status": "success",
            "project_id": project_id,
            "scene_count": len(created_nodes),
            "node_ids": created_nodes,
        }

    except Exception as exc:
        logger.error(
            "[Storyboard] split_script_to_storyboard failed — task=%s: %s",
            task_id, exc,
        )
        _fail_unified(task_id, f"Script split failed: {exc}")
        raise self.retry(exc=exc, countdown=30)


# ---------------------------------------------------------------------------
# Task 5: analyze_video_scenes
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def analyze_video_scenes(
    self,
    task_id: str,
    project_id: str,
    video_path: str,
):
    """Detect scene changes in a video and create a storyboard node per scene.

    Args:
        task_id: Unified task row UUID for progress tracking.
        project_id: Storyboard project UUID.
        video_path: Absolute path to the video file on the NAS.
    """
    logger.info(
        "[Storyboard] analyze_video_scenes start — task=%s project=%s",
        task_id, project_id,
    )
    _start_unified(task_id)
    _update_unified_progress(task_id, 5, "Analysing video scenes…")

    try:
        from app.repositories.storyboard_repository import (
            StoryboardNodeRepository,
            StoryboardFrameRepository,
        )
        from app.services.storyboard_ai_service import StoryboardAIService

        service = StoryboardAIService()
        scenes = run_async(
            service.analyze_video(
                video_path=video_path,
            )
        )

        _update_unified_progress(task_id, 50, f"Creating {len(scenes)} scene nodes…")

        node_repo = StoryboardNodeRepository()
        frame_repo = StoryboardFrameRepository()

        created_nodes = []
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
            node = run_async(node_repo.bulk_upsert(project_id, [node_data]))
            node_id = node[0]["id"] if node else None

            if node_id:
                frame_data = {
                    "order_index": 0,
                    "source_image_path": scene.get("image_path", ""),
                    "prompt": scene.get("description", ""),
                    "status": "completed" if scene.get("image_path") else "pending",
                }
                run_async(frame_repo.bulk_upsert(node_id, [frame_data]))
                created_nodes.append(node_id)

        logger.info(
            "[Storyboard] analyze_video_scenes done — task=%s scenes=%d",
            task_id, len(created_nodes),
        )
        _complete_unified(
            task_id,
            result_data={"scene_count": len(created_nodes), "node_ids": created_nodes},
        )
        return {
            "status": "success",
            "project_id": project_id,
            "scene_count": len(created_nodes),
            "node_ids": created_nodes,
        }

    except Exception as exc:
        logger.error(
            "[Storyboard] analyze_video_scenes failed — task=%s: %s",
            task_id, exc,
        )
        _fail_unified(task_id, f"Video analysis failed: {exc}")
        raise self.retry(exc=exc, countdown=30)


# ---------------------------------------------------------------------------
# Task 6: export_storyboard
# ---------------------------------------------------------------------------

_EXPORT_FORMATS = frozenset({"png", "pdf", "zip"})


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def export_storyboard(
    self,
    task_id: str,
    project_id: str,
    format: str = "pdf",
    options: dict | None = None,
):
    """Export a storyboard project to a downloadable file.

    Delegates to ``StoryboardExportService`` which selects the correct
    renderer based on *format*.

    Args:
        task_id: Unified task row UUID for progress tracking.
        project_id: Storyboard project UUID.
        format: One of ``'png'``, ``'pdf'``, or ``'zip'``.
        options: Optional format-specific export options dict.
    """
    if format not in _EXPORT_FORMATS:
        error_msg = f"Unsupported export format '{format}'. Must be one of: {sorted(_EXPORT_FORMATS)}"
        logger.error("[Storyboard] export_storyboard invalid format — task=%s: %s", task_id, error_msg)
        _fail_unified(task_id, error_msg)
        return {"status": "failed", "error": error_msg}

    logger.info(
        "[Storyboard] export_storyboard start — task=%s project=%s format=%s",
        task_id, project_id, format,
    )
    _start_unified(task_id)
    _update_unified_progress(task_id, 5, f"Exporting as {format.upper()}…")

    resolved_options = options or {}

    try:
        from app.services.storyboard_export_service import StoryboardExportService

        service = StoryboardExportService()

        if format == "pdf":
            result = run_async(
                service.export_pdf(project_id=project_id, options=resolved_options)
            )
        elif format == "zip":
            result = run_async(
                service.export_zip(project_id=project_id)
            )
        else:  # png
            result = run_async(
                service.export_png(project_id=project_id, options=resolved_options)
            )

        logger.info(
            "[Storyboard] export_storyboard done — task=%s project=%s format=%s",
            task_id, project_id, format,
        )
        _complete_unified(task_id, result_data=result)
        return {"status": "success", "project_id": project_id, "format": format, "result": result}

    except Exception as exc:
        logger.error(
            "[Storyboard] export_storyboard failed — task=%s: %s",
            task_id, exc,
        )
        _fail_unified(task_id, f"Export failed: {exc}")
        raise self.retry(exc=exc, countdown=30)


# ---------------------------------------------------------------------------
# Task 7: split_image_grid
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=15)
def split_image_grid(
    self,
    task_id: str,
    project_id: str,
    image_path: str,
    rows: int,
    cols: int,
):
    """Split a sprite-sheet / image grid into individual frame files.

    Uses the synchronous ``StoryboardImageService.split_image()`` directly
    since it is CPU-bound and does not require an event loop.

    Args:
        task_id: Unified task row UUID for progress tracking.
        project_id: Storyboard project UUID (for context/logging).
        image_path: Absolute path to the source image on the NAS.
        rows: Number of rows in the sprite sheet.
        cols: Number of columns in the sprite sheet.
    """
    logger.info(
        "[Storyboard] split_image_grid start — task=%s project=%s rows=%d cols=%d",
        task_id, project_id, rows, cols,
    )
    _start_unified(task_id)
    _update_unified_progress(task_id, 5, f"Splitting image into {rows}×{cols} frames…")

    if rows < 1 or cols < 1:
        error_msg = f"rows and cols must be ≥ 1, got rows={rows} cols={cols}"
        logger.error("[Storyboard] split_image_grid invalid params — task=%s: %s", task_id, error_msg)
        _fail_unified(task_id, error_msg)
        return {"status": "failed", "error": error_msg}

    try:
        from app.services.storyboard_image_service import StoryboardImageService

        service = StoryboardImageService()
        frame_paths = service.split_image(image_path=image_path, rows=rows, cols=cols)

        logger.info(
            "[Storyboard] split_image_grid done — task=%s frames=%d",
            task_id, len(frame_paths),
        )
        _complete_unified(
            task_id,
            result_data={"frame_count": len(frame_paths), "frame_paths": frame_paths},
        )
        return {
            "status": "success",
            "project_id": project_id,
            "frame_count": len(frame_paths),
            "frame_paths": frame_paths,
        }

    except Exception as exc:
        logger.error(
            "[Storyboard] split_image_grid failed — task=%s: %s",
            task_id, exc,
        )
        _fail_unified(task_id, f"Image split failed: {exc}")
        raise self.retry(exc=exc, countdown=15)


# ---------------------------------------------------------------------------
# Task 8: process_annotation
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def process_annotation(
    self,
    task_id: str,
    node_id: str,
    annotation_data: dict,
):
    """Apply an annotation to a storyboard node.

    Currently logs the annotation and marks the task complete.  Future
    versions may persist SVG overlays, trigger downstream re-exports, or
    feed annotation data back to the AI provider.

    Args:
        task_id: Unified task row UUID for progress tracking.
        node_id: Canvas node UUID to annotate.
        annotation_data: Arbitrary annotation payload (type, coordinates, text, …).
    """
    logger.info(
        "[Storyboard] process_annotation start — task=%s node=%s type=%s",
        task_id, node_id, annotation_data.get("type", "unknown"),
    )
    _start_unified(task_id)

    try:
        # Validate that annotation_data is a non-empty mapping
        if not isinstance(annotation_data, dict):
            raise ValueError(f"annotation_data must be a dict, got {type(annotation_data).__name__}")

        annotation_type = annotation_data.get("type", "unknown")
        logger.info(
            "[Storyboard] Applying annotation type='%s' to node %s — data keys: %s",
            annotation_type,
            node_id,
            list(annotation_data.keys()),
        )

        # TODO: persist annotation overlay to storyboard_frames or a dedicated
        #       storyboard_annotations table when the schema is extended.

        _complete_unified(
            task_id,
            result_data={"node_id": node_id, "annotation_type": annotation_type},
        )
        return {
            "status": "success",
            "node_id": node_id,
            "annotation_type": annotation_type,
        }

    except Exception as exc:
        logger.error(
            "[Storyboard] process_annotation failed — task=%s: %s",
            task_id, exc,
        )
        _fail_unified(task_id, f"Annotation processing failed: {exc}")
        raise self.retry(exc=exc, countdown=10)
