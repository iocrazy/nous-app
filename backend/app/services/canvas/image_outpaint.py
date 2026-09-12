"""Server-side blur-fill outpaint primitive (Phase 3 Day 13).

``extend_canvas`` grows an image's canvas by per-side padding
(fractions of the source dimensions) and fills the new area with a
blurred, stretched copy of the source — the classic "background blur
pad" used for aspect-ratio conversion (9:16 ↔ 16:9 etc).

This is the deterministic v1 fill. The outpaint UI also collects a
prompt; once a real generation provider (nous-center) ships an
outpaint workflow, the derive service swaps this primitive for the AI
path without touching the editor. No DB writes here.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageFilter

# Padding per side is a fraction of the source dimension on that axis.
# 2.0 = triple the axis when applied to both sides.
MAX_PAD_PER_SIDE = 2.0

# Hard ceiling on either output dimension — keeps a huge source plus
# max padding from allocating a gigapixel canvas.
MAX_OUTPUT_DIMENSION = 8192

# Blur radius scales with output size so the fill reads as "ambient
# background" at any resolution.
_BLUR_DIVISOR = 40
_MIN_BLUR_RADIUS = 8

_FORMAT_BY_MIME = {
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}


class OutpaintError(ValueError):
    """Raised for invalid outpaint inputs."""


@dataclass(frozen=True)
class Padding:
    """Per-side padding, each a fraction of the source dimension."""

    left: float
    top: float
    right: float
    bottom: float


def _validate_padding(padding: Padding) -> None:
    sides = (
        ("left", padding.left),
        ("top", padding.top),
        ("right", padding.right),
        ("bottom", padding.bottom),
    )
    for name, value in sides:
        if not (0.0 <= value <= MAX_PAD_PER_SIDE):
            raise OutpaintError(
                f"padding.{name} must be in [0, {MAX_PAD_PER_SIDE}], got {value}"
            )
    if all(value <= 0.0 for _, value in sides):
        raise OutpaintError("at least one side must have padding > 0")


def _resolve_format(image: Image.Image, mime_type: str | None) -> str:
    if mime_type:
        normalized = mime_type.lower().strip()
        if normalized in _FORMAT_BY_MIME:
            return _FORMAT_BY_MIME[normalized]
    if image.format:
        return image.format
    return "PNG"


def extend_canvas(
    image_bytes: bytes,
    padding: Padding,
    *,
    mime_type: str | None = None,
) -> bytes:
    """Grow the canvas by ``padding`` and blur-fill the new area.

    The fill is the source stretched to the full target size and
    Gaussian-blurred; the original is pasted on top at its offset, so
    every padded edge blends into colours sampled from the image.

    Raises:
        OutpaintError: invalid padding, output beyond
            ``MAX_OUTPUT_DIMENSION``, or undecodable source bytes.
    """
    _validate_padding(padding)

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.load()
            out_format = _resolve_format(source, mime_type)
            # NOTE — this encoder's alpha contract differs from its two
            # siblings, deliberately. Everything is flattened to RGB right
            # here, so outpaint DROPS a source's alpha (and, as a side effect,
            # never meets "cannot write mode CMYK as PNG"), whereas crop and
            # grid-split PRESERVE alpha and convert only modes the target
            # format refuses (``image_encode.to_encodable``). The blur fill
            # composites the whole frame, so a padded image has no meaningful
            # transparency left to keep. Spelled out because "the three
            # encoders treat alpha alike" is the assumption a reader of any
            # one of them would otherwise make.
            src = source.convert("RGB") if source.mode != "RGB" else source.copy()
    except OutpaintError:
        raise
    except Exception as exc:
        raise OutpaintError(f"source image failed to decode: {exc}") from exc

    width, height = src.size
    pad_left = int(round(padding.left * width))
    pad_top = int(round(padding.top * height))
    target_w = width + pad_left + int(round(padding.right * width))
    target_h = height + pad_top + int(round(padding.bottom * height))
    if target_w > MAX_OUTPUT_DIMENSION or target_h > MAX_OUTPUT_DIMENSION:
        raise OutpaintError(
            f"output {target_w}x{target_h} exceeds the "
            f"{MAX_OUTPUT_DIMENSION}px dimension cap"
        )
    if target_w == width and target_h == height:
        raise OutpaintError("padding rounds to zero pixels on every side")

    radius = max(_MIN_BLUR_RADIUS, max(target_w, target_h) // _BLUR_DIVISOR)
    background = src.resize((target_w, target_h), Image.LANCZOS).filter(
        ImageFilter.GaussianBlur(radius=radius)
    )
    background.paste(src, (pad_left, pad_top))

    if out_format == "JPEG" and background.mode != "RGB":
        background = background.convert("RGB")

    buffer = BytesIO()
    background.save(buffer, format=out_format)
    return buffer.getvalue()
