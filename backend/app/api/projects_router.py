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
from app.schemas.projects import (
    AddMemberRequest,
    CreateCollectionRequest,
    CreateCommentRequest,
    CreateFolderRequest,
    CreateShareRequest,
    LinkMediaRequest,
    MoveFileRequest,
    ProjectCreate,
    ProjectFileUpdate,
    ProjectUpdate,
    RenameFolderRequest,
    ReviewStatusUpdate,
    TaskCreateRequest,
    TaskUpdateRequest,
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
    team_id: Optional[str] = Query(
        None, description="Filter by team ID (null = personal)"
    ),
):
    """
    List all projects accessible to the current user.

    - **project_type**: Optional filter (internal, external, personal).
    - **starred**: Optional filter for starred projects.
    - **team_id**: Optional team filter. If provided, returns team projects.
      If omitted, returns all user projects (backward-compatible).
    """
    try:
        svc = ProjectsService()
        projects = await svc.get_projects_with_counts(auth.user_id, team_id=team_id)

        if project_type:
            projects = [p for p in projects if p.get("project_type") == project_type]
        if starred is not None:
            projects = [p for p in projects if p.get("is_starred") == starred]

        return {"success": True, "data": projects}
    except Exception as e:
        logger.exception(f"Failed to list projects: {e}")
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
        logger.exception(f"Failed to create project: {e}")
        raise HTTPException(status_code=500, detail="Failed to create project")


@router.get("/{project_id}")
async def get_project(project_id: str, auth: AuthDep):
    """Get a single project by ID with file count."""
    try:
        svc = ProjectsService()
        project = await svc.get_project(project_id)
        return {"success": True, "data": project}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to get project {project_id}: {e}")
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
        logger.exception(f"Failed to update project {project_id}: {e}")
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
        logger.exception(f"Failed to delete project {project_id}: {e}")
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
        svc = ProjectsService()
        files = await svc.get_project_files(project_id, include_trashed, folder_id)
        return {"success": True, "data": files}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to list files for project {project_id}: {e}")
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
        logger.exception(f"Failed to upload file to project {project_id}: {e}")
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
        logger.exception(f"Failed to link media to project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to link media")


@router.get("/{project_id}/files/{file_id}")
async def get_file_info(project_id: str, file_id: str, auth: AuthDep):
    """Get detailed info for a single file."""
    try:
        svc = ProjectsService()
        file_record = await svc.get_file_info(project_id, file_id)
        return {"success": True, "data": file_record}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to get file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get file info")


