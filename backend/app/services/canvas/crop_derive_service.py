"""Crop-derive service (Phase 3 Day 5).

Composes the pure ``crop_normalized`` primitive (Phase 3 Day 4) with
the shared derive pipeline (``derive_persistence``) so a user can crop
one of their images and end up with a new ``resources`` row plus
persisted file bytes.

Pipeline:

    1. ``load_source_image`` — resource + scope lookup, bytes read.
    2. ``crop_normalized(bytes, region, mime_type=...)``.
    3. ``persist_derived_image`` — new row, atomic file write,
       version + scope-link rows mirroring ``upload_resource``.

The service is intentionally thin (no DBOS workflow): cropping a
single image is fast and the response carries the new row, so the
front-end can use the result immediately without a Realtime hop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.repositories.resources_repository import ResourcesRepository
from app.services.canvas.derive_persistence import (
    DeriveError,
    ResourceRepoProtocol,
    load_source_image,
    persist_derived_image,
)
from app.services.canvas.image_crop import CropRegion, crop_normalized

logger = logging.getLogger(__name__)

# Backwards-compatible alias — the router and existing tests import
# ``CropDeriveError``; it has always carried (status_code, detail).
CropDeriveError = DeriveError


@dataclass(frozen=True)
class CropDeriveResult:
    resource: dict


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


def crop_image(file_bytes: bytes, mime_type: str | None, region: CropRegion) -> bytes:
    """Crop encoded image bytes by a normalized region.

    Shared by the resource derive path and the canvas derive path; the
    ``400 crop failed: …`` mapping lives here so both answer the same way.
    """
    try:
        return crop_normalized(file_bytes, region, mime_type=mime_type)
    except Exception as exc:
        raise CropDeriveError(status_code=400, detail=f"crop failed: {exc}") from exc


async def derive_crop_resource(
    *,
    source_resource_id: str,
    user_id: str,
    region: CropRegion,
    filename_override: Optional[str] = None,
    repo: Optional[ResourceRepoProtocol] = None,
) -> CropDeriveResult:
    """Crop ``source_resource_id`` by ``region`` and persist as a new
    sibling resource in the same scope.

    Raises:
        CropDeriveError: with the HTTP status code the router should
            surface (404 source missing, 400 invalid source / region,
            500 storage write failure).
    """
    repo = repo or ResourcesRepository()

    source = await load_source_image(repo, source_resource_id)
    cropped = crop_image(source.file_bytes, source.mime_type, region)

    new_resource = await persist_derived_image(
        repo,
        user_id=user_id,
        scope_id=source.scope_id,
        folder_id=source.folder_id,
        library_id=source.library_id,
        filename=_derived_filename(source.filename, filename_override),
        image_bytes=cropped,
        mime_type=source.mime_type,
    )
    return CropDeriveResult(resource=new_resource)
