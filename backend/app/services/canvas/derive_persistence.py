"""Shared source-loading + persistence layer for derive services.

Extracted from ``crop_derive_service`` (Phase 3 Day 7) so grid split
can reuse the exact same pipeline without duplicating it:

    load_source_image()     — resource lookup, scope link, bytes read
    persist_derived_image() — new row + atomic file write + version +
                              scope link, mirroring upload_resource

Behaviour is unchanged from the original crop-derive implementation;
``CropDeriveError`` remains importable as an alias of ``DeriveError``.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from app.core.config import settings


class DeriveError(Exception):
    """Raised for service-level failures (404 / 400 / 500 mapped at the
    router layer)."""

    def __init__(self, *, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ResourceRepoProtocol(Protocol):
    """Subset of ``ResourcesRepository`` actually used here — declared
    explicitly so tests can inject a fake without depending on the
    Supabase client surface."""

    async def get_resource_by_id(self, resource_id: str): ...
    async def get_first_resource_item(self, resource_id: str): ...
    async def create_resource(self, data: dict): ...
    async def update_resource(self, resource_id: str, data: dict): ...
    async def create_version(self, data: dict): ...
    async def create_resource_item(self, data: dict): ...


@dataclass(frozen=True)
class SourceImage:
    """A validated, loaded source image plus the scope it lives in."""

    resource: dict
    scope_id: str
    folder_id: Optional[str]
    library_id: Optional[str]
    file_bytes: bytes
    mime_type: Optional[str]

    @property
    def filename(self) -> str:
        return str(self.resource.get("filename") or "")


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
        raise DeriveError(
            status_code=400,
            detail="resource file_path escapes the download root",
        ) from exc
    if not abs_path.exists():
        raise DeriveError(
            status_code=404, detail="source resource file is missing on disk"
        )
    return abs_path.read_bytes()


def _is_image(resource: dict) -> bool:
    if resource.get("file_type") == "image":
        return True
    mime = (resource.get("mime_type") or "").lower()
    return mime.startswith("image/")


async def load_source_image(
    repo: ResourceRepoProtocol, source_resource_id: str
) -> SourceImage:
    """Look up, validate and read the source image for a derive call.

    Raises:
        DeriveError: 404 if the resource or its file is missing, 400 if
            it is not an image or has no scope link.
    """
    source = await repo.get_resource_by_id(source_resource_id)
    if not source:
        raise DeriveError(status_code=404, detail="source resource not found")
    if not _is_image(source):
        raise DeriveError(
            status_code=400, detail="derive is only valid for image resources"
        )
    source_file_path = source.get("file_path")
    if not source_file_path:
        raise DeriveError(
            status_code=400, detail="source resource has no file on disk yet"
        )

    item = await repo.get_first_resource_item(source_resource_id)
    if not item:
        raise DeriveError(status_code=400, detail="source resource has no scope link")
    scope_id = str(item.get("scope_id") or "")
    if not scope_id:
        raise DeriveError(status_code=400, detail="source resource has no scope_id")

    return SourceImage(
        resource=source,
        scope_id=scope_id,
        folder_id=item.get("folder_id"),
        library_id=item.get("library_id"),
        file_bytes=_resolve_source_bytes(source_file_path),
        mime_type=source.get("mime_type") or None,
    )


async def persist_derived_image(
    repo: ResourceRepoProtocol,
    *,
    user_id: str,
    scope_id: str,
    folder_id: Optional[str],
    library_id: Optional[str],
    filename: str,
    image_bytes: bytes,
    mime_type: Optional[str],
) -> dict:
    """Persist derived image bytes as a new resource in ``scope_id``.

    Creates the resource row first (storage path is named by snowflake
    id, same convention as ``upload_resource``), writes the bytes
    atomically, then records version + scope-link rows. Returns the
    updated resource row carrying ``file_path``.
    """
    new_resource = await repo.create_resource(
        {
            "creator_id": user_id,
            "source_type": "derived",
            "filename": filename,
            "file_type": "image",
            "mime_type": mime_type or "image/png",
            "file_size_bytes": len(image_bytes),
            "current_version": 1,
            # We deliberately don't compute a file_hash: a hash of derived
            # bytes won't match anything in the dedupe index. Leave null.
        }
    )
    new_resource_id = str(new_resource["id"])

    relative_path = f"teams/{scope_id}/derived/{new_resource_id}/v1/{filename}"
    save_dir = Path(settings.DOWNLOAD_PATH) / Path(relative_path).parent
    save_dir.mkdir(parents=True, exist_ok=True)
    target = save_dir / filename
    # Atomic write: tmp + rename. Keeps a half-written file from being
    # observed if we crash mid-write.
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(save_dir))
    try:
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(image_bytes)
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
            "filename": filename,
            "file_path": relative_path,
            "file_size_bytes": len(image_bytes),
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
    return new_resource
