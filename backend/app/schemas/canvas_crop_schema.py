"""Pydantic crop region model shared by the canvas derive schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CropRegionModel(BaseModel):
    """Normalized crop region — mirrors the front-end ``CropRegion``."""

    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)
    width: float = Field(..., gt=0.0, le=1.0)
    height: float = Field(..., gt=0.0, le=1.0)
