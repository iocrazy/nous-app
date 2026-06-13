"""Tests for the grid-split line geometry (Phase 3 Day 7)."""

from __future__ import annotations

import pytest

from app.services.canvas.grid_split import (
    MAX_LINES_PER_AXIS,
    MIN_GAP,
    GridSplitError,
    GridTile,
    tiles_from_lines,
)


def _assert_region(region, expected: tuple[float, float, float, float]) -> None:
    """Compare a CropRegion against an (x, y, width, height) tuple."""
    assert region.x == pytest.approx(expected[0])
    assert region.y == pytest.approx(expected[1])
    assert region.width == pytest.approx(expected[2])
    assert region.height == pytest.approx(expected[3])


# ============================================================
# Tile geometry
# ============================================================


class TestTileGeometry:
    def test_single_vertical_line_yields_two_tiles(self) -> None:
        tiles = tiles_from_lines(xs=[0.5], ys=[])
        assert len(tiles) == 2
        left, right = tiles
        assert (left.row, left.col) == (0, 0)
        assert (right.row, right.col) == (0, 1)
        assert left.region.x == 0.0
        assert left.region.width == 0.5
        assert right.region.x == 0.5
        assert right.region.width == 0.5
        # No horizontal lines → full height.
        assert left.region.y == 0.0
        assert left.region.height == 1.0

    def test_single_horizontal_line_yields_two_tiles(self) -> None:
        tiles = tiles_from_lines(xs=[], ys=[0.25])
        assert len(tiles) == 2
        top, bottom = tiles
        assert top.region.height == 0.25
        assert bottom.region.y == 0.25
        assert bottom.region.height == 0.75

    def test_one_line_each_axis_yields_row_major_quadrants(self) -> None:
        tiles = tiles_from_lines(xs=[0.5], ys=[0.5])
        assert [(t.row, t.col) for t in tiles] == [
            (0, 0),
            (0, 1),
            (1, 0),
            (1, 1),
        ]
        # Row-major: second tile is the top-right quadrant.
        _assert_region(tiles[1].region, (0.5, 0.0, 0.5, 0.5))

    def test_uneven_lines_produce_uneven_widths(self) -> None:
        tiles = tiles_from_lines(xs=[0.2, 0.7], ys=[])
        widths = [t.region.width for t in tiles]
        assert widths == pytest.approx([0.2, 0.5, 0.3])

    def test_regions_tile_the_unit_square_exactly(self) -> None:
        tiles = tiles_from_lines(xs=[0.3, 0.6], ys=[0.5])
        total_area = sum(t.region.width * t.region.height for t in tiles)
        assert total_area == pytest.approx(1.0)


# ============================================================
# Validation
# ============================================================


class TestValidation:
    def test_no_lines_at_all_is_rejected(self) -> None:
        with pytest.raises(GridSplitError, match="at least one"):
            tiles_from_lines(xs=[], ys=[])

    @pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
    def test_lines_must_be_strictly_inside_unit_interval(self, bad: float) -> None:
        with pytest.raises(GridSplitError):
            tiles_from_lines(xs=[bad], ys=[])

    def test_unsorted_lines_are_rejected(self) -> None:
        with pytest.raises(GridSplitError, match="increasing"):
            tiles_from_lines(xs=[0.7, 0.3], ys=[])

    def test_duplicate_lines_are_rejected(self) -> None:
        with pytest.raises(GridSplitError):
            tiles_from_lines(xs=[0.5, 0.5], ys=[])

    def test_lines_closer_than_min_gap_are_rejected(self) -> None:
        with pytest.raises(GridSplitError, match="gap"):
            tiles_from_lines(xs=[0.5, 0.5 + MIN_GAP / 2], ys=[])

    def test_line_too_close_to_edge_is_rejected(self) -> None:
        with pytest.raises(GridSplitError, match="gap"):
            tiles_from_lines(xs=[MIN_GAP / 2], ys=[])

    def test_too_many_lines_per_axis_is_rejected(self) -> None:
        step = 1.0 / (MAX_LINES_PER_AXIS + 2)
        too_many = [step * (i + 1) for i in range(MAX_LINES_PER_AXIS + 1)]
        with pytest.raises(GridSplitError, match="lines"):
            tiles_from_lines(xs=too_many, ys=[])

    def test_max_lines_per_axis_is_accepted(self) -> None:
        step = 1.0 / (MAX_LINES_PER_AXIS + 1)
        at_limit = [step * (i + 1) for i in range(MAX_LINES_PER_AXIS)]
        tiles = tiles_from_lines(xs=at_limit, ys=[])
        assert len(tiles) == MAX_LINES_PER_AXIS + 1


def test_grid_tile_is_immutable() -> None:
    tile = tiles_from_lines(xs=[0.5], ys=[])[0]
    assert isinstance(tile, GridTile)
    with pytest.raises(AttributeError):
        tile.row = 5  # type: ignore[misc]
