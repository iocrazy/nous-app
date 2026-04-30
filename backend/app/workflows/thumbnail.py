"""thumbnail DBOS workflow — port of legacy
`thumbnail_tasks.generate_thumbnail_task`.

Trivial port (the original task is 38 lines): wrap ThumbnailService.generate_thumbnail
in a single DBOS step. The retry policy (max_attempts=2) matches Celery's
max_retries=2 for parity. Memoized by workflow_id so re-runs after a worker
crash skip the ffmpeg pass on the second attempt.
"""

from __future__ import annotations

import asyncio
from typing import Any

from dbos import DBOS
from loguru import logger


@DBOS.step(retries_allowed=True, max_attempts=2)
def generate_thumbnail_step(resource_id: str, file_path: str, mime_type: str) -> bool:
    from app.services.thumbnail_service import ThumbnailService

    try:
        asyncio.run(
            ThumbnailService().generate_thumbnail(
                resource_id=resource_id,
                file_path=file_path,
                mime_type=mime_type,
            )
        )
        logger.info(f"[Thumbnail] resource={resource_id} generated")
        return True
    except Exception as e:
        logger.warning(f"[Thumbnail] resource={resource_id} failed: {e}")
        # Match Celery's "return False on non-retried failure" — DBOS will not
        # retry on the False return; it retries on raised exceptions only.
        return False


@DBOS.workflow()
def thumbnail_workflow(
    resource_id: str, file_path: str, mime_type: str
) -> dict[str, Any]:
    success = generate_thumbnail_step(resource_id, file_path, mime_type)
    return {"resource_id": resource_id, "success": success}
