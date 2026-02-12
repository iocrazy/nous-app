# backend/app/api/projects_router.py

"""
Projects Router

MediaTrack project management API endpoints: project CRUD, file uploads,
file management, and video linking.
Requires authentication (JWT or API Key).
"""

from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.projects_repository import ProjectsRepository
from app.schemas.projects import (
    LinkVideoRequest,
    ProjectCreate,
    ProjectFileUpdate,
    ProjectUpdate,
)
from app.services.projects_service import ProjectsService

router = APIRouter(prefix="/projects")

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


# ============================================
# Project endpoints
# ============================================


@router.get("")
async def list_projects(
    auth: AuthDep,
    project_type: Optional[str] = Query(None, description="Filter by project type"),
    starred: Optional[bool] = Query(None, description="Filter starred projects only"),
):
    """
    List all projects accessible to the current user.

    - **project_type**: Optional filter (internal, external, personal).
    - **starred**: Optional filter for starred projects.
    """
    try:
        svc = ProjectsService()
        projects = await svc.get_projects_with_counts(auth.user_id)

        if project_type:
            projects = [p for p in projects if p.get("project_type") == project_type]
        if starred is not None:
            projects = [p for p in projects if p.get("is_starred") == starred]

        return {"success": True, "data": projects}
    except Exception as e:
        logger.error(f"Failed to list projects: {e}")
        raise HTTPException(status_code=500, detail="Failed to list projects")


@router.post("")
async def create_project(data: ProjectCreate, auth: AuthDep):
    """Create a new project owned by the current user."""
    try:
        svc = ProjectsService()
        project = await svc.create_project(
            user_id=auth.user_id,
            data=data.model_dump(exclude_none=True),
        )
        return {"success": True, "data": project}
    except Exception as e:
        logger.error(f"Failed to create project: {e}")
        raise HTTPException(status_code=500, detail="Failed to create project")


@router.get("/{project_id}")
async def get_project(project_id: str, auth: AuthDep):
    """Get a single project by ID with file count."""
    try:
        repo = ProjectsRepository()
        project = await repo.get_project_by_id(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        project["file_count"] = await repo.get_project_file_count(project_id)
        return {"success": True, "data": project}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get project")


@router.put("/{project_id}")
async def update_project(project_id: str, data: ProjectUpdate, auth: AuthDep):
    """Update a project. Only the owner can update."""
    try:
        svc = ProjectsService()
        project = await svc.update_project(
            project_id=project_id,
            user_id=auth.user_id,
            data=data.model_dump(exclude_none=True),
        )
        return {"success": True, "data": project}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update project")


@router.delete("/{project_id}")
async def delete_project(project_id: str, auth: AuthDep):
    """Delete a project and all its files. Only the owner can delete."""
    try:
        svc = ProjectsService()
        await svc.delete_project(project_id=project_id, user_id=auth.user_id)
        return {"success": True, "message": "Project deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete project")


# ============================================
# File endpoints (nested under project)
# ============================================


@router.get("/{project_id}/files")
async def list_files(
    project_id: str,
    auth: AuthDep,
    include_trashed: bool = Query(False, description="Include trashed files"),
):
    """List files in a project."""
    try:
        repo = ProjectsRepository()
        project = await repo.get_project_by_id(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        files = await repo.get_project_files(project_id, include_trashed=include_trashed)
        return {"success": True, "data": files}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list files for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list files")


@router.post("/{project_id}/files/upload")
async def upload_file(
    project_id: str,
    auth: AuthDep,
    file: UploadFile = File(...),
    notes: Optional[str] = Query(None, description="Optional notes for the file"),
):
    """
    Upload a file to a project.

    Max file size: 500 MB. Video files will have metadata extracted via ffprobe.
    """
    try:
        # Check file size via content-length hint (if available)
        if file.size and file.size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413, detail="File too large. Maximum size is 500 MB."
            )

        svc = ProjectsService()
        result = await svc.upload_file(
            project_id=project_id,
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
        logger.error(f"Failed to upload file to project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload file")


@router.post("/{project_id}/files/link-video")
async def link_video(project_id: str, data: LinkVideoRequest, auth: AuthDep):
    """Link an existing video from the library to this project."""
    try:
        svc = ProjectsService()
        result = await svc.link_video(
            project_id=project_id,
            video_id=data.video_id,
            user_id=auth.user_id,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to link video to project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to link video")


@router.get("/{project_id}/files/{file_id}")
async def get_file_info(project_id: str, file_id: str, auth: AuthDep):
    """Get detailed info for a single file."""
    try:
        repo = ProjectsRepository()
        file_record = await repo.get_file_by_id(file_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        if file_record.get("project_id") != project_id:
            raise HTTPException(status_code=404, detail="File not found in this project")
        return {"success": True, "data": file_record}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get file info")


@router.put("/{project_id}/files/{file_id}")
async def update_file(
    project_id: str, file_id: str, data: ProjectFileUpdate, auth: AuthDep
):
    """Update file metadata (rename, add notes, trash/restore)."""
    try:
        repo = ProjectsRepository()
        file_record = await repo.get_file_by_id(file_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        if file_record.get("project_id") != project_id:
            raise HTTPException(status_code=404, detail="File not found in this project")

        update_data = data.model_dump(exclude_none=True)

        # Set trashed_at timestamp when trashing
        if data.is_trashed is True:
            from datetime import datetime, timezone

            update_data["trashed_at"] = datetime.now(timezone.utc).isoformat()
        elif data.is_trashed is False:
            update_data["trashed_at"] = None

        result = await repo.update_file(file_id, update_data)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update file")


@router.delete("/{project_id}/files/{file_id}")
async def delete_file(project_id: str, file_id: str, auth: AuthDep):
    """Permanently delete a file record."""
    try:
        repo = ProjectsRepository()
        file_record = await repo.get_file_by_id(file_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        if file_record.get("project_id") != project_id:
            raise HTTPException(status_code=404, detail="File not found in this project")

        await repo.delete_file(file_id)
        return {"success": True, "message": "File deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete file")
