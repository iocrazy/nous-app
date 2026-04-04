# backend/app/api/resources_router.py

"""
Resources Router

Resource library API endpoints: upload, CRUD, version management,
folder operations, tagging, and recycle bin.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.resources_repository import ResourcesRepository
from app.services.permission_service import PermissionService
from app.schemas.resources import (
    FolderCreate,
    FolderUpdate,
    ResourceMoveRequest,
    ResourceTagRequest,
    ResourceUpdate,
    SmartFolderCreate,
    SmartFolderUpdate,
)
from app.services.resources_service import ResourcesService
from app.services.thumbnail_service import ThumbnailService

router = APIRouter(prefix="/resources")

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


# ============================================
# Permission endpoints
# ============================================


@router.get("/permissions")
async def get_effective_permissions(
    auth: AuthDep,
    object_type: str = Query(..., pattern="^(folder|library|resource|project)$"),
    object_id: str = Query(...),
    team_id: str = Query(...),
):
    """Get the effective role and capabilities for the current user on an object."""
    try:
        svc = PermissionService()
        result = await svc.get_effective_role(
            user_id=auth.user_id,
            object_type=object_type,
            object_id=object_id,
            team_id=team_id,
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"Failed to get permissions: {e}")
        raise HTTPException(status_code=500, detail="Failed to get permissions")


# ============================================
# Duplicate detection endpoints
# ============================================


@router.get("/check-duplicate")
async def check_duplicate(
    auth: AuthDep,
    file_hash: str = Query(..., min_length=64, max_length=64),
    file_size: int = Query(..., gt=0),
):
    """Check if a file with the same hash already exists."""
    try:
        repo = ResourcesRepository()
        matches = await repo.find_by_hash(file_hash, auth.user_id)
        # Secondary file_size check to guard against hash collisions
        exact = [m for m in matches if m.get("file_size_bytes") == file_size]
        return {
            "duplicate": len(exact) > 0,
            "existing": exact[0] if exact else None,
        }
    except Exception as e:
        logger.error(f"Failed to check duplicate: {e}")
        raise HTTPException(status_code=500, detail="Failed to check duplicate")


@router.post("/link-existing")
async def link_existing_resource(
    auth: AuthDep,
    resource_id: str = Query(...),
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
    folder_id: Optional[str] = Query(None),
    library_id: Optional[str] = Query(None),
):
    """Link an existing resource to the current scope/folder (use existing)."""
    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        existing_item = await repo.find_resource_item(
            resource_id, scope_type, scope_id, folder_id
        )
        if existing_item:
            return {"success": True, "data": resource, "already_linked": True}

        item_data = {
            "resource_id": resource_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "folder_id": folder_id,
            "library_id": library_id,
            "added_by": auth.user_id,
        }
        await repo.create_resource_item(item_data)
        return {"success": True, "data": resource}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to link existing resource: {e}")
        raise HTTPException(status_code=500, detail="Failed to link existing resource")


# ============================================
# Resource endpoints
# ============================================


@router.post("/upload")
async def upload_resource(
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
    folder_id: Optional[str] = Query(None),
    library_id: Optional[str] = Query(None),
    file: UploadFile = File(...),
):
    """Upload a file to the resource library."""
    from app.services.unified_task_manager import get_task_manager

    tracker = get_task_manager()
    unified_task_id = None

    try:
        if file.size and file.size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413, detail="File too large. Maximum size is 500 MB."
            )

        # Create unified task for upload tracking
        try:
            unified_task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="upload",
                title=f"Upload {file.filename or 'file'}",
                total_bytes=file.size,
            )
            await tracker.start(unified_task_id)
        except Exception as e:
            logger.warning(f"[TaskManager] Failed to track upload: {e}")

        svc = ResourcesService()
        result = await svc.upload_resource(
            user_id=auth.user_id,
            file=file,
            scope_type=scope_type,
            scope_id=scope_id,
            folder_id=folder_id,
            library_id=library_id,
        )

        # Mark upload complete
        if unified_task_id:
            try:
                await tracker.complete(unified_task_id, metadata_patch={
                    "resource_id": str(result.get("id", "")),
                })
            except Exception:
                pass

        # Generate thumbnail synchronously so it's ready for the frontend
        if result.get("file_path") and result.get("mime_type"):
            try:
                thumbnail_svc = ThumbnailService()
                thumb_path = await thumbnail_svc.generate_thumbnail(
                    resource_id=str(result["id"]),
                    file_path=result["file_path"],
                    mime_type=result.get("mime_type", ""),
                )
                if thumb_path:
                    result["thumbnail_path"] = thumb_path
            except Exception as e:
                logger.warning(f"Thumbnail generation failed (non-fatal): {e}")

        return {"success": True, "data": result}
    except HTTPException:
        if unified_task_id:
            try:
                await tracker.fail(unified_task_id, "Upload rejected")
            except Exception:
                pass
        raise
    except Exception as e:
        logger.error(f"Failed to upload resource: {e}")
        if unified_task_id:
            try:
                await tracker.fail(unified_task_id, str(e)[:500])
            except Exception:
                pass
        raise HTTPException(status_code=500, detail="Failed to upload resource")


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


# ============================================
# Smart Folder endpoints
# ============================================


@router.post("/smart-folders")
async def create_smart_folder(data: SmartFolderCreate, auth: AuthDep):
    """Create a smart folder with rule-based filtering."""
    try:
        repo = ResourcesRepository()
        folder = await repo.create_smart_folder(
            {
                "name": data.name,
                "scope_type": data.scope_type,
                "scope_id": data.scope_id,
                "created_by": auth.user_id,
                "is_smart": True,
                "smart_rules": data.rules.model_dump(),
                "icon": data.icon,
                "color": data.color,
            }
        )
        return {"success": True, "data": folder}
    except Exception as e:
        logger.error(f"Failed to create smart folder: {e}")
        raise HTTPException(status_code=500, detail="Failed to create smart folder")


@router.get("/smart-folders")
async def list_smart_folders(
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
):
    """List smart folders in a scope."""
    try:
        repo = ResourcesRepository()
        folders = await repo.get_smart_folders(scope_type, scope_id)
        return {"success": True, "data": folders}
    except Exception as e:
        logger.error(f"Failed to list smart folders: {e}")
        raise HTTPException(status_code=500, detail="Failed to list smart folders")


@router.patch("/smart-folders/{folder_id}")
async def update_smart_folder(
    folder_id: str, data: SmartFolderUpdate, auth: AuthDep
):
    """Update a smart folder's name, rules, icon, or color."""
    try:
        repo = ResourcesRepository()
        folder = await repo.get_folder_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Smart folder not found")
        if not folder.get("is_smart"):
            raise HTTPException(
                status_code=400, detail="Folder is not a smart folder"
            )

        update_data = data.model_dump(exclude_none=True)
        # Convert rules Pydantic model to dict for JSONB storage
        if "rules" in update_data:
            update_data["smart_rules"] = update_data.pop("rules")

        result = await repo.update_folder(folder_id, update_data)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update smart folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update smart folder")


