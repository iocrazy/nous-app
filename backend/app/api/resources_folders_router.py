# backend/app/api/resources_folders_router.py

"""
Resources Folders Router

Regular folder and smart folder CRUD operations.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.resources_repository import ResourcesRepository
from app.schemas.resources import (
    FolderCreate,
    FolderUpdate,
    SmartFolderCreate,
    SmartFolderUpdate,
)
from app.services.resources_service import ResourcesService

router = APIRouter(prefix="/resources")


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
async def update_smart_folder(folder_id: str, data: SmartFolderUpdate, auth: AuthDep):
    """Update a smart folder's name, rules, icon, or color."""
    try:
        repo = ResourcesRepository()
        folder = await repo.get_folder_by_id(folder_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Smart folder not found")
        if not folder.get("is_smart"):
            raise HTTPException(status_code=400, detail="Folder is not a smart folder")

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
            raise HTTPException(status_code=400, detail="Folder is not a smart folder")

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
            raise HTTPException(status_code=400, detail="Folder is not a smart folder")

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


# ============================================
# Regular Folder endpoints
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
        return {
            "success": True,
            "message": "Folder permanently deleted",
            "data": result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete folder")
