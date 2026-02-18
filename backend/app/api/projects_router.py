# backend/app/api/projects_router.py

"""
Projects Router

MediaTrack project management API endpoints: project CRUD, file uploads,
file management, and video linking.
Requires authentication (JWT or API Key).
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from loguru import logger
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.repositories.projects_repository import ProjectsRepository
from app.schemas.projects import (
    AddMemberRequest,
    CreateCommentRequest,
    LinkMediaRequest,
    ProjectCreate,
    ProjectFileUpdate,
    ProjectUpdate,
    ReviewStatusUpdate,
    UpdateMemberRoleRequest,
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
    folder_id: Optional[str] = Query(None, description="Filter by folder ID"),
):
    """List files in a project, optionally filtered by folder."""
    try:
        repo = ProjectsRepository()
        project = await repo.get_project_by_id(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        files = await repo.get_project_files(
            project_id, include_trashed=include_trashed
        )
        # Apply folder filtering only when not fetching trashed files
        if not include_trashed:
            if folder_id is not None:
                files = [f for f in files if f.get("folder_id") == folder_id]
            else:
                files = [f for f in files if not f.get("folder_id")]
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


@router.post("/{project_id}/files/link-media")
async def link_media(project_id: str, data: LinkMediaRequest, auth: AuthDep):
    """Link an existing media item from the library to this project."""
    try:
        svc = ProjectsService()
        result = await svc.link_media(
            project_id=project_id,
            media_id=data.media_id,
            user_id=auth.user_id,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to link media to project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to link media")


@router.get("/{project_id}/files/{file_id}")
async def get_file_info(project_id: str, file_id: str, auth: AuthDep):
    """Get detailed info for a single file."""
    try:
        repo = ProjectsRepository()
        file_record = await repo.get_file_by_id(file_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        if file_record.get("project_id") != project_id:
            raise HTTPException(
                status_code=404, detail="File not found in this project"
            )
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
            raise HTTPException(
                status_code=404, detail="File not found in this project"
            )

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


@router.put("/{project_id}/files/{file_id}/restore")
async def restore_file(project_id: str, file_id: str, auth: AuthDep):
    """Restore a trashed file."""
    try:
        repo = ProjectsRepository()
        file_record = await repo.get_file_by_id(file_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        if file_record.get("project_id") != project_id:
            raise HTTPException(
                status_code=404, detail="File not found in this project"
            )
        result = await repo.update_file(
            file_id, {"is_trashed": False, "trashed_at": None}
        )
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to restore file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to restore file")


@router.get("/{project_id}/shares")
async def list_project_shares(project_id: str, auth: AuthDep):
    """List all shares for files in this project."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        # Get all file IDs in this project
        files_result = (
            await sb.table("project_files")
            .select("id")
            .eq("project_id", project_id)
            .execute()
        )
        file_ids = [f["id"] for f in (files_result.data or [])]
        if not file_ids:
            return {"success": True, "data": []}

        # Get shares for those files
        result = (
            await sb.table("shares")
            .select("*")
            .in_("project_file_id", file_ids)
            .order("created_at", desc=True)
            .execute()
        )
        return {"success": True, "data": result.data or []}
    except Exception as e:
        logger.error(f"Failed to list shares for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list project shares")


class CreateShareRequest(BaseModel):
    file_id: str
    share_type: str = "link"
    share_name: Optional[str] = None
    password: Optional[str] = None
    allow_download: bool = True
    expires_hours: Optional[int] = None


@router.post("/{project_id}/shares")
async def create_share(project_id: str, data: CreateShareRequest, auth: AuthDep):
    """Create a share link for a project file."""
    import secrets
    from datetime import datetime, timedelta, timezone

    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()

        # Verify file belongs to project
        file_result = (
            await sb.table("project_files")
            .select("id, filename")
            .eq("id", data.file_id)
            .eq("project_id", project_id)
            .single()
            .execute()
        )
        if not file_result.data:
            raise HTTPException(status_code=404, detail="File not found in project")

        share_code = secrets.token_urlsafe(8)[:12]
        share_name = data.share_name or file_result.data["filename"]

        share_data: Dict[str, Any] = {
            "project_file_id": data.file_id,
            "share_type": data.share_type,
            "shared_by": auth.user_id,
            "share_name": share_name,
            "share_code": share_code,
            "password": data.password,
            "allow_download": data.allow_download,
            "status": "active",
        }
        if data.expires_hours:
            share_data["expires_at"] = (
                datetime.now(timezone.utc) + timedelta(hours=data.expires_hours)
            ).isoformat()

        result = await sb.table("shares").insert(share_data).execute()
        share = result.data[0] if result.data else None
        if not share:
            raise HTTPException(status_code=500, detail="Failed to create share")
        return {"success": True, "data": share}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create share for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create share")


