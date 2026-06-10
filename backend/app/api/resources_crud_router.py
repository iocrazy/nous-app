# backend/app/api/resources_crud_router.py

"""
Resources CRUD Router

Core resource CRUD, file serving, recycle bin operations,
move, tags, and batch transcode.
"""

from datetime import date
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_dep import ScopedRequestDep
from app.core.scope_guards import verify_scope_access
from app.db.scope import Scope, request_scope, system_request_scope
from app.repositories.resources_repository import (
    UNTRANSCODED_BATCH,
    ResourcesRepository,
)
from app.schemas.canvas_crop_schema import CropDeriveRequest
from app.schemas.canvas_grid_schema import GridDeriveRequest
from app.schemas.canvas_mask_schema import MaskDeriveRequest
from app.schemas.resources import (
    ChorusUpdate,
    ResourceMoveRequest,
    ResourceTagRequest,
    ResourceUpdate,
)
from app.services.library.resources_service import ResourcesService

router = APIRouter(prefix="/resources")

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


# ============================================
# Resource list / CRUD endpoints
# ============================================


_ALLOWED_TYPE_CATEGORIES = {"video", "image", "audio", "document", "other"}
_ALLOWED_ASPECT_RATIOS = {"9:16", "16:9", "1:1", "4:3", "other"}
_ALLOWED_SOCIAL_COMBINE = {"and", "or"}


