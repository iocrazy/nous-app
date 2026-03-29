# app/services/projects_service.py

"""
Projects Service

Business logic for the MediaTrack project system: project CRUD,
file uploads with metadata extraction (ffprobe), and video linking.
"""

import asyncio
import json
import mimetypes
import re

import aiofiles
from pathlib import Path
from typing import Optional

from loguru import logger

from app.core.config import settings
from app.repositories.projects_repository import ProjectsRepository


class ProjectsService:
    """MediaTrack projects business logic"""

    def __init__(self):
        self.repo = ProjectsRepository()

    # ------------------------------------------------------------------ #
    # Projects
    # ------------------------------------------------------------------ #

    async def get_projects_with_counts(self, user_id: str) -> list:
        """
        Get all projects for a user with file counts attached.

        Args:
            user_id: UUID of the authenticated user.

        Returns:
            List of project dicts, each with a ``file_count`` key.
        """
        import asyncio

        projects = await self.repo.get_user_projects(user_id)
        if not projects:
            return []

        counts = await asyncio.gather(
            *(self.repo.get_project_file_count(p["id"]) for p in projects)
        )
        return [
            {**p, "file_count": count}
            for p, count in zip(projects, counts)
        ]

    async def create_project(self, user_id: str, data: dict) -> dict:
        """
        Create a new project owned by the given user.

        Args:
            user_id: UUID of the authenticated user.
            data: Project creation data.

        Returns:
            Created project dict.
        """
        data["owner_id"] = user_id
        project = await self.repo.create_project(data)

        # Generate display code if project belongs to a team
        team_id = project.get("team_id")
        if team_id:
            try:
                from app.services.display_code_service import generate_display_code

                display_code = await generate_display_code(int(team_id), "P")
                project = await self.repo.update_project(
                    project["id"], {"display_code": display_code}
                )
            except Exception as exc:
                logger.warning(f"Failed to generate display_code: {exc}")

        return project

    async def update_project(self, project_id: str, user_id: str, data: dict) -> dict:
        """
        Update a project after verifying ownership.

        Args:
            project_id: UUID of the project.
            user_id: UUID of the authenticated user.
            data: Fields to update.

        Returns:
            Updated project dict.

        Raises:
            ValueError: If project not found or user is not the owner.
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")
        if project["owner_id"] != user_id:
            raise PermissionError("Only the project owner can update this project")
        return await self.repo.update_project(project_id, data)

    async def delete_project(self, project_id: str, user_id: str) -> bool:
        """
        Delete a project after verifying ownership.

        Args:
            project_id: UUID of the project.
            user_id: UUID of the authenticated user.

        Returns:
            True if deleted.

        Raises:
            ValueError: If project not found.
            PermissionError: If user is not the owner.
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")
        if project["owner_id"] != user_id:
            raise PermissionError("Only the project owner can delete this project")
        return await self.repo.delete_project(project_id)

    # ------------------------------------------------------------------ #
    # File uploads
    # ------------------------------------------------------------------ #

    async def upload_file(
        self,
        project_id: str,
        user_id: str,
        file,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Upload a file to a project, extracting metadata for videos.

        Steps:
        1. Validate project exists
        2. Save file to disk under DOWNLOAD_PATH/mediatrack/{project_id}/
        3. Classify file type from MIME
        4. Extract video metadata via ffprobe (if applicable)
        5. Create DB record

        Args:
            project_id: UUID of the project.
            user_id: UUID of the authenticated user.
            file: FastAPI UploadFile instance.
            notes: Optional notes for the file.

        Returns:
            Created file dict.

        Raises:
            ValueError: If project not found.
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")

        # Save to disk
        safe_name = self._sanitize_filename(file.filename)
        save_dir = Path(settings.DOWNLOAD_PATH) / "mediatrack" / project_id
        save_dir.mkdir(parents=True, exist_ok=True)

        # Handle duplicate filenames
        target = save_dir / safe_name
        counter = 1
        stem = target.stem
        suffix = target.suffix
        while target.exists():
            target = save_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        content = await file.read()
        async with aiofiles.open(target, "wb") as f:
            await f.write(content)

        # Classify
        mime = file.content_type or mimetypes.guess_type(safe_name)[0] or ""
        file_type = self._classify_file_type(mime)

        # Extract video metadata
        metadata = {}
        if file_type == "video":
            metadata = await self._extract_video_metadata(str(target))

        # Create DB record
        relative_path = f"mediatrack/{project_id}/{target.name}"
        file_data = {
            "project_id": project_id,
            "filename": target.name,
            "file_type": file_type,
            "mime_type": mime,
            "file_path": relative_path,
            "file_size_bytes": len(content),
            "uploaded_by": user_id,
            "notes": notes,
            **metadata,
        }
        created_file = await self.repo.create_file(file_data)

        # Create V1 version record
        version_data = {
            "file_id": created_file["id"],
            "version_number": 1,
            "filename": target.name,
            "file_path": relative_path,
            "file_size_bytes": len(content),
            "mime_type": mime,
            "uploaded_by": user_id,
            "notes": notes,
            **metadata,
        }
        await self.repo.create_version(version_data)

        return created_file

    # ------------------------------------------------------------------ #
    # Link media
    # ------------------------------------------------------------------ #

    async def link_media(self, project_id: str, media_id: str, user_id: str) -> dict:
        """
        Link an existing media from the parsed_media table to a project.

        Args:
            project_id: UUID of the project.
            media_id: UUID of the media.
            user_id: UUID of the authenticated user.

        Returns:
            Created file dict.

        Raises:
            ValueError: If project or media not found.
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")

        media = await self.repo.get_media_metadata(media_id)
        if not media:
            raise ValueError("Media not found")

        file_data = {
            "project_id": project_id,
            "filename": media.get("title", "Untitled") or "Untitled",
            "file_type": "video",
            "mime_type": "video/mp4",
            "file_path": media.get("download_path"),
            "file_size_bytes": media.get("datasize_bytes"),
            "media_id": media_id,
            "duration_seconds": (
                int(media["duration"]) if media.get("duration") else None
            ),
            "resolution": media.get("resolution"),
            "uploaded_by": user_id,
            "cover_image_path": media.get("cover_download_path"),
        }
        return await self.repo.create_file(file_data)

    # ------------------------------------------------------------------ #
    # File versions
    # ------------------------------------------------------------------ #

    async def upload_new_version(
        self,
        project_id: str,
        file_id: str,
        user_id: str,
        file,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Upload a new version of an existing file.

        Steps:
        1. Validate project + file
        2. Get next version number
        3. Save file to disk
        4. Extract metadata if video
        5. Create version record
        6. Update current_version + metadata on project_files
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")

        file_record = await self.repo.get_file_by_id(file_id)
        if not file_record:
            raise ValueError("File not found")
        if file_record.get("project_id") != project_id:
            raise ValueError("File not found in this project")

        # Get next version number
        next_version = await self.repo.get_next_version_number(file_id)

        # Save to disk
        safe_name = self._sanitize_filename(file.filename)
        save_dir = (
            Path(settings.DOWNLOAD_PATH)
            / "mediatrack"
            / project_id
            / "versions"
            / file_id
        )
        save_dir.mkdir(parents=True, exist_ok=True)

        target = save_dir / f"v{next_version}_{safe_name}"
        content = await file.read()
        async with aiofiles.open(target, "wb") as f:
            await f.write(content)

        # Classify and extract metadata
        mime = file.content_type or mimetypes.guess_type(safe_name)[0] or ""
        file_type = self._classify_file_type(mime)
        metadata = {}
        if file_type == "video":
            metadata = await self._extract_video_metadata(str(target))

        # Create version record
        relative_path = (
            f"mediatrack/{project_id}/versions/{file_id}/v{next_version}_{safe_name}"
        )
        version_data = {
            "file_id": file_id,
            "version_number": next_version,
            "filename": safe_name,
            "file_path": relative_path,
            "file_size_bytes": len(content),
            "mime_type": mime,
            "uploaded_by": user_id,
            "notes": notes,
            **metadata,
        }
        version = await self.repo.create_version(version_data)

        # Update project_files with current version and latest metadata
        update_data = {
            "current_version": next_version,
            "file_path": relative_path,
            "file_size_bytes": len(content),
            "mime_type": mime,
            "filename": safe_name,
            **metadata,
        }
        await self.repo.update_file(file_id, update_data)

        return version

    # ------------------------------------------------------------------ #
    # Review comments
    # ------------------------------------------------------------------ #

    async def add_comment(
        self,
        project_id: str,
        file_id: str,
        author_id: str,
        content: str,
        timestamp_seconds: Optional[float] = None,
        version_id: Optional[str] = None,
        drawing_data: Optional[dict] = None,
    ) -> dict:
        """Add a review comment to a file after verifying project ownership."""
        await self._verify_file_in_project(project_id, file_id)
        comment_data = {
            "file_id": file_id,
            "author_id": author_id,
            "content": content,
        }
        if timestamp_seconds is not None:
            comment_data["timestamp_seconds"] = timestamp_seconds
        if version_id:
            comment_data["version_id"] = version_id
        if drawing_data:
            comment_data["drawing_data"] = drawing_data
        return await self.repo.create_comment(comment_data)

    # ------------------------------------------------------------------ #
    # File read / update / delete
    # ------------------------------------------------------------------ #

    async def _verify_file_in_project(self, project_id: str, file_id: str) -> dict:
        """Verify a file exists and belongs to the project. Returns the file."""
        file_record = await self.repo.get_file_by_id(file_id)
        if not file_record:
            raise ValueError("File not found")
        if file_record.get("project_id") != project_id:
            raise ValueError("File not found in this project")
        return file_record

    async def get_project(self, project_id: str) -> dict:
        """Get a single project with file count."""
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")
        project["file_count"] = await self.repo.get_project_file_count(project_id)
        return project

    async def get_project_files(
        self,
        project_id: str,
        include_trashed: bool = False,
        folder_id: Optional[str] = None,
    ) -> list:
        """List files in a project, optionally filtered by folder."""
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")
        files = await self.repo.get_project_files(
            project_id, include_trashed=include_trashed
        )
        if not include_trashed:
            if folder_id is not None:
                files = [f for f in files if f.get("folder_id") == folder_id]
            else:
                files = [f for f in files if not f.get("folder_id")]
        return files

    async def get_file_info(self, project_id: str, file_id: str) -> dict:
        """Get detailed info for a single file."""
        return await self._verify_file_in_project(project_id, file_id)

    async def update_file(self, project_id: str, file_id: str, data: dict) -> dict:
        """Update file metadata (rename, notes, trash/restore)."""
        await self._verify_file_in_project(project_id, file_id)
        if data.get("is_trashed") is True:
            from datetime import datetime, timezone

            data["trashed_at"] = datetime.now(timezone.utc).isoformat()
        elif data.get("is_trashed") is False:
            data["trashed_at"] = None
        return await self.repo.update_file(file_id, data)

    async def restore_file(self, project_id: str, file_id: str) -> dict:
        """Restore a trashed file."""
        await self._verify_file_in_project(project_id, file_id)
        return await self.repo.update_file(
            file_id, {"is_trashed": False, "trashed_at": None}
        )

    async def move_file(
        self, project_id: str, file_id: str, folder_id: Optional[str]
    ) -> dict:
        """Move a file to a different folder."""
        await self._verify_file_in_project(project_id, file_id)
        return await self.repo.update_file(file_id, {"folder_id": folder_id})

    async def delete_file(self, project_id: str, file_id: str) -> bool:
        """Permanently delete a file record."""
        await self._verify_file_in_project(project_id, file_id)
        return await self.repo.delete_file(file_id)

    async def get_file_versions(self, project_id: str, file_id: str) -> list:
        """List all versions of a file."""
        await self._verify_file_in_project(project_id, file_id)
        return await self.repo.get_file_versions(file_id)

    async def get_file_comments(
        self,
        project_id: str,
        file_id: str,
        version_id: Optional[str] = None,
    ) -> list:
        """List comments on a file."""
        await self._verify_file_in_project(project_id, file_id)
        return await self.repo.get_comments_for_file(file_id, version_id=version_id)

    async def delete_comment(self, comment_id: str, user_id: str) -> bool:
        """Delete a comment. Only the author can delete."""
        comment = await self.repo.get_comment_by_id(comment_id)
        if not comment:
            raise ValueError("Comment not found")
        if comment.get("author_id") != user_id:
            raise PermissionError("Can only delete your own comments")
        return await self.repo.delete_comment(comment_id)

    # ------------------------------------------------------------------ #
    # Folders
    # ------------------------------------------------------------------ #

    async def list_folders(
        self, project_id: str, parent_id: Optional[str] = None
    ) -> list:
        """List folders in a project."""
        return await self.repo.get_folders(project_id, parent_id)

    async def create_folder(
        self,
        project_id: str,
        name: str,
        user_id: str,
        parent_id: Optional[str] = None,
    ) -> dict:
        """Create a new folder in a project."""
        data: dict = {
            "project_id": project_id,
            "name": name,
            "created_by": user_id,
        }
        if parent_id:
            data["parent_id"] = parent_id
        return await self.repo.create_folder(data)

    async def rename_folder(
        self, project_id: str, folder_id: str, name: str
    ) -> dict:
        """Rename a folder."""
        result = await self.repo.update_folder(folder_id, project_id, {"name": name})
        if not result:
            raise ValueError("Folder not found")
        return result

    async def delete_folder(self, project_id: str, folder_id: str) -> bool:
        """Delete a folder, reparenting its children to the parent folder."""
        folder = await self.repo.get_folder(folder_id, project_id)
        if not folder:
            raise ValueError("Folder not found")
        parent_id = folder.get("parent_id")
        await self.repo.reparent_folder_children(folder_id, parent_id)
        return await self.repo.delete_folder_record(folder_id, project_id)

    # ------------------------------------------------------------------ #
    # Shares
    # ------------------------------------------------------------------ #

    async def list_shares(self, project_id: str) -> list:
        """List all shares for files in a project."""
        return await self.repo.get_shares_by_project(project_id)

    async def create_share(
        self, project_id: str, data: dict, user_id: str
    ) -> dict:
        """Create a share link for a project file."""
        import secrets
        from datetime import datetime, timedelta, timezone

        file_record = await self.repo.get_file_in_project(
            data["file_id"], project_id
        )
        if not file_record:
            raise ValueError("File not found in project")

        share_code = secrets.token_urlsafe(8)[:12]
        share_name = data.get("share_name") or file_record["filename"]

        share_data: dict = {
            "project_file_id": data["file_id"],
            "share_type": data.get("share_type", "link"),
            "shared_by": user_id,
            "share_name": share_name,
            "share_code": share_code,
            "password": data.get("password"),
            "allow_download": data.get("allow_download", True),
            "status": "active",
        }
        if data.get("expires_hours"):
            share_data["expires_at"] = (
                datetime.now(timezone.utc) + timedelta(hours=data["expires_hours"])
            ).isoformat()

        return await self.repo.create_share(share_data)

    # ------------------------------------------------------------------ #
    # Tasks
    # ------------------------------------------------------------------ #

    async def list_tasks(self, project_id: str) -> list:
        """List all tasks for a project."""
        return await self.repo.get_tasks(project_id)

    async def create_task(
        self, project_id: str, data: dict, user_id: str
    ) -> dict:
        """Create a new task in a project."""
        insert_data: dict = {
            "project_id": project_id,
            "title": data["title"],
            "created_by": user_id,
        }
        for field in ("description", "task_type", "assignee_id", "due_date", "status"):
            if data.get(field) is not None:
                insert_data[field] = data[field]
        return await self.repo.create_task(insert_data)

    async def update_task(
        self, project_id: str, task_id: str, data: dict
    ) -> dict:
        """Update a task."""
        if not data:
            raise ValueError("No fields to update")
        result = await self.repo.update_task(task_id, project_id, data)
        if not result:
            raise ValueError("Task not found")
        return result

    async def delete_task(self, project_id: str, task_id: str) -> bool:
        """Delete a task."""
        return await self.repo.delete_task(task_id, project_id)

    # ------------------------------------------------------------------ #
    # Members
    # ------------------------------------------------------------------ #

    async def list_members(self, project_id: str) -> list:
        """List all members of a project, enriched with email."""
        members = await self.repo.get_members(project_id)
        return await self.repo.enrich_members_with_email(members)

    async def add_member(
        self,
        project_id: str,
        user_id: str,
        role: str,
        invited_by: str,
    ) -> dict:
        """Add a member to a project."""
        data = {
            "project_id": project_id,
            "user_id": user_id,
            "role": role,
            "invited_by": invited_by,
        }
        member = await self.repo.create_member(data)
        member["email"] = await self.repo.get_user_email(user_id)
        return member

    async def update_member_role(
        self, project_id: str, member_id: str, role: str
    ) -> dict:
        """Update a member's role."""
        result = await self.repo.update_member(
            member_id, project_id, {"role": role}
        )
        if not result:
            raise ValueError("Member not found")
        return result

    async def remove_member(self, project_id: str, member_id: str) -> bool:
        """Remove a member from a project."""
        return await self.repo.delete_member(member_id, project_id)

    # ------------------------------------------------------------------ #
    # Collections
    # ------------------------------------------------------------------ #

    async def list_collections(self, project_id: str) -> list:
        """List all collections for a project."""
        return await self.repo.get_collections(project_id)

    async def create_collection(
        self, project_id: str, data: dict, user_id: str
    ) -> dict:
        """Create a collection link for external file uploads."""
        import secrets

        collection_code = secrets.token_urlsafe(8)[:12]
        insert_data: dict = {
            "project_id": project_id,
            "collection_code": collection_code,
            "collection_name": data["collection_name"],
            "max_file_size_mb": data.get("max_file_size_mb", 500),
            "created_by": user_id,
        }
        if data.get("allowed_types"):
            insert_data["allowed_types"] = data["allowed_types"]
        if data.get("deadline"):
            insert_data["deadline"] = data["deadline"]
        return await self.repo.create_collection(insert_data)

    async def delete_collection(
        self, project_id: str, collection_id: str
    ) -> bool:
        """Delete a collection link."""
        return await self.repo.delete_collection(collection_id, project_id)

    # ------------------------------------------------------------------ #
    # Review status
    # ------------------------------------------------------------------ #

    async def update_review_status(
        self, project_id: str, file_id: str, user_id: str, review_status: Optional[str]
    ) -> dict:
        """Update review status for a file after verifying project ownership."""
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")

        file_record = await self.repo.get_file_by_id(file_id)
        if not file_record:
            raise ValueError("File not found")
        if file_record.get("project_id") != project_id:
            raise ValueError("File not found in this project")

        return await self.repo.update_review_status(file_id, review_status)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _sanitize_filename(self, filename: str) -> str:
        """Remove invalid filesystem characters and truncate to 255 chars."""
        name = re.sub(r'[<>:"/\\|?*]', "_", filename)
        return name[:255]

    def _classify_file_type(self, mime: str) -> str:
        """Classify a MIME type into video/image/document."""
        if not mime:
            return "document"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("image/"):
            return "image"
        return "document"

    async def _extract_video_metadata(self, filepath: str) -> dict:
        """
        Extract video metadata using ffprobe.

        Returns a dict with keys like duration_seconds, resolution, fps,
        video_codec, audio_codec, etc. Returns empty dict on failure.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return {}

            info = json.loads(stdout)
            result = {}

            # Parse format-level metadata
            fmt = info.get("format", {})
            duration = fmt.get("duration")
            if duration:
                result["duration_seconds"] = int(float(duration))

            # Parse stream-level metadata
            for stream in info.get("streams", []):
                codec_type = stream.get("codec_type")
                if codec_type == "video":
                    result["video_codec"] = stream.get("codec_name")
                    w = stream.get("width")
                    h = stream.get("height")
                    if w and h:
                        result["resolution"] = f"{w}x{h}"
                    # FPS from r_frame_rate
                    r_frame_rate = stream.get("r_frame_rate", "")
                    if "/" in r_frame_rate:
                        num, den = r_frame_rate.split("/")
                        if int(den) > 0:
                            result["fps"] = round(int(num) / int(den), 2)
                    # Bitrate
                    bit_rate = stream.get("bit_rate")
                    if bit_rate:
                        result["video_bitrate_kbps"] = int(int(bit_rate) / 1000)
                elif codec_type == "audio":
                    result["audio_codec"] = stream.get("codec_name")
                    result["audio_channels"] = stream.get("channels")
                    sample_rate = stream.get("sample_rate")
                    if sample_rate:
                        result["audio_sample_rate"] = int(sample_rate)
                    bit_rate = stream.get("bit_rate")
                    if bit_rate:
                        result["audio_bitrate_kbps"] = int(int(bit_rate) / 1000)

            return result
        except Exception as e:
            logger.warning(f"ffprobe failed for {filepath}: {e}")
            return {}
