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
from app.repositories.resources_repository import (
    EXPIRED_TRASH_BATCH,
    ResourcesRepository,
)
from app.services.library.media_storage import (
    LIBRARY_BUCKET,
    derived_key_prefix,
    hls_key_prefix,
    resolve_media_source,
    store_local_file,
    to_file_path,
)
from app.services.library.object_gc import delete_object_if_unreferenced
from app.services.library.storage_errors import (
    discard_orphan_row,
    object_store_write_failed,
)
from app.services.library.storage_flag import unified_storage_enabled


async def _resolve_personal_team_id(user_id: str) -> str:
    """Return the snowflake of the user's personal team.

    After Spec 1 PR-C, ``resource_items.scope_id`` is always a
    ``teams.id`` snowflake. Legacy call sites that defaulted to
    ``scope_id or user_id`` (UUID) need this translation when scope_id
    is omitted.
    """
    from sqlalchemy import String, cast, select

    from app.db.session import read_scope
    from app.models import Teams

    async with read_scope() as session:
        team_id = await session.scalar(
            select(cast(Teams.id, String))
            .where(
                cast(Teams.owner_id, String) == str(user_id),
                Teams.kind == "personal",
            )
            .limit(1)
        )
    if not team_id:
        raise ValueError(f"No personal team found for user {user_id}")
    return team_id


async def _scope_type_for(scope_id: str) -> str:
    """Derive the legacy ``scope_type`` literal from a team's ``kind``.

    PR-E Phase 1: the frontend is being weaned off sending ``scope_type``.
    The ``resource_items`` / ``folders`` columns are still ``NOT NULL CHECK
    IN ('personal','team')`` until Phase 4, so every INSERT must supply a
    value. We derive it from the target team's ``kind`` (the single source
    of truth post PR-C) rather than trusting a client-supplied hint.
    """
    from sqlalchemy import String, cast, select

    from app.db.session import read_scope
    from app.models import Teams

    async with read_scope() as session:
        kind = await session.scalar(
            select(Teams.kind).where(cast(Teams.id, String) == str(scope_id))
        )
    return "personal" if kind == "personal" else "team"


