"""Pydantic schemas for the outpaint derive endpoint (Phase 3 Day 13)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.services.canvas.image_outpaint import MAX_PAD_PER_SIDE


class OutpaintDeriveRequest(BaseModel):
    """Per-side canvas extension, each a fraction of the source
    dimension on that axis. Geometry validation (at least one side,
    output dimension cap) lives in ``image_outpaint`` and surfaces
    as a 400.
    """

    left: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    top: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    right: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    bottom: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    # Collected for the future AI outpaint path; the v1 blur fill is
    # deterministic and ignores it.
    prompt: Optional[str] = Field(default=None, max_length=2000)
    # Optional override; defaults to ``outpaint-{source.filename}``.
    filename: Optional[str] = Field(default=None, max_length=255)


class OutpaintDeriveResponse(BaseModel):
    success: bool
    data: dict