@router.delete("/smart-folders/{folder_id}")
async def delete_smart_folder(folder_id: str, auth: AuthDep):
    """Delete a smart folder."""
    try:
        repo = ResourcesRepository()
        folder = await repo.get_folder_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Smart folder not found")
        if not folder.get("is_smart"):
            raise HTTPException(
                status_code=400, detail="Folder is not a smart folder"
            )

        await repo.delete_folder(folder_id)
        return {"success": True, "message": "Smart folder deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete smart folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete smart folder")


@router.get("/smart-folders/{folder_id}/results")
async def smart_folder_results(
    folder_id: str,
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
):
    """Execute smart folder rules and return matching resources."""
    try:
        repo = ResourcesRepository()
        folder = await repo.get_folder_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Smart folder not found")
        if not folder.get("is_smart"):
            raise HTTPException(
                status_code=400, detail="Folder is not a smart folder"
            )

        rules = folder.get("smart_rules")
        if not rules or not rules.get("conditions"):
            return {"success": True, "data": []}

        items = await repo.execute_smart_rules(scope_type, scope_id, rules)
        return {"success": True, "data": items}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to execute smart folder {folder_id}: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to execute smart folder rules"
        )


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
# Version endpoints
# ============================================


@router.get("/{resource_id}/versions")
async def list_versions(resource_id: str, auth: AuthDep):
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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    notes: Optional[str] = Query(None),
):
    """Upload a new version of a resource."""
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
                    thumbnail_svc.generate_thumbnail,
                    resource_id=resource_id,
                    file_path=result["file_path"],
                    mime_type=result.get("mime_type", ""),
                    scope_type=item.get("scope_type", "personal"),
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
    resource_id: str, version_number: int, auth: AuthDep
):
    """Set a specific version as the current active version."""
    try:
        svc = ResourcesService()
        result = await svc.set_current_version(resource_id, version_number, auth.user_id)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to set current version: {e}")
        raise HTTPException(status_code=500, detail="Failed to set current version")