@router.get("")
async def list_resources(
    auth: AuthDep,
    _tenant_scope: ScopedRequestDep,
    scope_id: str = Query(...),
    _scope_guard: None = Depends(verify_scope_access),
    folder_id: Optional[str] = Query(None),
    tag_ids: Optional[List[str]] = Query(
        None,
        description=(
            "Filter by tag ids (AND semantics — resource must carry every tag)."
        ),
    ),
    min_rating: Optional[int] = Query(
        None,
        ge=0,
        le=5,
        description="Return only resources with rating >= this value (0-5).",
    ),
    types: Optional[List[str]] = Query(
        None,
        description=(
            "Filter by resource type category (IN semantics). "
            "Allowed: video, image, audio, document, other."
        ),
    ),
    platforms: Optional[List[str]] = Query(
        None,
        description=(
            "Filter by source platform (IN semantics) — matches "
            "parsed_media.source_platform (e.g. douyin, youtube, bilibili)."
        ),
    ),
    ai_transcribed: Optional[bool] = Query(
        None,
        description=("If true, only resources whose transcript_status == 'completed'."),
    ),
    ai_summarized: Optional[bool] = Query(
        None,
        description=("If true, only resources whose summary_status == 'completed'."),
    ),
    ai_analyzed: Optional[bool] = Query(
        None,
        description=(
            "If true, only resources whose visual_analysis_status == 'completed'."
        ),
    ),
    created_after: Optional[date] = Query(
        None,
        description="Inclusive lower bound on resource.created_at (UTC date).",
    ),
    created_before: Optional[date] = Query(
        None,
        description="Inclusive upper bound on resource.created_at (UTC date).",
    ),
    duration_min: Optional[int] = Query(
        None,
        ge=0,
        description=(
            "Inclusive lower bound on resource.duration_seconds (in seconds)."
        ),
    ),
    duration_max: Optional[int] = Query(
        None,
        ge=0,
        description=(
            "Inclusive upper bound on resource.duration_seconds (in seconds)."
        ),
    ),
    aspect_ratios: Optional[List[str]] = Query(
        None,
        description=(
            "Filter by aspect-ratio bucket. Allowed: 9:16, 16:9, 1:1, 4:3, "
            "other. Accepted at the API surface; aspect filtering is "
            "currently applied client-side."
        ),
    ),
    min_likes: Optional[int] = Query(
        None,
        ge=0,
        description=(
            "Inclusive lower bound on parsed_media.like_count. "
            "Combined with the other social metrics via ``social_combine``."
        ),
    ),
    min_comments: Optional[int] = Query(
        None,
        ge=0,
        description=(
            "Inclusive lower bound on parsed_media.comment_count. "
            "Combined with the other social metrics via ``social_combine``."
        ),
    ),
    min_favorites: Optional[int] = Query(
        None,
        ge=0,
        description=(
            "Inclusive lower bound on parsed_media.favorite_count. "
            "Combined with the other social metrics via ``social_combine``."
        ),
    ),
    min_shares: Optional[int] = Query(
        None,
        ge=0,
        description=(
            "Inclusive lower bound on parsed_media.share_count. "
            "Combined with the other social metrics via ``social_combine``."
        ),
    ),
    social_combine: Optional[str] = Query(
        "and",
        description=(
            "Combine semantics across the four social >= thresholds. "
            '"and" requires every enabled threshold to match (default); '
            '"or" requires any one to match.'
        ),
    ),
    has_comments: Optional[bool] = Query(
        None,
        description=(
            "If true, only resources whose linked parsed_media has at "
            "least one comment (comment_count > 0). Applied on top of "
            "any ``min_comments`` threshold."
        ),
    ),
):
    """List resources in a scope, optionally filtered by folder/tag/rating/type/source/ai/date/duration/aspect."""
    # Validate and normalise `types` here so the repository can stay
    # strictly about data access (fail fast at the boundary).
    normalised_types: Optional[List[str]] = None
    if types:
        normalised_types = [t for t in types if t]
        for t in normalised_types:
            if t not in _ALLOWED_TYPE_CATEGORIES:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Invalid type category: {t!r}. "
                        f"Allowed: {sorted(_ALLOWED_TYPE_CATEGORIES)}."
                    ),
                )

    # Strip / drop empty strings from platforms so `?platforms=&platforms=`
    # is treated as absent rather than "match nothing".
    normalised_platforms: Optional[List[str]] = None
    if platforms:
        normalised_platforms = [p.strip() for p in platforms if p and p.strip()]
        if not normalised_platforms:
            normalised_platforms = None

    if (
        created_after is not None
        and created_before is not None
        and created_after > created_before
    ):
        raise HTTPException(
            status_code=422,
            detail="created_after must be <= created_before.",
        )

    if (
        duration_min is not None
        and duration_max is not None
        and duration_min > duration_max
    ):
        raise HTTPException(
            status_code=422,
            detail="duration_min must be <= duration_max.",
        )

    normalised_aspect_ratios: Optional[List[str]] = None
    if aspect_ratios:
        normalised_aspect_ratios = [a.strip() for a in aspect_ratios if a and a.strip()]
        for a in normalised_aspect_ratios:
            if a not in _ALLOWED_ASPECT_RATIOS:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Invalid aspect ratio: {a!r}. "
                        f"Allowed: {sorted(_ALLOWED_ASPECT_RATIOS)}."
                    ),
                )
        if not normalised_aspect_ratios:
            normalised_aspect_ratios = None

    normalised_social_combine: str = "and"
    if social_combine is not None:
        candidate = social_combine.strip().lower()
        if candidate not in _ALLOWED_SOCIAL_COMBINE:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid social_combine: {social_combine!r}. "
                    f"Allowed: {sorted(_ALLOWED_SOCIAL_COMBINE)}."
                ),
            )
        normalised_social_combine = candidate

    try:
        repo = ResourcesRepository()
        items = await repo.get_resource_items(
            scope_id=scope_id,
            folder_id=folder_id,
            tag_ids=tag_ids or None,
            min_rating=min_rating,
            types=normalised_types,
            platforms=normalised_platforms,
            ai_transcribed=ai_transcribed,
            ai_summarized=ai_summarized,
            ai_analyzed=ai_analyzed,
            created_after=created_after,
            created_before=created_before,
            duration_min=duration_min,
            duration_max=duration_max,
            aspect_ratios=normalised_aspect_ratios,
            min_likes=min_likes,
            min_comments=min_comments,
            min_favorites=min_favorites,
            min_shares=min_shares,
            social_combine=normalised_social_combine,
            has_comments=has_comments,
        )
        return {"success": True, "data": items}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list resources: {e}")
        raise HTTPException(status_code=500, detail="Failed to list resources")


