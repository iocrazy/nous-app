# backend/app/api/resources_versions_router.py

"""
Resources Versions Router

Version management: upload, set-current, delete, HLS serve, transcode.
"""

from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
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
from app.core.scope_guards import verify_resource_write_access
from app.db.scope import Scope, request_scope
from app.repositories.resources_repository import ResourcesRepository
from app.services.library.resources_service import ResourcesService
from app.services.media.render.thumbnail_service import ThumbnailService

router = APIRouter(prefix="/resources")

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


async def _scoped_generate_thumbnail(svc: ThumbnailService, user_id, **kwargs) -> None:
    """Run ``generate_thumbnail`` under its OWN ambient USER scope.

    FastAPI ``BackgroundTasks`` run AFTER the response is sent — i.e. AFTER the
    request's ``ScopedRequestDep`` generator ``finally`` has already reset the
    ambient scope. ``generate_thumbnail`` then issues an ORM write on
    ``resources`` (``ThumbnailService.update_resource``); with no ambient scope it
    would hit ``UnscopedQueryError`` once ``SCOPE_ENFORCE_RESOURCES`` flips (and be
    swallowed by generate_thumbnail's try/except → thumbnail silently not
    persisted on version upload). So we re-establish the scope INSIDE the
    background coroutine — the contextvar must be set in the task's OWN execution,
    not at dispatch time. We use a USER scope from the uploader's identity (it is
    the user's own resource — tighter than SYSTEM). Inert until the flag flips."""
    async with request_scope(Scope(user_id=user_id)):
        await svc.generate_thumbnail(**kwargs)


@router.get("/{resource_id}/versions")
async def list_versions(resource_id: str, auth: AuthDep, _scope: ScopedRequestDep):
    """List all versions of a resource."""
    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")
        versions = await repo.get_versions(resource_id)
        return {"success": True, "data": versions}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list versions for resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list versions")


@router.post("/{resource_id}/versions")
async def upload_version(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    notes: Optional[str] = Query(None),
    _resource_guard: None = Depends(verify_resource_write_access),
):
    """Upload a new version of a resource.

    `_resource_guard` enforces that the caller is the resource creator
    before the handler runs.
    """
    try:
        if file.size and file.size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413, detail="File too large. Maximum size is 500 MB."
            )
        svc = ResourcesService()
        result = await svc.upload_new_version(
            resource_id=resource_id,
            user_id=auth.user_id,
            file=file,
            notes=notes,
        )

        # Trigger thumbnail generation for new version
        if result.get("file_path") and result.get("mime_type"):
            thumbnail_svc = ThumbnailService()
            # Get scope info for thumbnail path
            repo = ResourcesRepository()
            item = await repo.get_first_resource_item(resource_id)
            if item:
                background_tasks.add_task(
                    _scoped_generate_thumbnail,
                    thumbnail_svc,
                    auth.user_id,
                    resource_id=resource_id,
                    file_path=result["file_path"],
                    mime_type=result.get("mime_type", ""),
                    # PR-E 4c: scope_type dropped; generate_thumbnail ignores it.
                    scope_id=item.get("scope_id", ""),
                )

        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to upload version for resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload version")


