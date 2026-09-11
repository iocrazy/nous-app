"""Pure pixel transforms used by the canvas derive path."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.services.canvas.crop_derive_service import CropDeriveError, crop_image
from app.services.canvas.grid_derive_service import (
    GridDeriveError,
    GridTileImage,
    split_image_by_lines,
)
from app.services.canvas.image_crop import CropRegion
from app.services.canvas.image_outpaint import Padding
from app.services.canvas.nous_center_runner import NousCenterNotConfigured
from app.services.canvas.outpaint_derive_service import (
    OutpaintDeriveError,
    extend_image,
)


def _png(w: int = 100, h: int = 50) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (10, 200, 50)).save(buf, format="PNG")
    return buf.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(data)) as img:
        return img.size


def test_crop_image_returns_the_region() -> None:
    out = crop_image(
        _png(100, 50), "image/png", CropRegion(x=0.0, y=0.0, width=0.5, height=1.0)
    )
    assert _size(out) == (50, 50)


def test_crop_image_maps_primitive_failure_to_400() -> None:
    with pytest.raises(CropDeriveError) as caught:
        crop_image(
            b"not an image",
            "image/png",
            CropRegion(x=0.0, y=0.0, width=0.5, height=0.5),
        )
    assert caught.value.status_code == 400
    assert caught.value.detail.startswith("crop failed:")


def test_split_image_by_lines_is_row_major() -> None:
    tiles = split_image_by_lines(_png(100, 50), "image/png", xs=[0.5], ys=[])
    assert [(t.row, t.col) for t in tiles] == [(0, 0), (0, 1)]
    assert all(isinstance(t, GridTileImage) for t in tiles)
    assert [_size(t.image_bytes) for t in tiles] == [(50, 50), (50, 50)]


def test_split_image_by_lines_rejects_no_lines_with_400() -> None:
    with pytest.raises(GridDeriveError) as caught:
        split_image_by_lines(_png(), "image/png", xs=[], ys=[])
    assert caught.value.status_code == 400
    assert caught.value.detail == "at least one split line is required"


def test_split_image_by_lines_maps_crop_failure_to_400() -> None:
    with pytest.raises(GridDeriveError) as caught:
        split_image_by_lines(b"not an image", "image/png", xs=[0.5], ys=[])
    assert caught.value.status_code == 400
    assert caught.value.detail.startswith("grid crop failed:")


async def test_extend_image_deterministic() -> None:
    out = await extend_image(
        _png(100, 50), "image/png", Padding(left=0.5, top=0.0, right=0.5, bottom=0.0)
    )
    assert _size(out) == (200, 50)


async def test_extend_image_rejects_zero_padding_with_400() -> None:
    with pytest.raises(OutpaintDeriveError) as caught:
        await extend_image(
            _png(), "image/png", Padding(left=0.0, top=0.0, right=0.0, bottom=0.0)
        )
    assert caught.value.status_code == 400


async def test_extend_image_ai_mode_falls_back_when_nous_unconfigured() -> None:
    with patch(
        "app.services.canvas.outpaint_derive_service.run_outpaint_via_nous",
        new=AsyncMock(side_effect=NousCenterNotConfigured("off")),
    ) as nous:
        out = await extend_image(
            _png(100, 50),
            "image/png",
            Padding(left=0.5, top=0.0, right=0.5, bottom=0.0),
            prompt="a windswept meadow",
            mode="ai",
            label="gen:5",
        )
    nous.assert_awaited_once()
    assert _size(out) == (200, 50)


async def test_extend_image_ai_mode_uses_nous_bytes_when_available() -> None:
    ai_bytes = _png(7, 7)
    with patch(
        "app.services.canvas.outpaint_derive_service.run_outpaint_via_nous",
        new=AsyncMock(return_value=ai_bytes),
    ) as nous:
        out = await extend_image(
            _png(100, 50),
            "image/png",
            Padding(left=0.5, top=0.0, right=0.5, bottom=0.0),
            prompt="a windswept meadow",
            mode="ai",
            label="gen:5",
        )
    assert out == ai_bytes
    assert nous.await_args.kwargs["prompt"] == "a windswept meadow"


async def test_extend_image_deterministic_mode_never_calls_nous() -> None:
    with patch(
        "app.services.canvas.outpaint_derive_service.run_outpaint_via_nous",
        new=AsyncMock(),
    ) as nous:
        out = await extend_image(
            _png(100, 50),
            "image/png",
            Padding(left=0.5, top=0.0, right=0.5, bottom=0.0),
            prompt="ignored",
            mode="deterministic",
        )
    nous.assert_not_awaited()
    assert _size(out) == (200, 50)