@router.get("/trash")
async def list_trashed_resources(
    auth: AuthDep,
    _tenant_scope: ScopedRequestDep,
    scope_id: str = Query(...),
    _scope_guard: None = Depends(verify_scope_access),
):
    """List trashed resources in a scope."""
    try:
        repo = ResourcesRepository()
        items = await repo.get_trashed_resources(None, scope_id)
        return {"success": True, "data": items}
    except Exception as e:
        logger.error(f"Failed to list trashed resources: {e}")
        raise HTTPException(status_code=500, detail="Failed to list trashed resources")


@router.get("/trash/folders")
async def list_trashed_folders(
    auth: AuthDep,
    _tenant_scope: ScopedRequestDep,
    scope_id: str = Query(...),
    _scope_guard: None = Depends(verify_scope_access),
):
    """List trashed folders in a scope."""
    try:
        repo = ResourcesRepository()
        folders = await repo.get_trashed_folders(None, scope_id)
        return {"success": True, "data": folders}
    except Exception as e:
        logger.error(f"Failed to list trashed folders: {e}")
        raise HTTPException(status_code=500, detail="Failed to list trashed folders")


@router.post("/transcode/batch")
async def batch_transcode(auth: AuthDep, _scope: ScopedRequestDep):
    """Queue HLS transcoding for all video versions with NULL transcode_status."""
    try:
        repo = ResourcesRepository()
        versions = await repo.get_untranscoded_video_versions()

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.transcode import transcode_workflow

        queued = 0
        for v in versions:
            try:
                vid = str(v["id"])
                await repo.update_version(vid, {"transcode_status": "pending"})
                await start_workflow_routed(
                    "transcode",
                    dbos_workflow_callable=transcode_workflow,
                    dbos_workflow_kwargs={
                        "resource_id": str(v["resource_id"]),
                        "version_id": vid,
                        "user_id": auth.user_id,
                    },
                )
                queued += 1
            except Exception as e:
                logger.warning(
                    f"[Transcode/Batch] Failed to queue version {v['id']}: {e}"
                )

        # A full batch means more untranscoded versions remain (the repo caps
        # the working set so the request never balloons / never silently clips
        # at PostgREST's 1000 row ceiling). Each queued version is now `pending`
        # so it leaves the set — re-invoking drains the rest.
        has_more = len(versions) >= UNTRANSCODED_BATCH
        if has_more:
            logger.info(
                "[Transcode/Batch] Batch full (%s) — more untranscoded versions "
                "remain; re-invoke to continue.",
                UNTRANSCODED_BATCH,
            )
        logger.info(f"[Transcode/Batch] Queued {queued}/{len(versions)} versions")
        return {
            "success": True,
            "queued": queued,
            "total_found": len(versions),
            "has_more": has_more,
        }
    except Exception as e:
        logger.error(f"Failed to batch transcode: {e}")
        raise HTTPException(status_code=500, detail="Failed to batch transcode")


@router.get("/{resource_id}")
async def get_resource(resource_id: str, auth: AuthDep, _scope: ScopedRequestDep):
    """Get a single resource by ID (creator or team member only)."""
    from app.api.media_permissions import check_media_access

    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        if not await check_media_access(resource_id, auth.user_id, None):
            raise HTTPException(status_code=403, detail="Access denied")

        return {"success": True, "data": resource}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get resource")


