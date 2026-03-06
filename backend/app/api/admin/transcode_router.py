"""Admin API routes for HLS Transcode management."""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.core.config import settings
from app.db import get_async_supabase_admin
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
    supabase = await get_async_supabase_admin()

    # Total video versions
    total_result = (
        await supabase.table("resource_versions")
        .select("id", count="exact")
        .like("mime_type", "video/%")
        .execute()
    )
    total = total_result.count or 0

    # Count by each transcode_status
    status_counts = {}
    for s in ("completed", "processing", "failed", "pending"):
        result = (
            await supabase.table("resource_versions")
            .select("id", count="exact")
            .like("mime_type", "video/%")
            .eq("transcode_status", s)
            .execute()
        )
        status_counts[s] = result.count or 0

    # Not transcoded = NULL transcode_status
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
    supabase = await get_async_supabase_admin()

    # Build base query: resource_versions joined with resources
    query = (
        supabase.table("resource_versions")
        .select(
            "id, resource_id, version_number, filename, file_size_bytes, "
            "mime_type, transcode_status, hls_path, transcode_at, created_at",
            count="exact",
        )
        .like("mime_type", "video/%")
    )

    # Status filter
    if status_filter:
        if status_filter == "null":
            query = query.is_("transcode_status", "null")
        elif status_filter in VALID_STATUSES:
            query = query.eq("transcode_status", status_filter)

    # Size filter
    if min_size_mb and min_size_mb > 0:
        query = query.gte("file_size_bytes", min_size_mb * 1024 * 1024)

    # Sorting
    valid_sort_fields = {"created_at", "file_size_bytes", "transcode_at"}
    if sort_by not in valid_sort_fields:
        sort_by = "created_at"
    desc = sort_order != "asc"
    query = query.order(sort_by, desc=desc)

    # Pagination
    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = await query.execute()
    rows = result.data or []
    total = result.count or 0

    # Collect resource_ids to look up parsed_media info
    resource_ids = list({r["resource_id"] for r in rows if r.get("resource_id")})

    # Get resources → media_id mapping
    media_info_map: dict[str, dict] = {}
    if resource_ids:
        res_result = (
            await supabase.table("resources")
            .select("id, media_id")
            .in_("id", resource_ids)
            .execute()
        )
        media_id_map = {
            str(r["id"]): str(r["media_id"])
            for r in (res_result.data or [])
            if r.get("media_id")
        }

        # Get parsed_media for video_title, cover_url, author
        media_ids = list(set(media_id_map.values()))
        if media_ids:
            media_result = (
                await supabase.table("parsed_media")
                .select("id, video_title, cover_url, author")
                .in_("id", media_ids)
                .execute()
            )
            media_by_id = {
                str(m["id"]): m for m in (media_result.data or [])
            }

            # Map resource_id → media info
            for res_id, mid in media_id_map.items():
                if mid in media_by_id:
                    media_info_map[res_id] = media_by_id[mid]

    # Search filter (post-query on title/filename since PostgREST can't join-search)
    if search:
        search_lower = search.lower()
        rows = [
            r for r in rows
            if search_lower in (r.get("filename") or "").lower()
            or search_lower in (
                media_info_map.get(str(r["resource_id"]), {}).get("video_title") or ""
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
                video_title=media.get("video_title"),
                cover_url=media.get("cover_url"),
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
    supabase = await get_async_supabase_admin()

    # Verify version exists and is a video
    result = (
        await supabase.table("resource_versions")
        .select("id, resource_id, mime_type")
        .eq("id", version_id)
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    version = result.data
    mime_type = version.get("mime_type") or ""
    if not mime_type.startswith("video/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Version is not a video file",
        )

    resource_id = str(version["resource_id"])

    # Trigger transcode with force=True
    from app.tasks.transcode_tasks import maybe_trigger_transcode
    maybe_trigger_transcode(
        resource_id=resource_id,
        version_id=version_id,
        mime_type=mime_type,
        user_id=auth.user_id,
        force=True,
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

    logger.info(f"[Admin] Transcode retry queued: version={version_id} by admin={auth.user_id}")
    return {"message": "Transcode retry queued", "version_id": version_id}


@router.post("/batch")
async def batch_transcode(
    auth: AdminAuthDep,
    request: Request,
    action: str = Query(..., pattern="^(retry_failed|transcode_new)$"),
):
    """Batch transcode operations: retry failed or transcode new (untranscoded)."""
    supabase = await get_async_supabase_admin()

    if action == "retry_failed":
        result = (
            await supabase.table("resource_versions")
            .select("id, resource_id, mime_type")
            .like("mime_type", "video/%")
            .eq("transcode_status", "failed")
            .execute()
        )
    else:  # transcode_new
        result = (
            await supabase.table("resource_versions")
            .select("id, resource_id, mime_type")
            .like("mime_type", "video/%")
            .is_("transcode_status", "null")
            .execute()
        )

    versions = result.data or []
    queued = 0

    from app.tasks.transcode_tasks import maybe_trigger_transcode

    for v in versions:
        try:
            maybe_trigger_transcode(
                resource_id=str(v["resource_id"]),
                version_id=str(v["id"]),
                mime_type=v.get("mime_type") or "video/mp4",
                user_id=auth.user_id,
                force=True,
            )
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
VALID_PRESETS = {"ultrafast", "veryfast", "fast", "medium", "slow", "veryslow", "p1", "p2", "p3", "p4", "p5", "p6", "p7"}


@router.get("/settings", response_model=AdminTranscodeSettingsResponse)
async def get_transcode_settings(auth: AdminAuthDep):
    """Get current HLS transcode settings."""
    from app.api.frontend_config_router import load_config

    config = load_config()
    transcode = config.get("transcode", {})

    return AdminTranscodeSettingsResponse(
        transcode_enabled=transcode.get("enabled", settings.TRANSCODE_ENABLED),
        transcode_tiers=transcode.get("tiers") or settings.TRANSCODE_TIERS,
        ffmpeg_encoder=transcode.get("encoder") or settings.FFMPEG_ENCODER,
        ffmpeg_preset=transcode.get("preset") or settings.FFMPEG_PRESET,
        transcode_parallel_tiers=transcode.get(
            "parallel_tiers", settings.TRANSCODE_PARALLEL_TIERS
        ),
    )


@router.put("/settings", response_model=AdminTranscodeSettingsResponse)
async def update_transcode_settings(
    body: AdminTranscodeSettingsUpdate,
    auth: AdminAuthDep,
    request: Request,
):
    """Update HLS transcode settings with validation and audit logging."""
    from app.api.frontend_config_router import load_config, save_config

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

    # Load, update, save config
    config = load_config()
    if "transcode" not in config:
        config["transcode"] = {}

    changes = {}
    if body.transcode_enabled is not None:
        config["transcode"]["enabled"] = body.transcode_enabled
        settings.TRANSCODE_ENABLED = body.transcode_enabled
        changes["enabled"] = body.transcode_enabled

    if body.transcode_tiers is not None:
        config["transcode"]["tiers"] = body.transcode_tiers
        settings.TRANSCODE_TIERS = body.transcode_tiers
        changes["tiers"] = body.transcode_tiers

    if body.ffmpeg_encoder is not None:
        config["transcode"]["encoder"] = body.ffmpeg_encoder
        settings.FFMPEG_ENCODER = body.ffmpeg_encoder
        changes["encoder"] = body.ffmpeg_encoder

    if body.ffmpeg_preset is not None:
        config["transcode"]["preset"] = body.ffmpeg_preset
        settings.FFMPEG_PRESET = body.ffmpeg_preset
        changes["preset"] = body.ffmpeg_preset

    if body.transcode_parallel_tiers is not None:
        config["transcode"]["parallel_tiers"] = body.transcode_parallel_tiers
        settings.TRANSCODE_PARALLEL_TIERS = body.transcode_parallel_tiers
        changes["parallel_tiers"] = body.transcode_parallel_tiers

    if not save_config(config):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save config",
        )

    # Audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="transcode_settings_update",
        target_type="settings",
        target_id="transcode",
        details=changes,
        ip_address=request.client.host if request.client else None,
    )

    logger.info(f"[Admin] Transcode settings updated: {changes} by admin={auth.user_id}")

    transcode = config.get("transcode", {})
    return AdminTranscodeSettingsResponse(
        transcode_enabled=transcode.get("enabled", settings.TRANSCODE_ENABLED),
        transcode_tiers=transcode.get("tiers") or settings.TRANSCODE_TIERS,
        ffmpeg_encoder=transcode.get("encoder") or settings.FFMPEG_ENCODER,
        ffmpeg_preset=transcode.get("preset") or settings.FFMPEG_PRESET,
        transcode_parallel_tiers=transcode.get(
            "parallel_tiers", settings.TRANSCODE_PARALLEL_TIERS
        ),
    )
