# backend/app/api/libraries_router.py

"""
Libraries Router

Team library CRUD endpoints.
"""

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.schemas.libraries import LibraryCreate, LibraryUpdate
from app.services.libraries_service import LibrariesService

router = APIRouter(prefix="/libraries")


@router.get("")
async def list_libraries(
    auth: AuthDep,
    scope_id: str = Query(..., description="Team ID"),
):
    """List all libraries for a team."""
    try:
        svc = LibrariesService()
        libraries = await svc.list_libraries(scope_id)
        return {"success": True, "data": libraries}
    except Exception as e:
        logger.error(f"Failed to list libraries: {e}")
        raise HTTPException(status_code=500, detail="Failed to list libraries")


@router.post("")
async def create_library(data: LibraryCreate, auth: AuthDep):
    """Create a new team library."""
    try:
        svc = LibrariesService()
        library = await svc.create_library(
            user_id=auth.user_id,
            name=data.name,
            scope_id=data.scope_id,
            icon=data.icon,
            color=data.color,
        )
        return {"success": True, "data": library}
    except Exception as e:
        logger.error(f"Failed to create library: {e}")
        raise HTTPException(status_code=500, detail="Failed to create library")


@router.get("/{library_id}")
async def get_library(library_id: str, auth: AuthDep):
    """Get a single library by ID."""
    try:
        svc = LibrariesService()
        library = await svc.get_library(library_id)
        if not library:
            raise HTTPException(status_code=404, detail="Library not found")
        return {"success": True, "data": library}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get library {library_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get library")


@router.patch("/{library_id}")
async def update_library(
    library_id: str, data: LibraryUpdate, auth: AuthDep
):
    """Update a library."""
    try:
        svc = LibrariesService()
        library = await svc.get_library(library_id)
        if not library:
            raise HTTPException(status_code=404, detail="Library not found")

        update_data = data.model_dump(exclude_none=True)
        if not update_data:
            return {"success": True, "data": library}

        result = await svc.update_library(library_id, update_data)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update library {library_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update library")


@router.delete("/{library_id}")
async def delete_library(library_id: str, auth: AuthDep):
    """Delete a library and all its contents."""
    try:
        svc = LibrariesService()
        library = await svc.get_library(library_id)
        if not library:
            raise HTTPException(status_code=404, detail="Library not found")

        await svc.delete_library(library_id)
        return {"success": True, "message": "Library deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete library {library_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete library")
