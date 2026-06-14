"""Pydantic schemas for the grid-split derive endpoint (classic mode)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.canvas.image_grid_split import MAX_AXIS


class SplitDeriveRequest(BaseModel):
    """A ``rows × cols`` grid request.

    Bounds are enforced here (payload guard) AND in
    ``image_grid_split.split_grid`` (single source of truth) — the
    latter surfaces as a 400 if a caller bypasses the schema.
    """

    rows: int = Field(..., ge=1, le=MAX_AXIS)
    cols: int = Field(..., ge=1, le=MAX_AXIS)