@router.post("/{resource_id}/versions/{version_number}/set-current")
async def set_current_version(
    resource_id: str,
    version_number: int,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Set a specific version as the current active version."""
    try:
        svc = ResourcesService()
        result = await svc.set_current_version(
            resource_id, version_number, auth.user_id
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to set current version: {e}")
        raise HTTPException(status_code=500, detail="Failed to set current version")


@router.delete("/{resource_id}/versions/{version_id}")
async def delete_version(
    resource_id: str,
    version_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Delete a specific version (must keep at least one)."""
    try:
        svc = ResourcesService()
        await svc.delete_version(resource_id, version_id, auth.user_id)
        return {"success": True, "message": "Version deleted"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete version: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete version")


@router.get("/{resource_id}/versions/{version_id}/hls/{path:path}")
async def serve_hls_file(
    resource_id: str,
    version_id: str,
    path: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    token: Optional[str] = Query(None),
):
    """Serve HLS playlist or segment files for a version.

    Supports: master.m3u8, {tier}/stream.m3u8, {tier}/segment_*.ts
    """
    from app.core.deps import get_auth

    try:
        effective_auth = authorization
        if not effective_auth and not x_api_key and token:
            effective_auth = f"Bearer {token}"
        # Auth is required (no public branch); resolved manually because the
        # endpoint accepts a ?token= query transport that a Depends(get_auth)
        # would not see. Establish the ambient user Scope from the resolved
        # identity (defensive: resource_versions is a sibling table — inert
        # today, ready if it joins the enforced set). Inert until the flag flips.
        _auth = await get_auth(request, effective_auth, x_api_key)

        async with request_scope(Scope(user_id=_auth.user_id)):
            repo = ResourcesRepository()
            version = await repo.get_version_by_id(version_id)
            if not version or str(version.get("resource_id")) != resource_id:
                raise HTTPException(status_code=404, detail="Version not found")

            hls_path = version.get("hls_path")
        if not hls_path:
            raise HTTPException(status_code=404, detail="HLS not available")

        from app.core.config import settings

        # hls_path points to master.m3u8, derive the hls directory
        hls_dir = Path(settings.DOWNLOAD_PATH) / Path(hls_path).parent
        target = hls_dir / path

        # Security: ensure resolved path is within hls_dir
        try:
            target.resolve().relative_to(hls_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")

        if not target.exists():
            raise HTTPException(status_code=404, detail="HLS file not found")

        # Determine content type
        suffix = target.suffix.lower()
        content_types = {
            ".m3u8": "application/vnd.apple.mpegurl",
            ".ts": "video/mp2t",
            ".m4s": "video/iso.segment",  # fMP4 segments
            ".mp4": "video/mp4",  # fMP4 init segments
        }
        media_type = content_types.get(suffix, "application/octet-stream")

        # Cache strategy: playlists short-lived, segments immutable
        cache_control = (
            "public, max-age=2"
            if suffix == ".m3u8"
            else "public, max-age=31536000, immutable"
        )

        headers = {
            "Cache-Control": cache_control,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Origin, Accept, Authorization",
        }

        return FileResponse(path=str(target), media_type=media_type, headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve HLS file: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve HLS file")


@router.post("/{resource_id}/versions/{version_id}/transcode")
async def retry_transcode(
    resource_id: str,
    version_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
):
    """Manually trigger or retry HLS transcoding for a version."""
    try:
        repo = ResourcesRepository()
        version = await repo.get_version_by_id(version_id)
        if not version or str(version.get("resource_id")) != resource_id:
            raise HTTPException(status_code=404, detail="Version not found")

        mime = version.get("mime_type", "")
        if not mime.startswith("video/"):
            raise HTTPException(
                status_code=400, detail="Only video files can be transcoded"
            )

        # Reset status before retrying
        await repo.update_version(version_id, {"transcode_status": "pending"})

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.workflows.transcode import transcode_workflow

        await start_workflow_routed(
            "transcode",
            dbos_workflow_callable=transcode_workflow,
            dbos_workflow_kwargs={
                "resource_id": resource_id,
                "version_id": version_id,
                "user_id": auth.user_id,
            },
        )

        return {"success": True, "message": "Transcoding queued"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to trigger transcode: {e}")
        raise HTTPException(status_code=500, detail="Failed to trigger transcoding")


@router.get("/{resource_id}/versions/{version_id}/file")
async def serve_version_file(
    resource_id: str,
    version_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    token: Optional[str] = Query(None),
):
    """Serve the file for a specific version."""
    from app.core.deps import get_auth

    try:
        effective_auth = authorization
        if not effective_auth and not x_api_key and token:
            effective_auth = f"Bearer {token}"
        # Auth is required (no public branch); resolved manually for the ?token=
        # transport. Establish the ambient user Scope from the resolved identity
        # (defensive sibling-table wiring). Inert until SCOPE_ENFORCE_RESOURCES.
        _auth = await get_auth(request, effective_auth, x_api_key)

        async with request_scope(Scope(user_id=_auth.user_id)):
            repo = ResourcesRepository()
            version = await repo.get_version_by_id(version_id)
            if not version or str(version.get("resource_id")) != resource_id:
                raise HTTPException(status_code=404, detail="Version not found")

            file_path = version.get("file_path")
        if not file_path:
            raise HTTPException(status_code=404, detail="No file available")

        from app.core.config import settings

        full_path = Path(settings.DOWNLOAD_PATH) / file_path
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")

        return FileResponse(
            path=str(full_path),
            filename=version.get("filename", "download"),
            media_type=version.get("mime_type", "application/octet-stream"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve version file: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve version file")
