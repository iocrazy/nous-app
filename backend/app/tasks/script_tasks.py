"""Script Celery tasks — async AI operations for the script editor."""

import asyncio
from typing import Optional

from celery import shared_task
from loguru import logger


def _run_async(coro):
    """Run an async coroutine in a sync Celery task."""
    return asyncio.run(coro)


async def _start_unified(task_id: str) -> None:
    try:
        from app.services.unified_task_manager import get_task_manager
        mgr = get_task_manager()
        await mgr.start(task_id)
    except Exception as e:
        logger.warning(f"[ScriptTasks] Failed to start unified task {task_id}: {e}")


async def _update_progress(task_id: str, progress: int, subtitle: str = "") -> None:
    try:
        from app.services.unified_task_manager import get_task_manager
        mgr = get_task_manager()
        await mgr.update_progress(task_id, progress, subtitle)
    except Exception as e:
        logger.warning(f"[ScriptTasks] Failed to update progress {task_id}: {e}")


async def _complete_unified(task_id: str, result_data: dict) -> None:
    try:
        from app.services.unified_task_manager import get_task_manager
        mgr = get_task_manager()
        await mgr.complete(task_id, subtitle="Done")
    except Exception as e:
        logger.warning(f"[ScriptTasks] Failed to complete unified task {task_id}: {e}")


async def _fail_unified(task_id: str, error_msg: str) -> None:
    try:
        from app.services.unified_task_manager import get_task_manager
        mgr = get_task_manager()
        await mgr.fail(task_id, error_msg)
    except Exception as e:
        logger.warning(f"[ScriptTasks] Failed to fail unified task {task_id}: {e}")


async def _generate_outline_async(
    task_id: str,
    script_id: str,
    premise: str,
    chapter_count: int,
    style_guide: Optional[str],
) -> dict:
    from app.services.script_ai_service import ScriptAIService
    from app.services.script_service import ScriptService

    await _start_unified(task_id)

    try:
        ai_svc = ScriptAIService()
        await _update_progress(task_id, 10, "Generating outline...")

        chapters = await ai_svc.generate_outline(premise, chapter_count, style_guide)
        await _update_progress(task_id, 60, f"Creating {len(chapters)} chapters...")

        script_svc = ScriptService()
        VERTICAL_GAP = 200
        START_X = 400
        START_Y = 100

        created_ids = []
        for i, ch in enumerate(chapters):
            chapter_data = {
                "script_id": script_id,
                "title": ch["title"],
                "summary": ch["summary"],
                "chapter_number": i + 1,
                "position_x": START_X,
                "position_y": START_Y + i * VERTICAL_GAP,
            }
            result = await script_svc.chapter_repo.create(chapter_data)
            created_ids.append(result.get("id", ""))

        await _update_progress(task_id, 90, "Finalizing...")

        result_data = {
            "status": "success",
            "chapter_count": len(chapters),
            "chapter_ids": created_ids,
        }
        await _complete_unified(task_id, result_data)
        return result_data

    except Exception as e:
        logger.error("[ScriptTasks] generate_outline failed: %s", e)
        await _fail_unified(task_id, "Outline generation failed")
        return {"status": "failed", "error": "Outline generation failed"}


@shared_task(
    name="generate_script_outline",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def generate_script_outline(
    self,
    task_id: str,
    script_id: str,
    premise: str,
    chapter_count: int = 5,
    style_guide: Optional[str] = None,
) -> dict:
    """Generate a story outline via LLM and create chapter nodes."""
    return _run_async(
        _generate_outline_async(task_id, script_id, premise, chapter_count, style_guide)
    )
