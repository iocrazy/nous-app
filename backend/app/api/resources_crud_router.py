# backend/app/api/resources_crud_router.py

"""
Resources CRUD Router

Core resource CRUD, file serving, recycle bin operations,
move, tags, and batch transcode.
"""

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi import BackgroundTasks, Header
from fastapi.responses import FileResponse
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.resources_repository import ResourcesRepository
from app.schemas.resources import ResourceTagRequest, ResourceMoveRequest, ResourceUpdate
from app.services.resources_service import ResourcesService
from app.services.thumbnail_service import ThumbnailService

router = APIRouter(prefix="/resources")

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


# ============================================
# Resource list / CRUD endpoints
# ============================================


@router.get("")
async def list_resources(
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
    folder_id: Optional[str] = Query(None),
):
    """List resources in a scope, optionally filtered by folder."""
    try:
        repo = ResourcesRepository()
        items = await repo.get_resource_items(
            scope_type=scope_type,
            scope_id=scope_id,
            folder_id=folder_id,
        )
        return {"success": True, "data": items}
    except Exception as e:
        logger.error(f"Failed to list resources: {e}")
        raise HTTPException(status_code=500, detail="Failed to list resources")


@router.get("/trash")
async def list_trashed_resources(
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
):
    """List trashed resources in a scope."""
    try:
        repo = ResourcesRepository()
        items = await repo.get_trashed_resources(scope_type, scope_id)
        return {"success": True, "data": items}
    except Exception as e:
        logger.error(f"Failed to list trashed resources: {e}")
        raise HTTPException(status_code=500, detail="Failed to list trashed resources")


@router.get("/trash/folders")
async def list_trashed_folders(
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
):
    """List trashed folders in a scope."""
    try:
        repo = ResourcesRepository()
        folders = await repo.get_trashed_folders(scope_type, scope_id)
        return {"success": True, "data": folders}
    except Exception as e:
        logger.error(f"Failed to list trashed folders: {e}")
        raise HTTPException(status_code=500, detail="Failed to list trashed folders")


@router.post("/transcode/batch")
async def batch_transcode(auth: AuthDep):
    """Queue HLS transcoding for all video versions with NULL transcode_status."""
    try:
        repo = ResourcesRepository()
        versions = await repo.get_untranscoded_video_versions()

        from app.tasks.transcode_tasks import transcode_to_hls
        queued = 0
        for v in versions:
            try:
                vid = str(v["id"])
                await repo.update_version(vid, {"transcode_status": "pending"})
                await asyncio.to_thread(transcode_to_hls.delay, str(v["resource_id"]), vid, auth.user_id)
                queued += 1
            except Exception as e:
                logger.warning(f"[Transcode/Batch] Failed to queue version {v['id']}: {e}")

        logger.info(f"[Transcode/Batch] Queued {queued}/{len(versions)} versions")
        return {"success": True, "queued": queued, "total_found": len(versions)}
    except Exception as e:
        logger.error(f"Failed to batch transcode: {e}")
        raise HTTPException(status_code=500, detail="Failed to batch transcode")


