"""Pure rows×cols grid split primitive.

Server-side counterpart to the classic-mode "split into a grid" action.
A single primitive — ``split_grid`` — takes raw image bytes plus a
``rows × cols`` grid and returns one re-encoded blob per cell in
row-major order. No DB writes, no storage side-effects: those belong to
``split_derive_service`` which wraps this primitive on top of the shared
derive pipeline.

This mirrors ``image_crop.crop_normalized`` in spirit: pure, in-memory,
unit-testable, and it re-encodes to the source MIME so the persisted
bytes match the content-type the resource library enforces.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from app.services.canvas.image_encode import to_encodable

# Subset of Pillow formats we round-trip cleanly — kept in sync with
# ``image_crop._FORMAT_BY_MIME``.
_FORMAT_BY_MIME: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
}

# Bound the grid so a single request can never mint hundreds of
# resources. 10×10 = 100 cells is the hard ceiling.
MAX_AXIS = 10


@dataclass(frozen=True)
class SplitCell:
    """One grid cell: its re-encoded bytes plus where it sits.

    ``index`` is the row-major position (``row * cols + col``)."""

    bytes: bytes
    row: int
    col: int
    index: int
    width: int
    height: int


def _resolve_format(image: Image.Image, mime_type: str | None) -> str:
    """Pick the Pillow format to encode each cell with.

    Priority: explicit ``mime_type`` > Pillow's detected source format >
    PNG fallback. Mirrors ``image_crop._resolve_format`` (PNG fallback
    here keeps lossless tiles by default)."""
    if mime_type:
        normalized = mime_type.lower().strip()
        if normalized in _FORMAT_BY_MIME:
            return _FORMAT_BY_MIME[normalized]
        raise ValueError(f"unsupported mime_type: {mime_type}")
    if image.format:
        return image.format
    return "PNG"


def _validate_axes(rows: int, cols: int) -> None:
    for name, value in (("rows", rows), ("cols", cols)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer, got {value!r}")
        if not (1 <= value <= MAX_AXIS):
            raise ValueError(f"{name} must be in [1, {MAX_AXIS}], got {value}")


def _encode_cell(cell: Image.Image, out_format: str) -> bytes:
    # JPEG can't carry alpha — flatten so the encode doesn't blow up.
    if out_format == "JPEG" and cell.mode in ("RGBA", "LA", "P"):
        cell = cell.convert("RGB")
    # Same for a mode the target refuses outright (CMYK reaching the PNG path
    # through ``transform_mime``): convert rather than let Pillow raise.
    cell = to_encodable(cell, out_format)
    buffer = BytesIO()
    cell.save(buffer, format=out_format)
    return buffer.getvalue()


def split_grid(
    image_bytes: bytes,
    rows: int,
    cols: int,
    mime_type: str | None = None,
) -> list[SplitCell]:
    """Split ``image_bytes`` into a ``rows × cols`` grid (row-major).

    Cell size is ``width // cols`` by ``height // rows``; the right-most
    column and bottom row absorb any integer-division remainder so no
    pixels are dropped.

    Args:
        image_bytes: raw bytes of the source image.
        rows: number of grid rows, ``1 <= rows <= 10``.
        cols: number of grid columns, ``1 <= cols <= 10``.
        mime_type: optional content-type driving the output encoding;
            falls back to the source's detected format (else PNG).

    Returns:
        A list of ``SplitCell`` in row-major order (``len == rows*cols``).

    Raises:
        ValueError: on out-of-range ``rows``/``cols``, an un-openable
            image, or a grid finer than the image's pixels.
    """
    _validate_axes(rows, cols)

    try:
        image = Image.open(BytesIO(image_bytes))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(f"could not open source image: {exc}") from exc

    with image:
        out_format = _resolve_format(image, mime_type)
        width, height = image.size
        cell_w = width // cols
        cell_h = height // rows
        if cell_w == 0 or cell_h == 0:
            raise ValueError(
                f"image {width}x{height} is too small for a {rows}x{cols} grid"
            )

        cells: list[SplitCell] = []
        for row in range(rows):
            top = row * cell_h
            bottom = height if row == rows - 1 else top + cell_h
            for col in range(cols):
                left = col * cell_w
                right = width if col == cols - 1 else left + cell_w
                tile = image.crop((left, top, right, bottom))
                cells.append(
                    SplitCell(
                        bytes=_encode_cell(tile, out_format),
                        row=row,
                        col=col,
                        index=row * cols + col,
                        width=right - left,
                        height=bottom - top,
                    )
                )
        return cells
