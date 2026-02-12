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
        projects = await self.repo.get_user_projects(user_id)
        for p in projects:
            p["file_count"] = await self.repo.get_project_file_count(p["id"])
        return projects

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
        return await self.repo.create_project(data)

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
        with open(target, "wb") as f:
            f.write(content)

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
    # Link video
    # ------------------------------------------------------------------ #

    async def link_video(
        self, project_id: str, video_id: str, user_id: str
    ) -> dict:
        """
        Link an existing video from the videos table to a project.

        Args:
            project_id: UUID of the project.
            video_id: UUID of the video.
            user_id: UUID of the authenticated user.

        Returns:
            Created file dict.

        Raises:
            ValueError: If project or video not found.
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")

        video = await self.repo.get_video_metadata(video_id)
        if not video:
            raise ValueError("Video not found")

        file_data = {
            "project_id": project_id,
            "filename": video.get("title", "Untitled") or "Untitled",
            "file_type": "video",
            "mime_type": "video/mp4",
            "file_path": video.get("download_path"),
            "file_size_bytes": video.get("datasize_bytes"),
            "video_id": video_id,
            "duration_seconds": (
                int(video["duration"]) if video.get("duration") else None
            ),
            "resolution": video.get("resolution"),
            "uploaded_by": user_id,
            "cover_image_path": video.get("cover_download_path"),
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
        with open(target, "wb") as f:
            f.write(content)

        # Classify and extract metadata
        mime = file.content_type or mimetypes.guess_type(safe_name)[0] or ""
        file_type = self._classify_file_type(mime)
        metadata = {}
        if file_type == "video":
            metadata = await self._extract_video_metadata(str(target))

        # Create version record
        relative_path = f"mediatrack/{project_id}/versions/{file_id}/v{next_version}_{safe_name}"
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
        file_id: str,
        author_id: str,
        content: str,
        timestamp_seconds: Optional[float] = None,
        version_id: Optional[str] = None,
    ) -> dict:
        """Add a review comment to a file."""
        comment_data = {
            "file_id": file_id,
            "author_id": author_id,
            "content": content,
        }
        if timestamp_seconds is not None:
            comment_data["timestamp_seconds"] = timestamp_seconds
        if version_id:
            comment_data["version_id"] = version_id
        return await self.repo.create_comment(comment_data)

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
