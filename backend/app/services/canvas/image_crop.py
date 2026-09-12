"""Server-side counterpart to ``cropMath.ts`` (Phase 3 Day 4).

A single primitive — ``crop_normalized`` — that takes raw image bytes
plus a normalized ``[0, 1]`` crop region and returns a new image's
bytes containing only the cropped pixels. No DB writes, no storage
side-effects: those belong to a separate pipeline layer that wraps
this primitive.

The math matches the front-end ``cropMath.ts`` /
``regionToPixels`` semantics: integer pixel coordinates are derived
with ``round`` so the round-trip is symmetric. ``MIN_CROP``-style
floors are NOT applied here (the caller is expected to have already
clamped via the UI layer); we only reject geometrically impossible
inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from app.services.canvas.image_encode import to_encodable

# Subset of Pillow formats we round-trip cleanly. Pillow can encode many
# more, but resource library MIME enforcement keeps the live set small.
_FORMAT_BY_MIME: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
}


@dataclass(frozen=True)
class CropRegion:
    """Normalized crop region; mirrors the front-end ``CropRegion``."""

    x: float
    y: float
    width: float
    height: float


class CropError(ValueError):
    """Raised for invalid crop inputs (bad region, unsupported MIME, etc)."""


def _validate_region(region: CropRegion) -> None:
    """Reject geometrically impossible inputs.

    The front-end ``clampRegion`` already enforces ``[0, 1]`` bounds +
    ``MIN_CROP``; this is a server-side belt-and-braces.
    """
    for name, value in (
        ("x", region.x),
        ("y", region.y),
        ("width", region.width),
        ("height", region.height),
    ):
        if not (0.0 <= value <= 1.0):
            raise CropError(f"region.{name} must be in [0, 1], got {value}")
    if region.x + region.width > 1.0 + 1e-9:
        raise CropError(
            f"region.x + region.width must be <= 1, got {region.x + region.width}"
        )
    if region.y + region.height > 1.0 + 1e-9:
        raise CropError(
            f"region.y + region.height must be <= 1, got {region.y + region.height}"
        )
    if region.width <= 0 or region.height <= 0:
        raise CropError("region.width and region.height must be > 0")


def _resolve_format(image: Image.Image, mime_type: str | None) -> str:
    """Pick the Pillow format to encode with.

    Priority: explicit ``mime_type`` (caller knows their content-type) >
    Pillow's detected format on the source image > JPEG fallback.
    """
    if mime_type:
        normalized = mime_type.lower().strip()
        if normalized in _FORMAT_BY_MIME:
            return _FORMAT_BY_MIME[normalized]
        raise CropError(f"unsupported mime_type: {mime_type}")
    if image.format:
        return image.format
    return "JPEG"


def crop_normalized(
    image_bytes: bytes,
    region: CropRegion,
    *,
    mime_type: str | None = None,
) -> bytes:
    """Crop ``image_bytes`` by a normalized region and return the new bytes.

    Args:
        image_bytes: raw bytes of the source image.
        region: ``(x, y, width, height)`` in ``[0, 1]`` image-relative
            coordinates. The pixel rectangle is computed with
            ``round(value * pixel_dimension)`` to match the front-end
            ``regionToPixels`` helper.
        mime_type: optional content-type — when given, drives the output
            encoding. When omitted, falls back to the source image's
            detected format (else JPEG).

    Raises:
        CropError: on invalid region geometry or unsupported MIME.
    """
    _validate_region(region)

    with Image.open(BytesIO(image_bytes)) as image:
        # Decoding is lazy; force it now so we can inspect dimensions
        # and so the BytesIO can be closed once we leave the block.
        image.load()
        out_format = _resolve_format(image, mime_type)
        pixel_width, pixel_height = image.size
        left = int(round(region.x * pixel_width))
        top = int(round(region.y * pixel_height))
        right = int(round((region.x + region.width) * pixel_width))
        bottom = int(round((region.y + region.height) * pixel_height))
        # PIL's crop is end-exclusive; round-trip with regionToPixels
        # already accounts for that.
        if right <= left or bottom <= top:
            raise CropError(
                f"crop collapses to zero pixels (left={left}, right={right}, "
                f"top={top}, bottom={bottom})"
            )
        cropped = image.crop((left, top, right, bottom))

        # JPEG can't carry alpha — flatten if we'd otherwise lose a
        # transparency channel on encode.
        if out_format == "JPEG" and cropped.mode in ("RGBA", "LA", "P"):
            cropped = cropped.convert("RGB")
        # And any other mode the target format refuses (a CMYK scan reaching
        # the PNG path via ``transform_mime``) — otherwise Pillow raises and
        # the user reads it as "400 crop failed".
        cropped = to_encodable(cropped, out_format)

        buffer = BytesIO()
        cropped.save(buffer, format=out_format)
        return buffer.getvalue()
