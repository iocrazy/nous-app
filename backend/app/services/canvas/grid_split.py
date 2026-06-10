"""Split-line geometry for the grid-split tool (Phase 3 Day 7).

Turns user-drawn split lines into the row-major list of normalized
``CropRegion`` tiles they delimit. Each tile feeds straight into the
existing ``crop_normalized`` primitive, so grid split is "crop, N
times" — no new pixel code.

Coordinates mirror the front-end convention: normalized ``[0, 1]``
with (0, 0) at the image's top-left. ``xs`` are vertical split lines
(x positions); ``ys`` are horizontal split lines (y positions).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.services.canvas.image_crop import CropRegion

# 5 lines → 6 bands per axis → at most 36 tiles per request. Keeps a
# single derive call from fanning out into hundreds of DB writes.
MAX_LINES_PER_AXIS = 5

# Minimum normalized distance between adjacent lines (and between a
# line and the image edge). Stops degenerate slivers that would crop
# to zero pixels on small sources.
MIN_GAP = 0.02


class GridSplitError(ValueError):
    """Raised for invalid split-line inputs."""


@dataclass(frozen=True)
class GridTile:
    """One cell of the grid, in row-major order."""

    row: int
    col: int
    region: CropRegion


def _validate_axis(name: str, lines: Sequence[float]) -> None:
    if len(lines) > MAX_LINES_PER_AXIS:
        raise GridSplitError(
            f"{name}: at most {MAX_LINES_PER_AXIS} lines per axis, " f"got {len(lines)}"
        )
    for value in lines:
        if not (0.0 < value < 1.0):
            raise GridSplitError(
                f"{name}: lines must be strictly inside (0, 1), got {value}"
            )
    for prev, curr in zip(lines, lines[1:]):
        if curr <= prev:
            raise GridSplitError(
                f"{name}: lines must be strictly increasing " f"({prev} then {curr})"
            )
    # Gap check covers edges too: 0 → first line and last line → 1.
    bounds = [0.0, *lines, 1.0]
    for prev, curr in zip(bounds, bounds[1:]):
        if curr - prev < MIN_GAP:
            raise GridSplitError(
                f"{name}: gap between {prev} and {curr} is below the "
                f"minimum of {MIN_GAP}"
            )


def tiles_from_lines(*, xs: Sequence[float], ys: Sequence[float]) -> list[GridTile]:
    """Compute the row-major tiles delimited by the given split lines.

    Args:
        xs: vertical split-line positions, sorted ascending, each in
            the open interval (0, 1).
        ys: horizontal split-line positions, same constraints.

    Raises:
        GridSplitError: if both axes are empty, lines are out of range,
            unsorted, too dense (< ``MIN_GAP`` apart, edges included),
            or exceed ``MAX_LINES_PER_AXIS``.
    """
    if not xs and not ys:
        raise GridSplitError("at least one split line is required")
    _validate_axis("xs", xs)
    _validate_axis("ys", ys)

    col_edges = [0.0, *xs, 1.0]
    row_edges = [0.0, *ys, 1.0]
    return [
        GridTile(
            row=row,
            col=col,
            region=CropRegion(
                x=col_edges[col],
                y=row_edges[row],
                width=col_edges[col + 1] - col_edges[col],
                height=row_edges[row + 1] - row_edges[row],
            ),
        )
        for row in range(len(row_edges) - 1)
        for col in range(len(col_edges) - 1)
    ]
