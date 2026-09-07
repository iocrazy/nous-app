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
from app.services.library.media_storage import materialize, store_local_file
from app.services.library.storage_errors import (
    discard_orphan_row,
    object_store_write_failed,
)
from app.services.library.storage_flag import unified_storage_enabled


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
    async def delete_resource(self, resource_id: str): ...


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


async def _resolve_source_bytes(file_path: str) -> bytes:
    """Read source resource bytes for either storage shape.

    ``sb://`` rows stream from the object store; filesystem rows read from
    under DOWNLOAD_PATH. Both go through ``materialize()``, which enforces
    the same containment guard the old fs-only reader had (a malformed
    ``../`` rel_path can never escape the resources volume).
    """
    import httpx

    try:
        async with materialize(file_path) as abs_path:
            if not abs_path.exists():
                raise FileNotFoundError(file_path)
            return abs_path.read_bytes()
    except ValueError as exc:  # materialize containment guard
        raise DeriveError(
            status_code=400,
            detail="resource file_path escapes the download root",
        ) from exc
    except FileNotFoundError as exc:
        raise DeriveError(
            status_code=404, detail="source resource file is missing on disk"
        ) from exc
    except httpx.HTTPStatusError as exc:
        # Object missing in the store (404) reads the same as a missing fs
        # file; any other storage-api failure is a real 5xx — let it
        # propagate to the router's generic 500 handler.
        if exc.response.status_code == 404:
            raise DeriveError(
                status_code=404, detail="source resource file is missing on disk"
            ) from exc
        raise


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
        file_bytes=await _resolve_source_bytes(source_file_path),
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

    # Dual-track write (storage unification, mirrors upload_resource): the
    # derived bytes are staged to a tmp file first — flag on tries the
    # object store (store_local_file sha256-hashes the tmp internally for
    # its content KEY; we deliberately still don't write file_hash to the
    # row, see create_resource above), flag off keeps the legacy atomic
    # tmp + os.replace filesystem write byte-identical. A storage FAILURE
    # with the flag on is hard and typed (2026-09-07) — the row created
    # above is discarded again.
    base = Path(settings.DOWNLOAD_PATH)
    base.mkdir(parents=True, exist_ok=True)
    # Tmp lives under DOWNLOAD_PATH so the fallback os.replace stays a
    # same-filesystem atomic rename.
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(base))
    try:
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(image_bytes)

        stored = None
        if await unified_storage_enabled():
            try:
                stored = await store_local_file(
                    scope_id=int(scope_id),
                    source_path=tmp_name,
                    mime=mime_type or "image/png",
                    filename=filename,
                )
            except Exception as exc:
                await discard_orphan_row(
                    repo.delete_resource, new_resource_id, where="persist_derived_image"
                )
                raise object_store_write_failed(
                    exc,
                    where="persist_derived_image",
                    scope_id=scope_id,
                    resource_id=new_resource_id,
                    filename=filename,
                    mime=mime_type or "image/png",
                    size_bytes=len(image_bytes),
                ) from exc
        if stored is not None:
            relative_path = stored.file_path
        else:
            relative_path = f"teams/{scope_id}/derived/{new_resource_id}/v1/{filename}"
            save_dir = base / Path(relative_path).parent
            save_dir.mkdir(parents=True, exist_ok=True)
            # Atomic write: tmp + rename. Keeps a half-written file from
            # being observed if we crash mid-write.
            os.replace(tmp_name, save_dir / filename)
    finally:
        # No-op when os.replace consumed the tmp; when the object-store
        # write succeeded (or anything raised), this cleans up the blob
        # (store_local_file only reads it, never deletes it).
        Path(tmp_name).unlink(missing_ok=True)

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