@router.put("/{project_id}/files/{file_id}")
async def update_file(
    project_id: str, file_id: str, data: ProjectFileUpdate, auth: AuthDep
):
    """Update file metadata (rename, add notes, trash/restore)."""
    try:
        svc = ProjectsService()
        result = await svc.update_file(
            project_id, file_id, data.model_dump(exclude_none=True)
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to update file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update file")


@router.put("/{project_id}/files/{file_id}/restore")
async def restore_file(project_id: str, file_id: str, auth: AuthDep):
    """Restore a trashed file."""
    try:
        svc = ProjectsService()
        result = await svc.restore_file(project_id, file_id)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to restore file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to restore file")


@router.get("/{project_id}/shares")
async def list_project_shares(project_id: str, auth: AuthDep):
    """List all shares for files in this project."""
    try:
        svc = ProjectsService()
        shares = await svc.list_shares(project_id)
        return {"success": True, "data": shares}
    except Exception as e:
        logger.exception(f"Failed to list shares for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list project shares")


@router.post("/{project_id}/shares")
async def create_share(project_id: str, data: CreateShareRequest, auth: AuthDep):
    """Create a share link for a project file."""
    try:
        svc = ProjectsService()
        share = await svc.create_share(
            project_id, data.model_dump(exclude_none=True), auth.user_id
        )
        return {"success": True, "data": share}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to create share for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create share")


# ============================================
# Folder endpoints
# ============================================


@router.get("/{project_id}/folders")
async def list_folders(
    project_id: str,
    auth: AuthDep,
    parent_id: Optional[str] = Query(
        None, description="Parent folder ID, null for root"
    ),
):
    """List folders in a project."""
    try:
        svc = ProjectsService()
        folders = await svc.list_folders(project_id, parent_id)
        return {"success": True, "data": folders}
    except Exception as e:
        logger.exception(f"Failed to list folders for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list folders")


@router.post("/{project_id}/folders")
async def create_folder(project_id: str, data: CreateFolderRequest, auth: AuthDep):
    """Create a new folder in a project."""
    try:
        svc = ProjectsService()
        folder = await svc.create_folder(
            project_id, data.name, auth.user_id, data.parent_id
        )
        return {"success": True, "data": folder}
    except Exception as e:
        logger.exception(f"Failed to create folder in project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create folder")


@router.put("/{project_id}/folders/{folder_id}")
async def rename_folder(
    project_id: str, folder_id: str, data: RenameFolderRequest, auth: AuthDep
):
    """Rename a folder."""
    try:
        svc = ProjectsService()
        folder = await svc.rename_folder(project_id, folder_id, data.name)
        return {"success": True, "data": folder}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to rename folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to rename folder")


@router.delete("/{project_id}/folders/{folder_id}")
async def delete_folder(project_id: str, folder_id: str, auth: AuthDep):
    """Delete a folder (files inside are moved to parent)."""
    try:
        svc = ProjectsService()
        await svc.delete_folder(project_id, folder_id)
        return {"success": True, "message": "Folder deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to delete folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete folder")


@router.put("/{project_id}/files/{file_id}/move")
async def move_file(
    project_id: str, file_id: str, data: MoveFileRequest, auth: AuthDep
):
    """Move a file to a different folder."""
    try:
        svc = ProjectsService()
        result = await svc.move_file(project_id, file_id, data.folder_id)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to move file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to move file")


@router.delete("/{project_id}/files/{file_id}")
async def delete_file(project_id: str, file_id: str, auth: AuthDep):
    """Permanently delete a file record."""
    try:
        svc = ProjectsService()
        await svc.delete_file(project_id, file_id)
        return {"success": True, "message": "File deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to delete file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete file")


# ============================================
# Version endpoints
# ============================================


@router.get("/{project_id}/files/{file_id}/versions")
async def list_versions(project_id: str, file_id: str, auth: AuthDep):
    """List all versions of a file."""
    try:
        svc = ProjectsService()
        versions = await svc.get_file_versions(project_id, file_id)
        return {"success": True, "data": versions}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to list versions for file {file_id}: {e}")
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
        logger.exception(f"Failed to upload version for file {file_id}: {e}")
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
        svc = ProjectsService()
        comments = await svc.get_file_comments(project_id, file_id, version_id)
        return {"success": True, "data": comments}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to list comments for file {file_id}: {e}")
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
        svc = ProjectsService()
        result = await svc.add_comment(
            project_id=project_id,
            file_id=file_id,
            author_id=auth.user_id,
            content=data.content,
            timestamp_seconds=data.timestamp_seconds,
            version_id=data.version_id,
            drawing_data=data.drawing_data,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to add comment to file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add comment")


@router.delete("/{project_id}/files/{file_id}/comments/{comment_id}")
async def delete_comment(project_id: str, file_id: str, comment_id: str, auth: AuthDep):
    """Delete a comment. Only the author can delete their own comment."""
    try:
        svc = ProjectsService()
        await svc.delete_comment(comment_id, auth.user_id)
        return {"success": True, "message": "Comment deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to delete comment {comment_id}: {e}")
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
        logger.exception(f"Failed to update review status for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update review status")


# ============================================
# Task endpoints (Kanban board)
# ============================================


@router.get("/{project_id}/tasks")
async def list_tasks(project_id: str, auth: AuthDep):
    """List all tasks for a project."""
    try:
        svc = ProjectsService()
        tasks = await svc.list_tasks(project_id)
        return {"success": True, "data": tasks}
    except Exception as e:
        logger.exception(f"Failed to list tasks for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list tasks")


@router.post("/{project_id}/tasks")
async def create_task(project_id: str, data: TaskCreateRequest, auth: AuthDep):
    """Create a new task in a project."""
    try:
        svc = ProjectsService()
        task = await svc.create_task(
            project_id, data.model_dump(exclude_none=True), auth.user_id
        )
        return {"success": True, "data": task}
    except Exception as e:
        logger.exception(f"Failed to create task in project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create task")


@router.put("/{project_id}/tasks/{task_id}")
async def update_task(
    project_id: str, task_id: str, data: TaskUpdateRequest, auth: AuthDep
):
    """Update a task."""
    try:
        svc = ProjectsService()
        task = await svc.update_task(
            project_id, task_id, data.model_dump(exclude_none=True)
        )
        return {"success": True, "data": task}
    except ValueError as e:
        status = 400 if "No fields" in str(e) else 404
        raise HTTPException(status_code=status, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to update task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update task")


@router.delete("/{project_id}/tasks/{task_id}")
async def delete_task(project_id: str, task_id: str, auth: AuthDep):
    """Delete a task."""
    try:
        svc = ProjectsService()
        await svc.delete_task(project_id, task_id)
        return {"success": True, "message": "Task deleted"}
    except Exception as e:
        logger.exception(f"Failed to delete task {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete task")


# ============================================
# Project Members
# ============================================


@router.get("/{project_id}/members")
async def list_members(project_id: str, auth: AuthDep):
    """List all members of a project."""
    try:
        svc = ProjectsService()
        members = await svc.list_members(project_id)
        return {"success": True, "data": members}
    except Exception as e:
        logger.exception(f"Failed to list members for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list members")


@router.post("/{project_id}/members")
async def add_member(project_id: str, data: AddMemberRequest, auth: AuthDep):
    """Add a member to a project."""
    try:
        svc = ProjectsService()
        member = await svc.add_member(project_id, data.user_id, data.role, auth.user_id)
        return {"success": True, "data": member}
    except Exception as e:
        error_msg = str(e)
        if "duplicate key" in error_msg or "unique" in error_msg.lower():
            raise HTTPException(
                status_code=409, detail="User is already a member of this project"
            )
        logger.exception(f"Failed to add member to project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add member")


@router.put("/{project_id}/members/{member_id}")
async def update_member_role(
    project_id: str, member_id: str, data: UpdateMemberRoleRequest, auth: AuthDep
):
    """Update a member's role."""
    try:
        svc = ProjectsService()
        member = await svc.update_member_role(project_id, member_id, data.role)
        return {"success": True, "data": member}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"Failed to update member {member_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update member role")


@router.delete("/{project_id}/members/{member_id}")
async def remove_member(project_id: str, member_id: str, auth: AuthDep):
    """Remove a member from a project."""
    try:
        svc = ProjectsService()
        await svc.remove_member(project_id, member_id)
        return {"success": True, "message": "Member removed"}
    except Exception as e:
        logger.exception(f"Failed to remove member {member_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove member")


# ============================================
# Collection endpoints
# ============================================


@router.get("/{project_id}/collections")
async def list_collections(project_id: str, auth: AuthDep):
    """List all collection links for a project."""
    try:
        svc = ProjectsService()
        collections = await svc.list_collections(project_id)
        return {"success": True, "data": collections}
    except Exception as e:
        logger.exception(f"Failed to list collections for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list collections")


@router.post("/{project_id}/collections")
async def create_collection(
    project_id: str, data: CreateCollectionRequest, auth: AuthDep
):
    """Create a collection link for external file uploads."""
    try:
        svc = ProjectsService()
        collection = await svc.create_collection(
            project_id, data.model_dump(exclude_none=True), auth.user_id
        )
        return {"success": True, "data": collection}
    except Exception as e:
        logger.exception(f"Failed to create collection for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create collection")


@router.delete("/{project_id}/collections/{collection_id}")
async def delete_collection(project_id: str, collection_id: str, auth: AuthDep):
    """Delete a collection link."""
    try:
        svc = ProjectsService()
        await svc.delete_collection(project_id, collection_id)
        return {"success": True, "message": "Collection deleted"}
    except Exception as e:
        logger.exception(f"Failed to delete collection {collection_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete collection")