@router.get("/{resource_id}/file")
async def serve_resource_file(
    resource_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    token: Optional[str] = Query(None),
    share_token: Optional[str] = Query(None),
):
    """Serve the actual file for preview/download.

    Supports auth via:
    - Authorization header (Bearer token)
    - X-API-Key header
    - ?token= query parameter (for <video>, <img>, <iframe> src)
    - ?share_token= query parameter (public share access)
    """
    from app.api.media_permissions import check_media_access
    from app.core.deps import get_auth

    try:
        user_id: Optional[str] = None

        # ?token= is the URL-auth transport for <a download>/<img>/<video>
        # src (no headers possible there). It may carry the signed media
        # token (frontend's `mediaToken`, purpose-built for URLs) OR a
        # Supabase JWT (legacy callers e.g. canvas OutputNodeView). Try
        # the media token first, fall back to treating it as a JWT.
        if token and not authorization and not x_api_key:
            from app.api.media_auth import validate_media_cookie

            user_id = await validate_media_cookie(token)

        effective_auth = authorization
        if not effective_auth and not x_api_key and token and user_id is None:
            effective_auth = f"Bearer {token}"

        if user_id is None and (effective_auth or x_api_key):
            try:
                auth = await get_auth(request, effective_auth, x_api_key)
                user_id = auth.user_id
            except HTTPException:
                if not share_token:
                    raise

        if not await check_media_access(resource_id, user_id, share_token):
            raise HTTPException(status_code=403, detail="Access denied")

        # Hybrid auth: an authenticated caller gets a USER tenant Scope; a
        # share-token-only (no auth) caller gets SYSTEM (cross-user public
        # serve). Establish the matching ambient scope around the resources
        # access. Inert until SCOPE_ENFORCE_RESOURCES flips on.
        if user_id is not None:
            _scope_cm = request_scope(Scope(user_id=user_id))
        else:
            _scope_cm = system_request_scope(reason="public-share-serve")

        async with _scope_cm:
            repo = ResourcesRepository()
            resource = await repo.get_resource_by_id(resource_id)
            if not resource:
                raise HTTPException(status_code=404, detail="Resource not found")

            file_path = resource.get("file_path")
            # PR-B: parsed-media-backed resources don't carry the shared
            # file path on the resources row. Fall through to parsed_media.
            if not file_path:
                media_id = resource.get("media_id")
                if media_id:
                    from app.db.supabase_client import get_async_supabase_admin

                    client = await get_async_supabase_admin()
                    try:
                        pm_res = (
                            await client.table("parsed_media")
                            .select("download_path")
                            .eq("id", media_id)
                            .maybe_single()
                            .execute()
                        )
                        if pm_res.data and pm_res.data.get("download_path"):
                            file_path = pm_res.data["download_path"]
                    except Exception as e:
                        logger.warning(
                            f"parsed_media file lookup failed for "
                            f"media_id={media_id}: {e}"
                        )
        if not file_path:
            raise HTTPException(status_code=404, detail="No file available")

        # When served with ?token= in URL, prevent intermediate caching.
        cache_headers = {"Cache-Control": "private, no-store"} if token else {}

        # Resolve full path from DOWNLOAD_PATH base
        from app.core.config import settings

        full_path = Path(settings.DOWNLOAD_PATH) / file_path

        if not full_path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")

        return FileResponse(
            path=str(full_path),
            media_type=resource.get("mime_type", "application/octet-stream"),
            content_disposition_type="inline",
            headers=cache_headers,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve file for resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve file")


@router.get("/{resource_id}/cover")
async def serve_resource_cover(resource_id: str):
    """Serve cover/thumbnail image for a resource (no auth required).

    Priority for INDEPENDENT resources (user uploads):
        thumbnail_path > cover_image_path > original file (if mime=image/*).

    For PARSED-MEDIA-BACKED resources (i.e., ``resource.media_id`` is set),
    cover lives on parsed_media — fall through to that as the source of
    truth. The duplicated ``cover_image_path`` / ``thumbnail_path``
    columns on resources are deprecated for this case (PR-A:
    cover-only).
    """
    try:
        # Public (no-auth) serve → SYSTEM scope for the resources lookup
        # (deliberate cross-user access). Inert until SCOPE_ENFORCE_RESOURCES.
        async with system_request_scope(reason="public-share-serve"):
            repo = ResourcesRepository()
            resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        import mimetypes

        from app.core.config import settings

        # Try thumbnail first, then cover image (independent uploads).
        for field in ("thumbnail_path", "cover_image_path"):
            rel_path = resource.get(field)
            if not rel_path:
                continue
            if rel_path.startswith("http"):
                continue
            full_path = Path(settings.DOWNLOAD_PATH) / rel_path
            if full_path.exists():
                mime, _ = mimetypes.guess_type(str(full_path))
                return FileResponse(
                    path=str(full_path),
                    media_type=mime or "image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800, immutable"},
                )

        # Parsed-media-backed resource: cover lives on parsed_media now,
        # not on the resources row. Resolve via the join.
        media_id = resource.get("media_id")
        if media_id:
            from app.db.supabase_client import get_async_supabase_admin

            client = await get_async_supabase_admin()
            try:
                pm_res = (
                    await client.table("parsed_media")
                    .select("cover_download_path,cover_download_status")
                    .eq("id", media_id)
                    .maybe_single()
                    .execute()
                )
                if pm_res.data:
                    pm_path = pm_res.data.get("cover_download_path")
                    if pm_path and not pm_path.startswith("http"):
                        full_path = Path(settings.DOWNLOAD_PATH) / pm_path
                        if full_path.exists():
                            mime, _ = mimetypes.guess_type(str(full_path))
                            return FileResponse(
                                path=str(full_path),
                                media_type=mime or "image/jpeg",
                                headers={
                                    "Cache-Control": "public, max-age=604800, immutable"
                                },
                            )
            except Exception as e:
                logger.warning(
                    f"parsed_media cover lookup failed for media_id={media_id}: {e}"
                )

        # Fallback for image files: serve the original file as cover.
        if resource.get("mime_type", "").startswith("image/"):
            file_path = resource.get("file_path")
            if file_path and not file_path.startswith("http"):
                full_path = Path(settings.DOWNLOAD_PATH) / file_path
                if full_path.exists():
                    mime, _ = mimetypes.guess_type(str(full_path))
                    return FileResponse(
                        path=str(full_path),
                        media_type=mime or "image/jpeg",
                    )

        raise HTTPException(status_code=404, detail="No cover image available")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve cover for resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve cover image")


