"""Pydantic schemas for the mask-cutout derive endpoint (Phase 3 Day 10)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.services.canvas.mask_derive_service import MAX_MASK_BYTES


class MaskDeriveRequest(BaseModel):
    """Painted mask as a base64 PNG (white = keep, black = transparent).

    Decoding, size enforcement and emptiness checks live in
    ``mask_derive_service`` — the schema only bounds the raw payload.
    """

    mask_png_base64: str = Field(..., min_length=1, max_length=MAX_MASK_BYTES * 2)
    # Optional override; defaults to ``cutout-{source stem}.png``.
    filename: Optional[str] = Field(default=None, max_length=255)


class MaskDeriveResponse(BaseModel):
    success: bool
    data: dict
