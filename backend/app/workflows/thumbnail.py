"""thumbnail DBOS workflow — port of legacy
`thumbnail_tasks.generate_thumbnail_task`.

Trivial port (the original task is 38 lines): wrap ThumbnailService.generate_thumbnail
in a single DBOS step. The retry policy (max_attempts=2) matches Celery's
max_retries=2 for parity. Memoized by workflow_id so re-runs after a worker
crash skip the ffmpeg pass on the second attempt.
"""

from __future__ import annotations

from typing import Any

from dbos import DBOS
from loguru import logger

from app.db.scope import system_request_scope


@DBOS.step(retries_allowed=True, max_attempts=2)
async def generate_thumbnail_step(
    resource_id: str, file_path: str, mime_type: str
) -> bool:
    from app.services.media.render.thumbnail_service import ThumbnailService

    try:
        await ThumbnailService().generate_thumbnail(
            resource_id=resource_id,
            file_path=file_path,
            mime_type=mime_type,
        )
        logger.info(f"[Thumbnail] resource={resource_id} generated")
        return True
    except Exception as e:
        logger.warning(f"[Thumbnail] resource={resource_id} failed: {e}")
        # Match Celery's "return False on non-retried failure" — DBOS will not
        # retry on the False return; it retries on raised exceptions only.
        return False


@DBOS.workflow()
async def thumbnail_workflow(
    resource_id: str, file_path: str, mime_type: str
) -> dict[str, Any]:
    # Thumbnail regeneration is an owner-agnostic derived-asset write keyed by
    # resource_id (the step → ThumbnailService writes resources.thumbnail_path).
    # It is dispatched OUT-OF-BAND as its own DBOS workflow with no user_id and no
    # ambient scope to inherit, so SYSTEM is the correct minimal treatment
    # (consistent with transcode's None→SYSTEM branch and the sweepers). The
    # constant SYSTEM is deterministic across DBOS replay. INERT until
    # SCOPE_ENFORCE_RESOURCES — the choke point ignores _scope today.
    async with system_request_scope(reason="system-thumbnail"):
        success = await generate_thumbnail_step(resource_id, file_path, mime_type)
    return {"resource_id": resource_id, "success": success}
