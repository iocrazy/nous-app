# backend/app/api/resources_upload_router.py

"""
Resources Upload Router

Upload, duplicate detection, link-existing, and permission endpoints.
"""

from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.resources_repository import ResourcesRepository
from app.services.permission_service import PermissionService
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
# Upload endpoint
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
    from app.services.task_tracking_manager import get_task_manager

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
                await tracker.complete(
                    unified_task_id,
                    metadata_patch={
                        "resource_id": str(result.get("id", "")),
                    },
                )
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
