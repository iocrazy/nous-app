"""Outpaint transform shared by the canvas derive path.

Extends encoded image bytes by per-side padding with the deterministic
blur fill (``extend_canvas``). Always free, no external deps.

The canvas outpaint UI still sends ``prompt`` + ``mode`` (the derive service
records them in the lineage params), but no generative fill is wired here: the
legacy nous-center workflow bridge that used to be tried first was retired on
2026-09-24. It was never configured in production, so every request already
ended up on this deterministic fill.
"""

from __future__ import annotations

from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.image_outpaint import (
    OutpaintError,
    Padding,
    extend_canvas,
)

# Same (status_code, detail) error contract as the other derive services.
OutpaintDeriveError = DeriveError


async def extend_image(
    file_bytes: bytes,
    mime_type: str | None,
    padding: Padding,
) -> bytes:
    """Extend encoded image bytes by ``padding`` using the blur fill.

    Raises ``OutpaintDeriveError`` (400) when the primitive rejects the input.
    """
    try:
        return extend_canvas(file_bytes, padding, mime_type=mime_type)
    except OutpaintError as exc:
        raise OutpaintDeriveError(status_code=400, detail=str(exc)) from exc
