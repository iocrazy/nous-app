"""``to_encodable`` — convert only the modes the target format refuses.

Characterization pin for the "do not change existing images" half of the
CMYK fix: the conversion has to rescue an unwritable mode without touching
anything Pillow already writes, or every transparent crop would go opaque
and every palette GIF would be re-quantized.
"""

from __future__ import annotations

import pytest
from PIL import Image

from app.services.canvas.image_encode import to_encodable


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "P", "L", "1", "LA"])
def test_png_writable_modes_come_back_untouched(mode: str) -> None:
    img = Image.new(mode, (4, 4))
    # The SAME object: no copy, no convert, no behaviour change.
    assert to_encodable(img, "PNG") is img


@pytest.mark.parametrize("mode", ["CMYK", "LAB", "HSV", "YCbCr"])
def test_png_unwritable_modes_become_rgb(mode: str) -> None:
    assert to_encodable(Image.new(mode, (4, 4)), "PNG").mode == "RGB"


def test_alpha_survives_a_conversion_into_png() -> None:
    # PA is not PNG-writable, but PNG carries alpha — dropping to RGB here
    # would lose it for no reason.
    assert to_encodable(Image.new("PA", (4, 4)), "PNG").mode == "RGBA"


@pytest.mark.parametrize("mode", ["RGBA", "LA", "P"])
def test_jpeg_flattens_alpha_modes(mode: str) -> None:
    assert to_encodable(Image.new(mode, (4, 4)), "JPEG").mode == "RGB"


def test_jpeg_keeps_cmyk(mode: str = "CMYK") -> None:
    # JPEG writes CMYK natively; converting would throw away the separation.
    img = Image.new(mode, (4, 4))
    assert to_encodable(img, "JPEG") is img


def test_webp_writes_cmyk_itself_so_it_is_left_alone() -> None:
    # Measured, not assumed: on the pinned Pillow, WEBP's encoder accepts CMYK
    # (it converts internally). Converting here would be a lossy no-op.
    img = Image.new("CMYK", (4, 4))
    assert to_encodable(img, "WEBP") is img


def test_gif_does_not_write_cmyk_and_is_converted() -> None:
    # The earlier comment claimed WEBP and GIF both swallow every mode. GIF
    # does not: `Image.new("CMYK").save(fmt="GIF")` raises on Pillow 12.1.1.
    # Unreachable today (a source that sniffs as GIF decodes to P/L/RGB), but
    # a table that documents a false fact is worse than no table.
    assert to_encodable(Image.new("CMYK", (4, 4)), "GIF").mode == "RGB"


def test_premultiplied_alpha_takes_the_only_route_pillow_offers() -> None:
    # `La` is PNG-unwritable AND cannot convert straight to RGB/RGBA —
    # Pillow raises "conversion from La to L not supported". Going via `LA`
    # (which it does support, and which PNG writes) is the only path that does
    # not turn a rescue into a crash.
    assert to_encodable(Image.new("La", (4, 4)), "PNG").mode in ("LA", "RGBA")
    # JPEG carries no alpha, so the same source has to land on RGB there.
    assert to_encodable(Image.new("La", (4, 4)), "JPEG").mode == "RGB"


def test_premultiplied_rgba_keeps_its_alpha() -> None:
    # RGBa is PNG-unwritable; it is still an alpha mode, so the target is RGBA.
    assert to_encodable(Image.new("RGBa", (4, 4)), "PNG").mode == "RGBA"


def test_mode_i_is_converted_rather_than_written_as_png() -> None:
    # Pillow 12.1.1 already warns that saving mode I as PNG is removed in
    # Pillow 13 (2026-10-15). Converting now keeps these images working across
    # that bump instead of turning the deprecation into a 400 on upgrade day.
    assert to_encodable(Image.new("I", (4, 4)), "PNG").mode == "RGB"