@router.get("/{resource_id}")
async def get_resource(resource_id: str, auth: AuthDep):
    """Get a single resource by ID."""
    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")
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
):
    """Serve the actual file for preview/download.

    Supports auth via:
    - Authorization header (Bearer token)
    - X-API-Key header
    - ?token= query parameter (for <video>, <img>, <iframe> src)
    """
    from app.core.deps import get_auth

    try:
        # Accept token as query parameter for HTML element src usage
        effective_auth = authorization
        if not effective_auth and not x_api_key and token:
            effective_auth = f"Bearer {token}"

        auth = await get_auth(request, effective_auth, x_api_key)

        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        file_path = resource.get("file_path")
        if not file_path:
            raise HTTPException(status_code=404, detail="No file available")

        # Resolve full path from DOWNLOAD_PATH base
        from app.core.config import settings

        full_path = Path(settings.DOWNLOAD_PATH) / file_path

        if not full_path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")

        return FileResponse(
            path=str(full_path),
            media_type=resource.get("mime_type", "application/octet-stream"),
            content_disposition_type="inline",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve file for resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve file")


@router.get("/{resource_id}/cover")
async def serve_resource_cover(resource_id: str):
    """Serve cover/thumbnail image for a resource (no auth required).

    Priority: thumbnail_path > cover_image_path.
    Both are relative paths resolved against DOWNLOAD_PATH.
    """
    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        from app.core.config import settings

        import mimetypes

        # Try thumbnail first, then cover image
        for field in ("thumbnail_path", "cover_image_path"):
            rel_path = resource.get(field)
            if not rel_path:
                continue
            # Skip old Supabase Storage URLs (http://...)
            if rel_path.startswith("http"):
                continue
            full_path = Path(settings.DOWNLOAD_PATH) / rel_path
            if full_path.exists():
                mime, _ = mimetypes.guess_type(str(full_path))
                return FileResponse(
                    path=str(full_path),
                    media_type=mime or "image/jpeg",
                )

        # Fallback for image files: serve the original file as cover
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
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        file_path = resource.get("file_path")
        if not file_path:
            raise HTTPException(status_code=404, detail="No file path")

        from app.core.config import settings

        sprite_path = Path(settings.DOWNLOAD_PATH) / Path(file_path).parent / "preview_sprite.jpg"
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
async def update_resource(resource_id: str, data: ResourceUpdate, auth: AuthDep):
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


@router.post("/by-platform-id/{platform_id}/trash")
async def trash_resource_by_platform_id(
    platform_id: str,
    auth: AuthDep,
    scope_type: str = Query("personal", pattern="^(personal|team)$"),
    scope_id: Optional[str] = Query(None),
):
    """Move a resource to trash or unlink from team.

    - Personal scope: sets is_trashed=true on the resource (global trash).
    - Team scope: removes the resource_item link from the team only,
      leaving the resource intact in the creator's personal library.
    """
    try:
        svc = ResourcesService()
        resource = await svc.repo.get_resource_by_platform_id(platform_id)
        if not resource:
            raise ValueError("No resource found for this platform_id")

        resource_id = str(resource["id"])

        if scope_type == "team" and scope_id:
            # Team context: try to unlink from team first
            try:
                await svc.remove_from_library(
                    resource_id=resource_id,
                    user_id=auth.user_id,
                    scope_type="team",
                    scope_id=scope_id,
                )
                return {"success": True, "message": "Resource removed from team library"}
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
    scope_type: str = Query("personal", pattern="^(personal|team)$"),
    scope_id: Optional[str] = Query(None),
):
    """Move a resource to trash or unlink from team, looked up by parsed_media.id.

    - Personal scope: sets is_trashed=true on the resource (global trash).
    - Team scope: removes the resource_item link from the team only.
    """
    try:
        svc = ResourcesService()
        resource = await svc.repo.get_resource_by_media_id(media_id)
        if not resource:
            raise ValueError("No resource found for this media_id")

        resource_id = str(resource["id"])

        if scope_type == "team" and scope_id:
            try:
                await svc.remove_from_library(
                    resource_id=resource_id,
                    user_id=auth.user_id,
                    scope_type="team",
                    scope_id=scope_id,
                )
                return {"success": True, "message": "Resource removed from team library"}
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
    scope_type: str = Query("personal", pattern="^(personal|team)$"),
    scope_id: Optional[str] = Query(None),
):
    """Remove a downloaded video from the user's library by platform_id.

    1. If a resource_item exists in the given scope → unlink it.
    2. Otherwise → delete the videos record (legacy / orphan case).
    """
    try:
        svc = ResourcesService()
        resource = await svc.repo.get_resource_by_platform_id(platform_id)

        if resource:
            # Try to find & remove the resource_item in the requested scope
            target_scope_id = scope_id or auth.user_id
            item = await svc.repo.get_resource_item(
                str(resource["id"]), scope_type, target_scope_id,
            )
            if item:
                await svc.repo.delete_resource_item(item["id"])
                return {"success": True, "message": "Resource unlinked from library"}

        # Fallback: no resource or no resource_item → delete video record
        from app.db.supabase_client import get_async_supabase_admin
        client = await get_async_supabase_admin()
        result = await (
            client.table("parsed_media")
            .delete()
            .eq("platform_id", platform_id)
            .eq("user_id", auth.user_id)
            .execute()
        )
        if not result.data:
            raise ValueError("No video found for this platform_id")
        return {"success": True, "message": "Video record deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to unlink resource by platform_id {platform_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to unlink resource")


@router.delete("/{resource_id}")
async def delete_resource(
    resource_id: str,
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
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
            scope_type=scope_type,
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
async def restore_resource(resource_id: str, auth: AuthDep):
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
async def permanent_delete_resource(resource_id: str, auth: AuthDep):
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
async def move_resource(resource_id: str, data: ResourceMoveRequest, auth: AuthDep):
    """Move a resource to a different folder."""
    try:
        svc = ResourcesService()
        result = await svc.move_resource(
            resource_id=resource_id,
            user_id=auth.user_id,
            scope_type=data.scope_type,
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
async def list_resource_tags(resource_id: str, auth: AuthDep):
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
    resource_id: str, data: ResourceTagRequest, auth: AuthDep
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
async def remove_resource_tag(resource_id: str, tag_id: str, auth: AuthDep):
    """Remove a tag from a resource."""
    try:
        repo = ResourcesRepository()
        await repo.remove_resource_tag(resource_id, tag_id)
        return {"success": True, "message": "Tag removed"}
    except Exception as e:
        logger.error(f"Failed to remove tag {tag_id} from resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove tag")
