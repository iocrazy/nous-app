"""Crop transform shared by the canvas derive path."""

from __future__ import annotations

from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.image_crop import CropRegion, crop_normalized

# Same (status_code, detail) error contract as the other derive transforms.
CropDeriveError = DeriveError


def crop_image(file_bytes: bytes, mime_type: str | None, region: CropRegion) -> bytes:
    """Crop encoded image bytes by a normalized region.

    The ``400 crop failed: …`` mapping lives here so every caller answers
    the same way.
    """
    try:
        return crop_normalized(file_bytes, region, mime_type=mime_type)
    except Exception as exc:
        raise CropDeriveError(status_code=400, detail=f"crop failed: {exc}") from exc
