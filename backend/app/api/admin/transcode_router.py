"""Admin API routes for HLS Transcode management."""

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.core.config import settings
from app.repositories.admin.transcode_repository import AdminTranscodeRepository
from app.schemas.admin import (
    AdminTranscodeVersionResponse,
    AdminTranscodeListResponse,
    AdminTranscodeStatsResponse,
    AdminTranscodeSettingsResponse,
    AdminTranscodeSettingsUpdate,
)
from app.utils.admin_helpers import create_audit_log


router = APIRouter()

# Valid transcode status values (None = not transcoded)
VALID_STATUSES = {"pending", "processing", "completed", "failed", "null"}

# Tiers to check for HLS status
HLS_TIER_NAMES = ["480p", "720p", "1080p", "source"]


def _scan_hls_tiers(hls_path: str) -> dict[str, bool]:
    """Scan HLS directory to check which tier subdirectories exist.

    Args:
        hls_path: Relative path to master.m3u8 (e.g. "teams/.../hls/master.m3u8")

    Returns:
        Dict mapping tier name to whether its stream.m3u8 exists.
    """
    base = Path(settings.DOWNLOAD_PATH)
    # hls_path points to master.m3u8; parent is the hls/ dir
    hls_dir = base / hls_path.replace("/master.m3u8", "").replace("\\master.m3u8", "")

    result = {}
    for tier in HLS_TIER_NAMES:
        tier_playlist = hls_dir / tier / "stream.m3u8"
        result[tier] = tier_playlist.exists()
    return result


@router.get("/stats", response_model=AdminTranscodeStatsResponse)
async def get_transcode_stats(auth: AdminAuthDep):
    """Get transcode status distribution for video resource versions."""
    repo = AdminTranscodeRepository()

    # Total + 4 status counts concurrently instead of 5 sequential queries.
    total, status_counts = await asyncio.gather(
        repo.count_total_video_versions(),
        repo.status_counts(["completed", "processing", "failed", "pending"]),
    )

    not_transcoded = total - sum(status_counts.values())

    return AdminTranscodeStatsResponse(
        total_video_versions=total,
        completed=status_counts.get("completed", 0),
        processing=status_counts.get("processing", 0),
        failed=status_counts.get("failed", 0),
        pending=status_counts.get("pending", 0),
        not_transcoded=not_transcoded,
    )


@router.get("", response_model=AdminTranscodeListResponse)
async def list_transcode_versions(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    min_size_mb: Optional[int] = Query(None, ge=0),
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
):
    """List video resource versions with transcode info."""
    # Route status_filter through the repo's validator set
    resolved_status = status_filter if status_filter in VALID_STATUSES else None

    repo = AdminTranscodeRepository()
    rows, total = await repo.list_video_versions(
        page=page,
        page_size=page_size,
        status_filter=resolved_status,
        min_size_mb=min_size_mb,
        sort_by=sort_by or "created_at",
        sort_desc=(sort_order != "asc"),
    )

    # Lookup display fields (title / cover / author) for each resource_id
    resource_ids = list({r["resource_id"] for r in rows if r.get("resource_id")})
    media_info_map: dict[str, dict] = {}
    if resource_ids:
        media_id_map = await repo.resources_to_media(resource_ids)
        media_by_id = await repo.media_info_bulk(list(set(media_id_map.values())))
        for res_id, mid in media_id_map.items():
            if mid in media_by_id:
                media_info_map[res_id] = media_by_id[mid]

    # Search filter (post-query on title/filename since PostgREST can't join-search)
    if search:
        search_lower = search.lower()
        rows = [
            r
            for r in rows
            if search_lower in (r.get("filename") or "").lower()
            or search_lower
            in (
                media_info_map.get(str(r["resource_id"]), {}).get("title") or ""
            ).lower()
        ]

    # Build response
    items = []
    for row in rows:
        rid = str(row["resource_id"])
        media = media_info_map.get(rid, {})
        hls_path = row.get("hls_path")
        hls_tiers = _scan_hls_tiers(hls_path) if hls_path else None
        items.append(
            AdminTranscodeVersionResponse(
                id=str(row["id"]),
                resource_id=rid,
                version_number=row.get("version_number", 1),
                filename=row.get("filename"),
                file_size_bytes=row.get("file_size_bytes") or 0,
                mime_type=row.get("mime_type"),
                transcode_status=row.get("transcode_status"),
                hls_path=hls_path,
                hls_tiers=hls_tiers,
                transcode_at=row.get("transcode_at"),
                created_at=row.get("created_at"),
                video_title=media.get("title"),
                cover_url=(media.get("cover_urls") or [None])[0],
                cover_download_path=media.get("cover_download_path"),
                source_platform=media.get("source_platform"),
                author=media.get("author"),
            )
        )

    return AdminTranscodeListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{version_id}/retry")
