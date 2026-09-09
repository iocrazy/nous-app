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
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_dep import ScopedRequestDep
from app.core.scope_guards import verify_scope_access
from app.db.scope import Scope, request_scope, system_request_scope
from app.models.media import RESOURCE_SOURCE_TYPES
from app.repositories.resources_repository import (
    UNTRANSCODED_BATCH,
    ResourcesRepository,
)
from app.schemas.canvas_crop_schema import CropDeriveRequest
from app.schemas.canvas_grid_schema import GridDeriveRequest
from app.schemas.canvas_mask_schema import MaskDeriveRequest
from app.schemas.canvas_outpaint_schema import OutpaintDeriveRequest
from app.schemas.canvas_split_schema import SplitDeriveRequest
from app.schemas.resources import (
    ChorusUpdate,
    ResourceMoveRequest,
    ResourceTagRequest,
    ResourceUpdate,
)
from app.services.library.resource_file_path import resolve_resource_file_path
from app.services.library.resources_service import ResourcesService
from app.services.prompts.origin import stamp_origin

router = APIRouter(prefix="/resources")

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


# ============================================
# Resource list / CRUD endpoints
# ============================================


_ALLOWED_TYPE_CATEGORIES = {"video", "image", "audio", "document", "gallery", "other"}
_ALLOWED_ASPECT_RATIOS = {"9:16", "16:9", "1:1", "4:3", "other"}
_ALLOWED_SOCIAL_COMBINE = {"and", "or"}
# resources.source_type CHECK enum (see promote_generated_media_service):
# web = platform parse/download, upload = user upload,
# generated = AI/canvas promote artifact, derived = derived from another.
# The set itself lives on the model beside the CHECK it mirrors.
_ALLOWED_SOURCE_TYPES = frozenset(RESOURCE_SOURCE_TYPES)


