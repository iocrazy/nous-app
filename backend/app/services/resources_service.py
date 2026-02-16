# app/services/resources_service.py

"""
Resources Service

Business logic for the resource library: file upload with metadata
extraction, version management, folder operations, and tagging.
Reuses upload patterns from projects_service.py.
"""

import asyncio
import json
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from app.core.config import settings
from app.repositories.resources_repository import ResourcesRepository


class ResourcesService:
    """Resource library business logic"""

    def __init__(self):
        self.repo = ResourcesRepository()

    # ------------------------------------------------------------------ #
    # Upload
    # ------------------------------------------------------------------ #

    async def upload_resource(
        self,
        user_id: str,
        file,
        scope_type: str,
        scope_id: str,
        folder_id: Optional[str] = None,
    ) -> dict:
        """
        Upload a file to the resource library.

        1. Save file to disk
        2. Classify file type
        3. Extract video metadata (if applicable)
        4. Create resource record
        5. Create V1 version record
        6. Create resource_item linking to scope/folder
        """
        safe_name = self._sanitize_filename(file.filename)
        content = await file.read()

        # Classify
        mime = file.content_type or mimetypes.guess_type(safe_name)[0] or ""
        file_type = self._classify_file_type(mime)

        # Create resource record first to get ID for storage path
        resource_data = {
            "creator_id": user_id,
            "source_type": "upload",
            "filename": safe_name,
            "file_type": file_type,
            "mime_type": mime,
            "file_size_bytes": len(content),
            "current_version": 1,
        }
        resource = await self.repo.create_resource(resource_data)
        resource_id = resource["id"]

        # Save to disk
        save_dir = Path(settings.DOWNLOAD_PATH) / "resources" / resource_id
        save_dir.mkdir(parents=True, exist_ok=True)
        target = save_dir / safe_name
        with open(target, "wb") as f:
            f.write(content)

        relative_path = f"resources/{resource_id}/{safe_name}"

        # Extract video metadata
        metadata = {}
        if file_type == "video":
            metadata = await self._extract_video_metadata(str(target))

        # Update resource with file path and metadata
        update_data = {"file_path": relative_path, **metadata}
        resource = await self.repo.update_resource(resource_id, update_data)

        # Create V1 version
        version_data = {
            "resource_id": resource_id,
            "version_number": 1,
            "filename": safe_name,
            "file_path": relative_path,
            "file_size_bytes": len(content),
            "mime_type": mime,
            "uploaded_by": user_id,
            **metadata,
        }
        await self.repo.create_version(version_data)

        # Create resource_item for scope
        item_data = {
            "resource_id": resource_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "folder_id": folder_id,
            "added_by": user_id,
        }
        await self.repo.create_resource_item(item_data)

        return resource

    # ------------------------------------------------------------------ #
    # Upload new version
    # ------------------------------------------------------------------ #

    async def upload_new_version(
        self,
        resource_id: str,
        user_id: str,
        file,
        notes: Optional[str] = None,
    ) -> dict:
        """Upload a new version of an existing resource."""
        resource = await self.repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")

        next_version = await self.repo.get_next_version_number(resource_id)

        safe_name = self._sanitize_filename(file.filename)
        content = await file.read()

        save_dir = (
            Path(settings.DOWNLOAD_PATH) / "resources" / resource_id / "versions"
        )
        save_dir.mkdir(parents=True, exist_ok=True)
        target = save_dir / f"v{next_version}_{safe_name}"
        with open(target, "wb") as f:
            f.write(content)

        mime = file.content_type or mimetypes.guess_type(safe_name)[0] or ""
        file_type = self._classify_file_type(mime)
        metadata = {}
        if file_type == "video":
            metadata = await self._extract_video_metadata(str(target))

        relative_path = (
            f"resources/{resource_id}/versions/v{next_version}_{safe_name}"
        )
        version_data = {
            "resource_id": resource_id,
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

        # Update resource with latest version info
        update_data = {
            "current_version": next_version,
            "file_path": relative_path,
            "file_size_bytes": len(content),
            "mime_type": mime,
            "filename": safe_name,
            **metadata,
        }
        await self.repo.update_resource(resource_id, update_data)

        return version

    # ------------------------------------------------------------------ #
    # Create resource from parser download (dedup)
    # ------------------------------------------------------------------ #

    async def create_from_video(
        self,
        video_id: str,
        user_id: str,
        filename: str,
        file_path: Optional[str] = None,
        file_size_bytes: Optional[int] = None,
        duration_seconds: Optional[int] = None,
        resolution: Optional[str] = None,
        cover_image_path: Optional[str] = None,
        scope_type: str = "personal",
        scope_id: Optional[str] = None,
    ) -> dict:
        """
        Create a resource record from a parser-downloaded video.

        Dedup logic: if resource with same video_id exists, only create
        a resource_item reference (zero-copy). Otherwise create new resource.
        """
        existing = await self.repo.get_resource_by_video_id(video_id)

        if existing:
            # Zero-copy: just add a resource_item reference
            target_scope_id = scope_id or user_id
            item = await self.repo.get_resource_item(
                existing["id"], scope_type, target_scope_id
            )
            if not item:
                await self.repo.create_resource_item(
                    {
                        "resource_id": existing["id"],
                        "scope_type": scope_type,
                        "scope_id": target_scope_id,
                        "added_by": user_id,
                    }
                )
            return existing

        # Create new resource
        resource_data = {
            "creator_id": user_id,
            "source_type": "web",
            "video_id": video_id,
            "filename": filename,
            "file_type": "video",
            "mime_type": "video/mp4",
            "file_path": file_path,
            "file_size_bytes": file_size_bytes,
            "duration_seconds": duration_seconds,
            "resolution": resolution,
            "thumbnail_path": cover_image_path,
            "cover_image_path": cover_image_path,
        }
        resource = await self.repo.create_resource(resource_data)

        # Create resource_item for user's personal scope
        target_scope_id = scope_id or user_id
        await self.repo.create_resource_item(
            {
                "resource_id": resource["id"],
                "scope_type": scope_type,
                "scope_id": target_scope_id,
                "added_by": user_id,
            }
        )

        return resource

    # ------------------------------------------------------------------ #
    # Soft delete / restore
    # ------------------------------------------------------------------ #

    async def remove_from_library(
        self,
        resource_id: str,
        user_id: str,
        scope_type: str,
        scope_id: str,
    ) -> bool:
        """
        Remove a resource from the user's library by deleting their
        resource_item. The DB trigger auto-trashes the resource if this
        was the last reference (orphan detection).
        """
        item = await self.repo.get_resource_item(resource_id, scope_type, scope_id)
        if not item:
            raise ValueError("Resource not found in this scope")

        return await self.repo.delete_resource_item(item["id"])

    async def trash_resource(self, resource_id: str, user_id: str) -> dict:
        resource = await self.repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")
        if resource["creator_id"] != user_id:
            raise PermissionError("Only the creator can trash this resource")

        return await self.repo.update_resource(
            resource_id,
            {
                "is_trashed": True,
                "trashed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def restore_resource(self, resource_id: str, user_id: str) -> dict:
        resource = await self.repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")
        if resource["creator_id"] != user_id:
            raise PermissionError("Only the creator can restore this resource")

        return await self.repo.update_resource(
            resource_id,
            {"is_trashed": False, "trashed_at": None},
        )

    async def permanent_delete(self, resource_id: str, user_id: str) -> bool:
        resource = await self.repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")
        if resource["creator_id"] != user_id:
            raise PermissionError("Only the creator can delete this resource")

        self._delete_physical_files(resource)
        return await self.repo.delete_resource(resource_id)

    async def cleanup_expired_trash(self, older_than_days: int = 30) -> int:
        """
        Permanently delete trashed resources older than N days.
        Removes physical files and database records.
        Returns count of cleaned-up resources.
        """
        expired = await self.repo.get_expired_trashed_resources(older_than_days)
        cleaned = 0

        for resource in expired:
            try:
                self._delete_physical_files(resource)
                await self.repo.delete_resource(resource["id"])
                cleaned += 1
            except Exception as e:
                logger.error(
                    f"Failed to cleanup resource {resource['id']}: {e}"
                )

        if cleaned:
            logger.info(f"Cleaned up {cleaned} expired trashed resources")
        return cleaned

    def _delete_physical_files(self, resource: dict) -> None:
        """Delete physical files for a resource from disk."""
        import shutil

        base = Path(settings.DOWNLOAD_PATH)
        file_path = resource.get("file_path")

        if file_path:
            full_path = base / file_path
            # If file is in a dedicated directory (resources/web/{platform}/{id}/),
            # remove the entire directory
            parent = full_path.parent
            if parent != base and parent.exists() and parent.name != base.name:
                try:
                    shutil.rmtree(parent)
                    logger.info(f"Deleted directory: {parent}")
                    return
                except Exception as e:
                    logger.warning(f"Failed to delete directory {parent}: {e}")

            # Otherwise delete individual files
            if full_path.exists():
                try:
                    full_path.unlink()
                    logger.info(f"Deleted file: {full_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete file {full_path}: {e}")

        cover_path = resource.get("cover_image_path")
        if cover_path:
            cover_full = base / cover_path
            if cover_full.exists():
                try:
                    cover_full.unlink()
                    logger.info(f"Deleted cover: {cover_full}")
                except Exception as e:
                    logger.warning(f"Failed to delete cover {cover_full}: {e}")

    # ------------------------------------------------------------------ #
    # Move resource to folder
    # ------------------------------------------------------------------ #

    async def move_resource(
        self,
        resource_id: str,
        user_id: str,
        scope_type: str,
        scope_id: str,
        folder_id: Optional[str] = None,
    ) -> dict:
        item = await self.repo.get_resource_item(resource_id, scope_type, scope_id)
        if not item:
            raise ValueError("Resource not found in this scope")

        return await self.repo.update_resource_item(
            item["id"], {"folder_id": folder_id}
        )

    # ------------------------------------------------------------------ #
    # Helpers (reused from projects_service)
    # ------------------------------------------------------------------ #

    def _sanitize_filename(self, filename: str) -> str:
        name = re.sub(r'[<>:"/\\|?*]', "_", filename)
        return name[:255]

    def _classify_file_type(self, mime: str) -> str:
        if not mime:
            return "document"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("audio/"):
            return "audio"
        return "document"

    async def _extract_video_metadata(self, filepath: str) -> dict:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
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

            fmt = info.get("format", {})
            duration = fmt.get("duration")
            if duration:
                result["duration_seconds"] = int(float(duration))

            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video":
                    w = stream.get("width")
                    h = stream.get("height")
                    if w and h:
                        result["resolution"] = f"{w}x{h}"

            return result
        except Exception as e:
            logger.warning(f"ffprobe failed for {filepath}: {e}")
            return {}
