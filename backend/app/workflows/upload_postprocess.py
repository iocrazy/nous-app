"""upload_postprocess DBOS workflow.

Promotes the post-upload processing pipeline (ffprobe/Pillow metadata
probe + thumbnail generation + HLS transcode trigger) out of the upload
HTTP request and into its own DBOS workflow with a visible task_tracking
row + status.

Why
---
``ResourcesService.upload_file`` used to run the metadata probe, thumbnail
generation and transcode gating synchronously, inline, before returning
the HTTP response. For large uploads that meant the client waited on an
ffprobe pass and a thumbnail render after the bytes were already on disk.

This workflow lets the upload route return as soon as the bytes are
written; a later task wires the route to dispatch this workflow with the
freshly-created resource. The processing then runs asynchronously with an
independent task_tracking row the user can watch in the Task Center.

Design (mirrors extract_audio / thumbnail conventions)
------------------------------------------------------
  - Lifecycle (phase / status / progress) is driven exclusively through
    the ``UnifiedTaskManager`` API (create → start → complete). We never
    PATCH the phase columns directly (CLAUDE.md 路线 C rule 2).
  - The genuinely-retryable I/O probes (metadata, thumbnail) are isolated
    in ``@DBOS.step`` functions so a worker restart mid-processing replays
    the cached result instead of re-running ffprobe. Steps take primitives
    only and construct their service internally — a service instance holds
    async DB clients and is not checkpoint-serializable, so it must never
    cross the step boundary (same pattern as extract_audio / thumbnail).
  - The single body-level ``ResourcesService`` (``svc``) owns ALL of the
    ``resources`` / ``resource_versions`` repo writes plus the transcode
    dispatch. The transcode trigger MUST run in the workflow body (not a
    step) because ``_trigger_transcode_async`` → ``start_workflow_routed``
    → ``DBOS.start_workflow`` asserts when invoked inside a step context
    (same constraint extract_audio hits with its transcript/summary chain).
  - Each of the three phases (metadata / thumbnail / transcode) is wrapped
    in its own try/except so one failing asset never aborts the others —
    this mirrors today's non-fatal inline behavior. None of them are fatal
    to the workflow; only an unexpected error outside the phase guards
    would propagate and let DBOS mark the workflow FAILED (CLAUDE.md 路线 C
    rule 4: raise, never return a failed dict).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.core.config import settings
from app.db.scope import Scope, request_scope


@DBOS.step(retries_allowed=True, max_attempts=2)
async def probe_metadata_step(file_path: str, file_type: str) -> dict[str, Any]:
    """Probe media metadata (duration/resolution via ffprobe for
    video/audio, resolution via Pillow for image).

    Pure read I/O — returns the metadata dict (empty on probe failure or
    an unsupported file type). The body persists it; this step only
    extracts so the ffprobe/Pillow pass is the memoized unit on replay.

    Constructs its own ``ResourcesService`` (the ``_extract_*`` helpers
    live on it) — never receives one across the step boundary.
    """
    from app.services.library.resources_service import ResourcesService

    svc = ResourcesService()
    abs_path = Path(settings.DOWNLOAD_PATH) / file_path
    if file_type in ("video", "audio"):
        return await svc._extract_video_metadata(str(abs_path))
    if file_type == "image":
        return await svc._extract_image_metadata(str(abs_path))
    return {}


@DBOS.step(retries_allowed=True, max_attempts=2)
async def generate_thumbnail_step(
    resource_id: str, file_path: str, mime_type: str
) -> Optional[str]:
    """Generate a thumbnail next to the source file. Returns the relative
    thumbnail path, or None on failure/skip. The body persists it onto the
    resource. ThumbnailService is a distinct class — constructed here."""
    from app.services.media.render.thumbnail_service import ThumbnailService

    return await ThumbnailService().generate_thumbnail(
        resource_id=resource_id, file_path=file_path, mime_type=mime_type
    )


@DBOS.workflow()
async def upload_postprocess_workflow(
    resource_id: str,
    file_path: str,
    file_type: str,
    mime_type: str,
    user_id: str,
) -> dict[str, Any]:
    """Run metadata + thumbnail + transcode post-processing for a freshly
    uploaded resource asynchronously.

    workflow_id idempotency: re-running with the same id replays the cached
    probe/thumbnail step results. The body-level repo writes are idempotent
    (overwriting the same derived metadata / thumbnail_path).
    """
    from app.services.infra.unified_task_manager import get_task_manager
    from app.services.library.resources_service import ResourcesService

    manager = get_task_manager()
    task_id = DBOS.workflow_id
    filename = file_path.rsplit("/", 1)[-1]

    # ── task_tracking row (lifecycle via manager API only) ──────────────
    try:
        await manager.create(
            user_id=user_id,
            task_type="upload_postprocess",
            title=f"Post-process {filename}"[:200],
            subtitle=f"Post-processing {filename}",
            resource_id=str(resource_id) if resource_id else None,
            dbos_workflow_id=task_id,
        )
    except Exception as e:
        logger.warning(
            f"[upload_postprocess] create task_tracking failed for "
            f"{resource_id} (non-fatal): {e}"
        )
    try:
        await manager.start(task_id, user_id=user_id, task_type="upload_postprocess")
    except Exception as e:
        logger.warning(f"[upload_postprocess] start {task_id}: {e}")

    # Single body-level service: owns every resources/resource_versions
    # write + the transcode dispatch. Resource writes are on-behalf-of the
    # uploading user, so establish the ambient USER scope here — it is
    # visible to the awaited repo calls (same async context). INERT until
    # SCOPE_ENFORCE_RESOURCES flips (the choke point ignores _scope today).
    svc = ResourcesService()

    async with request_scope(Scope(user_id=user_id)):
        # ── Phase A: metadata probe + persist (non-fatal) ──────────────
        try:
            metadata = await probe_metadata_step(file_path, file_type)
            if metadata:
                await svc.repo.update_resource(resource_id, metadata)
                version = await svc.repo.get_version_by_number(resource_id, 1)
                if version and version.get("id"):
                    await svc.repo.update_version(str(version["id"]), metadata)
        except Exception as e:
            logger.warning(
                f"[upload_postprocess] metadata phase failed for "
                f"{resource_id} (non-fatal): {e}"
            )

        # ── Phase B: thumbnail (non-fatal) ─────────────────────────────
        try:
            thumb = await generate_thumbnail_step(resource_id, file_path, mime_type)
            if thumb:
                await svc.repo.update_resource(resource_id, {"thumbnail_path": thumb})
        except Exception as e:
            logger.warning(
                f"[upload_postprocess] thumbnail phase failed for "
                f"{resource_id} (non-fatal): {e}"
            )

        # ── Phase C: transcode trigger (video only, non-fatal) ─────────
        # In the workflow body, NOT a @DBOS.step — _trigger_transcode_async
        # → start_workflow_routed → DBOS.start_workflow asserts inside a
        # step. It self-gates (size/duration) + dispatches its own workflow.
        if file_type == "video":
            try:
                version = await svc.repo.get_version_by_number(resource_id, 1)
                if version and version.get("id"):
                    await svc._trigger_transcode_async(
                        resource_id, str(version["id"]), mime_type, user_id=user_id
                    )
            except Exception as e:
                logger.warning(
                    f"[upload_postprocess] transcode phase failed for "
                    f"{resource_id} (non-fatal): {e}"
                )

    try:
        await manager.complete(task_id, subtitle="Post-processing complete")
    except Exception as e:
        logger.warning(f"[upload_postprocess] complete {task_id}: {e}")

    return {"status": "success", "resource_id": resource_id}
