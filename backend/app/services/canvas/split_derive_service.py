"""Grid-split derive service (classic-mode split).

Mirrors ``crop_derive_service`` exactly, but produces N frames instead
of one. Composes the pure ``split_grid`` primitive with the shared
derive pipeline (``derive_persistence``) so a user can split one of
their images into a ``rows × cols`` grid and end up with N new
``resources`` rows — each in the SAME scope as the source, so they
automatically appear in the Project Assets (工程资产) UI.

Pipeline:

    1. ``load_source_image`` — resource + scope lookup, bytes read.
    2. ``split_grid(bytes, rows, cols, mime_type=...)`` — all cells
       computed in memory first, so a bad grid fails the whole request
       before any DB write.
    3. ``persist_derived_image`` per cell — new row, atomic file write,
       version + scope-link rows mirroring ``upload_resource``.

Intentionally thin (no DBOS workflow): splitting a single image is
fast and the response carries the new rows, so the front-end can use
the result immediately without a Realtime hop.
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
from app.services.canvas.image_grid_split import split_grid

logger = logging.getLogger(__name__)

# Same (status_code, detail) error contract as CropDeriveError.
SplitDeriveError = DeriveError


@dataclass(frozen=True)
class SplitDeriveResult:
    frames: list[dict]


def _frame_filename(source_filename: str, row: int, col: int) -> str:
    """``split-{row}x{col}-{source}`` keeping the source extension so the
    persisted bytes match the encoding."""
    suffix = source_filename or "split.png"
    return f"split-{row}x{col}-{suffix}"


async def derive_split_resource(
    *,
    source_resource_id: str,
    user_id: str,
    rows: int,
    cols: int,
    repo: Optional[ResourceRepoProtocol] = None,
) -> SplitDeriveResult:
    """Split ``source_resource_id`` into a ``rows × cols`` grid and
    persist every frame as a new sibling resource in the same scope.

    Raises:
        SplitDeriveError: with the HTTP status code the router should
            surface (404 source missing, 400 invalid source / grid,
            500 storage write failure).
    """
    repo = repo or ResourcesRepository()

    source = await load_source_image(repo, source_resource_id)
    try:
        cells = split_grid(source.file_bytes, rows, cols, source.mime_type)
    except ValueError as exc:
        raise SplitDeriveError(status_code=400, detail=f"split failed: {exc}") from exc

    frames: list[dict] = []
    for cell in cells:
        new_resource = await persist_derived_image(
            repo,
            user_id=user_id,
            scope_id=source.scope_id,
            folder_id=source.folder_id,
            library_id=source.library_id,
            filename=_frame_filename(source.filename, cell.row, cell.col),
            image_bytes=cell.bytes,
            mime_type=source.mime_type,
        )
        frames.append(
            {
                **new_resource,
                "row": cell.row,
                "col": cell.col,
                "index": cell.index,
            }
        )

    return SplitDeriveResult(frames=frames)