@router.delete("/{resource_id}/versions/{version_id}")
async def delete_version(
    resource_id: str, version_id: str, auth: AuthDep
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
        await get_auth(request, effective_auth, x_api_key)

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
            ".m4s": "video/iso.segment",   # fMP4 segments
            ".mp4": "video/mp4",            # fMP4 init segments
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
):
    """Manually trigger or retry HLS transcoding for a version."""
    try:
        repo = ResourcesRepository()
        version = await repo.get_version_by_id(version_id)
        if not version or str(version.get("resource_id")) != resource_id:
            raise HTTPException(status_code=404, detail="Version not found")

        mime = version.get("mime_type", "")
        if not mime.startswith("video/"):
            raise HTTPException(status_code=400, detail="Only video files can be transcoded")

        # Reset status before retrying
        await repo.update_version(version_id, {"transcode_status": "pending"})

        from app.tasks.transcode_tasks import transcode_to_hls
        await asyncio.to_thread(transcode_to_hls.delay, resource_id, version_id, auth.user_id)

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
        await get_auth(request, effective_auth, x_api_key)

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


# ============================================
# Folder endpoints
# ============================================


@router.get("/folders/list")
async def list_folders(
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
):
    """List folders in a scope."""
    try:
        repo = ResourcesRepository()
        folders = await repo.get_folders(scope_type, scope_id)
        return {"success": True, "data": folders}
    except Exception as e:
        logger.error(f"Failed to list folders: {e}")
        raise HTTPException(status_code=500, detail="Failed to list folders")


@router.post("/folders")
async def create_folder(data: FolderCreate, auth: AuthDep):
    """Create a new folder."""
    try:
        repo = ResourcesRepository()
        folder = await repo.create_folder(
            {
                "name": data.name,
                "parent_id": data.parent_id,
                "scope_type": data.scope_type,
                "scope_id": data.scope_id,
                "created_by": auth.user_id,
                "icon": data.icon,
                "color": data.color,
            }
        )
        return {"success": True, "data": folder}
    except Exception as e:
        logger.error(f"Failed to create folder: {e}")
        raise HTTPException(status_code=500, detail="Failed to create folder")


@router.patch("/folders/{folder_id}")
async def update_folder(folder_id: str, data: FolderUpdate, auth: AuthDep):
    """Update a folder."""
    try:
        repo = ResourcesRepository()
        folder = await repo.get_folder_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        update_data = data.model_dump(exclude_none=True)

        if data.is_trashed is True:
            update_data["trashed_at"] = datetime.now(timezone.utc).isoformat()
        elif data.is_trashed is False:
            update_data["trashed_at"] = None

        result = await repo.update_folder(folder_id, update_data)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update folder")


@router.get("/folders/{folder_id}/content-count")
async def get_folder_content_count(folder_id: str, auth: AuthDep):
    """Get count of resources and sub-folders inside a folder (including nested)."""
    try:
        repo = ResourcesRepository()
        folder = await repo.get_folder_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        descendant_ids = await repo.get_descendant_folder_ids(folder_id)
        all_folder_ids = [folder_id] + descendant_ids
        counts = await repo.count_folder_contents(all_folder_ids)
        return {"success": True, "data": counts}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to count folder contents {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to count folder contents")


@router.post("/folders/{folder_id}/trash")
async def trash_folder_cascade(folder_id: str, auth: AuthDep):
    """Move a folder, all sub-folders, and their resources to the recycle bin."""
    try:
        repo = ResourcesRepository()
        folder = await repo.get_folder_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        result = await repo.trash_folder_cascade(folder_id)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to trash folder cascade {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to trash folder")


@router.post("/folders/{folder_id}/restore")
async def restore_folder_cascade(folder_id: str, auth: AuthDep):
    """Restore a trashed folder, all sub-folders, and their resources."""
    try:
        repo = ResourcesRepository()
        folder = await repo.get_folder_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        result = await repo.restore_folder_cascade(folder_id)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to restore folder cascade {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to restore folder")


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, auth: AuthDep):
    """Permanently delete a folder and all resources inside it (cascade)."""
    try:
        repo = ResourcesRepository()
        svc = ResourcesService()
        folder = await repo.get_folder_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        result = await svc.permanent_delete_folder(folder_id, auth.user_id)
        return {"success": True, "message": "Folder permanently deleted", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete folder")