@router.get("")
async def list_resources(
    auth: AuthDep,
    _tenant_scope: ScopedRequestDep,
    scope_id: str = Query(...),
    _scope_guard: None = Depends(verify_scope_access),
    folder_id: Optional[str] = Query(None),
    all_folders: bool = Query(
        False,
        description=(
            "When true (and folder_id is absent), list items across ALL "
            "folders in the scope instead of root-level only. Used by "
            "scope-wide pickers (e.g. Distribution Publish)."
        ),
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=1000,
        description="Cap the number of returned items (newest first).",
    ),
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
    source_types: Optional[List[str]] = Query(
        None,
        description=(
            "Filter by resource provenance (IN semantics) — matches "
            "resources.source_type. Allowed: web (platform download), "
            "upload, generated (AI/canvas promote), derived. The "
            "Distribution Publish picker passes upload/generated/derived "
            "to exclude platform-downloaded material."
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
    ai_has_prompt: Optional[bool] = Query(
        None,
        description=(
            "If true, only resources with a non-empty AI prompt — any of "
            "gen_prompt, gen_prompt_negative, gen_prompt_json, slide_prompts."
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

    # Validate + normalise `source_types` (resources.source_type provenance).
    # Drop empty strings so `?source_types=` is treated as absent.
    normalised_source_types: Optional[List[str]] = None
    if source_types:
        normalised_source_types = [s.strip() for s in source_types if s and s.strip()]
        for s in normalised_source_types:
            if s not in _ALLOWED_SOURCE_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Invalid source_type: {s!r}. "
                        f"Allowed: {sorted(_ALLOWED_SOURCE_TYPES)}."
                    ),
                )
        if not normalised_source_types:
            normalised_source_types = None

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
            all_folders=all_folders,
            limit=limit,
            tag_ids=tag_ids or None,
            min_rating=min_rating,
            types=normalised_types,
            source_types=normalised_source_types,
            platforms=normalised_platforms,
            ai_transcribed=ai_transcribed,
            ai_summarized=ai_summarized,
            ai_analyzed=ai_analyzed,
            ai_has_prompt=ai_has_prompt,
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

            # PR-B 两级阶梯：resources.file_path → parsed_media.download_path。
            # 这段逻辑原本内联在这里，现已提取到 resource_file_path（行为不变，
            # 含 parsed_media 查询失败时降级为 None 的语义）—— 提取的原因是每个
            # 不经过本端点却需要真文件的调用方都只实现了第一级，于是对
            # source_type='web' 的行（生产里过半视频）静默判成"没有文件"。
            file_path = await resolve_resource_file_path(resource)
        if not file_path:
            raise HTTPException(status_code=404, detail="No file available")

        # When served with ?token= in URL, prevent intermediate caching.
        cache_headers = {"Cache-Control": "private, no-store"} if token else {}

        # Storage unification: file_path may be a legacy filesystem-relative
        # path OR an `sb://` object-store row (Task 2.1/2.2 dual-track
        # uploads). serve_stored_file is the ONE reader that handles both
        # shapes. (The P3 nginx direct-serve 302 for legacy fs rows was
        # retired 2026-09-07: no row has a local path any more.)
        from app.services.library.media_serving import serve_stored_file

        return await serve_stored_file(
            file_path,
            mime=resource.get("mime_type", "application/octet-stream"),
            request=request,
            extra_headers=cache_headers,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve file for resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve file")


# ─── Lazy thumbnail helpers (million-files P1) ──────────────────────────────

# Warm dark-neutral placeholder tiles per media class — shown while the
# on-demand thumbnail generates. 16:10 so justified/grid tiles look intentional.
_PLACEHOLDER_FILL = {
    "video": "#2c2547",
    "image": "#1c3129",
    "audio": "#372a49",
}
_PLACEHOLDER_DEFAULT_FILL = "#221c2e"


def _cover_placeholder(mime_type: str):
    from fastapi.responses import Response

    kind = (mime_type or "").split("/", 1)[0]
    fill = _PLACEHOLDER_FILL.get(kind, _PLACEHOLDER_DEFAULT_FILL)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" '
        'viewBox="0 0 320 200">'
        f'<rect width="320" height="200" fill="{fill}"/>'
        '<circle cx="160" cy="100" r="22" fill="none" stroke="#8a7fa1" '
        'stroke-width="3" stroke-dasharray="8 6"/>'
        "</svg>"
    )
    # no-store: the client must re-ask so the real thumbnail replaces this
    # as soon as the workflow lands it.
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


async def _enqueue_lazy_thumbnail(
    resource_id: str, file_path: str, mime_type: str
) -> None:
    """Fire-and-forget thumbnail generation for a viewed-but-thumbnail-less
    resource. The workflow id is date-bucketed: concurrent cover misses for
    the same resource collapse onto ONE workflow (DBOS idempotency), while a
    failed generation naturally becomes retryable the next day. Enqueue
    failures must never break cover serving — log and move on (the client
    just sees the placeholder again)."""
    from datetime import datetime, timezone

    try:
        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.thumbnail import thumbnail_workflow

        date_bucket = datetime.now(timezone.utc).strftime("%Y%m%d")
        await start_workflow_routed(
            "thumbnail",
            dbos_workflow_callable=thumbnail_workflow,
            dbos_workflow_kwargs={
                "resource_id": resource_id,
                "file_path": file_path,
                "mime_type": mime_type,
            },
            workflow_id=f"thumb-lazy-{resource_id}-{date_bucket}",
        )
    except Exception as e:
        logger.warning(f"[cover] lazy thumbnail enqueue failed for {resource_id}: {e}")


@router.get("/{resource_id}/cover")
async def serve_resource_cover(resource_id: str, request: Request):
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
        from app.services.library.media_serving import serve_stored_file
        from app.services.library.media_storage import resolve_media_source

        # Try thumbnail first, then cover image (independent uploads).
        for field in ("thumbnail_path", "cover_image_path"):
            rel_path = resource.get(field)
            if not rel_path:
                continue
            if rel_path.startswith("http"):
                continue

            # storage-migration fix round 1: the derived module migrates
            # these two columns to sb://library/derived/{rid}/... (see
            # storage_migration.py::_migrate_derived_row). Before this
            # branch, an sb:// value here would fall straight through to
            # `Path(DOWNLOAD_PATH) / "sb://library/..."` — never exists() on
            # disk — silently treated as "no thumbnail", which cascades into
            # the lazy-thumbnail placeholder path below regenerating a LOCAL
            # thumbnail and overwriting the column right back to a filesystem
            # path. That would quietly undo the migration and orphan the S3
            # copy every time this endpoint is hit for a migrated resource.
            loc = resolve_media_source(rel_path)
            if loc.is_object_store:
                mime, _ = mimetypes.guess_type(rel_path)
                return await serve_stored_file(
                    rel_path,
                    mime=mime or "image/jpeg",
                    request=request,
                    extra_headers={
                        "Cache-Control": "public, max-age=604800, immutable"
                    },
                )

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
            from sqlalchemy import select

            from app.db.session import read_scope
            from app.models import ParsedMedia

            try:
                async with read_scope() as session:
                    pm_row = (
                        (
                            await session.execute(
                                select(
                                    ParsedMedia.cover_download_path,
                                    ParsedMedia.cover_download_status,
                                )
                                .where(ParsedMedia.id == int(media_id))
                                .limit(1)
                            )
                        )
                        .mappings()
                        .first()
                    )
                if pm_row:
                    pm_path = pm_row.get("cover_download_path")
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

        # ── Lazy thumbnail generation (million-files P1) ─────────────────
        # Independent resources (no parsed_media backing) without a
        # pre-generated thumbnail get one ON FIRST VIEW: enqueue the
        # thumbnail workflow (date-bucketed idempotency key — concurrent
        # misses collapse into one workflow, failures retry next day) and
        # answer immediately. Small images serve the original bytes as the
        # cover (no workflow needed); everything else gets an inline SVG
        # placeholder with no-store so the client re-asks until the real
        # thumbnail lands.
        mime_type = resource.get("mime_type") or ""
        file_path = resource.get("file_path")
        if not media_id and file_path and not file_path.startswith("http"):
            loc = resolve_media_source(file_path)
            # Storage unification: an sb:// original always "exists" from the
            # router's point of view — serve_stored_file resolves it lazily
            # (and 404s on a genuinely missing object). A legacy fs row keeps
            # the existence check so we don't hand FileResponse a dead path.
            exists = (
                loc.is_object_store
                or (Path(settings.DOWNLOAD_PATH) / (loc.rel_path or "")).exists()
            )
            if exists:
                size = resource.get("file_size_bytes") or 0
                if mime_type.startswith("image/") and 0 < size <= 512_000:
                    mime, _ = mimetypes.guess_type(file_path)
                    return await serve_stored_file(
                        file_path,
                        mime=mime or "image/jpeg",
                        request=request,
                        extra_headers={"Cache-Control": "public, max-age=604800"},
                    )
                await _enqueue_lazy_thumbnail(str(resource_id), file_path, mime_type)
                return _cover_placeholder(mime_type)

        # Legacy fallback for image files without a local file-size record.
        if mime_type.startswith("image/"):
            if file_path and not file_path.startswith("http"):
                loc = resolve_media_source(file_path)
                exists = (
                    loc.is_object_store
                    or (Path(settings.DOWNLOAD_PATH) / (loc.rel_path or "")).exists()
                )
                if exists:
                    mime, _ = mimetypes.guess_type(file_path)
                    return await serve_stored_file(
                        file_path,
                        mime=mime or "image/jpeg",
                        request=request,
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

        from app.core.config import settings
        from app.services.library import media_storage

        # Storage unification: derived sprite 只靠 resource_id 定位,不依赖
        # file_path —— 458 个 file_path=NULL 的 web 视频(真实文件路径在
        # parsed_media,resources.file_path 常年 NULL)之前会被下面这行提前
        # 早退:
        #   if not file_path: raise HTTPException(404)
        # 挡在 derived 探测之前,即使 sprite 已经生成也永远 404。现在先探
        # derived 的两种落地形态,只有两者都没找到、且 file_path 存在时,
        # 才走文件系统 next-to-source 兜底(legacy fs 行未变)。
        derived_sprite = (
            Path(settings.DOWNLOAD_PATH)
            / "derived"
            / "thumbnails"
            / str(resource_id)
            / "preview_sprite.jpg"
        )
        if derived_sprite.exists():
            return FileResponse(path=str(derived_sprite), media_type="image/jpeg")

        # derived 迁移(PR-4,storage_migration._MODULES["derived"])跑完后,
        # 同一份 sprite 可能已经搬到对象存储、本地文件已删除 —— 探一次
        # sb://library/derived/{resource_id}/preview_sprite.jpg。
        # ObjectStore.exists() 对任何失败(未配置/网络/404)都吞异常返回
        # False,探测失败不会变成 500,只是继续往下走 fallback。
        derived_key = (
            media_storage.derived_key_prefix(resource_id) + "preview_sprite.jpg"
        )
        store = media_storage.library_store()
        if await store.exists(derived_key):
            return StreamingResponse(
                store.get_stream(derived_key), media_type="image/jpeg"
            )

        if not file_path:
            raise HTTPException(status_code=404, detail="No sprite")

        # 文件系统 next-to-source 兜底(旧逻辑保留)。对 sb:// 原始行,
        # Path(file_path).parent 拼出的本地路径本来就不存在于 DOWNLOAD_PATH
        # 下,自然落到下面的 404,不需要额外特判 sb:// scheme。
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

        # mig 455: a PATCH that carries prompt text is the user typing. The
        # column is not in ResourceUpdate on purpose — clients do not get to
        # claim an origin, the server derives it from what was written.
        stamp_origin(update_data, "typed")

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
    """Upload/replace the cover image for a resource. Sets cover_image_path.

    Legacy filesystem originals keep writing next to the source file
    (byte-identical to pre-storage-unification behavior). Object-store
    (``sb://``) originals write into a resource_id-keyed derived tree
    instead — the original's "parent dir" for an sb:// row is a literal
    non-existent ``sb:/...`` path on disk, and content-addressed dedup means
    two resources can point at the same fanout dir, so writing "next to the
    original" there would let two unrelated resources' covers overwrite each
    other. Mirrors thumbnail_service's ``derived/thumbnails/{resource_id}/``
    choice for the same reason.
    """
    from app.api.media_permissions import check_media_access
    from app.core.config import settings
    from app.services.library.media_storage import resolve_media_source

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

    loc = resolve_media_source(resource["file_path"])
    if loc.is_object_store:
        cover_abs = (
            Path(settings.DOWNLOAD_PATH)
            / "derived"
            / "covers"
            / str(resource_id)
            / f"cover.{ext}"
        )
    else:
        abs_src = Path(settings.DOWNLOAD_PATH) / resource["file_path"]
        cover_abs = abs_src.parent / f"cover.{ext}"
    cover_abs.parent.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    cover_abs.write_bytes(data)
    rel = str(cover_abs.relative_to(Path(settings.DOWNLOAD_PATH)))
    # DOWNLOAD_PATH is a transit dir (2026-09-07): with unified storage on,
    # the cover leaves for the object store and the staged file is discarded;
    # ``rel`` becomes the sb:// value. Flag off keeps the filesystem behavior.
    from app.services.library.transit_upload import upload_transit_file

    rel = await upload_transit_file(
        user_id=auth.user_id,
        local_path=str(cover_abs),
        relative_path=rel,
        mime=file.content_type or "image/jpeg",
    )

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
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import ParsedMedia

        async with read_scope() as session:
            media_pk = (
                await session.execute(
                    select(ParsedMedia.id)
                    .where(ParsedMedia.platform_id == platform_id)
                    .limit(1)
                )
            ).scalar()
        if media_pk is not None:
            owned = await svc.repo.get_resource_by_media_id_and_creator(
                str(media_pk), auth.user_id
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


@router.post("/{resource_id}/derive-outpaint")
async def derive_outpaint_endpoint(
    resource_id: str,
    body: OutpaintDeriveRequest,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Extend the canvas of the image at ``resource_id`` (blur-fill
    v1) and persist the result as a new sibling resource (same scope,
    source_type='derived').

    The request body is an ``OutpaintDeriveRequest`` (per-side padding
    fractions + optional prompt/filename). Access is gated by
    ``check_media_access`` against the source resource.
    """
    from app.api.media_permissions import check_media_access
    from app.services.canvas.image_outpaint import Padding
    from app.services.canvas.outpaint_derive_service import (
        OutpaintDeriveError,
        derive_outpaint_resource,
    )

    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        result = await derive_outpaint_resource(
            source_resource_id=resource_id,
            user_id=auth.user_id,
            padding=Padding(
                left=body.left,
                top=body.top,
                right=body.right,
                bottom=body.bottom,
            ),
            prompt=body.prompt,
            mode=body.mode,
            filename_override=body.filename,
        )
        return {"success": True, "data": result.resource}
    except OutpaintDeriveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"derive_outpaint failed for {resource_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to derive outpaint")


@router.post("/{resource_id}/split")
async def derive_split_resource_endpoint(
    resource_id: str,
    body: SplitDeriveRequest,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Split the image at ``resource_id`` into a ``rows × cols`` grid and
    persist every frame as a new sibling resource (same scope,
    source_type='derived').

    The request body is a ``SplitDeriveRequest`` (rows + cols). Access is
    gated by ``check_media_access`` against the source resource — the new
    resources inherit the source's scope, so they land in Project Assets.
    """
    from app.api.media_permissions import check_media_access
    from app.services.canvas.split_derive_service import (
        SplitDeriveError,
        derive_split_resource,
    )

    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        result = await derive_split_resource(
            source_resource_id=resource_id,
            user_id=auth.user_id,
            rows=body.rows,
            cols=body.cols,
        )
        return {"success": True, "data": {"frames": result.frames}}
    except SplitDeriveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"derive_split failed for {resource_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to derive grid split")
