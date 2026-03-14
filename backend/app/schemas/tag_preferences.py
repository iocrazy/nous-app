"""Schemas for user tag picker preferences."""

from typing import Optional

from pydantic import BaseModel, Field


class PickerSettings(BaseModel):
    """Display settings for the tag picker panel."""
    layout: str = Field(default="list", pattern="^(list|grid)$")
    columnWidth: str = Field(default="medium", pattern="^(small|medium|large)$")
    showStarred: bool = True
    showRecently: bool = True
    showRecommended: bool = False
    showCount: bool = True


class PanelSize(BaseModel):
    """Persisted panel dimensions."""
    width: int = Field(default=480, ge=300, le=1200)
    height: int = Field(default=400, ge=250, le=800)


class TagPreferencesResponse(BaseModel):
    """Full preferences response."""
    starred_tag_ids: list[str] = Field(default_factory=list)
    picker_settings: PickerSettings = Field(default_factory=PickerSettings)
    panel_size: PanelSize = Field(default_factory=PanelSize)


class TagPreferencesUpdate(BaseModel):
    """Partial update — all fields optional."""
    starred_tag_ids: Optional[list[str]] = None
    picker_settings: Optional[dict] = None  # partial JSONB merge
    panel_size: Optional[PanelSize] = None