def _invalidate_media_path_cache(resource_id: str) -> None:
    """Drop the /media/{id} path cache for a resource after its current bytes
    change (overwrite / new version / set-current). Fail-safe: cache
    invalidation must never break a write — swallow any error."""
    try:
        from app.services.media import media_path_cache

        media_path_cache.invalidate(resource_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[media-cache] invalidate failed for {resource_id}: {e}")


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
        3. Create resource record
        4. Create V1 version record
        5. Create resource_item linking to scope/folder

        Media metadata extraction (ffprobe/Pillow) and HLS transcode are
        deferred to upload_postprocess_workflow so this returns fast.
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

            stored = None
            if await unified_storage_enabled():
                try:
                    stored = await store_local_file(
                        scope_id=int(scope_id),
                        source_path=str(tmp_path),
                        mime=mime,
                        filename=safe_name,
                        sha256=file_hash,
                    )
                except Exception as exc:
                    # Hard failure (2026-09-07): the row went in ahead of the
                    # bytes, so take it out again before surfacing.
                    await discard_orphan_row(
                        self.repo.delete_resource, resource_id, where="upload_resource"
                    )
                    raise object_store_write_failed(
                        exc,
                        where="upload_resource",
                        scope_id=scope_id,
                        resource_id=resource_id,
                        filename=safe_name,
                        mime=mime,
                        size_bytes=file_size,
                    ) from exc
            if stored is not None:
                relative_path = stored.file_path
            else:
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
                relative_path = f"teams/{scope_id}/uploads/{resource_id}/v1/{safe_name}"
        finally:
            # No-op if the move succeeded (tmp_path no longer exists); when
            # the object-store write succeeded, this is what cleans up the
            # tmp file (store_local_file only reads it, never deletes it).
            tmp_path.unlink(missing_ok=True)

        # Update resource with file path only. Media metadata extraction
        # (ffprobe duration/resolution, Pillow image dimensions) and HLS
        # transcode are deferred to upload_postprocess_workflow so this
        # response can return fast. Metadata columns stay null until the
        # workflow backfills them.
        update_data = {"file_path": relative_path}
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

        # HLS transcode (video) is deferred to upload_postprocess_workflow.

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

        # Stream to a temp file first (mirrors upload_resource): the final
        # location depends on the storage track, and the object-store PUT
        # needs a local source file either way.
        tmp_fd, tmp_name = tempfile.mkstemp()
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            file_size, file_hash = await stream_upload_to_disk(
                file, tmp_path, MAX_UPLOAD_SIZE
            )

            mime = (
                sniff_mime(tmp_path)
                or file.content_type
                or mimetypes.guess_type(safe_name)[0]
                or ""
            )

            stored = None
            if await unified_storage_enabled():
                # The resource's scope comes from resource_items — an
                # sb:// (or missing) file_path has no directory to
                # derive it from, so the item row is the one
                # authoritative source for the object key's t{scope}.
                item = await self.repo.get_first_resource_item(resource_id)
                if item is None:
                    # Not a storage failure — the resource has no scope
                    # association at all. Skip the object-store attempt
                    # quietly; the fs branch below raises the legit
                    # "Resource has no scope association" ValueError.
                    logger.debug(
                        f"[upload_new_version] no resource_item for "
                        f"resource={resource_id} — skipping object-store write"
                    )
                else:
                    try:
                        stored = await store_local_file(
                            scope_id=int(item["scope_id"]),
                            source_path=str(tmp_path),
                            mime=mime,
                            filename=safe_name,
                            sha256=file_hash,
                        )
                    except Exception as exc:
                        raise object_store_write_failed(
                            exc,
                            where="upload_new_version",
                            scope_id=str(item["scope_id"]),
                            resource_id=str(resource_id),
                            filename=safe_name,
                            mime=mime,
                            size_bytes=file_size,
                        ) from exc
            if stored is not None:
                relative_path = stored.file_path
            else:
                # Determine the storage base path from the existing
                # file_path or resource_items — fs-fallback only: sb://
                # rows have no directory semantics ("/v" never matches an
                # sb:// value — bucket/key segments are hash-hex — so they
                # fall through to the resource_items scope branch).
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

                save_dir = (
                    Path(settings.DOWNLOAD_PATH) / base_relative / f"v{next_version}"
                )
                save_dir.mkdir(parents=True, exist_ok=True)
                target = save_dir / safe_name
                await asyncio.to_thread(shutil.move, str(tmp_path), str(target))
                relative_path = f"{base_relative}/v{next_version}/{safe_name}"
        finally:
            # No-op if the move succeeded (tmp_path no longer exists); when
            # the object-store write succeeded, this is what cleans up the
            # tmp file (store_local_file only reads it, never deletes it).
            tmp_path.unlink(missing_ok=True)

        # NOTE: metadata (ffprobe/Pillow), thumbnail, and HLS transcode are
        # deferred to upload_postprocess_workflow (dispatched by the router) so
        # the upload request returns as soon as the bytes are written. The
        # version/resource rows are created with file_path now; metadata fills
        # in asynchronously and the frontend refreshes via resources Realtime.
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
        }
        version = await self.repo.create_version(version_data)

        # Update resource with latest version info (metadata deferred)
        update_data = {
            "current_version": next_version,
            "file_path": relative_path,
            "file_size_bytes": file_size,
            "mime_type": mime,
            "filename": safe_name,
            "file_hash": file_hash,
        }
        await self.repo.update_resource(resource_id, update_data)

        _invalidate_media_path_cache(resource_id)
        return version

    async def overwrite_version_content(
        self,
        resource_id: str,
        version_id: str,
        user_id: str,
        file,
    ) -> dict:
        """Replace the bytes of an existing version in place (text editing).

        Unlike ``upload_new_version`` this does NOT create a new
        ``resource_versions`` row — it content-addresses the new bytes and
        repoints the given version's ``file_path`` / ``file_size_bytes`` /
        ``mime_type``. The previous object may become orphaned; that is
        harmless and dedup-safe under content addressing (a future GC
        reclaims it). Creator-only enforcement lives in the router guard.
        """
        resource = await self.repo.get_resource_by_id(resource_id)
        if not resource:
            raise ValueError("Resource not found")

        version = await self.repo.get_version_by_id(version_id)
        if not version or str(version.get("resource_id")) != str(resource_id):
            raise ValueError("Version not found")

        safe_name = sanitize_filename(file.filename)

        tmp_fd, tmp_name = tempfile.mkstemp()
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            file_size, file_hash = await stream_upload_to_disk(
                file, tmp_path, MAX_UPLOAD_SIZE
            )
            mime = (
                sniff_mime(tmp_path)
                or file.content_type
                or mimetypes.guess_type(safe_name)[0]
                or ""
            )

            stored = None
            if await unified_storage_enabled():
                item = await self.repo.get_first_resource_item(resource_id)
                if item is not None:
                    try:
                        stored = await store_local_file(
                            scope_id=int(item["scope_id"]),
                            source_path=str(tmp_path),
                            mime=mime,
                            filename=safe_name,
                            sha256=file_hash,
                        )
                    except Exception as exc:
                        raise object_store_write_failed(
                            exc,
                            where="overwrite_version_content",
                            scope_id=str(item["scope_id"]),
                            resource_id=str(resource_id),
                            version_id=str(version_id),
                            filename=safe_name,
                            mime=mime,
                            size_bytes=file_size,
                        ) from exc

            if stored is not None:
                relative_path = stored.file_path
            else:
                # Filesystem fallback: keep the file next to the resource's
                # existing versioned tree. Content addressing has no name
                # collisions, but the fs path needs a version-scoped folder.
                from app.core.config import settings

                save_dir = (
                    Path(settings.DOWNLOAD_PATH)
                    / "resources"
                    / str(resource_id)
                    / f"v{version.get('version_number', 1)}"
                )
                save_dir.mkdir(parents=True, exist_ok=True)
                target = save_dir / safe_name
                await asyncio.to_thread(shutil.move, str(tmp_path), str(target))
                relative_path = str(target.relative_to(Path(settings.DOWNLOAD_PATH)))
        finally:
            tmp_path.unlink(missing_ok=True)

        updated = await self.repo.update_version(
            version_id,
            {
                "file_path": relative_path,
                "file_size_bytes": file_size,
                "mime_type": mime,
                "filename": safe_name,
                "file_hash": file_hash,
            },
        )

        # The detail page + downloads read the denormalized resources.file_path,
        # not the version row — so when overwriting the CURRENT version we must
        # repoint the parent row too, or the edit reverts on reload. (Mirrors
        # upload_new_version; current_version is unchanged by an overwrite.)
        if str(version.get("version_number")) == str(resource.get("current_version")):
            await self.repo.update_resource(
                resource_id,
                {
                    "file_path": relative_path,
                    "file_size_bytes": file_size,
                    "mime_type": mime,
                    "filename": safe_name,
                    "file_hash": file_hash,
                },
            )
        _invalidate_media_path_cache(resource_id)
        return updated

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
        _invalidate_media_path_cache(resource_id)
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

        await self.repo.delete_version(version_id)

        # If we deleted the current version, switch to the latest remaining
        # BEFORE cleaning up its physical files (I4). Until this switch
        # runs, ``resources.file_path`` still points at the version being
        # deleted — content dedup routinely makes it the exact same raw
        # sb:// string — so a reference check performed earlier would always
        # see that column as a live reference to ITSELF and never delete
        # (permanent leak on every current-version delete; unreachable today
        # since production has 0 multi-version resources, but silent once
        # any exist). Once the switch has run, ``resources.file_path``
        # points at the NEW current version: if it deduped to the same key,
        # the reference check correctly keeps the object; if different, the
        # old key is now genuinely unreferenced and gets removed.
        if target["version_number"] == resource.get("current_version"):
            remaining = await self.repo.get_versions(resource_id)
            if remaining:
                latest = remaining[0]  # ordered desc by version_number
                await self.set_current_version(
                    resource_id, latest["version_number"], user_id
                )

        # Delete physical files for this version. sb:// rows go through the
        # reference-safe primitive (a version's file_path can dedup with
        # resources.file_path / parsed_media.download_path — see
        # object_gc.py); legacy filesystem rows keep the original directory
        # removal untouched.
        file_path = target.get("file_path")
        if file_path:
            loc = resolve_media_source(file_path)
            if loc.is_object_store:
                outcome = await delete_object_if_unreferenced(
                    file_path, exclude={"resource_versions": [version_id]}
                )
                logger.info(
                    f"[object-gc] version {version_id} file_path {file_path!r}: "
                    f"{outcome}"
                )
            else:
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
                        logger.warning(
                            f"Failed to delete version dir {version_dir}: {e}"
                        )

        # HLS output tree for this version — path-addressed, namespaced by
        # (resource_id, version_id), so never shared with any other row.
        # Deleted as a prefix (whole tree, not just the master.m3u8 that
        # hls_path points at) with no reference check needed.
        if target.get("hls_path"):
            hls_prefix_raw = to_file_path(
                LIBRARY_BUCKET, hls_key_prefix(resource_id, version_id) + "/"
            )
            outcome = await delete_object_if_unreferenced(hls_prefix_raw)
            logger.info(f"[object-gc] version {version_id} hls prefix: {outcome}")

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
                # datetime OBJECT, not .isoformat() — asyncpg refuses str
                # for a timestamptz bind and the whole UPDATE would fail.
                "trashed_at": datetime.now(timezone.utc),
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
            from sqlalchemy import select

            from app.db.session import read_scope
            from app.models import Folders

            async with read_scope() as session:
                folder_row = (
                    (
                        await session.execute(
                            select(Folders.id, Folders.is_trashed)
                            .where(Folders.id == int(folder_id))
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
            if not folder_row or folder_row.get("is_trashed"):
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
            # The refcount drives a DESTRUCTIVE decision on SHARED files: if it
            # raises (transient DB error), we must NOT delete — a fabricated
            # "0 references" would wipe files other users still reference. The
            # DB row is already deleted (fine, idempotent), so we preserve the
            # delete return contract and SKIP the shared-file/media GC. NOTE: no
            # scheduled sweeper reclaims these — the orphan sweeper only walks
            # teams/{scope}/uploads/{resource_id}, not the global download tree
            # _delete_physical_files handles nor the parsed_media row — they leak
            # until a later successful permanent_delete or manual cleanup.
            # Accepted: leaking on a rare transient count error beats deleting
            # files another user still references.
            try:
                remaining = await self.repo.count_resources_by_media_id(media_id)
            except Exception as e:
                logger.error(
                    f"Reference count failed for media {media_id} during "
                    f"permanent_delete of {resource_id}; SKIPPING shared-file/"
                    f"media GC (files + parsed_media leak until a later "
                    f"successful permanent_delete or manual cleanup): {e}"
                )
            else:
                if remaining == 0:
                    await self._delete_physical_files(resource)
                    await self._delete_media_record(media_id)
                else:
                    logger.info(
                        f"Skipping file/media cleanup for media {media_id}: "
                        f"{remaining} resource(s) still reference it"
                    )
        else:
            # No media_id (direct upload) — always delete physical files
            await self._delete_physical_files(resource)

        return result

    async def permanent_delete_folder(self, folder_id: str, user_id: str) -> dict:
        """Permanently delete a folder, all sub-folders, and their resources."""
        # 1. Collect the folder + all descendant folder IDs. include_trashed=True:
        #    a permanent purge must reach trashed sub-folders too (the legacy REST
        #    child-select had no is_trashed filter). The root is seeded here so a
        #    nonexistent id is still counted/deleted (no-op) as before.
        descendants = await self.repo.get_descendant_folder_ids(
            folder_id, include_trashed=True
        )
        all_folder_ids = [folder_id] + descendants

        # 2. Permanently delete resources in each folder. include_trashed=True so
        #    trashed resources in the tree are purged too (the legacy loop
        #    selected resource_items regardless of the resource's trashed state).
        deleted_resources = 0
        for fid in all_folder_ids:
            resources = await self.repo.list_resources_in_folder(
                fid, include_trashed=True
            )
            for res in resources:
                rid = str(res["id"])
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

                # Only delete files + media when last reference is gone.
                # count_resources_by_media_id RE-RAISES on a DB error (A4): the
                # per-resource try/except below catches it, so a count failure
                # SKIPS this resource's shared-file/media GC (the destructive
                # _delete_* calls sit AFTER the count and never run on failure)
                # and does NOT abort the whole sweep — the next resource is
                # still processed. Never GC on an uncertain refcount.
                if media_id:
                    remaining = await self.repo.count_resources_by_media_id(media_id)
                    if remaining == 0:
                        await self._delete_physical_files(resource)
                        await self._delete_media_record(media_id)
                else:
                    await self._delete_physical_files(resource)

                cleaned += 1
            except Exception as e:
                logger.error(f"Failed to cleanup resource {resource['id']}: {e}")

        if cleaned:
            logger.info(f"Cleaned up {cleaned} expired trashed resources")
        # A full batch means more expired resources remain; the daily sweeper
        # re-runs and continues from the next-oldest. (The repo caps the working
        # set — previously an unbounded SELECT that silently clipped at 1000.)
        if len(expired) >= EXPIRED_TRASH_BATCH:
            logger.info(
                "Expired-trash batch full (%s) — more remain; next sweep continues.",
                EXPIRED_TRASH_BATCH,
            )
        return cleaned

    async def _delete_physical_files(self, resource: dict) -> None:
        """Delete physical files for a resource — filesystem or sb:// object.

        Legacy filesystem layout examples (unchanged, kept line-for-line):
          uploads:  teams/{scope}/uploads/{resource_id}/v1/{file}
          downloads: global/resources/web/{platform}/{media_id}/{file}
        Strategy there: find the resource-specific directory (identified by a
        numeric/snowflake-ID segment in the path) and remove it entirely,
        then prune empty ancestor directories up to DOWNLOAD_PATH.

        For ``sb://`` rows, each of file_path / cover_image_path /
        thumbnail_path is content-addressed and can be shared with another
        live row (991/967 dedup groups — see object_gc.py's module
        docstring), so deletion goes through
        ``delete_object_if_unreferenced``. ``exclude`` declares the rows this
        very call is in the middle of retiring: this resource's own id, and
        (when present) its originating ``parsed_media`` row — the caller
        (``permanent_delete`` / ``cleanup_expired_trash``) always deletes the
        ``resources`` row before invoking this, but the ``parsed_media`` row
        is still live at this point (deleted right after, by
        ``_delete_media_record``), so without excluding it here a shared key
        would see its own soon-to-be-orphaned parsed_media row as a
        "reference" and never get cleaned up.

        The resource's ``derived/{rid}/`` prefix (thumbnails + preview
        sprite) is namespaced by resource_id alone — never shared — so it is
        always removed outright, no reference check.
        """
        import shutil

        base = Path(settings.DOWNLOAD_PATH).resolve()
        rid = resource.get("id")
        media_id = resource.get("media_id")
        exclude: dict = {}
        if rid:
            exclude["resources"] = [rid]
        if media_id:
            exclude["parsed_media"] = [media_id]

        # ⛔ 永远不要在这里接 ``resolve_resource_file_path``（PR-B 阶梯）。
        #
        # 别处那样做是对的 —— 读文件时 ``resources.file_path`` 为空要回落到
        # ``parsed_media.download_path``。**删除路径正好相反**：这一列为空恰恰
        # 表示"这一行没有自己的文件"，也就是**没有东西该由它来删**。
        #
        # parsed_media 那份是**多个用户共享的下载**（同一条素材被 N 个用户存进
        # 各自的库，每人一行 resources、共指同一个对象）。顺着阶梯删下去，就是
        # 用户删自己的信封时，把别人还在引用的共享文件一起删掉 —— 静默的跨租户
        # 数据丢失，而且发现时已经找不回来了。
        #
        # 共享那份由 ``_delete_media_record`` 走自己的引用检查回收，那才是它的
        # 归属地。本仓库已经为"内容寻址存储的删除必须先查引用"吃过一次亏，这条
        # 注释是为了不吃第二次。
        file_path = resource.get("file_path")
        if file_path:
            loc = resolve_media_source(file_path)
            if loc.is_object_store:
                outcome = await delete_object_if_unreferenced(
                    file_path, exclude=exclude or None
                )
                logger.info(
                    f"[object-gc] resource {rid} file_path {file_path!r}: {outcome}"
                )
            else:
                full_path = base / file_path
                # Walk up from the file to find the resource-specific
                # directory. Pattern: .../{resource_id}/v1/{file} → delete
                # {resource_id}/. Or: .../{media_id}/{file} → delete
                # {media_id}/.
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
            loc = resolve_media_source(cover_path)
            if loc.is_object_store:
                outcome = await delete_object_if_unreferenced(
                    cover_path, exclude=exclude or None
                )
                logger.info(
                    f"[object-gc] resource {rid} cover_image_path "
                    f"{cover_path!r}: {outcome}"
                )
            else:
                cover_full = base / cover_path
                if cover_full.exists():
                    try:
                        cover_full.unlink()
                        logger.info(f"Deleted cover: {cover_full}")
                    except Exception as e:
                        logger.warning(f"Failed to delete cover {cover_full}: {e}")

        thumbnail_path = resource.get("thumbnail_path")
        if thumbnail_path:
            loc = resolve_media_source(thumbnail_path)
            if loc.is_object_store:
                outcome = await delete_object_if_unreferenced(
                    thumbnail_path, exclude=exclude or None
                )
                logger.info(
                    f"[object-gc] resource {rid} thumbnail_path "
                    f"{thumbnail_path!r}: {outcome}"
                )
            # Legacy filesystem thumbnails were never cleaned up here before
            # this change; not adding new filesystem-deletion scope now.

        if rid:
            derived_prefix_raw = to_file_path(LIBRARY_BUCKET, derived_key_prefix(rid))
            outcome = await delete_object_if_unreferenced(derived_prefix_raw)
            logger.info(f"[object-gc] resource {rid} derived prefix: {outcome}")

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
            from sqlalchemy import delete

            from app.db.session import write_scope
            from app.models import ParsedMedia

            async with write_scope() as session:
                await session.execute(
                    delete(ParsedMedia).where(ParsedMedia.id == int(media_id))
                )
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

            loc = resolve_media_source(version["file_path"])
            if loc.is_object_store:
                # sb:// rows have no local file to stat/ffprobe — gate from
                # facts already on the version row instead of downloading a
                # temp copy just to decide the gate: file_size_bytes is
                # written at upload, duration_seconds is persisted by
                # upload_postprocess Phase A (which reads via materialize)
                # before this Phase C dispatch runs. The transcode workflow
                # itself materializes the source when it actually runs.
                file_size_mb = (version.get("file_size_bytes") or 0) / (1024 * 1024)
                duration_sec = version.get("duration_seconds")
            else:
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
                if stream.get("codec_type") == "audio":
                    br = stream.get("bit_rate")
                    if br:
                        try:
                            result["audio_bitrate_kbps"] = round(int(br) / 1000)
                        except (TypeError, ValueError):
                            pass

            if "audio_bitrate_kbps" not in result:
                fbr = fmt.get("bit_rate")
                if fbr:
                    try:
                        result["audio_bitrate_kbps"] = round(int(fbr) / 1000)
                    except (TypeError, ValueError):
                        pass

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
