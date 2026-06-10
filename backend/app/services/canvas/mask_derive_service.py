"""Mask-cutout derive service (Phase 3 Day 10).

Applies a user-painted mask to a source image and persists the
resulting RGBA cutout as a new sibling resource, via the shared
derive pipeline:

    1. ``load_source_image`` — resource + scope lookup, bytes read.
    2. base64-decode + size-cap the painted mask.
    3. ``apply_mask_cutout`` — mask resized to source dims,
       thresholded, alpha applied; rejects empty masks BEFORE any
       DB write.
    4. ``persist_derived_image`` — new row, atomic file write,
       version + scope link. Output is always ``image/png`` (the only
       library format that round-trips alpha).
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional

from app.repositories.resources_repository import ResourcesRepository
from app.services.canvas.derive_persistence import (
    DeriveError,
    ResourceRepoProtocol,
    load_source_image,
    persist_derived_image,
)
from app.services.canvas.image_mask import MaskError, apply_mask_cutout

logger = logging.getLogger(__name__)

# Same (status_code, detail) error contract as the other derive services.
MaskDeriveError = DeriveError

# Masks are painted at display resolution and binary — a PNG anywhere
# near this cap means something is wrong on the client.
MAX_MASK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class MaskDeriveResult:
    resource: dict


def _cutout_filename(source_filename: str, override: Optional[str]) -> str:
    """``cutout-{source stem}.png`` — extension forced to .png because
    the cutout always carries an alpha channel."""
    if override:
        return override
    stem = PurePosixPath(source_filename or "image").stem or "image"
    return f"cutout-{stem}.png"


def _decode_mask(mask_png_base64: str) -> bytes:
    # Reject oversized payloads before the (CPU-bound) b64 decode of
    # the full blob; base64 inflates by 4/3, so compare on that basis.
    if len(mask_png_base64) > MAX_MASK_BYTES * 4 // 3 + 4:
        raise MaskDeriveError(
            status_code=413,
            detail=f"mask exceeds the {MAX_MASK_BYTES // (1024 * 1024)}MB limit",
        )
    try:
        decoded = base64.b64decode(mask_png_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MaskDeriveError(
            status_code=400, detail="mask is not valid base64"
        ) from exc
    if len(decoded) > MAX_MASK_BYTES:
        raise MaskDeriveError(
            status_code=413,
            detail=f"mask exceeds the {MAX_MASK_BYTES // (1024 * 1024)}MB limit",
        )
    return decoded


async def derive_mask_cutout(
    *,
    source_resource_id: str,
    user_id: str,
    mask_png_base64: str,
    filename_override: Optional[str] = None,
    repo: Optional[ResourceRepoProtocol] = None,
) -> MaskDeriveResult:
    """Apply a painted mask to ``source_resource_id`` and persist the
    RGBA cutout as a new sibling resource in the same scope.

    Raises:
        MaskDeriveError: with the HTTP status code the router should
            surface (404 source missing, 400 invalid source / mask,
            413 oversized mask, 500 storage write failure).
    """
    repo = repo or ResourcesRepository()

    mask_bytes = _decode_mask(mask_png_base64)
    source = await load_source_image(repo, source_resource_id)
    try:
        cutout = apply_mask_cutout(source.file_bytes, mask_bytes)
    except MaskError as exc:
        raise MaskDeriveError(status_code=400, detail=str(exc)) from exc

    new_resource = await persist_derived_image(
        repo,
        user_id=user_id,
        scope_id=source.scope_id,
        folder_id=source.folder_id,
        library_id=source.library_id,
        filename=_cutout_filename(source.filename, filename_override),
        image_bytes=cutout,
        mime_type="image/png",
    )
    return MaskDeriveResult(resource=new_resource)
