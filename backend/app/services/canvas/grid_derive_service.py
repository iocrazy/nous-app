"""Grid-derive service (Phase 3 Day 7).

Splits a source image along user-drawn split lines and persists every
tile as a new sibling resource — "crop, N times" on top of the shared
derive pipeline:

    1. ``load_source_image`` — resource + scope lookup, bytes read.
    2. ``tiles_from_lines`` — split lines → row-major CropRegions.
    3. ``crop_normalized`` per tile, ALL in memory first, so a bad
       region fails the whole request before any DB write.
    4. ``persist_derived_image`` per tile (row + atomic file write +
       version + scope link).

Tile count is bounded by ``MAX_LINES_PER_AXIS`` (≤ 36 tiles), so the
sequential persist loop stays well under request-timeout territory.
If a persist fails mid-loop the earlier tiles remain (they are valid
resources in their own right); the router surfaces a 500.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from app.repositories.resources_repository import ResourcesRepository
from app.services.canvas.derive_persistence import (
    DeriveError,
    ResourceRepoProtocol,
    load_source_image,
    persist_derived_image,
)
from app.services.canvas.grid_split import GridSplitError, tiles_from_lines
from app.services.canvas.image_crop import crop_normalized

logger = logging.getLogger(__name__)

# Same (status_code, detail) error contract as CropDeriveError.
GridDeriveError = DeriveError

_DEFAULT_PREFIX = "grid"


@dataclass(frozen=True)
class GridTileResult:
    row: int
    col: int
    resource: dict


@dataclass(frozen=True)
class GridDeriveResult:
    rows: int
    cols: int
    tiles: list[GridTileResult]


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


def _tile_filename(prefix: str, row: int, col: int, source_filename: str) -> str:
    """``{prefix}-r{row}c{col}-{source}`` with 1-based row/col, keeping
    the source extension so the persisted bytes match the encoding."""
    suffix = source_filename or "tile.png"
    return f"{prefix}-r{row + 1}c{col + 1}-{suffix}"


async def derive_grid_resources(
    *,
    source_resource_id: str,
    user_id: str,
    xs: Sequence[float],
    ys: Sequence[float],
    filename_prefix: Optional[str] = None,
    repo: Optional[ResourceRepoProtocol] = None,
) -> GridDeriveResult:
    """Split ``source_resource_id`` along ``xs``/``ys`` and persist each
    tile as a new sibling resource in the same scope.

    Raises:
        GridDeriveError: with the HTTP status code the router should
            surface (404 source missing, 400 invalid source / lines,
            500 storage write failure).
    """
    repo = repo or ResourcesRepository()

    source = await load_source_image(repo, source_resource_id)
    tile_images = split_image_by_lines(
        source.file_bytes, source.mime_type, xs=xs, ys=ys
    )

    prefix = filename_prefix or _DEFAULT_PREFIX
    results: list[GridTileResult] = []
    for tile in tile_images:
        new_resource = await persist_derived_image(
            repo,
            user_id=user_id,
            scope_id=source.scope_id,
            folder_id=source.folder_id,
            library_id=source.library_id,
            filename=_tile_filename(prefix, tile.row, tile.col, source.filename),
            image_bytes=tile.image_bytes,
            mime_type=source.mime_type,
        )
        results.append(
            GridTileResult(row=tile.row, col=tile.col, resource=new_resource)
        )

    return GridDeriveResult(
        rows=len({t.row for t in tile_images}),
        cols=len({t.col for t in tile_images}),
        tiles=results,
    )
