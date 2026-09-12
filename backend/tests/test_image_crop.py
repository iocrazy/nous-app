"""Tests for the server-side normalized-region crop primitive
(Phase 3 Day 4)."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.services.canvas.image_crop import (
    CropError,
    CropRegion,
    crop_normalized,
)


def _png_bytes(
    width: int, height: int, fill: tuple[int, int, int] = (255, 0, 0)
) -> bytes:
    """Helper: render a solid-colour PNG of the given size."""
    img = Image.new("RGB", (width, height), fill)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpg_bytes(
    width: int, height: int, fill: tuple[int, int, int] = (0, 255, 0)
) -> bytes:
    img = Image.new("RGB", (width, height), fill)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _png_rgba_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 128))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode(blob: bytes) -> Image.Image:
    return Image.open(BytesIO(blob))


# ============================================================
# Region → pixel math
# ============================================================


class TestCropRegionMath:
    def test_full_image_region_returns_same_dimensions(self) -> None:
        src = _png_bytes(200, 100)
        out = crop_normalized(src, CropRegion(0.0, 0.0, 1.0, 1.0))
        decoded = _decode(out)
        assert decoded.size == (200, 100)

    def test_half_width_region_returns_half_width_pixels(self) -> None:
        src = _png_bytes(200, 100)
        out = crop_normalized(src, CropRegion(0.0, 0.0, 0.5, 1.0))
        decoded = _decode(out)
        assert decoded.size == (100, 100)

    def test_centre_quarter_region(self) -> None:
        src = _png_bytes(400, 400)
        out = crop_normalized(src, CropRegion(0.25, 0.25, 0.5, 0.5))
        decoded = _decode(out)
        assert decoded.size == (200, 200)


# ============================================================
# Invalid region rejection
# ============================================================


class TestRegionValidation:
    @pytest.mark.parametrize(
        "region",
        [
            CropRegion(-0.1, 0.0, 0.5, 0.5),
            CropRegion(0.0, -0.1, 0.5, 0.5),
            CropRegion(0.0, 0.0, 1.1, 0.5),
            CropRegion(0.0, 0.0, 0.5, 1.1),
        ],
    )
    def test_out_of_bounds_axis_value_raises(self, region: CropRegion) -> None:
        src = _png_bytes(100, 100)
        with pytest.raises(CropError):
            crop_normalized(src, region)

    def test_region_extending_past_right_edge_raises(self) -> None:
        src = _png_bytes(100, 100)
        with pytest.raises(CropError):
            crop_normalized(src, CropRegion(0.6, 0.0, 0.5, 0.5))

    def test_zero_width_region_raises(self) -> None:
        src = _png_bytes(100, 100)
        with pytest.raises(CropError):
            crop_normalized(src, CropRegion(0.1, 0.1, 0.0, 0.5))

    def test_region_collapsing_to_zero_pixels_raises(self) -> None:
        # 1x1 image + a region that rounds to zero pixels.
        src = _png_bytes(1, 1)
        with pytest.raises(CropError):
            crop_normalized(src, CropRegion(0.0, 0.0, 0.4, 0.4))


# ============================================================
# Output format selection
# ============================================================


class TestOutputFormat:
    def test_png_source_round_trips_as_png(self) -> None:
        src = _png_bytes(80, 80)
        out = crop_normalized(src, CropRegion(0.0, 0.0, 0.5, 0.5))
        assert _decode(out).format == "PNG"

    def test_jpeg_source_round_trips_as_jpeg(self) -> None:
        src = _jpg_bytes(80, 80)
        out = crop_normalized(src, CropRegion(0.0, 0.0, 0.5, 0.5))
        assert _decode(out).format == "JPEG"

    def test_explicit_mime_overrides_source_format(self) -> None:
        src = _png_bytes(80, 80)
        out = crop_normalized(
            src, CropRegion(0.0, 0.0, 0.5, 0.5), mime_type="image/jpeg"
        )
        assert _decode(out).format == "JPEG"

    def test_unsupported_mime_raises(self) -> None:
        src = _png_bytes(80, 80)
        with pytest.raises(CropError):
            crop_normalized(
                src,
                CropRegion(0.0, 0.0, 0.5, 0.5),
                mime_type="image/heic",
            )

    def test_rgba_source_flattens_when_target_is_jpeg(self) -> None:
        # JPEG can't carry alpha — the cropper must convert to RGB
        # before encoding instead of crashing.
        src = _png_rgba_bytes(80, 80)
        out = crop_normalized(
            src,
            CropRegion(0.0, 0.0, 0.5, 0.5),
            mime_type="image/jpeg",
        )
        decoded = _decode(out)
        assert decoded.format == "JPEG"
        assert decoded.mode == "RGB"


# ============================================================
# Pixel-content sanity
# ============================================================


class TestPixelContent:
    def test_cropped_region_pixel_matches_source(self) -> None:
        # Source: two halves — left red, right blue. Crop the right
        # half; the resulting pixel at (0, 0) must be blue.
        src_image = Image.new("RGB", (200, 100), (0, 0, 255))
        # Paint the left half red.
        for x in range(100):
            for y in range(100):
                src_image.putpixel((x, y), (255, 0, 0))
        buf = BytesIO()
        src_image.save(buf, format="PNG")
        out = crop_normalized(buf.getvalue(), CropRegion(0.5, 0.0, 0.5, 1.0))
        decoded = _decode(out)
        assert decoded.getpixel((0, 0)) == (0, 0, 255)
        assert decoded.size == (100, 100)


# ============================================================
# Source modes Pillow cannot write in the target format
# ============================================================


def _cmyk_tiff_bytes(width: int, height: int) -> bytes:
    """A CMYK TIFF — what a print-shop export or a scanner gives you.

    ``transform_mime`` hands every decodable-but-unwritable format to the
    transforms as ``image/png``, and Pillow refuses ``CMYK`` there
    ("cannot write mode CMYK as PNG"), which surfaced as a 400 on the crop.
    """
    img = Image.new("CMYK", (width, height), (0, 40, 80, 10))
    buf = BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()


class TestUnwritableSourceModes:
    def test_cmyk_source_encodes_as_png(self) -> None:
        out = crop_normalized(
            _cmyk_tiff_bytes(10, 8),
            CropRegion(0.0, 0.0, 0.5, 1.0),
            mime_type="image/png",
        )
        decoded = _decode(out)
        assert decoded.format == "PNG"
        assert decoded.mode in ("RGB", "RGBA")
        assert decoded.size == (5, 8)

    def test_rgba_png_keeps_its_alpha(self) -> None:
        # Negative control for the conversion above: a mode PNG *can* write
        # must not be touched, or every transparent crop would go opaque.
        out = crop_normalized(
            _png_rgba_bytes(80, 80),
            CropRegion(0.0, 0.0, 0.5, 0.5),
            mime_type="image/png",
        )
        decoded = _decode(out)
        assert decoded.mode == "RGBA"
        assert decoded.getpixel((0, 0))[3] == 128
