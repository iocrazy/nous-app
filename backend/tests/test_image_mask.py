"""Tests for the server-side mask-cutout primitive (Phase 3 Day 10)."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.services.canvas.image_mask import MaskError, apply_mask_cutout


def _png_bytes(
    width: int, height: int, fill: tuple[int, int, int] = (255, 0, 0)
) -> bytes:
    img = Image.new("RGB", (width, height), fill)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpg_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), (0, 255, 0))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _mask_bytes(
    width: int,
    height: int,
    *,
    white_box: tuple[int, int, int, int] | None = None,
) -> bytes:
    """Black mask with an optional white rectangle (kept region).

    ``white_box`` is (left, top, right, bottom), end-exclusive.
    """
    img = Image.new("L", (width, height), 0)
    if white_box:
        left, top, right, bottom = white_box
        for y in range(top, bottom):
            for x in range(left, right):
                img.putpixel((x, y), 255)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode(blob: bytes) -> Image.Image:
    return Image.open(BytesIO(blob))


class TestCutout:
    def test_output_is_rgba_png_with_source_dimensions(self) -> None:
        src = _png_bytes(100, 50)
        mask = _mask_bytes(100, 50, white_box=(0, 0, 50, 50))
        out = _decode(apply_mask_cutout(src, mask))
        assert out.format == "PNG"
        assert out.mode == "RGBA"
        assert out.size == (100, 50)

    def test_white_mask_area_is_opaque_black_area_transparent(self) -> None:
        src = _png_bytes(100, 50)
        mask = _mask_bytes(100, 50, white_box=(0, 0, 50, 50))
        out = _decode(apply_mask_cutout(src, mask))
        assert out.getpixel((25, 25))[3] == 255  # inside the white box
        assert out.getpixel((75, 25))[3] == 0  # outside

    def test_mask_is_rescaled_to_source_dimensions(self) -> None:
        # Mask painted at half resolution: white left half.
        src = _png_bytes(200, 100)
        mask = _mask_bytes(100, 50, white_box=(0, 0, 50, 50))
        out = _decode(apply_mask_cutout(src, mask))
        assert out.size == (200, 100)
        assert out.getpixel((50, 50))[3] == 255  # left half kept
        assert out.getpixel((150, 50))[3] == 0  # right half transparent

    def test_kept_pixels_preserve_source_colour(self) -> None:
        src = _png_bytes(10, 10, fill=(12, 34, 56))
        mask = _mask_bytes(10, 10, white_box=(0, 0, 10, 10))
        out = _decode(apply_mask_cutout(src, mask))
        assert out.getpixel((5, 5)) == (12, 34, 56, 255)

    def test_jpeg_source_is_supported(self) -> None:
        src = _jpg_bytes(60, 40)
        mask = _mask_bytes(60, 40, white_box=(0, 0, 30, 40))
        out = _decode(apply_mask_cutout(src, mask))
        assert out.mode == "RGBA"
        assert out.getpixel((10, 10))[3] == 255

    def test_grey_mask_thresholds_at_128(self) -> None:
        src = _png_bytes(4, 1)
        mask_img = Image.new("L", (4, 1))
        mask_img.putpixel((0, 0), 0)
        mask_img.putpixel((1, 0), 120)
        mask_img.putpixel((2, 0), 135)
        mask_img.putpixel((3, 0), 255)
        buf = BytesIO()
        mask_img.save(buf, format="PNG")
        out = _decode(apply_mask_cutout(src, buf.getvalue()))
        assert out.getpixel((0, 0))[3] == 0
        assert out.getpixel((1, 0))[3] == 0
        assert out.getpixel((2, 0))[3] == 255
        assert out.getpixel((3, 0))[3] == 255


class TestValidation:
    def test_empty_mask_rejected(self) -> None:
        src = _png_bytes(10, 10)
        mask = _mask_bytes(10, 10)  # all black — nothing kept
        with pytest.raises(MaskError, match="empty"):
            apply_mask_cutout(src, mask)

    def test_garbage_mask_bytes_rejected(self) -> None:
        src = _png_bytes(10, 10)
        with pytest.raises(MaskError):
            apply_mask_cutout(src, b"not a png")

    def test_garbage_image_bytes_rejected(self) -> None:
        mask = _mask_bytes(10, 10, white_box=(0, 0, 5, 5))
        with pytest.raises(MaskError):
            apply_mask_cutout(b"not an image", mask)
