"""Crop-derive service (Phase 3 Day 5).

Composes the pure ``crop_normalized`` primitive (Phase 3 Day 4) with
the existing ``ResourcesRepository`` so a user can crop one of their
images and end up with a new ``resources`` row plus persisted file
bytes.

Pipeline:

    1. Look up source resource and its scope (via ``resource_items``).
    2. Read source bytes from disk under ``settings.DOWNLOAD_PATH``.
    3. Run ``crop_normalized(bytes, region, mime_type=...)``.
    4. Insert a new resource row, compute storage path
       ``teams/{scope_id}/derived/{new_id}/v1/{filename}``, write bytes.
    5. Patch the resource with ``file_path``; create version + scope
       link rows mirroring ``ResourcesService.upload_resource``.

The service is intentionally thin (no DBOS workflow): cropping a
single image is fast and the response carries the new row, so the
front-end can use the result immediately without a Realtime hop.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from app.core.config import settings
from app.repositories.resources_repository import ResourcesRepository
from app.services.canvas.image_crop import CropRegion, crop_normalized

logger = logging.getLogger(__name__)


class CropDeriveError(Exception):
    """Raised for service-level failures (404 / 400 / 500 mapped at the
    router layer)."""

    def __init__(self, *, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class CropDeriveResult:
    resource: dict


class _ResourceRepoProtocol(Protocol):
    """Subset of ``ResourcesRepository`` actually used here — declared
    explicitly so tests can inject a fake without depending on the
    Supabase client surface."""

    async def get_resource_by_id(self, resource_id: str): ...
    async def get_first_resource_item(self, resource_id: str): ...
    async def create_resource(self, data: dict): ...
    async def update_resource(self, resource_id: str, data: dict): ...
    async def create_version(self, data: dict): ...
    async def create_resource_item(self, data: dict): ...


def _derived_filename(source_filename: str, override: Optional[str]) -> str:
    """Pick the filename for the cropped artifact.

    Priority: explicit override > ``crop-{source}`` prefix. We keep the
    extension on the source filename so the persisted bytes match the
    encoded format.
    """
    if override:
        return override
    if not source_filename:
        return "crop.png"
    return f"crop-{source_filename}"


def _resolve_source_bytes(file_path: str) -> bytes:
    """Read source resource bytes from the configured download root."""
    base = Path(settings.DOWNLOAD_PATH)
    abs_path = (base / file_path).resolve()
    # Defence in depth: ensure the resolved path is still under the
    # configured root. Stops a malformed `file_path` (e.g. ``../``) from
    # reading anything outside the resources volume.
    try:
        abs_path.relative_to(base.resolve())
    except ValueError as exc:
        raise CropDeriveError(
            status_code=400,
            detail="resource file_path escapes the download root",
        ) from exc
    if not abs_path.exists():
        raise CropDeriveError(
            status_code=404, detail="source resource file is missing on disk"
        )
    return abs_path.read_bytes()


def _is_image(resource: dict) -> bool:
    if resource.get("file_type") == "image":
        return True
    mime = (resource.get("mime_type") or "").lower()
    return mime.startswith("image/")


async def derive_crop_resource(
    *,
    source_resource_id: str,
    user_id: str,
    region: CropRegion,
    filename_override: Optional[str] = None,
    repo: Optional[_ResourceRepoProtocol] = None,
) -> CropDeriveResult:
    """Crop ``source_resource_id`` by ``region`` and persist as a new
    sibling resource in the same scope.

    Raises:
        CropDeriveError: with the HTTP status code the router should
            surface (404 source missing, 400 invalid source / region,
            500 storage write failure).
    """
    repo = repo or ResourcesRepository()

    source = await repo.get_resource_by_id(source_resource_id)
    if not source:
        raise CropDeriveError(status_code=404, detail="source resource not found")
    if not _is_image(source):
        raise CropDeriveError(
            status_code=400, detail="crop-derive is only valid for image resources"
        )
    source_file_path = source.get("file_path")
    if not source_file_path:
        raise CropDeriveError(
            status_code=400, detail="source resource has no file on disk yet"
        )

    item = await repo.get_first_resource_item(source_resource_id)
    if not item:
        raise CropDeriveError(
            status_code=400, detail="source resource has no scope link"
        )
    scope_id = str(item.get("scope_id") or "")
    folder_id = item.get("folder_id")
    library_id = item.get("library_id")
    if not scope_id:
        raise CropDeriveError(status_code=400, detail="source resource has no scope_id")

    source_bytes = _resolve_source_bytes(source_file_path)
    mime_type = source.get("mime_type") or None
    try:
        cropped = crop_normalized(source_bytes, region, mime_type=mime_type)
    except Exception as exc:
        raise CropDeriveError(status_code=400, detail=f"crop failed: {exc}") from exc

    out_filename = _derived_filename(
        str(source.get("filename") or ""), filename_override
    )

    # Create the new resource row first so we can name the storage path
    # by snowflake id (same convention as ``upload_resource``).
    new_resource = await repo.create_resource(
        {
            "creator_id": user_id,
            "source_type": "derived",
            "filename": out_filename,
            "file_type": "image",
            "mime_type": mime_type or "image/png",
            "file_size_bytes": len(cropped),
            "current_version": 1,
            # We deliberately don't compute a file_hash: a hash of cropped
            # bytes won't match anything in the dedupe index. Leave null.
        }
    )
    new_resource_id = str(new_resource["id"])

    relative_path = f"teams/{scope_id}/derived/{new_resource_id}/v1/{out_filename}"
    save_dir = Path(settings.DOWNLOAD_PATH) / Path(relative_path).parent
    save_dir.mkdir(parents=True, exist_ok=True)
    target = save_dir / out_filename
    # Atomic write: tmp + rename. Keeps a half-written file from being
    # observed if we crash mid-write.
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(save_dir))
    try:
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(cropped)
        os.replace(tmp_name, target)
    except Exception:
        # Best-effort cleanup of the temp blob.
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            pass
        raise

    new_resource = await repo.update_resource(
        new_resource_id, {"file_path": relative_path}
    )
    await repo.create_version(
        {
            "resource_id": new_resource_id,
            "version_number": 1,
            "filename": out_filename,
            "file_path": relative_path,
            "file_size_bytes": len(cropped),
            "mime_type": mime_type or "image/png",
            "uploaded_by": user_id,
        }
    )
    await repo.create_resource_item(
        {
            "resource_id": new_resource_id,
            "scope_id": scope_id,
            "folder_id": folder_id,
            "library_id": library_id,
            "added_by": user_id,
        }
    )
    return CropDeriveResult(resource=new_resource)
