# app/repositories/projects_repository.py

"""
Projects Repository

Data access layer for the MediaTrack project system, covering projects
and project files CRUD operations. Uses async Supabase client.
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class ProjectsRepository:
    """Projects and project files data access (async)"""

    TABLE_PROJECTS = "projects"
    TABLE_FILES = "project_files"
    TABLE_MEDIA = "parsed_media"
    TABLE_VERSIONS = "file_versions"
    TABLE_COMMENTS = "review_comments"
    TABLE_FOLDERS = "project_folders"
    TABLE_SHARES = "shares"
    TABLE_TASKS = "project_tasks"
    TABLE_MEMBERS = "project_members"
    TABLE_COLLECTIONS = "project_collections"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------ #
    # Projects CRUD
    # ------------------------------------------------------------------ #

    async def get_user_projects(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all projects accessible to a user, ordered by updated_at desc.

        Args:
            user_id: UUID of the authenticated user.

        Returns:
            List of project row dicts.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_PROJECTS)
                .select("*")
                .or_(f"owner_id.eq.{user_id}")
                .order("updated_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get projects for user {user_id}: {e}")
            return []

    async def get_project_by_id(self, project_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a single project by its UUID.

        Args:
            project_id: UUID of the project.

        Returns:
            Project row dict or None.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_PROJECTS)
                .select("*")
                .eq("id", project_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get project {project_id}: {e}")
            return None

    async def create_project(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new project.

        Args:
            data: Project data dict.

        Returns:
            Created project row dict.
        """
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_PROJECTS).insert(data).execute()
            logger.info(f"Created project: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create project: {e}")
            raise

    async def update_project(
        self, project_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update an existing project.

        Args:
            project_id: UUID of the project.
            data: Fields to update.

        Returns:
            Updated project row dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_PROJECTS)
                .update(data)
                .eq("id", project_id)
                .execute()
            )
            logger.info(f"Updated project {project_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update project {project_id}: {e}")
            raise

    async def delete_project(self, project_id: str) -> bool:
        """
        Delete a project by its UUID. Cascade deletes files.

        Args:
            project_id: UUID of the project.

        Returns:
            True if deleted successfully.
        """
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_PROJECTS)
                .delete()
                .eq("id", project_id)
                .execute()
            )
            logger.info(f"Deleted project {project_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete project {project_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # File count
    # ------------------------------------------------------------------ #

    async def get_project_file_count(self, project_id: str) -> int:
        """
        Get the number of non-trashed files in a project.

        Args:
            project_id: UUID of the project.

        Returns:
            File count integer.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FILES)
                .select("id", count="exact")
                .eq("project_id", project_id)
                .eq("is_trashed", False)
                .execute()
            )
            return result.count or 0
        except Exception as e:
            logger.error(f"Failed to get file count for project {project_id}: {e}")
            return 0

    # ------------------------------------------------------------------ #
    # Files CRUD
    # ------------------------------------------------------------------ #

    async def get_project_files(
        self, project_id: str, include_trashed: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get files in a project, ordered by created_at desc.

        Args:
            project_id: UUID of the project.
            include_trashed: Whether to include trashed files.

        Returns:
            List of file row dicts.
        """
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_FILES).select("*").eq("project_id", project_id)
            )
            if not include_trashed:
                query = query.eq("is_trashed", False)
            query = query.order("created_at", desc=True)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get files for project {project_id}: {e}")
            return []

    async def get_file_by_id(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a single file by its UUID.

        Args:
            file_id: UUID of the file.

        Returns:
            File row dict or None.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FILES)
                .select("*")
                .eq("id", file_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get file {file_id}: {e}")
            return None

    async def create_file(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new file record.

        Args:
            data: File data dict.

        Returns:
            Created file row dict.
        """
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_FILES).insert(data).execute()
            logger.info(
                f"Created file '{data.get('filename')}' in project {data.get('project_id')}"
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create file: {e}")
            raise

    async def update_file(self, file_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing file record.

        Args:
            file_id: UUID of the file.
            data: Fields to update.

        Returns:
            Updated file row dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FILES)
                .update(data)
                .eq("id", file_id)
                .execute()
            )
            logger.info(f"Updated file {file_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update file {file_id}: {e}")
            raise

    async def delete_file(self, file_id: str) -> bool:
        """
        Delete a file record by its UUID.

        Args:
            file_id: UUID of the file.

        Returns:
            True if deleted successfully.
        """
        try:
            client = await self._get_client()
            await client.table(self.TABLE_FILES).delete().eq("id", file_id).execute()
            logger.info(f"Deleted file {file_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete file {file_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Media metadata (for link-media feature)
    # ------------------------------------------------------------------ #

    async def get_media_metadata(self, media_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch media metadata from the parsed_media table for linking.

        Args:
            media_id: UUID of the media.

        Returns:
            Media row dict or None.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_MEDIA)
                .select("*")
                .eq("id", media_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get media metadata {media_id}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # File versions
    # ------------------------------------------------------------------ #

    async def get_file_versions(self, file_id: str) -> List[Dict[str, Any]]:
        """Get all versions of a file, ordered by version_number DESC."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("*")
                .eq("file_id", file_id)
                .order("version_number", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get versions for file {file_id}: {e}")
            return []

    async def get_next_version_number(self, file_id: str) -> int:
        """Get the next version number for a file (max + 1)."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VERSIONS)
                .select("version_number")
                .eq("file_id", file_id)
                .order("version_number", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                return result.data[0]["version_number"] + 1
            return 1
        except Exception as e:
            logger.error(f"Failed to get next version for file {file_id}: {e}")
            return 1

    async def create_version(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new file version record."""
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_VERSIONS).insert(data).execute()
            logger.info(
                f"Created version {data.get('version_number')} for file {data.get('file_id')}"
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create version: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Review comments
    # ------------------------------------------------------------------ #

    async def get_comments_for_file(
        self, file_id: str, version_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get comments for a file, optionally filtered by version.
        Ordered by timestamp_seconds ASC (nulls last), then created_at ASC.
        """
        try:
            client = await self._get_client()
            query = client.table(self.TABLE_COMMENTS).select("*").eq("file_id", file_id)
            if version_id:
                query = query.eq("version_id", version_id)
            query = query.order("timestamp_seconds", desc=False, nullsfirst=False)
            query = query.order("created_at", desc=False)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get comments for file {file_id}: {e}")
            return []

    async def create_comment(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new review comment."""
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_COMMENTS).insert(data).execute()
            logger.info(f"Created comment on file {data.get('file_id')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create comment: {e}")
            raise

    async def get_comment_by_id(self, comment_id: str) -> Optional[Dict[str, Any]]:
        """Get a single comment by its UUID."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_COMMENTS)
                .select("*")
                .eq("id", comment_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get comment {comment_id}: {e}")
            return None

    async def delete_comment(self, comment_id: str) -> bool:
        """Delete a comment by its UUID."""
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_COMMENTS)
                .delete()
                .eq("id", comment_id)
                .execute()
            )
            logger.info(f"Deleted comment {comment_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete comment {comment_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Review status
    # ------------------------------------------------------------------ #

    async def update_review_status(
        self, file_id: str, status: Optional[str]
    ) -> Dict[str, Any]:
        """Update the review status of a project file."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FILES)
                .update({"review_status": status})
                .eq("id", file_id)
                .execute()
            )
            logger.info(f"Updated review status for file {file_id} to {status}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update review status for file {file_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Folders
    # ------------------------------------------------------------------ #

    async def get_folders(
        self, project_id: str, parent_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get folders in a project, optionally filtered by parent."""
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_FOLDERS)
                .select("*")
                .eq("project_id", project_id)
            )
            if parent_id:
                query = query.eq("parent_id", parent_id)
            else:
                query = query.is_("parent_id", "null")
            result = await query.order("name").execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get folders for project {project_id}: {e}")
            return []

    async def get_folder(
        self, folder_id: str, project_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a single folder by ID, scoped to project."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FOLDERS)
                .select("*")
                .eq("id", folder_id)
                .eq("project_id", project_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get folder {folder_id}: {e}")
            return None

    async def create_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new folder."""
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_FOLDERS).insert(data).execute()
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    async def update_folder(
        self, folder_id: str, project_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a folder, scoped to project."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FOLDERS)
                .update(data)
                .eq("id", folder_id)
                .eq("project_id", project_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to update folder {folder_id}: {e}")
            raise

    async def delete_folder_record(self, folder_id: str, project_id: str) -> bool:
        """Delete a folder record."""
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_FOLDERS)
                .delete()
                .eq("id", folder_id)
                .eq("project_id", project_id)
                .execute()
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete folder {folder_id}: {e}")
            raise

    async def reparent_folder_children(
        self, folder_id: str, new_parent_id: Optional[str]
    ) -> None:
        """Move files and sub-folders to a new parent when deleting a folder."""
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_FILES)
                .update({"folder_id": new_parent_id})
                .eq("folder_id", folder_id)
                .execute()
            )
            await (
                client.table(self.TABLE_FOLDERS)
                .update({"parent_id": new_parent_id})
                .eq("parent_id", folder_id)
                .execute()
            )
        except Exception as e:
            logger.error(f"Failed to reparent children of folder {folder_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Shares
    # ------------------------------------------------------------------ #

    async def get_shares_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all shares for files in a project."""
        try:
            client = await self._get_client()
            files_result = (
                await client.table(self.TABLE_FILES)
                .select("id")
                .eq("project_id", project_id)
                .execute()
            )
            file_ids = [f["id"] for f in (files_result.data or [])]
            if not file_ids:
                return []
            result = (
                await client.table(self.TABLE_SHARES)
                .select("*")
                .in_("project_file_id", file_ids)
                .order("created_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get shares for project {project_id}: {e}")
            return []

    async def get_file_in_project(
        self, file_id: str, project_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a file verifying it belongs to the project (id + filename only)."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_FILES)
                .select("id, filename")
                .eq("id", file_id)
                .eq("project_id", project_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get file {file_id} in project {project_id}: {e}")
            return None

    async def create_share(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new share record."""
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_SHARES).insert(data).execute()
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create share: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Tasks
    # ------------------------------------------------------------------ #

    async def get_tasks(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all tasks for a project, ordered by sort_order."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_TASKS)
                .select("*")
                .eq("project_id", project_id)
                .order("sort_order")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get tasks for project {project_id}: {e}")
            return []

    async def create_task(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new task."""
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_TASKS).insert(data).execute()
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create task: {e}")
            raise

    async def update_task(
        self, task_id: str, project_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a task, scoped to project."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_TASKS)
                .update(data)
                .eq("id", task_id)
                .eq("project_id", project_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to update task {task_id}: {e}")
            raise

    async def delete_task(self, task_id: str, project_id: str) -> bool:
        """Delete a task, scoped to project."""
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_TASKS)
                .delete()
                .eq("id", task_id)
                .eq("project_id", project_id)
                .execute()
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete task {task_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Members
    # ------------------------------------------------------------------ #

    async def get_members(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all members of a project."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_MEMBERS)
                .select("*")
                .eq("project_id", project_id)
                .order("created_at")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get members for project {project_id}: {e}")
            return []

    async def create_member(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new member record."""
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_MEMBERS).insert(data).execute()
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create member: {e}")
            raise

    async def update_member(
        self, member_id: str, project_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a member, scoped to project."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_MEMBERS)
                .update(data)
                .eq("id", member_id)
                .eq("project_id", project_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to update member {member_id}: {e}")
            raise

    async def delete_member(self, member_id: str, project_id: str) -> bool:
        """Delete a member, scoped to project."""
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_MEMBERS)
                .delete()
                .eq("id", member_id)
                .eq("project_id", project_id)
                .execute()
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete member {member_id}: {e}")
            raise

    async def enrich_members_with_email(
        self, members: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Enrich member records with user email from auth."""
        if not members:
            return members
        try:
            client = await self._get_client()
            user_ids = [m["user_id"] for m in members]
            users_result = await client.auth.admin.list_users()
            user_map = {
                str(u.id): u.email for u in users_result if str(u.id) in user_ids
            }
            for m in members:
                m["email"] = user_map.get(m["user_id"], "")
            return members
        except Exception as e:
            logger.warning(f"Failed to enrich members with email: {e}")
            return members

    async def get_user_email(self, user_id: str) -> str:
        """Get a user's email from auth."""
        try:
            client = await self._get_client()
            user = await client.auth.admin.get_user_by_id(user_id)
            return user.user.email if user.user else ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    # Collections
    # ------------------------------------------------------------------ #

    async def get_collections(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all collections for a project."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_COLLECTIONS)
                .select("*")
                .eq("project_id", project_id)
                .order("created_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get collections for project {project_id}: {e}")
            return []

    async def create_collection(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new collection."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_COLLECTIONS).insert(data).execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create collection: {e}")
            raise

    async def delete_collection(self, collection_id: str, project_id: str) -> bool:
        """Delete a collection, scoped to project."""
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_COLLECTIONS)
                .delete()
                .eq("id", collection_id)
                .eq("project_id", project_id)
                .execute()
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete collection {collection_id}: {e}")
            raise
