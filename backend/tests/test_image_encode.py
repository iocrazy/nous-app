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


@pytest.mark.parametrize("fmt", ["WEBP", "GIF"])
def test_formats_that_convert_internally_are_left_alone(fmt: str) -> None:
    img = Image.new("CMYK", (4, 4))
    assert to_encodable(img, fmt) is img
