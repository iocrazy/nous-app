# backend/app/api/libraries_router.py

"""
Libraries Router

Team library CRUD endpoints.
"""

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.api.library_access import library_not_found, require_library_access, team_role
from app.api.row_guard import require_row
from app.core.deps import AuthDep
from app.core.workflow_roles import WRITE_ROLES
from app.schemas.libraries import (
    LibraryCreate,
    LibraryDeleteResponse,
    LibraryListResponse,
    LibraryResponse,
    LibraryUpdate,
)
from app.services.library.libraries_service import LibrariesService

router = APIRouter(prefix="/libraries")


@router.get("", response_model=LibraryListResponse)
async def list_libraries(
    auth: AuthDep,
    scope_id: str = Query(..., description="Team ID"),
):
    """List all libraries for a team."""
    if await team_role(scope_id, auth.user_id) is None:
        raise HTTPException(status_code=403, detail="You are not a member of this team")
    try:
        svc = LibrariesService()
        libraries = await svc.list_libraries(scope_id)
        return {"success": True, "data": libraries}
    except Exception as e:
        logger.error(f"Failed to list libraries: {e}")
        raise HTTPException(status_code=500, detail="Failed to list libraries")


@router.post("", response_model=LibraryResponse)
async def create_library(data: LibraryCreate, auth: AuthDep):
    """Create a new team library."""
    role = await team_role(data.scope_id, auth.user_id)
    if role is None:
        raise HTTPException(status_code=403, detail="You are not a member of this team")
    if role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role")
    try:
        svc = LibrariesService()
        library = await svc.create_library(
            user_id=auth.user_id,
            name=data.name,
            scope_id=data.scope_id,
            icon=data.icon,
            color=data.color,
        )
        return {"success": True, "data": require_row(library)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create library: {e}")
        raise HTTPException(status_code=500, detail="Failed to create library")


async def _load(svc: LibrariesService, library_id: str) -> dict | None:
    """The row, or None — also for an id that is not a number (was a 500)."""
    try:
        int(library_id)
    except ValueError:
        raise library_not_found()
    return await svc.get_library(library_id)


@router.get("/{library_id}", response_model=LibraryResponse)
async def get_library(library_id: str, auth: AuthDep):
    """Get a single library by ID."""
    try:
        svc = LibrariesService()
        library = await _load(svc, library_id)
        await require_library_access(library, auth.user_id, write=False)
        return {"success": True, "data": library}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get library {library_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get library")


@router.patch("/{library_id}", response_model=LibraryResponse)
async def update_library(library_id: str, data: LibraryUpdate, auth: AuthDep):
    """Update a library."""
    try:
        svc = LibrariesService()
        library = await _load(svc, library_id)
        await require_library_access(library, auth.user_id, write=True)

        update_data = data.model_dump(exclude_none=True)
        if not update_data:
            return {"success": True, "data": library}

        result = await svc.update_library(library_id, update_data)
        # {} = the row went away between the guard and the write.
        return {"success": True, "data": require_row(result)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update library {library_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update library")


@router.delete("/{library_id}", response_model=LibraryDeleteResponse)
async def delete_library(library_id: str, auth: AuthDep):
    """Delete a library and all its contents."""
    try:
        svc = LibrariesService()
        library = await _load(svc, library_id)
        await require_library_access(library, auth.user_id, write=True)

        await svc.delete_library(library_id)
        return {"success": True, "message": "Library deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete library {library_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete library")
