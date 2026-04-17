# app/tasks/thumbnail_tasks.py

"""
Thumbnail generation tasks.

Run out-of-band from the download task queue so that a slow ffmpeg pass on a
single video cannot back up every other download waiting to finalize.

Public entry point: ``generate_thumbnail_task.delay(resource_id, file_path, mime_type)``.
"""

from celery import shared_task
from loguru import logger

from app.services.thumbnail_service import ThumbnailService
from app.tasks.utils import run_async


@shared_task(name="app.tasks.thumbnail_tasks.generate_thumbnail_task", max_retries=2)
def generate_thumbnail_task(resource_id: str, file_path: str, mime_type: str) -> bool:
    """Generate thumbnail + preview sprite for a video resource.

    Returns True on success, False on (non-retried) failure.  Retries are handled
    by Celery for transient ffmpeg errors.
    """
    try:
        run_async(
            ThumbnailService().generate_thumbnail(
                resource_id=resource_id,
                file_path=file_path,
                mime_type=mime_type,
            )
        )
        logger.info(
            f"[Thumbnail] resource={resource_id} generated"
        )
        return True
    except Exception as e:
        logger.warning(
            f"[Thumbnail] resource={resource_id} failed: {e}"
        )
        return False