# ============================================
# Folder endpoints
# ============================================


class CreateFolderRequest(BaseModel):
    name: str = "New Folder"
    parent_id: Optional[str] = None


class RenameFolderRequest(BaseModel):
    name: str


class MoveFileRequest(BaseModel):
    folder_id: Optional[str] = None


@router.get("/{project_id}/folders")
async def list_folders(
    project_id: str,
    auth: AuthDep,
    parent_id: Optional[str] = Query(None, description="Parent folder ID, null for root"),
):
    """List folders in a project."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        query = sb.table("project_folders").select("*").eq("project_id", project_id)
        if parent_id:
            query = query.eq("parent_id", parent_id)
        else:
            query = query.is_("parent_id", "null")
        result = await query.order("name").execute()
        return {"success": True, "data": result.data or []}
    except Exception as e:
        logger.error(f"Failed to list folders for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list folders")


@router.post("/{project_id}/folders")
async def create_folder(project_id: str, data: CreateFolderRequest, auth: AuthDep):
    """Create a new folder in a project."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        insert_data: Dict[str, Any] = {
            "project_id": project_id,
            "name": data.name,
            "created_by": auth.user_id,
        }
        if data.parent_id:
            insert_data["parent_id"] = data.parent_id
        result = await sb.table("project_folders").insert(insert_data).execute()
        folder = result.data[0] if result.data else None
        return {"success": True, "data": folder}
    except Exception as e:
        logger.error(f"Failed to create folder in project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create folder")


@router.put("/{project_id}/folders/{folder_id}")
async def rename_folder(
    project_id: str, folder_id: str, data: RenameFolderRequest, auth: AuthDep
):
    """Rename a folder."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        result = (
            await sb.table("project_folders")
            .update({"name": data.name})
            .eq("id", folder_id)
            .eq("project_id", project_id)
            .execute()
        )
        folder = result.data[0] if result.data else None
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        return {"success": True, "data": folder}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to rename folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to rename folder")


@router.delete("/{project_id}/folders/{folder_id}")
async def delete_folder(project_id: str, folder_id: str, auth: AuthDep):
    """Delete a folder (files inside are moved to parent)."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        # Get folder to find parent_id
        folder_result = (
            await sb.table("project_folders")
            .select("parent_id")
            .eq("id", folder_id)
            .eq("project_id", project_id)
            .single()
            .execute()
        )
        if not folder_result.data:
            raise HTTPException(status_code=404, detail="Folder not found")
        parent_id = folder_result.data.get("parent_id")

        # Move files to parent folder
        await (
            sb.table("project_files")
            .update({"folder_id": parent_id})
            .eq("folder_id", folder_id)
            .execute()
        )
        # Move sub-folders to parent
        await (
            sb.table("project_folders")
            .update({"parent_id": parent_id})
            .eq("parent_id", folder_id)
            .execute()
        )
        # Delete the folder
        await sb.table("project_folders").delete().eq("id", folder_id).execute()
        return {"success": True, "message": "Folder deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete folder")


