# backend/app/api/resources_router.py

"""
Resources Router

Resource library API endpoints: upload, CRUD, version management,
folder operations, tagging, and recycle bin.
"""

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
# Resource endpoints
# ============================================


@router.post("/upload")
async def upload_resource(
    auth: AuthDep,
    background_tasks: BackgroundTasks,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
    folder_id: Optional[str] = Query(None),
    file: UploadFile = File(...),
):
    """Upload a file to the resource library."""
    try:
        if file.size and file.size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413, detail="File too large. Maximum size is 500 MB."
            )

        svc = ResourcesService()
        result = await svc.upload_resource(
            user_id=auth.user_id,
            file=file,
            scope_type=scope_type,
            scope_id=scope_id,
            folder_id=folder_id,
        )

        # Trigger thumbnail generation in the background
        if result.get("file_path") and result.get("mime_type"):
            thumbnail_svc = ThumbnailService()
            background_tasks.add_task(
                thumbnail_svc.generate_thumbnail,
                resource_id=result["id"],
                file_path=result["file_path"],
                mime_type=result.get("mime_type", ""),
                scope_type=scope_type,
                scope_id=scope_id,
            )

        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to upload resource: {e}")
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
            filename=resource.get("filename", "download"),
            media_type=resource.get("mime_type", "application/octet-stream"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve file for resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve file")


@router.patch("/{resource_id}")
async def update_resource(resource_id: str, data: ResourceUpdate, auth: AuthDep):
    """Update resource metadata."""
    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        update_data = data.model_dump(exclude_none=True)

        if data.is_trashed is True:
            update_data["trashed_at"] = datetime.now(timezone.utc).isoformat()
        elif data.is_trashed is False:
            update_data["trashed_at"] = None

        result = await repo.update_resource(resource_id, update_data)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update resource")


@router.delete("/{resource_id}")
async def delete_resource(
    resource_id: str,
    auth: AuthDep,
    scope_type: str = Query(..., pattern="^(personal|team)$"),
    scope_id: str = Query(...),
):
    """Remove a resource from the user's library.

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
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to upload version for resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload version")


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


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, auth: AuthDep):
    """Permanently delete a folder."""
    try:
        repo = ResourcesRepository()
        folder = await repo.get_folder_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        await repo.delete_folder(folder_id)
        return {"success": True, "message": "Folder deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete folder")