async def retry_transcode(
    version_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """Retry HLS transcode for a specific resource version."""
    repo = AdminTranscodeRepository()

    version = await repo.get_version(version_id)
    if not version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Version not found"
        )

    mime_type = version.get("mime_type") or ""
    if not mime_type.startswith("video/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Version is not a video file",
        )

    resource_id = str(version["resource_id"])

    # Mark as pending and dispatch Celery task directly.
    # NOTE: cannot call maybe_trigger_transcode() here because it uses
    # run_async(asyncio.run()) which crashes inside an already-running
    # event loop (FastAPI's async handler).
    await repo.mark_pending(version_id)

    from app.tasks.transcode_tasks import transcode_to_hls

    await asyncio.to_thread(
        transcode_to_hls.delay, resource_id, version_id, auth.user_id
    )

    # Audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="transcode_retry",
        target_type="resource_version",
        target_id=version_id,
        details={"resource_id": resource_id},
        ip_address=request.client.host if request.client else None,
    )

    logger.info(
        f"[Admin] Transcode retry queued: version={version_id} by admin={auth.user_id}"
    )
    return {"message": "Transcode retry queued", "version_id": version_id}


@router.post("/batch")
async def batch_transcode(
    auth: AdminAuthDep,
    request: Request,
    action: str = Query(..., pattern="^(retry_failed|transcode_new)$"),
):
    """Batch transcode operations: retry failed or transcode new (untranscoded)."""
    repo = AdminTranscodeRepository()
    versions = await repo.list_versions_for_batch(action)
    queued = 0

    # Dispatch Celery tasks directly (cannot use maybe_trigger_transcode in async context)
    from app.tasks.transcode_tasks import transcode_to_hls

    for v in versions:
        try:
            vid = str(v["id"])
            rid = str(v["resource_id"])
            await repo.mark_pending(vid)
            await asyncio.to_thread(transcode_to_hls.delay, rid, vid, auth.user_id)
            queued += 1
        except Exception as e:
            logger.warning(f"[Admin] Batch transcode failed for version {v['id']}: {e}")

    # Audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action=f"transcode_batch_{action}",
        target_type="resource_version",
        target_id="batch",
        details={"action": action, "total_found": len(versions), "queued": queued},
        ip_address=request.client.host if request.client else None,
    )

    logger.info(
        f"[Admin] Batch transcode {action}: {queued}/{len(versions)} queued "
        f"by admin={auth.user_id}"
    )
    return {
        "message": f"Batch {action}: {queued} transcode tasks queued",
        "total_found": len(versions),
        "queued": queued,
    }


# Valid values for settings validation
VALID_TIERS = {"480p", "720p", "1080p"}
VALID_ENCODERS = {"auto", "libx264", "h264_nvenc", "h264_videotoolbox", "h264_qsv"}
VALID_PRESETS = {
    "ultrafast",
    "veryfast",
    "fast",
    "medium",
    "slow",
    "veryslow",
    "p1",
    "p2",
    "p3",
    "p4",
    "p5",
    "p6",
    "p7",
}


