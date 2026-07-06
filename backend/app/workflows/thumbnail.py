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


# ─── Lazy-generation backfill sweeper (million-files P1) ────────────────────
#
# Bulk import paths deliberately do NOT chain thumbnail generation any more —
# covers generate on first view (see serve_resource_cover's lazy enqueue).
# This sweeper is the OPTIONAL long-tail eraser: when enabled it drains the
# backlog of thumbnail-less resources at a gentle rate during idle time.
#
# OFF BY DEFAULT. Toggle via the system_settings row:
#   key='thumbnail_backfill', value jsonb {"enabled": true, "batch": 60}
# (config lives in the DB per the env→DB convention — no env var.)

_BACKFILL_SETTINGS_KEY = "thumbnail_backfill"
_BACKFILL_DEFAULT_BATCH = 60
_BACKFILL_MAX_BATCH = 500


@DBOS.step(retries_allowed=False)
async def _backfill_scan_step() -> list[dict[str, Any]]:
    """Read the toggle + claim a batch of thumbnail-less resources.

    Returns [] when disabled. DB reads only — dispatching happens in the
    WORKFLOW body, never inside a step (retry_failed_downloads lesson:
    a step that dispatches re-dispatches on replay).
    """
    import json

    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT value FROM public.system_settings WHERE key = :k",
        {"k": _BACKFILL_SETTINGS_KEY},
    )
    if not row:
        return []
    value = row.get("value")
    # asyncpg hands jsonb back as a JSON string; be tolerant of both shapes.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, dict) or not value.get("enabled"):
        return []

    batch = value.get("batch") or _BACKFILL_DEFAULT_BATCH
    try:
        batch = max(1, min(int(batch), _BACKFILL_MAX_BATCH))
    except (TypeError, ValueError):
        batch = _BACKFILL_DEFAULT_BATCH

    rows = await db_engine.fetch_all(
        """
        SELECT id, file_path, mime_type
          FROM public.resources
         WHERE thumbnail_path IS NULL
           AND media_id IS NULL
           AND is_trashed = false
           AND file_path IS NOT NULL
           AND file_path NOT LIKE 'http%'
         ORDER BY id
         LIMIT :batch
        """,
        {"batch": batch},
    )
    return [
        {
            "resource_id": str(r["id"]),
            "file_path": r["file_path"],
            "mime_type": r.get("mime_type") or "",
        }
        for r in rows
    ]


@DBOS.scheduled("*/5 * * * *")
@DBOS.workflow()
async def thumbnail_backfill_workflow(scheduled_time: Any, actual_time: Any) -> None:
    """Every 5 minutes: when the toggle is on, enqueue one gentle batch.

    Dispatch uses the same date-bucketed idempotency key as the lazy cover
    path (`thumb-lazy-{id}-{date}`), so the sweeper and a concurrent viewer
    can never double-generate the same resource on the same day.
    """
    from datetime import timezone

    from app.services.infra.dbos_orchestrator import start_workflow_routed

    items = await _backfill_scan_step()
    if not items:
        return

    # scheduled_time is DBOS-provided and replay-deterministic — safe to
    # derive the idempotency date bucket from (never datetime.now()).
    date_bucket = scheduled_time.astimezone(timezone.utc).strftime("%Y%m%d")
    dispatched = 0
    for item in items:
        try:
            await start_workflow_routed(
                "thumbnail",
                dbos_workflow_callable=thumbnail_workflow,
                dbos_workflow_kwargs=item,
                workflow_id=f"thumb-lazy-{item['resource_id']}-{date_bucket}",
            )
            dispatched += 1
        except Exception as e:
            logger.warning(
                f"[thumbnail.backfill] dispatch {item['resource_id']} failed: {e}"
            )
    logger.info(f"[thumbnail.backfill] tick: dispatched {dispatched}/{len(items)}")
