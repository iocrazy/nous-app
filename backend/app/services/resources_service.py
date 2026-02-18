# app/services/resources_service.py

"""
Resources Service

Business logic for the resource library: file upload with metadata
extraction, version management, folder operations, and tagging.
Reuses upload patterns from projects_service.py.
"""

import asyncio
import hashlib
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

        # Compute SHA-256 hash for duplicate detection
        file_hash = hashlib.sha256(content).hexdigest()

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
            "file_hash": file_hash,
        }
        resource = await self.repo.create_resource(resource_data)
        resource_id = str(resource["id"])

        # Save to disk — teams/{scope_id}/uploads/{resource_id}/v1/
        save_dir = Path(settings.DOWNLOAD_PATH) / "teams" / scope_id / "uploads" / resource_id / "v1"
        save_dir.mkdir(parents=True, exist_ok=True)
        target = save_dir / safe_name
        with open(target, "wb") as f:
            f.write(content)

        relative_path = f"teams/{scope_id}/uploads/{resource_id}/v1/{safe_name}"

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
            "file_hash": file_hash,
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

        # Trigger HLS transcode for video files
        if file_type == "video":
            versions = await self.repo.get_versions(resource_id)
            if versions:
                self._trigger_transcode(resource_id, str(versions[0]["id"]), mime, user_id=user_id)

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

        # Compute SHA-256 hash for duplicate detection
        file_hash = hashlib.sha256(content).hexdigest()

        # Determine storage base path from existing file_path or resource_items
        existing_path = resource.get("file_path", "")
        if existing_path and "/v" in existing_path:
            # Extract base path before /v{n}/
            parts = existing_path.split("/")
            # Find the vN segment and take everything before it
            base_parts = []
            for p in parts:
                if p.startswith("v") and p[1:].isdigit():
                    break
                base_parts.append(p)
            base_relative = "/".join(base_parts)
        else:
            # Fallback: use resource_items scope
            item = await self.repo.get_first_resource_item(resource_id)
            if not item:
                raise ValueError("Resource has no scope association")
            base_relative = f"teams/{item['scope_id']}/uploads/{resource_id}"

        save_dir = Path(settings.DOWNLOAD_PATH) / base_relative / f"v{next_version}"
        save_dir.mkdir(parents=True, exist_ok=True)
        target = save_dir / safe_name
        with open(target, "wb") as f:
            f.write(content)

        mime = file.content_type or mimetypes.guess_type(safe_name)[0] or ""
        file_type = self._classify_file_type(mime)
        metadata = {}
        if file_type == "video":
            metadata = await self._extract_video_metadata(str(target))

        relative_path = f"{base_relative}/v{next_version}/{safe_name}"
        version_data = {
            "resource_id": resource_id,
            "version_number": next_version,
            "filename": safe_name,
            "file_path": relative_path,
            "file_size_bytes": len(content),
            "mime_type": mime,
            "uploaded_by": user_id,
            "notes": notes,
            "file_hash": file_hash,
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
            "file_hash": file_hash,
            **metadata,
        }
        await self.repo.update_resource(resource_id, update_data)

        # Trigger HLS transcode for video files
        if file_type == "video":
            self._trigger_transcode(resource_id, str(version["id"]), mime, user_id=user_id)

        return version

    # ------------------------------------------------------------------ #
    # Version management
    # ------------------------------------------------------------------ #

    async def set_current_version(
        self, resource_id: str, version_number: int, user_id: str
    ) -> dict:
        """Set a specific version as the current active version."""
        resource = await self.repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")

        version = await self.repo.get_version_by_number(resource_id, version_number)
        if not version:
            raise ValueError(f"Version {version_number} not found")

        update_data = {
            "current_version": version_number,
            "file_path": version.get("file_path"),
            "file_size_bytes": version.get("file_size_bytes"),
            "mime_type": version.get("mime_type"),
            "filename": version.get("filename"),
        }
        if version.get("duration_seconds"):
            update_data["duration_seconds"] = version["duration_seconds"]
        if version.get("resolution"):
            update_data["resolution"] = version["resolution"]
        if version.get("thumbnail_path"):
            update_data["thumbnail_path"] = version["thumbnail_path"]

        await self.repo.update_resource(resource_id, update_data)
        return version

    async def delete_version(
        self, resource_id: str, version_id: str, user_id: str
    ) -> bool:
        """Delete a specific version (must keep at least one)."""
        resource = await self.repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")

        versions = await self.repo.get_versions(resource_id)
        if len(versions) <= 1:
            raise ValueError("Cannot delete the last version")

        target = next((v for v in versions if str(v["id"]) == version_id), None)
        if not target:
            raise ValueError("Version not found")

        # Delete physical files for this version
        file_path = target.get("file_path")
        if file_path:
            import shutil
            base = Path(settings.DOWNLOAD_PATH)
            full = base / file_path
            # Remove the v{n}/ directory
            version_dir = full.parent
            if version_dir.exists() and version_dir.name.startswith("v"):
                try:
                    shutil.rmtree(version_dir)
                    logger.info(f"Deleted version directory: {version_dir}")
                except Exception as e:
                    logger.warning(f"Failed to delete version dir {version_dir}: {e}")

        await self.repo.delete_version(version_id)

        # If we deleted the current version, switch to the latest remaining
        if target["version_number"] == resource.get("current_version"):
            remaining = await self.repo.get_versions(resource_id)
            if remaining:
                latest = remaining[0]  # ordered desc by version_number
                await self.set_current_version(
                    resource_id, latest["version_number"], user_id
                )

        return True

    # ------------------------------------------------------------------ #
    # Create resource from parser download (dedup)
    # ------------------------------------------------------------------ #

    async def create_from_media(
        self,
        media_id: str,
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
        Create a resource record from a parser-downloaded media.

        Dedup logic: if resource with same media_id exists, only create
        a resource_item reference (zero-copy). Otherwise create new resource.
        """
        existing = await self.repo.get_resource_by_media_id(media_id)

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
            "media_id": media_id,
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

        media_id = resource.get("media_id")
        result = await self.repo.delete_resource(resource_id)

        if media_id:
            await self._delete_media_record(media_id)

        return result

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
                media_id = resource.get("media_id")
                await self.repo.delete_resource(resource["id"])
                if media_id:
                    await self._delete_media_record(media_id)
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
            # If file is in a dedicated directory (global/resources/web/{platform}/{id}/),
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

    async def _delete_media_record(self, media_id: str) -> None:
        """Delete the parsed_media table record (orphaned after resource deletion)."""
        try:
            from app.db.supabase_client import get_async_supabase_admin

            client = await get_async_supabase_admin()
            await client.table("parsed_media").delete().eq("id", media_id).execute()
            logger.info(f"Deleted media record: {media_id}")
        except Exception as e:
            logger.warning(f"Failed to delete media record {media_id}: {e}")

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

    def _trigger_transcode(self, resource_id: str, version_id: str, mime_type: str, user_id: str = None):
        """Queue HLS transcoding for a video version."""
        try:
            from app.tasks.transcode_tasks import maybe_trigger_transcode
            maybe_trigger_transcode(resource_id, version_id, mime_type, user_id=user_id)
        except Exception as e:
            logger.warning(f"Failed to trigger transcode for {resource_id}: {e}")

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
