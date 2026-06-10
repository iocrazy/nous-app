"""Tests for the server-side blur-fill outpaint primitive (Phase 3 Day 13)."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.services.canvas.image_outpaint import (
    MAX_PAD_PER_SIDE,
    OutpaintError,
    Padding,
    extend_canvas,
)


def _png_bytes(
    width: int, height: int, fill: tuple[int, int, int] = (200, 30, 30)
) -> bytes:
    img = Image.new("RGB", (width, height), fill)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpg_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), (10, 200, 50))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _decode(blob: bytes) -> Image.Image:
    return Image.open(BytesIO(blob))


class TestGeometry:
    def test_symmetric_horizontal_pad_grows_width(self) -> None:
        src = _png_bytes(100, 100)
        out = _decode(extend_canvas(src, Padding(left=0.5, top=0, right=0.5, bottom=0)))
        assert out.size == (200, 100)

    def test_single_side_pad(self) -> None:
        src = _png_bytes(100, 50)
        out = _decode(extend_canvas(src, Padding(left=0, top=0, right=0, bottom=1.0)))
        assert out.size == (100, 100)

    def test_original_pixels_preserved_at_offset(self) -> None:
        src = _png_bytes(50, 50, fill=(1, 2, 3))
        out = _decode(extend_canvas(src, Padding(left=0.2, top=0.2, right=0, bottom=0)))
        # Original pasted at (10, 10); its centre keeps the exact colour.
        assert out.size == (60, 60)
        assert out.getpixel((35, 35))[:3] == (1, 2, 3)

    def test_fill_area_is_painted_not_black(self) -> None:
        src = _png_bytes(50, 50, fill=(200, 30, 30))
        out = _decode(extend_canvas(src, Padding(left=0.5, top=0, right=0, bottom=0)))
        # The blur fill derives from the source, so the pad region
        # should be reddish — definitely not pure black.
        pad_pixel = out.getpixel((5, 25))[:3]
        assert pad_pixel != (0, 0, 0)
        assert pad_pixel[0] > 100  # red-dominant

    def test_jpeg_source_round_trips_as_jpeg(self) -> None:
        src = _jpg_bytes(80, 40)
        out = _decode(extend_canvas(src, Padding(0.25, 0, 0.25, 0)))
        assert out.format == "JPEG"
        assert out.size == (120, 40)

    def test_png_source_round_trips_as_png(self) -> None:
        src = _png_bytes(80, 40)
        out = _decode(extend_canvas(src, Padding(0, 0.5, 0, 0)))
        assert out.format == "PNG"


class TestValidation:
    def test_zero_padding_everywhere_is_rejected(self) -> None:
        with pytest.raises(OutpaintError, match="at least one side"):
            extend_canvas(_png_bytes(10, 10), Padding(0, 0, 0, 0))

    @pytest.mark.parametrize("bad", [-0.1, MAX_PAD_PER_SIDE + 0.01])
    def test_out_of_range_padding_is_rejected(self, bad: float) -> None:
        with pytest.raises(OutpaintError):
            extend_canvas(_png_bytes(10, 10), Padding(bad, 0, 0, 0))

    def test_output_dimension_cap(self) -> None:
        # 6000px wide source padded 2x per side → 30000px > cap.
        src = _png_bytes(6000, 10)
        with pytest.raises(OutpaintError, match="exceeds"):
            extend_canvas(src, Padding(MAX_PAD_PER_SIDE, 0, MAX_PAD_PER_SIDE, 0))

    def test_garbage_image_rejected(self) -> None:
        with pytest.raises(OutpaintError):
            extend_canvas(b"not an image", Padding(0.5, 0, 0, 0))
