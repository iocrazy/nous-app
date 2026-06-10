"""Pydantic schemas for the crop-derive endpoint (Phase 3 Day 5)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CropRegionModel(BaseModel):
    """Normalized crop region — mirrors the front-end ``CropRegion``."""

    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)
    width: float = Field(..., gt=0.0, le=1.0)
    height: float = Field(..., gt=0.0, le=1.0)


class CropDeriveRequest(BaseModel):
    region: CropRegionModel
    # Optional override; defaults to ``crop-{source.filename}``.
    filename: Optional[str] = Field(default=None, max_length=255)


class CropDeriveResponse(BaseModel):
    success: bool
    data: dict
