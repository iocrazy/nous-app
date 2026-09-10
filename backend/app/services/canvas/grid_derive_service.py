"""Grid split transform shared by the canvas derive path.

Splits encoded image bytes along user-drawn split lines, row-major:

    1. ``tiles_from_lines`` — split lines → row-major CropRegions.
    2. ``crop_normalized`` per tile, ALL in memory first, so a bad
       region fails the whole call before any caller persists a tile.

Tile count is bounded by ``MAX_LINES_PER_AXIS`` (≤ 36 tiles).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.grid_split import GridSplitError, tiles_from_lines
from app.services.canvas.image_crop import crop_normalized

# Same (status_code, detail) error contract as CropDeriveError.
GridDeriveError = DeriveError


@dataclass(frozen=True)
class GridTileImage:
    row: int
    col: int
    image_bytes: bytes


def split_image_by_lines(
    file_bytes: bytes,
    mime_type: str | None,
    *,
    xs: Sequence[float],
    ys: Sequence[float],
) -> list[GridTileImage]:
    """Split encoded image bytes along normalized lines, row-major.

    Every tile is cropped in memory before returning, so a bad region fails
    the whole call before any caller persists a single tile.
    """
    try:
        tiles = tiles_from_lines(xs=list(xs), ys=list(ys))
    except GridSplitError as exc:
        raise GridDeriveError(status_code=400, detail=str(exc)) from exc
    try:
        return [
            GridTileImage(
                row=tile.row,
                col=tile.col,
                image_bytes=crop_normalized(
                    file_bytes, tile.region, mime_type=mime_type
                ),
            )
            for tile in tiles
        ]
    except Exception as exc:
        raise GridDeriveError(
            status_code=400, detail=f"grid crop failed: {exc}"
        ) from exc
