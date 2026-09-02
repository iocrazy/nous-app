"""The preview tier: the canvas must never paint an original.

Measured 2026-09-02 on the user's 24-node canvas: 12 images, 15 MB, each
~1.3 MB PNG at 1672x941, served by /cover which claims to be a thumbnail and
is not. Every zoom re-uploads ~75 MB of decoded bitmap to the GPU.

Rules pinned here: 1024px max edge, WebP, key = original + ".preview.webp",
generated on first request and written back, and — the one that matters most
— any failure degrades to the original (today's behaviour), never to a 500.
"""

from __future__ import annotations

import io
import os

import pytest
from PIL import Image

from app.services.library.media_preview import (
    PREVIEW_MAX_EDGE,
    PREVIEW_SUFFIX,
    ensure_preview,
    preview_key,
    render_preview_webp,
)


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _noise_png(w: int, h: int) -> bytes:
    """An incompressible image — the honest stand-in for a real render.

    A flat colour is a degenerate input: both codecs collapse it to almost
    nothing, so a size ratio measured on it says very little. Noise is the
    opposite extreme and both codecs are at their worst on it, which makes the
    "preview is much smaller" claim mean something.
    """
    buf = io.BytesIO()
    Image.frombytes("RGB", (w, h), os.urandom(w * h * 3)).save(buf, "PNG")
    return buf.getvalue()


def test_preview_key_is_sibling_of_original():
    assert preview_key("t1/ab/cd/abcdef") == "t1/ab/cd/abcdef" + PREVIEW_SUFFIX


def test_render_scales_longest_edge_to_1024_and_emits_webp():
    out = render_preview_webp(_png(1672, 941))
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP"
    assert max(img.size) == PREVIEW_MAX_EDGE
    assert abs(img.size[0] / img.size[1] - 1672 / 941) < 0.01  # aspect preserved


def test_render_scales_the_longest_edge_when_the_image_is_portrait():
    """The cap is on the LONGEST edge, whichever one that is."""
    img = Image.open(io.BytesIO(render_preview_webp(_png(941, 1672))))
    assert img.size[1] == PREVIEW_MAX_EDGE
    assert img.size[0] < PREVIEW_MAX_EDGE


def test_render_does_not_upscale_small_images():
    out = render_preview_webp(_png(640, 480))
    assert Image.open(io.BytesIO(out)).size == (640, 480)


def test_render_is_much_smaller_than_the_original():
    for src in (_png(1672, 941), _noise_png(1672, 941)):
        assert len(render_preview_webp(src)) < len(src) / 4


def test_render_raises_on_input_that_is_not_an_image():
    """The pure renderer raises; only ensure_preview turns that into None."""
    with pytest.raises(Exception):
        render_preview_webp(b"not a png")


class _FakeStore:
    """Mirrors ObjectStore's real surface — including put_bytes' positional
    ``mime`` third parameter. A stub with a looser signature would let a call
    that TypeErrors in production pass here (2026-08-12 discipline)."""

    def __init__(self, objects: dict[str, bytes], *, fail_put: bool = False):
        self.objects = objects
        self.fail_put = fail_put
        self.puts: list[str] = []
        self.put_mimes: list[str] = []

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    async def put_bytes(
        self, key: str, data: bytes, mime: str, *, upsert: bool = True
    ) -> None:
        if self.fail_put:
            raise RuntimeError("object store down")
        self.objects[key] = data
        self.puts.append(key)
        self.put_mimes.append(mime)


@pytest.mark.asyncio
async def test_ensure_preview_generates_writes_back_and_returns():
    store = _FakeStore({"t1/a/b/orig": _png(1672, 941)})
    row = {"file_path": "sb://library/t1/a/b/orig", "mime": "image/png"}
    out = await ensure_preview(row, store_factory=lambda bucket: store)
    assert out is not None and Image.open(io.BytesIO(out)).format == "WEBP"
    assert store.puts == ["t1/a/b/orig" + PREVIEW_SUFFIX]  # written back once
    assert store.put_mimes == ["image/webp"]


@pytest.mark.asyncio
async def test_ensure_preview_writes_back_into_the_originals_bucket():
    seen: list[str] = []
    store = _FakeStore({"t1/a/b/orig": _png(1672, 941)})

    def _factory(bucket: str):
        seen.append(bucket)
        return store

    await ensure_preview(
        {"file_path": "sb://library/t1/a/b/orig", "mime": "image/png"},
        store_factory=_factory,
    )
    assert seen == ["library"]


@pytest.mark.asyncio
async def test_ensure_preview_serves_existing_without_regenerating():
    existing = render_preview_webp(_png(800, 600))
    store = _FakeStore(
        {
            "t1/a/b/orig": _png(1672, 941),
            "t1/a/b/orig" + PREVIEW_SUFFIX: existing,
        }
    )
    out = await ensure_preview(
        {"file_path": "sb://library/t1/a/b/orig", "mime": "image/png"},
        store_factory=lambda b: store,
    )
    assert out == existing and store.puts == []


@pytest.mark.asyncio
async def test_ensure_preview_returns_none_when_write_back_fails():
    # The caller falls back to the original — a failed preview must never 500.
    store = _FakeStore({"t1/a/b/orig": _png(1672, 941)}, fail_put=True)
    out = await ensure_preview(
        {"file_path": "sb://library/t1/a/b/orig", "mime": "image/png"},
        store_factory=lambda b: store,
    )
    assert out is None


@pytest.mark.asyncio
async def test_ensure_preview_returns_none_for_corrupt_original():
    store = _FakeStore({"t1/a/b/orig": b"not a png"})
    assert (
        await ensure_preview(
            {"file_path": "sb://library/t1/a/b/orig", "mime": "image/png"},
            store_factory=lambda b: store,
        )
        is None
    )


@pytest.mark.asyncio
async def test_ensure_preview_returns_none_when_the_original_is_gone():
    store = _FakeStore({})
    assert (
        await ensure_preview(
            {"file_path": "sb://library/t1/a/b/orig", "mime": "image/png"},
            store_factory=lambda b: store,
        )
        is None
    )


@pytest.mark.asyncio
async def test_ensure_preview_returns_none_for_filesystem_rows_this_release():
    assert (
        await ensure_preview({"file_path": "generated/x.png", "mime": "image/png"})
        is None
    )


@pytest.mark.asyncio
async def test_ensure_preview_returns_none_for_a_row_with_no_file_path():
    assert await ensure_preview({"mime": "image/png"}) is None