@router.get("/{resource_id}/preview-sprite")
async def serve_preview_sprite(resource_id: str):
    """Serve preview sprite sheet for hover scrub (no auth required)."""
    try:
        # Public (no-auth) serve → SYSTEM scope for the resources lookup
        # (deliberate cross-user access). Inert until SCOPE_ENFORCE_RESOURCES.
        async with system_request_scope(reason="public-share-serve"):
            repo = ResourcesRepository()
            resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        file_path = resource.get("file_path")
        if not file_path:
            raise HTTPException(status_code=404, detail="No file path")

        from app.core.config import settings

        sprite_path = (
            Path(settings.DOWNLOAD_PATH) / Path(file_path).parent / "preview_sprite.jpg"
        )
        if not sprite_path.exists():
            raise HTTPException(status_code=404, detail="Preview sprite not found")

        return FileResponse(
            path=str(sprite_path),
            media_type="image/jpeg",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve sprite for resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve preview sprite")


@router.patch("/{resource_id}")
async def update_resource(
    resource_id: str,
    data: ResourceUpdate,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Update resource metadata. Use DELETE endpoint for trashing."""
    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        update_data = data.model_dump(exclude_none=True)

        # Prevent direct is_trashed manipulation via PATCH.
        # Trash must go through DELETE (resource_item removal).
        update_data.pop("is_trashed", None)
        update_data.pop("trashed_at", None)

        if not update_data:
            return {"success": True, "data": resource}

        result = await repo.update_resource(resource_id, update_data)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update resource")


@router.post("/{resource_id}/cover")
async def upload_resource_cover(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    file: UploadFile = File(...),
):
    """Upload/replace the cover image for a resource. Stores next to the source
    file and sets cover_image_path."""
    from app.api.media_permissions import check_media_access
    from app.core.config import settings

    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Cover must be an image")
    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")
    if not resource.get("file_path"):
        raise HTTPException(status_code=400, detail="Resource has no storage path")

    ext = (file.filename or "cover").rsplit(".", 1)[-1].lower()
    if ext not in {"jpg", "jpeg", "png", "webp", "gif"}:
        ext = "jpg"
    abs_src = Path(settings.DOWNLOAD_PATH) / resource["file_path"]
    cover_abs = abs_src.parent / f"cover.{ext}"
    cover_abs.parent.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    cover_abs.write_bytes(data)
    rel = str(cover_abs.relative_to(Path(settings.DOWNLOAD_PATH)))

    result = await repo.update_resource(resource_id, {"cover_image_path": rel})
    return {"success": True, "data": result}


@router.post("/{resource_id}/lyrics")
async def upload_resource_lyrics(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    file: UploadFile = File(...),
):
    """Upload an .lrc file; parse it and store on the resource."""
    from app.api.media_permissions import check_media_access
    from app.services.lrc_parser import parse_lrc

    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")
    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        lyrics = parse_lrc(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid LRC: {e}")
    result = await repo.update_resource(resource_id, {"lyrics_json": lyrics})
    return {"success": True, "data": {"lyrics_json": lyrics, "resource": result}}


@router.get("/{resource_id}/lyrics")
async def get_resource_lyrics(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Return the resource's stored lyrics_json (or null)."""
    from app.api.media_permissions import check_media_access

    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")
    return {"success": True, "data": resource.get("lyrics_json")}


@router.put("/{resource_id}/chorus")
async def set_resource_chorus(
    resource_id: str,
    data: ChorusUpdate,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Set or clear (null) the chorus marker for an uploaded audio resource."""
    from app.api.media_permissions import check_media_access

    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")
    if (resource.get("source_type") != "upload") or not (
        resource.get("mime_type") or ""
    ).startswith("audio/"):
        raise HTTPException(
            status_code=400, detail="Chorus is only settable on uploaded audio"
        )

    result = await repo.update_resource(
        resource_id, {"chorus_start_ms": data.chorus_start_ms}
    )
    return {"success": True, "data": result}


@router.post("/by-platform-id/{platform_id}/trash")
async def trash_resource_by_platform_id(
    platform_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    scope_id: Optional[str] = Query(None),
):
    """Move a resource to trash or unlink from a team scope.

    - No scope_id: sets is_trashed=true on the resource (global trash).
    - scope_id present: removes the resource_item link from that scope
      only, leaving the resource intact in the creator's personal library.

    PR-E Phase 3: routing keys off ``scope_id`` presence; the vestigial
    ``scope_type`` query param has been dropped.
    """
    try:
        svc = ResourcesService()
        resource = await svc.repo.get_resource_by_platform_id(platform_id)
        if not resource:
            raise ValueError("No resource found for this platform_id")

        resource_id = str(resource["id"])

        if scope_id:
            # Scoped context: try to unlink from that scope first
            try:
                await svc.remove_from_library(
                    resource_id=resource_id,
                    user_id=auth.user_id,
                    scope_id=scope_id,
                )
                return {
                    "success": True,
                    "message": "Resource removed from team library",
                }
            except Exception:
                # No team link found, fall through to trash
                pass

        # Personal context or team unlink failed: trash the resource globally
        await svc.trash_resource(
            resource_id=resource_id,
            user_id=auth.user_id,
        )
        return {"success": True, "message": "Resource moved to trash"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to trash resource by platform_id {platform_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to trash resource")


@router.post("/by-media-id/{media_id}/trash")
async def trash_resource_by_media_id(
    media_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    scope_id: Optional[str] = Query(None),
):
    """Move a resource to trash or unlink from a scope, by parsed_media.id.

    - No scope_id: sets is_trashed=true on the resource (global trash).
    - scope_id present: removes the resource_item link from that scope only.

    PR-E Phase 3: routing keys off ``scope_id`` presence; the vestigial
    ``scope_type`` query param has been dropped.
    """
    try:
        svc = ResourcesService()
        resource = await svc.repo.get_resource_by_media_id(media_id)
        if not resource:
            raise ValueError("No resource found for this media_id")

        resource_id = str(resource["id"])

        if scope_id:
            try:
                await svc.remove_from_library(
                    resource_id=resource_id,
                    user_id=auth.user_id,
                    scope_id=scope_id,
                )
                return {
                    "success": True,
                    "message": "Resource removed from team library",
                }
            except Exception:
                pass

        await svc.trash_resource(
            resource_id=resource_id,
            user_id=auth.user_id,
        )
        return {"success": True, "message": "Resource moved to trash"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to trash resource by media_id {media_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to trash resource")


@router.delete("/by-platform-id/{platform_id}")
async def unlink_resource_by_platform_id(
    platform_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    scope_id: Optional[str] = Query(None),
):
    """Remove a downloaded video from the user's library by platform_id.

    1. If a resource_item exists in the given scope → unlink it.
    2. Otherwise, if the caller owns a resource for this media → unlink their
       first item (the DB orphan-GC trigger auto-trashes the resource when no
       references remain).

    PR-E Phase 3: the vestigial ``scope_type`` query param has been dropped;
    the scope is identified by ``scope_id`` (defaults to the caller's
    personal team).

    NOTE (schema-drift sweep): the old fallback deleted the GLOBAL parsed_media
    row gated on the now-dropped ``parsed_media.user_id`` (mig 083). That gate is
    gone, so a raw global delete here would be an authz hole (any user could
    purge any user's downloaded media by platform_id) AND it contradicts this
    endpoint's documented contract — it must NOT delete parsed_media or physical
    files (the orphan-GC trigger owns that). We now resolve the CALLER's own
    resource (creator_id-scoped) and unlink it; a true orphan parsed_media row
    with no owning resource yields 404 rather than a cross-user global delete.
    """
    try:
        svc = ResourcesService()
        resource = await svc.repo.get_resource_by_platform_id(platform_id)

        if resource:
            # Try to find & remove the resource_item in the requested scope
            from app.services.library.resources_service import (
                _resolve_personal_team_id,
            )

            target_scope_id = scope_id or await _resolve_personal_team_id(auth.user_id)
            item = await svc.repo.get_resource_item(
                str(resource["id"]),
                None,
                target_scope_id,
            )
            if item:
                await svc.repo.delete_resource_item(item["id"])
                return {"success": True, "message": "Resource unlinked from library"}

        # Fallback: no item in the requested scope. Only act on a resource the
        # CALLER owns (authz gate via creator_id) — never touch global
        # parsed_media. Resolve the media_id from platform_id, then the caller's
        # own resource, and unlink its first item (trigger GC handles the rest).
        from app.db.supabase_client import get_async_supabase_admin

        client = await get_async_supabase_admin()
        media_row = await (
            client.table("parsed_media")
            .select("id")
            .eq("platform_id", platform_id)
            .limit(1)
            .execute()
        )
        if media_row.data:
            owned = await svc.repo.get_resource_by_media_id_and_creator(
                str(media_row.data[0]["id"]), auth.user_id
            )
            if owned:
                item = await svc.repo.get_first_resource_item(str(owned["id"]))
                if item:
                    await svc.repo.delete_resource_item(item["id"])
                    return {
                        "success": True,
                        "message": "Resource unlinked from library",
                    }

        raise ValueError("No resource found for this platform_id")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to unlink resource by platform_id {platform_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to unlink resource")


@router.delete("/{resource_id}")
async def delete_resource(
    resource_id: str,
    auth: AuthDep,
    _tenant_scope: ScopedRequestDep,
    scope_id: str = Query(...),
    _scope_guard: None = Depends(verify_scope_access),
    folder_id: Optional[str] = Query(None),
):
    """Remove a resource from a specific folder.

    Deletes the resource_item reference. If this was the last reference,
    the DB trigger auto-trashes the parent resource (orphan GC).
    """
    try:
        svc = ResourcesService()
        await svc.remove_from_library(
            resource_id=resource_id,
            user_id=auth.user_id,
            scope_id=scope_id,
            folder_id=folder_id,
        )
        return {"success": True, "message": "Resource removed from library"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to remove resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove resource")


@router.post("/{resource_id}/restore")
async def restore_resource(resource_id: str, auth: AuthDep, _scope: ScopedRequestDep):
    """Restore a trashed resource."""
    try:
        svc = ResourcesService()
        result = await svc.restore_resource(resource_id, auth.user_id)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to restore resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to restore resource")


@router.delete("/{resource_id}/permanent")
async def permanent_delete_resource(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Permanently delete a resource."""
    try:
        svc = ResourcesService()
        await svc.permanent_delete(resource_id, auth.user_id)
        return {"success": True, "message": "Resource permanently deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to permanently delete resource {resource_id}: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to permanently delete resource"
        )


# ============================================
# Move endpoint
# ============================================


@router.post("/{resource_id}/move")
async def move_resource(
    resource_id: str,
    data: ResourceMoveRequest,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Move a resource to a different folder."""
    try:
        svc = ResourcesService()
        result = await svc.move_resource(
            resource_id=resource_id,
            user_id=auth.user_id,
            scope_id=data.scope_id,
            folder_id=data.folder_id,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to move resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to move resource")


# ============================================
# Tag endpoints
# ============================================


@router.get("/{resource_id}/tags")
async def list_resource_tags(resource_id: str, auth: AuthDep, _scope: ScopedRequestDep):
    """Get all tags for a resource."""
    try:
        repo = ResourcesRepository()
        tags = await repo.get_resource_tags(resource_id)
        return {"success": True, "data": tags}
    except Exception as e:
        logger.error(f"Failed to list tags for resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list resource tags")


@router.post("/{resource_id}/tags")
async def add_resource_tag(
    resource_id: str,
    data: ResourceTagRequest,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Add a tag to a resource."""
    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        result = await repo.add_resource_tag(
            resource_id=resource_id,
            tag_id=data.tag_id,
            tagged_by=auth.user_id,
        )
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add tag to resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add tag")


@router.delete("/{resource_id}/tags/{tag_id}")
async def remove_resource_tag(
    resource_id: str,
    tag_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Remove a tag from a resource."""
    try:
        repo = ResourcesRepository()
        await repo.remove_resource_tag(resource_id, tag_id)
        return {"success": True, "message": "Tag removed"}
    except Exception as e:
        logger.error(f"Failed to remove tag {tag_id} from resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove tag")


@router.post("/{resource_id}/derive-crop")
async def derive_crop_resource_endpoint(
    resource_id: str,
    body: CropDeriveRequest,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Crop the image at ``resource_id`` and persist the result as a
    new sibling resource (same scope, source_type='derived').

    The request body is a ``CropDeriveRequest`` (region + optional
    filename). Access is gated by ``check_media_access`` against the
    source resource — the new resource inherits the source's scope.
    """
    from app.api.media_permissions import check_media_access
    from app.services.canvas.crop_derive_service import (
        CropDeriveError,
        derive_crop_resource,
    )
    from app.services.canvas.image_crop import CropRegion

    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        result = await derive_crop_resource(
            source_resource_id=resource_id,
            user_id=auth.user_id,
            region=CropRegion(
                x=body.region.x,
                y=body.region.y,
                width=body.region.width,
                height=body.region.height,
            ),
            filename_override=body.filename,
        )
        return {"success": True, "data": result.resource}
    except CropDeriveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"derive_crop failed for {resource_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to derive crop")


@router.post("/{resource_id}/derive-grid")
async def derive_grid_resource_endpoint(
    resource_id: str,
    body: GridDeriveRequest,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Split the image at ``resource_id`` along normalized split lines
    and persist every tile as a new sibling resource (same scope,
    source_type='derived').

    The request body is a ``GridDeriveRequest`` (xs / ys split lines +
    optional filename prefix). Access is gated by ``check_media_access``
    against the source resource — the new resources inherit its scope.
    """
    from app.api.media_permissions import check_media_access
    from app.services.canvas.grid_derive_service import (
        GridDeriveError,
        derive_grid_resources,
    )

    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        result = await derive_grid_resources(
            source_resource_id=resource_id,
            user_id=auth.user_id,
            xs=body.xs,
            ys=body.ys,
            filename_prefix=body.filename_prefix,
        )
        return {
            "success": True,
            "data": {
                "rows": result.rows,
                "cols": result.cols,
                "tiles": [
                    {"row": t.row, "col": t.col, "resource": t.resource}
                    for t in result.tiles
                ],
            },
        }
    except GridDeriveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"derive_grid failed for {resource_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to derive grid split")


@router.post("/{resource_id}/derive-mask-cutout")
async def derive_mask_cutout_endpoint(
    resource_id: str,
    body: MaskDeriveRequest,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Apply a painted mask to the image at ``resource_id`` and persist
    the RGBA cutout as a new sibling resource (same scope,
    source_type='derived', always image/png).

    The request body is a ``MaskDeriveRequest`` (base64 mask PNG +
    optional filename). Access is gated by ``check_media_access``
    against the source resource — the new resource inherits its scope.
    """
    from app.api.media_permissions import check_media_access
    from app.services.canvas.mask_derive_service import (
        MaskDeriveError,
        derive_mask_cutout,
    )

    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        result = await derive_mask_cutout(
            source_resource_id=resource_id,
            user_id=auth.user_id,
            mask_png_base64=body.mask_png_base64,
            filename_override=body.filename,
        )
        return {"success": True, "data": result.resource}
    except MaskDeriveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"derive_mask_cutout failed for {resource_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to derive mask cutout")
