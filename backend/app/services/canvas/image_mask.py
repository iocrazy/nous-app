"""Server-side mask-cutout primitive (Phase 3 Day 10).

A single primitive — ``apply_mask_cutout`` — that takes raw image
bytes plus a painted mask (any Pillow-decodable image, typically the
PNG exported from the front-end brush editor) and returns an RGBA PNG
where mask-white pixels keep the source colour and mask-black pixels
become fully transparent.

The mask is rescaled to the source dimensions with nearest-neighbour
(masks are binary; interpolation would invent grey edges) and
thresholded at 128, so the front-end can paint at display resolution
without caring about the source bitmap size.

Output is always PNG: it is the only format in the resource library's
image set that round-trips an alpha channel losslessly.

No DB writes, no storage side-effects — those belong to the derive
pipeline that wraps this primitive.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

# White (kept) pixels at or above this luminance survive the cutout.
MASK_THRESHOLD = 128


class MaskError(ValueError):
    """Raised for invalid mask inputs (undecodable bytes, empty mask)."""


def apply_mask_cutout(image_bytes: bytes, mask_bytes: bytes) -> bytes:
    """Apply ``mask_bytes`` to ``image_bytes`` and return an RGBA PNG.

    Args:
        image_bytes: raw bytes of the source image (any Pillow format).
        mask_bytes: raw bytes of the painted mask. Converted to
            greyscale, rescaled to the source size, thresholded at
            ``MASK_THRESHOLD`` — white = keep, black = transparent.

    Raises:
        MaskError: when either input fails to decode or the thresholded
            mask keeps zero pixels (an all-transparent cutout is never
            what the user meant).
    """
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.load()
            rgba = source.convert("RGBA")
    except MaskError:
        raise
    except Exception as exc:
        raise MaskError(f"source image failed to decode: {exc}") from exc

    try:
        with Image.open(BytesIO(mask_bytes)) as mask_img:
            mask_img.load()
            grey = mask_img.convert("L")
    except Exception as exc:
        raise MaskError(f"mask failed to decode: {exc}") from exc

    if grey.size != rgba.size:
        grey = grey.resize(rgba.size, Image.NEAREST)
    alpha = grey.point(lambda v: 255 if v >= MASK_THRESHOLD else 0)

    if alpha.getbbox() is None:
        raise MaskError("mask is empty — no pixels would be kept")

    rgba.putalpha(alpha)
    buffer = BytesIO()
    rgba.save(buffer, format="PNG")
    return buffer.getvalue()
