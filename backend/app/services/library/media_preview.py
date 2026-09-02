"""Canvas preview tier for generated media.

``/cover`` used to hand the browser the ORIGINAL (1.3 MB PNGs on a 24-node
canvas = 15 MB, ~75 MB decoded and re-uploaded to the GPU on every zoom). The
canvas never needs more than ~1024px; the lightbox and the editor ask for the
original explicitly with ``?full=1``.

Previews are derived by key convention (original key + ``.preview.webp``, same
bucket), generated on first request and written back. No table, no column, no
migration — which also means every historical row self-heals the first time it
is painted.

Every failure returns ``None`` so the caller serves the original — today's
behaviour — instead of a 500. That is the whole safety story of this module:
a preview is an optimisation, and an optimisation that can break the page is
not one.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any, Callable, Optional

from loguru import logger

from app.services.library.media_storage import ObjectStore, resolve_media_source

PREVIEW_MAX_EDGE = 1024
PREVIEW_SUFFIX = ".preview.webp"
PREVIEW_MIME = "image/webp"
_WEBP_QUALITY = 82
_WEBP_METHOD = 4


def preview_key(key: str) -> str:
    """The preview object's key: a sibling of the original, same bucket."""
    return f"{key}{PREVIEW_SUFFIX}"


def render_preview_webp(data: bytes) -> bytes:
    """Longest edge → ``PREVIEW_MAX_EDGE`` (never upscaled), encoded as WebP.

    Pure function. Raises on anything Pillow cannot decode — ``ensure_preview``
    is the only place that turns a failure into the fall-back-to-original None.
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as opened:
        opened.load()
        img = opened
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        w, h = img.size
        scale = PREVIEW_MAX_EDGE / max(w, h)
        if scale < 1:
            img = img.resize(
                (max(1, round(w * scale)), max(1, round(h * scale))),
                Image.LANCZOS,
            )
        out = io.BytesIO()
        img.save(out, "WEBP", quality=_WEBP_QUALITY, method=_WEBP_METHOD)
        return out.getvalue()


async def ensure_preview(
    row: dict[str, Any], *, store_factory: Optional[Callable[[str], Any]] = None
) -> Optional[bytes]:
    """Preview bytes for an object-store image row, generating and writing back
    on first use.

    Returns ``None`` — never raises — for a filesystem row (dev without object
    storage, this release) and for ANY failure along the way: the object is
    gone, the bytes are not an image, the write-back fails. The caller serves
    the original in every one of those cases.

    Two concurrent first hits may both render and both PUT. The write is an
    idempotent overwrite of identical bytes, so the only cost is one wasted
    render; locking would buy nothing.
    """
    loc = resolve_media_source(str(row.get("file_path") or ""))
    if not loc.is_object_store or not loc.key or not loc.bucket:
        return None
    store = (store_factory or ObjectStore)(loc.bucket)
    pkey = preview_key(loc.key)
    try:
        if await store.exists(pkey):
            return await store.get_bytes(pkey)
        original = await store.get_bytes(loc.key)
        # Off the event loop for two reasons. (1) Decode + LANCZOS + WebP
        # method-4 is ~100-200 ms of pure CPU for the measured 1672x941
        # originals, and a cold 24-node canvas fires a dozen first-hits at
        # once — inline, nothing else in this worker progresses for 1-2 s.
        # (2) ObjectStore._capped puts a WALL-CLOCK asyncio.wait_for around
        # every storage call, so a blocked loop burns other requests' timeout
        # budget without them making progress and turns a CPU spike into
        # someone else's failed upload. Pillow drops the GIL in the codec
        # loops, so this genuinely parallelises rather than just relocating
        # the block. Exceptions raised in the thread propagate through the
        # await into the same degradation branch below — unchanged contract.
        preview = await asyncio.to_thread(render_preview_webp, original)
        await store.put_bytes(pkey, preview, PREVIEW_MIME)
        return preview
    except Exception as exc:
        logger.warning("[preview] falling back to original for {}: {}", loc.key, exc)
        return None
