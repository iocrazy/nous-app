"""Request bodies for the canvas-scoped derive endpoints.

Geometry validation (region bounds beyond the field ranges, line ordering,
padding caps) stays in the pure primitives and surfaces as a 400 — the schema
only bounds payload shape and size, same split as the resource derive schemas.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.canvas_crop_schema import CropRegionModel
from app.services.canvas.grid_split import MAX_LINES_PER_AXIS
from app.services.canvas.image_outpaint import MAX_PAD_PER_SIDE


class _CanvasDeriveBase(BaseModel):
    # The URL of the image being edited, exactly as the canvas holds it.
    source_url: str = Field(..., min_length=1, max_length=2048)
    # The node the edit was made from — provenance on the registered row.
    node_id: str | None = Field(default=None, max_length=128)


class CanvasCropDeriveRequest(_CanvasDeriveBase):
    region: CropRegionModel


class CanvasGridDeriveRequest(_CanvasDeriveBase):
    xs: list[float] = Field(default_factory=list, max_length=MAX_LINES_PER_AXIS)
    ys: list[float] = Field(default_factory=list, max_length=MAX_LINES_PER_AXIS)


class CanvasOutpaintDeriveRequest(_CanvasDeriveBase):
    left: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    top: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    right: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    bottom: float = Field(default=0.0, ge=0.0, le=MAX_PAD_PER_SIDE)
    mode: Literal["deterministic", "ai"] = Field(default="deterministic")
    prompt: str | None = Field(default=None, max_length=2000)
