"""Tests for the pure ``split_grid`` primitive."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.services.canvas.image_grid_split import MAX_AXIS, split_grid


def _png_bytes(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (10, 200, 50))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode(blob: bytes) -> Image.Image:
    return Image.open(BytesIO(blob))


# ============================================================
# Happy path
# ============================================================


def test_2x2_split_of_4x4_image_yields_four_2x2_cells() -> None:
    cells = split_grid(_png_bytes(4, 4), rows=2, cols=2, mime_type="image/png")

    assert len(cells) == 4
    # Row-major order with correct row/col/index.
    assert [(c.row, c.col, c.index) for c in cells] == [
        (0, 0, 0),
        (0, 1, 1),
        (1, 0, 2),
        (1, 1, 3),
    ]
    # Every cell is 2x2 and decodes cleanly.
    for cell in cells:
        assert (cell.width, cell.height) == (2, 2)
        assert _decode(cell.bytes).size == (2, 2)


def test_1x1_split_returns_whole_image() -> None:
    cells = split_grid(_png_bytes(8, 6), rows=1, cols=1, mime_type="image/png")
    assert len(cells) == 1
    assert (cells[0].width, cells[0].height) == (8, 6)
    assert _decode(cells[0].bytes).size == (8, 6)


def test_remainder_pixels_absorbed_by_last_row_and_col() -> None:
    # 5x5 split 2x2: cell_w = cell_h = 2; last col/row absorb the
    # remaining pixel -> sizes 2 and 3.
    cells = split_grid(_png_bytes(5, 5), rows=2, cols=2, mime_type="image/png")
    by_pos = {(c.row, c.col): c for c in cells}
    assert (by_pos[(0, 0)].width, by_pos[(0, 0)].height) == (2, 2)
    assert (by_pos[(0, 1)].width, by_pos[(0, 1)].height) == (3, 2)
    assert (by_pos[(1, 1)].width, by_pos[(1, 1)].height) == (3, 3)
    # No pixels dropped: widths/heights tile back to the source.
    assert by_pos[(0, 0)].width + by_pos[(0, 1)].width == 5
    assert by_pos[(0, 0)].height + by_pos[(1, 0)].height == 5


def test_mime_drives_output_encoding() -> None:
    cells = split_grid(_png_bytes(4, 4), rows=2, cols=1, mime_type="image/jpeg")
    assert _decode(cells[0].bytes).format == "JPEG"


# ============================================================
# Validation / error paths
# ============================================================


@pytest.mark.parametrize("rows,cols", [(0, 2), (2, 0), (11, 1), (1, 11), (-1, 2)])
def test_out_of_range_axes_raise(rows: int, cols: int) -> None:
    with pytest.raises(ValueError):
        split_grid(_png_bytes(8, 8), rows=rows, cols=cols, mime_type="image/png")


def test_axis_ceiling_is_max_axis() -> None:
    # The exact ceiling is accepted; one beyond raises.
    cells = split_grid(
        _png_bytes(MAX_AXIS, MAX_AXIS),
        rows=MAX_AXIS,
        cols=MAX_AXIS,
        mime_type="image/png",
    )
    assert len(cells) == MAX_AXIS * MAX_AXIS


def test_bad_bytes_raise_value_error() -> None:
    with pytest.raises(ValueError):
        split_grid(b"not an image", rows=2, cols=2, mime_type="image/png")


def test_grid_finer_than_pixels_raises() -> None:
    # A 2x2 image can't be split into a 3x3 grid.
    with pytest.raises(ValueError):
        split_grid(_png_bytes(2, 2), rows=3, cols=3, mime_type="image/png")


def _cmyk_tiff_bytes(w: int, h: int) -> bytes:
    img = Image.new("CMYK", (w, h), (0, 40, 80, 10))
    buf = BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()


def _png_rgba_bytes(w: int, h: int) -> bytes:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 128))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_cmyk_source_splits_into_png_cells() -> None:
    # `transform_mime` hands a TIFF to the transforms as image/png, and Pillow
    # cannot write CMYK there — the split 400'd instead of re-encoding.
    cells = split_grid(_cmyk_tiff_bytes(8, 8), rows=2, cols=2, mime_type="image/png")
    assert len(cells) == 4
    for cell in cells:
        decoded = _decode(cell.bytes)
        assert decoded.format == "PNG"
        assert decoded.mode in ("RGB", "RGBA")


def test_rgba_cells_keep_their_alpha() -> None:
    # Negative control: a mode PNG can write is left alone.
    cells = split_grid(_png_rgba_bytes(8, 8), rows=2, cols=2, mime_type="image/png")
    assert {_decode(c.bytes).mode for c in cells} == {"RGBA"}