async def _load_transcode_settings_from_db() -> dict:
    """Load transcode settings from system_settings table."""
    repo = AdminTranscodeRepository()
    db_map = await repo.load_settings()
    return {
        "transcode_enabled": db_map.get(
            "transcode_enabled", settings.TRANSCODE_ENABLED
        ),
        "transcode_tiers": db_map.get("transcode_tiers", settings.TRANSCODE_TIERS),
        "ffmpeg_encoder": db_map.get("transcode_encoder", settings.FFMPEG_ENCODER),
        "ffmpeg_preset": db_map.get("transcode_preset", settings.FFMPEG_PRESET),
        "transcode_parallel_tiers": db_map.get(
            "transcode_parallel_tiers", settings.TRANSCODE_PARALLEL_TIERS
        ),
        "transcode_min_size_mb": db_map.get(
            "transcode_min_size_mb", settings.TRANSCODE_MIN_SIZE_MB
        ),
    }


@router.get("/settings", response_model=AdminTranscodeSettingsResponse)
async def get_transcode_settings(auth: AdminAuthDep):
    """Get current HLS transcode settings from database."""
    vals = await _load_transcode_settings_from_db()
    return AdminTranscodeSettingsResponse(**vals)


@router.put("/settings", response_model=AdminTranscodeSettingsResponse)
async def update_transcode_settings(
    body: AdminTranscodeSettingsUpdate,
    auth: AdminAuthDep,
    request: Request,
):
    """Update HLS transcode settings in database with validation and audit logging."""

    # Validate tiers
    if body.transcode_tiers is not None:
        tiers = {t.strip() for t in body.transcode_tiers.split(",") if t.strip()}
        if not tiers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one tier must be specified",
            )
        invalid = tiers - VALID_TIERS
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid tiers: {invalid}. Valid: {VALID_TIERS}",
            )

    # Validate encoder
    if body.ffmpeg_encoder is not None and body.ffmpeg_encoder not in VALID_ENCODERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid encoder: {body.ffmpeg_encoder}. Valid: {VALID_ENCODERS}",
        )

    # Validate preset
    if body.ffmpeg_preset is not None and body.ffmpeg_preset not in VALID_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid preset: {body.ffmpeg_preset}. Valid: {VALID_PRESETS}",
        )

    # Validate min_size_mb
    if body.transcode_min_size_mb is not None and body.transcode_min_size_mb < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_size_mb must be >= 0 (0 means transcode all videos)",
        )

    # Save to database
    repo = AdminTranscodeRepository()
    changes = {}
    field_to_db_key = {
        "transcode_enabled": "transcode_enabled",
        "transcode_tiers": "transcode_tiers",
        "ffmpeg_encoder": "transcode_encoder",
        "ffmpeg_preset": "transcode_preset",
        "transcode_parallel_tiers": "transcode_parallel_tiers",
        "transcode_min_size_mb": "transcode_min_size_mb",
    }

    for field, db_key in field_to_db_key.items():
        value = getattr(body, field, None)
        if value is not None:
            await repo.upsert_setting(db_key, value, auth.user_id)
            changes[field] = value
            # Also update in-memory settings
            settings_attr = {
                "transcode_enabled": "TRANSCODE_ENABLED",
                "transcode_tiers": "TRANSCODE_TIERS",
                "ffmpeg_encoder": "FFMPEG_ENCODER",
                "ffmpeg_preset": "FFMPEG_PRESET",
                "transcode_parallel_tiers": "TRANSCODE_PARALLEL_TIERS",
                "transcode_min_size_mb": "TRANSCODE_MIN_SIZE_MB",
            }.get(field)
            if settings_attr:
                setattr(settings, settings_attr, value)

    # Audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="transcode_settings_update",
        target_type="settings",
        target_id="transcode",
        details=changes,
        ip_address=request.client.host if request.client else None,
    )

    logger.info(
        f"[Admin] Transcode settings updated: {changes} by admin={auth.user_id}"
    )

    vals = await _load_transcode_settings_from_db()
    return AdminTranscodeSettingsResponse(**vals)
