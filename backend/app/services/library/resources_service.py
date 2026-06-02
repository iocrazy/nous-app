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
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.core.config import settings
from app.core.file_utils import (
    MAX_UPLOAD_SIZE,
    sanitize_filename,
    sniff_mime,
    stream_upload_to_disk,
)
from app.repositories.resources_repository import ResourcesRepository


async def _resolve_personal_team_id(user_id: str) -> str:
    """Return the snowflake of the user's personal team.

    After Spec 1 PR-C, ``resource_items.scope_id`` is always a
    ``teams.id`` snowflake. Legacy call sites that defaulted to
    ``scope_id or user_id`` (UUID) need this translation when scope_id
    is omitted.
    """
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT id::text AS id FROM public.teams "
        "WHERE owner_id::text = :uid AND kind = 'personal' "
        "LIMIT 1",
        {"uid": user_id},
    )
    if not row:
        raise ValueError(f"No personal team found for user {user_id}")
    return row["id"]


async def _scope_type_for(scope_id: str) -> str:
    """Derive the legacy ``scope_type`` literal from a team's ``kind``.

    PR-E Phase 1: the frontend is being weaned off sending ``scope_type``.
    The ``resource_items`` / ``folders`` columns are still ``NOT NULL CHECK
    IN ('personal','team')`` until Phase 4, so every INSERT must supply a
    value. We derive it from the target team's ``kind`` (the single source
    of truth post PR-C) rather than trusting a client-supplied hint.
    """
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT kind FROM public.teams WHERE id::text = :sid",
        {"sid": str(scope_id)},
    )
    return "personal" if (row and row["kind"] == "personal") else "team"


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
        scope_id: str,
        scope_type: Optional[str] = None,
        folder_id: Optional[str] = None,
        library_id: Optional[str] = None,
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
        safe_name = sanitize_filename(file.filename)

        # Stream to a temp file first: the final path needs the resource_id
        # (assigned by the DB insert), but we must not hold the whole upload
        # in RAM. Once the row exists, move the temp file into place.
        tmp_fd, tmp_name = tempfile.mkstemp()
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            file_size, file_hash = await stream_upload_to_disk(
                file, tmp_path, MAX_UPLOAD_SIZE
            )

            # Classify — sniff real content type first so a binary
            # masquerading as media via a forged Content-Type is recorded
            # as what it actually is, not what the client claimed.
            mime = (
                sniff_mime(tmp_path)
                or file.content_type
                or mimetypes.guess_type(safe_name)[0]
                or ""
            )
            file_type = self._classify_file_type(mime)

            # Create resource record first to get ID for storage path
            resource_data = {
                "creator_id": user_id,
                "source_type": "upload",
                "filename": safe_name,
                "file_type": file_type,
                "mime_type": mime,
                "file_size_bytes": file_size,
                "current_version": 1,
                "file_hash": file_hash,
            }
            resource = await self.repo.create_resource(resource_data)
            resource_id = str(resource["id"])

            # Move the streamed file into teams/{scope_id}/uploads/{id}/v1/
            save_dir = (
                Path(settings.DOWNLOAD_PATH)
                / "teams"
                / scope_id
                / "uploads"
                / resource_id
                / "v1"
            )
            save_dir.mkdir(parents=True, exist_ok=True)
            target = save_dir / safe_name
            await asyncio.to_thread(shutil.move, str(tmp_path), str(target))
        finally:
            # No-op if the move succeeded (tmp_path no longer exists).
            tmp_path.unlink(missing_ok=True)

        relative_path = f"teams/{scope_id}/uploads/{resource_id}/v1/{safe_name}"

        # Extract media metadata (video & audio: duration, resolution;
        # image: resolution via Pillow).
        metadata = {}
        if file_type in ("video", "audio"):
            metadata = await self._extract_video_metadata(str(target))
        elif file_type == "image":
            metadata = await self._extract_image_metadata(str(target))

        # Update resource with file path and metadata
        update_data = {"file_path": relative_path, **metadata}
        resource = await self.repo.update_resource(resource_id, update_data)

        # Create V1 version
        version_data = {
            "resource_id": resource_id,
            "version_number": 1,
            "filename": safe_name,
            "file_path": relative_path,
            "file_size_bytes": file_size,
            "mime_type": mime,
            "uploaded_by": user_id,
            "file_hash": file_hash,
            **metadata,
        }
        await self.repo.create_version(version_data)

        # Create resource_item for scope. PR-E Phase 4b: no longer write
        # scope_type (column is nullable post mig 240, dropped in 4c); scope_id
        # alone locates the scope.
        item_data = {
            "resource_id": resource_id,
            "scope_id": scope_id,
            "folder_id": folder_id,
            "library_id": library_id,
            "added_by": user_id,
        }
        await self.repo.create_resource_item(item_data)

        # Trigger HLS transcode for video files
        if file_type == "video":
            versions = await self.repo.get_versions(resource_id)
            if versions:
                await self._trigger_transcode_async(
                    resource_id, str(versions[0]["id"]), mime, user_id=user_id
                )

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

        safe_name = sanitize_filename(file.filename)

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
        file_size, file_hash = await stream_upload_to_disk(
            file, target, MAX_UPLOAD_SIZE
        )

        mime = (
            sniff_mime(target)
            or file.content_type
            or mimetypes.guess_type(safe_name)[0]
            or ""
        )
        file_type = self._classify_file_type(mime)
        metadata = {}
        if file_type in ("video", "audio"):
            metadata = await self._extract_video_metadata(str(target))
        elif file_type == "image":
            metadata = await self._extract_image_metadata(str(target))

        relative_path = f"{base_relative}/v{next_version}/{safe_name}"
        version_data = {
            "resource_id": resource_id,
            "version_number": next_version,
            "filename": safe_name,
            "file_path": relative_path,
            "file_size_bytes": file_size,
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
            "file_size_bytes": file_size,
            "mime_type": mime,
            "filename": safe_name,
            "file_hash": file_hash,
            **metadata,
        }
        await self.repo.update_resource(resource_id, update_data)

        # Trigger HLS transcode for video files
        if file_type == "video":
            await self._trigger_transcode_async(
                resource_id, str(version["id"]), mime, user_id=user_id
            )

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
            target_scope_id = scope_id or await _resolve_personal_team_id(user_id)
            item = await self.repo.get_resource_item(
                existing["id"], None, target_scope_id
            )
            if not item:
                await self.repo.create_resource_item(
                    {
                        "resource_id": existing["id"],
                        "scope_id": target_scope_id,
                        "added_by": user_id,
                    }
                )
            return existing

        # Compute file hash for cross-path duplicate detection (upload ↔ parser)
        file_hash = None
        if file_path:
            abs_path = Path(settings.DOWNLOAD_PATH) / file_path
            if abs_path.exists():
                try:
                    h = hashlib.sha256()
                    with open(abs_path, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                            h.update(chunk)
                    file_hash = h.hexdigest()
                except Exception as e:
                    logger.warning(f"Failed to compute file hash for {abs_path}: {e}")

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
        if file_hash:
            resource_data["file_hash"] = file_hash
        resource = await self.repo.create_resource(resource_data)

        # Create resource_item for user's personal scope
        target_scope_id = scope_id or await _resolve_personal_team_id(user_id)
        await self.repo.create_resource_item(
            {
                "resource_id": resource["id"],
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
        scope_id: str,
        scope_type: Optional[str] = None,
        folder_id: str | None = None,
    ) -> bool:
        """
        Remove a resource from a specific folder by deleting the resource_item.
        Saves last location on the resource for restore.
        The DB trigger auto-trashes the resource if this was the last reference.

        PR-E Phase 1: ``scope_type`` is derived from ``scope_id`` when the
        caller no longer supplies it, so the ``last_scope_type`` snapshot
        stays accurate for restore.
        """
        if scope_type is None:
            scope_type = await _scope_type_for(scope_id)

        if folder_id is not None:
            item = await self.repo.get_resource_item_in_folder(
                resource_id, scope_type, scope_id, folder_id
            )
        else:
            item = await self.repo.get_resource_item(resource_id, scope_type, scope_id)
        if not item:
            raise ValueError("Resource not found in this scope/folder")

        # Save last location for restore
        await self.repo.update_resource(
            resource_id,
            {
                "last_folder_id": item.get("folder_id"),
                "last_library_id": item.get("library_id"),
                "last_scope_type": scope_type,
                "last_scope_id": scope_id,
            },
        )

        return await self.repo.delete_resource_item(item["id"])

    async def trash_resource(self, resource_id: str, user_id: str) -> dict:
        resource = await self.repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")
        if resource["creator_id"] != user_id:
            raise PermissionError("Only the creator can trash this resource")

        # last_scope_id remembers where to restore to. It feeds
        # resource_items.scope_id (bigint, PR-E 4c-3) on restore, so it must be
        # the personal-team snowflake — not the user UUID, which would fail
        # 22P02 when restored.
        last_scope_id = await _resolve_personal_team_id(user_id)
        return await self.repo.update_resource(
            resource_id,
            {
                "is_trashed": True,
                "trashed_at": datetime.now(timezone.utc).isoformat(),
                "last_scope_type": "personal",
                "last_scope_id": last_scope_id,
            },
        )

    async def restore_resource(self, resource_id: str, user_id: str) -> dict:
        resource = await self.repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")
        if resource["creator_id"] != user_id:
            raise PermissionError("Only the creator can restore this resource")
        if not resource.get("is_trashed"):
            raise ValueError("Resource is not in trash")

        # Determine restore location (last_scope_type is no longer needed —
        # PR-E 4b stopped writing resource_items.scope_type; scope_id locates it)
        folder_id = resource.get("last_folder_id")
        library_id = resource.get("last_library_id")
        # last_scope_id is a text column and legacy rows stored a user UUID
        # there; only a numeric value is a valid teams.id (bigint) for
        # resource_items.scope_id (PR-E 4c-3). Fall back to the personal team
        # for UUID / missing values so restore can't fail with 22P02.
        last_scope = resource.get("last_scope_id")
        if last_scope is not None and str(last_scope).isdigit():
            scope_id = str(last_scope)
        else:
            scope_id = await _resolve_personal_team_id(user_id)

        # If last_folder_id references a trashed/deleted folder, clear it
        if folder_id:
            from app.db.supabase_client import get_async_supabase_admin

            client = await get_async_supabase_admin()
            folder_check = await (
                client.table("folders")
                .select("id, is_trashed")
                .eq("id", folder_id)
                .limit(1)
                .execute()
            )
            if not folder_check.data or folder_check.data[0].get("is_trashed"):
                folder_id = None  # Folder gone or trashed -> restore to library root

        # Recreate the resource_item
        await self.repo.create_resource_item(
            {
                "resource_id": resource_id,
                "scope_id": scope_id,
                "folder_id": folder_id,
                "library_id": library_id,
                "added_by": user_id,
            }
        )

        # Un-trash the resource
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

        media_id = resource.get("media_id")

        # 1. Delete resource DB record first
        result = await self.repo.delete_resource(resource_id)

        # 2. Physical file + media cleanup
        if media_id:
            remaining = await self.repo.count_resources_by_media_id(media_id)
            if remaining == 0:
                self._delete_physical_files(resource)
                await self._delete_media_record(media_id)
            else:
                logger.info(
                    f"Skipping file/media cleanup for media {media_id}: "
                    f"{remaining} resource(s) still reference it"
                )
        else:
            # No media_id (direct upload) — always delete physical files
            self._delete_physical_files(resource)

        return result

    async def permanent_delete_folder(self, folder_id: str, user_id: str) -> dict:
        """Permanently delete a folder, all sub-folders, and their resources."""
        # 1. Collect all descendant folder IDs
        all_folder_ids = [folder_id]
        queue = [folder_id]
        while queue:
            parent_id = queue.pop(0)
            client = await self.repo._get_client()
            result = await (
                client.table("folders")
                .select("id")
                .eq("parent_id", parent_id)
                .execute()
            )
            for row in result.data or []:
                cid = str(row["id"])
                all_folder_ids.append(cid)
                queue.append(cid)

        # 2. Permanently delete resources in each folder
        deleted_resources = 0
        for fid in all_folder_ids:
            client = await self.repo._get_client()
            items_result = await (
                client.table("resource_items")
                .select("resource_id")
                .eq("folder_id", fid)
                .execute()
            )
            for item in items_result.data or []:
                rid = str(item["resource_id"])
                try:
                    await self.permanent_delete(rid, user_id)
                    deleted_resources += 1
                except (ValueError, PermissionError):
                    pass  # already deleted or not owned

        # 3. Delete folders (children first)
        for fid in reversed(all_folder_ids):
            await self.repo.delete_folder(fid)

        return {
            "deleted_folders": len(all_folder_ids),
            "deleted_resources": deleted_resources,
        }

    async def cleanup_expired_trash(self, older_than_days: int = 30) -> int:
        """
        Permanently delete trashed resources older than N days.
        Removes physical files and database records only when no other
        resources reference the same parsed_media.
        Returns count of cleaned-up resources.
        """
        expired = await self.repo.get_expired_trashed_resources(older_than_days)
        cleaned = 0

        for resource in expired:
            try:
                media_id = resource.get("media_id")
                await self.repo.delete_resource(resource["id"])

                # Only delete files + media when last reference is gone
                if media_id:
                    remaining = await self.repo.count_resources_by_media_id(media_id)
                    if remaining == 0:
                        self._delete_physical_files(resource)
                        await self._delete_media_record(media_id)
                else:
                    self._delete_physical_files(resource)

                cleaned += 1
            except Exception as e:
                logger.error(f"Failed to cleanup resource {resource['id']}: {e}")

        if cleaned:
            logger.info(f"Cleaned up {cleaned} expired trashed resources")
        return cleaned

    def _delete_physical_files(self, resource: dict) -> None:
        """Delete physical files for a resource from disk.

        Storage layout examples:
          uploads:  teams/{scope}/uploads/{resource_id}/v1/{file}
          downloads: global/resources/web/{platform}/{media_id}/{file}

        Strategy: find the resource-specific directory (identified by a
        numeric/snowflake-ID segment in the path) and remove it entirely,
        then prune empty ancestor directories up to DOWNLOAD_PATH.
        """
        import shutil

        base = Path(settings.DOWNLOAD_PATH).resolve()
        file_path = resource.get("file_path")

        if file_path:
            full_path = base / file_path
            # Walk up from the file to find the resource-specific directory.
            # Pattern: .../{resource_id}/v1/{file}  →  want to delete {resource_id}/
            # Or:      .../{media_id}/{file}        →  want to delete {media_id}/
            target_dir = self._find_resource_dir(full_path, base)

            if target_dir and target_dir.exists():
                try:
                    shutil.rmtree(target_dir)
                    logger.info(f"Deleted resource directory: {target_dir}")
                except Exception as e:
                    logger.warning(f"Failed to delete directory {target_dir}: {e}")
            elif full_path.exists():
                try:
                    full_path.unlink()
                    logger.info(f"Deleted file: {full_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete file {full_path}: {e}")

            # Prune empty ancestor directories up to base
            self._prune_empty_parents(target_dir or full_path, base)

        cover_path = resource.get("cover_image_path")
        if cover_path:
            cover_full = base / cover_path
            if cover_full.exists():
                try:
                    cover_full.unlink()
                    logger.info(f"Deleted cover: {cover_full}")
                except Exception as e:
                    logger.warning(f"Failed to delete cover {cover_full}: {e}")

    @staticmethod
    def _find_resource_dir(file_path: Path, base: Path) -> Optional[Path]:
        """Walk up from file_path to find the resource/media ID directory.

        Looks for a directory whose name is a numeric ID (Snowflake) and
        whose parent is still under base.  Returns None if not found.
        """
        current = file_path.parent
        base_resolved = base.resolve()
        while current.resolve() != base_resolved and current != current.parent:
            if current.name.isdigit() and len(current.name) >= 6:
                return current
            current = current.parent
        return None

    @staticmethod
    def _prune_empty_parents(start: Path, base: Path) -> None:
        """Remove empty ancestor directories between start and base."""
        base_resolved = base.resolve()
        current = (
            start.parent if start.is_file() or not start.exists() else start.parent
        )
        while current.resolve() != base_resolved and current != current.parent:
            try:
                if current.exists() and not any(current.iterdir()):
                    current.rmdir()
                    logger.info(f"Pruned empty directory: {current}")
                else:
                    break
            except Exception:
                break
            current = current.parent

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
        scope_id: str,
        scope_type: Optional[str] = None,
        folder_id: Optional[str] = None,
    ) -> dict:
        # PR-E Phase 1: scope_type no longer filters the lookup (scope_id is
        # globally unique); accepted for compatibility but unused here.
        item = await self.repo.get_resource_item(resource_id, scope_type, scope_id)
        if not item:
            raise ValueError("Resource not found in this scope")

        return await self.repo.update_resource_item(
            item["id"], {"folder_id": folder_id}
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

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

    def _trigger_transcode(
        self, resource_id: str, version_id: str, mime_type: str, user_id: str = None
    ):
        """Queue HLS transcoding for a video version (sync — for legacy context).

        PR-D7 phase 3: dispatches via DBOS workflow instead of Celery.
        Skips the legacy size/duration gating — DBOS workflow does its
        own short-circuit if the version is too small."""
        if not mime_type or not mime_type.startswith("video/"):
            return
        try:
            import asyncio

            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.workflows.transcode import transcode_workflow

            asyncio.run(
                start_workflow_routed(
                    "transcode",
                    dbos_workflow_callable=transcode_workflow,
                    dbos_workflow_kwargs={
                        "resource_id": resource_id,
                        "version_id": version_id,
                        "user_id": user_id,
                    },
                )
            )
        except Exception as e:
            logger.warning(f"Failed to trigger transcode for {resource_id}: {e}")

    async def _trigger_transcode_async(
        self, resource_id: str, version_id: str, mime_type: str, user_id: str = None
    ):
        """Queue HLS transcoding for a video version (async — for FastAPI context).

        Performs the same gating logic as ``maybe_trigger_transcode`` but uses
        native async calls so it works inside a running event loop.
        """
        if not mime_type or not mime_type.startswith("video/"):
            return

        MIN_SIZE_MB = 100
        MIN_DURATION_SEC = 600

        try:
            version = await self.repo.get_version_by_id(version_id)
            if not version or not version.get("file_path"):
                logger.info(f"[Transcode] Skip: no file_path for version {version_id}")
                return

            file_path = Path(settings.DOWNLOAD_PATH) / version["file_path"]
            if not file_path.exists():
                logger.info(f"[Transcode] Skip: file not found {file_path}")
                return

            file_size_mb = file_path.stat().st_size / (1024 * 1024)

            # Async duration probe
            duration_sec = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(file_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **safe_popen_kwargs(),
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0 and stdout.strip():
                    duration_sec = float(stdout.strip())
            except Exception:
                pass

            if file_size_mb < MIN_SIZE_MB and (duration_sec or 0) < MIN_DURATION_SEC:
                logger.info(
                    f"[Transcode] Skip: too small ({file_size_mb:.0f}MB, "
                    f"{duration_sec or '?'}s) for version {version_id}"
                )
                return

            logger.info(
                f"[Transcode] Gating passed: {file_size_mb:.0f}MB, "
                f"{duration_sec or '?'}s — version {version_id}"
            )
        except Exception as e:
            logger.warning(f"[Transcode] Gating check failed, proceeding: {e}")

        # PR-D7 phase 3: legacy unified_task_manager.acquire_or_subscribe
        # dedup is no longer needed — DBOS workflow_id memoization
        # provides equivalent dedup via the workflow_id derived from
        # version_id. Two simultaneous dispatches for the same version
        # collide on workflow_id and the second one short-circuits to
        # the cached result.

        try:
            await self.repo.update_version(version_id, {"transcode_status": "pending"})

            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.workflows.transcode import transcode_workflow

            await start_workflow_routed(
                "transcode",
                dbos_workflow_callable=transcode_workflow,
                dbos_workflow_kwargs={
                    "resource_id": resource_id,
                    "version_id": version_id,
                    "user_id": user_id,
                },
            )
            logger.info(
                f"[Transcode] Queued HLS transcode: resource={resource_id}, version={version_id}"
            )
        except Exception as e:
            logger.warning(
                f"[Transcode] Failed to queue transcode for {resource_id}: {e}"
            )

    async def _extract_video_metadata(self, filepath: str) -> dict:
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
                **safe_popen_kwargs(),
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

    async def _extract_image_metadata(self, filepath: str) -> dict:
        """Read pixel dimensions from an image via Pillow. Returns
        ``{"resolution": "WxH"}`` (same format as video) or ``{}`` on failure.
        Runs the blocking PIL call off the event loop."""

        def _probe() -> dict:
            from PIL import Image

            with Image.open(filepath) as img:
                w, h = img.size
            if w and h:
                return {"resolution": f"{w}x{h}"}
            return {}

        try:
            return await asyncio.to_thread(_probe)
        except Exception as e:
            logger.warning(f"PIL image probe failed for {filepath}: {e}")
            return {}
