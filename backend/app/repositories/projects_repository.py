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
    TABLE_VIDEOS = "videos"
    TABLE_VERSIONS = "file_versions"
    TABLE_COMMENTS = "review_comments"

    def __init__(self):
        self._client = None

    async def _get_client(self):
        """Get async Supabase admin client"""
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client

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
            result = (
                await client.table(self.TABLE_PROJECTS)
                .insert(data)
                .execute()
            )
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
            logger.error(
                f"Failed to get file count for project {project_id}: {e}"
            )
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
                client.table(self.TABLE_FILES)
                .select("*")
                .eq("project_id", project_id)
            )
            if not include_trashed:
                query = query.eq("is_trashed", False)
            query = query.order("created_at", desc=True)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(
                f"Failed to get files for project {project_id}: {e}"
            )
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
            result = (
                await client.table(self.TABLE_FILES)
                .insert(data)
                .execute()
            )
            logger.info(
                f"Created file '{data.get('filename')}' in project {data.get('project_id')}"
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create file: {e}")
            raise

    async def update_file(
        self, file_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
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
            await (
                client.table(self.TABLE_FILES)
                .delete()
                .eq("id", file_id)
                .execute()
            )
            logger.info(f"Deleted file {file_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete file {file_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Video metadata (for link-video feature)
    # ------------------------------------------------------------------ #

    async def get_video_metadata(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch video metadata from the videos table for linking.

        Args:
            video_id: UUID of the video.

        Returns:
            Video row dict or None.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_VIDEOS)
                .select("*")
                .eq("id", video_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get video metadata {video_id}: {e}")
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
            result = (
                await client.table(self.TABLE_VERSIONS)
                .insert(data)
                .execute()
            )
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
            query = (
                client.table(self.TABLE_COMMENTS)
                .select("*")
                .eq("file_id", file_id)
            )
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
            result = (
                await client.table(self.TABLE_COMMENTS)
                .insert(data)
                .execute()
            )
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