@router.put("/{project_id}/files/{file_id}/move")
async def move_file(
    project_id: str, file_id: str, data: MoveFileRequest, auth: AuthDep
):
    """Move a file to a different folder."""
    try:
        repo = ProjectsRepository()
        file_record = await repo.get_file_by_id(file_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        if file_record.get("project_id") != project_id:
            raise HTTPException(
                status_code=404, detail="File not found in this project"
            )
        result = await repo.update_file(file_id, {"folder_id": data.folder_id})
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to move file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to move file")


@router.delete("/{project_id}/files/{file_id}")
async def delete_file(project_id: str, file_id: str, auth: AuthDep):
    """Permanently delete a file record."""
    try:
        repo = ProjectsRepository()
        file_record = await repo.get_file_by_id(file_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        if file_record.get("project_id") != project_id:
            raise HTTPException(
                status_code=404, detail="File not found in this project"
            )

        await repo.delete_file(file_id)
        return {"success": True, "message": "File deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete file")


# ============================================
# Version endpoints
# ============================================


@router.get("/{project_id}/files/{file_id}/versions")
async def list_versions(project_id: str, file_id: str, auth: AuthDep):
    """List all versions of a file."""
    try:
        repo = ProjectsRepository()
        file_record = await repo.get_file_by_id(file_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        if file_record.get("project_id") != project_id:
            raise HTTPException(
                status_code=404, detail="File not found in this project"
            )
        versions = await repo.get_file_versions(file_id)
        return {"success": True, "data": versions}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list versions for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list versions")


@router.post("/{project_id}/files/{file_id}/versions")
async def upload_version(
    project_id: str,
    file_id: str,
    auth: AuthDep,
    file: UploadFile = File(...),
    notes: Optional[str] = Query(None, description="Optional notes for this version"),
):
    """Upload a new version of a file."""
    try:
        if file.size and file.size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413, detail="File too large. Maximum size is 500 MB."
            )
        svc = ProjectsService()
        result = await svc.upload_new_version(
            project_id=project_id,
            file_id=file_id,
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
        logger.error(f"Failed to upload version for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload version")


# ============================================
# Comment endpoints
# ============================================


@router.get("/{project_id}/files/{file_id}/comments")
async def list_comments(
    project_id: str,
    file_id: str,
    auth: AuthDep,
    version_id: Optional[str] = Query(None, description="Filter by version ID"),
):
    """List comments on a file."""
    try:
        repo = ProjectsRepository()
        file_record = await repo.get_file_by_id(file_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        if file_record.get("project_id") != project_id:
            raise HTTPException(
                status_code=404, detail="File not found in this project"
            )
        comments = await repo.get_comments_for_file(file_id, version_id=version_id)
        return {"success": True, "data": comments}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list comments for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list comments")


@router.post("/{project_id}/files/{file_id}/comments")
async def add_comment(
    project_id: str,
    file_id: str,
    data: CreateCommentRequest,
    auth: AuthDep,
):
    """Add a comment to a file."""
    try:
        repo = ProjectsRepository()
        file_record = await repo.get_file_by_id(file_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File not found")
        if file_record.get("project_id") != project_id:
            raise HTTPException(
                status_code=404, detail="File not found in this project"
            )

        svc = ProjectsService()
        result = await svc.add_comment(
            file_id=file_id,
            author_id=auth.user_id,
            content=data.content,
            timestamp_seconds=data.timestamp_seconds,
            version_id=data.version_id,
            drawing_data=data.drawing_data,
        )
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add comment to file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add comment")


@router.delete("/{project_id}/files/{file_id}/comments/{comment_id}")
async def delete_comment(project_id: str, file_id: str, comment_id: str, auth: AuthDep):
    """Delete a comment. Only the author can delete their own comment."""
    try:
        repo = ProjectsRepository()
        comment = await repo.get_comment_by_id(comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        if comment.get("author_id") != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Can only delete your own comments"
            )

        await repo.delete_comment(comment_id)
        return {"success": True, "message": "Comment deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete comment {comment_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete comment")


# ============================================
# Review status endpoint
# ============================================


@router.put("/{project_id}/files/{file_id}/review-status")
async def update_review_status(
    project_id: str, file_id: str, data: ReviewStatusUpdate, auth: AuthDep
):
    """Update the review status of a file."""
    try:
        svc = ProjectsService()
        result = await svc.update_review_status(
            project_id=project_id,
            file_id=file_id,
            user_id=auth.user_id,
            review_status=data.review_status,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update review status for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update review status")


# ============================================
# Task endpoints (Kanban board)
# ============================================


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    task_type: Optional[str] = "general"
    assignee_id: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = "todo"


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    task_type: Optional[str] = None
    assignee_id: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None
    sort_order: Optional[int] = None


@router.get("/{project_id}/tasks")
async def list_tasks(project_id: str, auth: AuthDep):
    """List all tasks for a project."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        result = await sb.table("project_tasks").select("*").eq(
            "project_id", project_id
        ).order("sort_order").execute()
        return {"success": True, "data": result.data or []}
    except Exception as e:
        logger.error(f"Failed to list tasks for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list tasks")


@router.post("/{project_id}/tasks")
async def create_task(project_id: str, data: TaskCreate, auth: AuthDep):
    """Create a new task in a project."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        insert_data: Dict[str, Any] = {
            "project_id": project_id,
            "title": data.title,
            "created_by": auth.user_id,
        }
        if data.description is not None:
            insert_data["description"] = data.description
        if data.task_type is not None:
            insert_data["task_type"] = data.task_type
        if data.assignee_id is not None:
            insert_data["assignee_id"] = data.assignee_id
        if data.due_date is not None:
            insert_data["due_date"] = data.due_date
        if data.status is not None:
            insert_data["status"] = data.status

        result = await sb.table("project_tasks").insert(insert_data).execute()
        task = result.data[0] if result.data else None
        return {"success": True, "data": task}
    except Exception as e:
        logger.error(f"Failed to create task in project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create task")


@router.put("/{project_id}/tasks/{task_id}")
async def update_task(project_id: str, task_id: str, data: TaskUpdate, auth: AuthDep):
    """Update a task."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        update_data = data.model_dump(exclude_none=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        result = await sb.table("project_tasks").update(update_data).eq(
            "id", task_id
        ).eq("project_id", project_id).execute()

        task = result.data[0] if result.data else None
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"success": True, "data": task}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update task")


@router.delete("/{project_id}/tasks/{task_id}")
async def delete_task(project_id: str, task_id: str, auth: AuthDep):
    """Delete a task."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        await sb.table("project_tasks").delete().eq(
            "id", task_id
        ).eq("project_id", project_id).execute()
        return {"success": True, "message": "Task deleted"}
    except Exception as e:
        logger.error(f"Failed to delete task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete task")


# ============================================
# Project Members
# ============================================


@router.get("/{project_id}/members")
async def list_members(project_id: str, auth: AuthDep):
    """List all members of a project."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        result = (
            await sb.table("project_members")
            .select("*")
            .eq("project_id", project_id)
            .order("created_at")
            .execute()
        )

        # Enrich with user email from auth.users
        members = result.data or []
        if members:
            user_ids = [m["user_id"] for m in members]
            users_result = await sb.auth.admin.list_users()
            user_map = {
                str(u.id): u.email for u in users_result if str(u.id) in user_ids
            }
            for m in members:
                m["email"] = user_map.get(m["user_id"], "")

        return {"success": True, "data": members}
    except Exception as e:
        logger.error(f"Failed to list members for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list members")


@router.post("/{project_id}/members")
async def add_member(project_id: str, data: AddMemberRequest, auth: AuthDep):
    """Add a member to a project."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()

        member_data = {
            "project_id": project_id,
            "user_id": data.user_id,
            "role": data.role,
            "invited_by": auth.user_id,
        }
        result = await sb.table("project_members").insert(member_data).execute()
        member = result.data[0] if result.data else None
        if not member:
            raise HTTPException(status_code=500, detail="Failed to add member")

        # Get email
        try:
            user = await sb.auth.admin.get_user_by_id(data.user_id)
            member["email"] = user.user.email if user.user else ""
        except Exception:
            member["email"] = ""

        return {"success": True, "data": member}
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "duplicate key" in error_msg or "unique" in error_msg.lower():
            raise HTTPException(
                status_code=409, detail="User is already a member of this project"
            )
        logger.error(f"Failed to add member to project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add member")


@router.put("/{project_id}/members/{member_id}")
async def update_member_role(
    project_id: str, member_id: str, data: UpdateMemberRoleRequest, auth: AuthDep
):
    """Update a member's role."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        result = (
            await sb.table("project_members")
            .update({"role": data.role})
            .eq("id", member_id)
            .eq("project_id", project_id)
            .execute()
        )
        member = result.data[0] if result.data else None
        if not member:
            raise HTTPException(status_code=404, detail="Member not found")
        return {"success": True, "data": member}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update member {member_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update member role")


@router.delete("/{project_id}/members/{member_id}")
async def remove_member(project_id: str, member_id: str, auth: AuthDep):
    """Remove a member from a project."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        await sb.table("project_members").delete().eq("id", member_id).eq(
            "project_id", project_id
        ).execute()
        return {"success": True, "message": "Member removed"}
    except Exception as e:
        logger.error(f"Failed to remove member {member_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove member")
