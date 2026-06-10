"""Pydantic schemas for the grid-derive endpoint (Phase 3 Day 7)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.services.canvas.grid_split import MAX_LINES_PER_AXIS


class GridDeriveRequest(BaseModel):
    """Split-line positions, normalized [0, 1], strictly increasing.

    Geometry validation (range / ordering / minimum gap) lives in
    ``grid_split.tiles_from_lines`` — the single source of truth —
    and surfaces as a 400. The schema only bounds the payload size.
    """

    xs: list[float] = Field(default_factory=list, max_length=MAX_LINES_PER_AXIS)
    ys: list[float] = Field(default_factory=list, max_length=MAX_LINES_PER_AXIS)
    # Optional override; tiles default to ``grid-r{row}c{col}-{source}``.
    filename_prefix: Optional[str] = Field(default=None, max_length=64)


class GridTileModel(BaseModel):
    row: int
    col: int
    resource: dict


class GridDeriveResponse(BaseModel):
    success: bool
    data: dict
