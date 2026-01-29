# app/schemas/tags.py

"""
Pydantic schemas for Tags API.

Defines validation schemas for tag management operations including
creating, updating, and associating tags with videos.
"""

from datetime import datetime
from typing import Optional, List, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TagBase(BaseModel):
    """Base tag schema with common fields."""
    name: str = Field(..., min_length=1, max_length=50, description="Tag name")
    color: Optional[str] = Field("#6366f1", description="Hex color code")
    icon: Optional[str] = Field(None, description="Emoji or icon identifier")


class TagCreate(TagBase):
    """Schema for creating a user tag."""
    pass


class TagUpdate(BaseModel):
    """Schema for updating a tag."""
    name: Optional[str] = Field(None, min_length=1, max_length=50, description="Tag name")
    color: Optional[str] = Field(None, description="Hex color code")
    icon: Optional[str] = Field(None, description="Emoji or icon identifier")


class TagResponse(TagBase):
    """Schema for tag response."""
    id: UUID
    type: Literal["system", "user", "time"]
    user_id: Optional[UUID] = None
    created_at: datetime

    model_config = {
        "from_attributes": True
    }


class TagListResponse(BaseModel):
    """Schema for list of tags response."""
    tags: List[TagResponse]
    total: int


class VideoTagCreate(BaseModel):
    """Schema for adding tag to video."""
    tag_id: UUID
    confidence: Optional[float] = Field(None, ge=0, le=1, description="Confidence score for auto/AI tags")
    source: Literal["auto", "manual", "ai"] = Field("manual", description="Source of the tag assignment")


class VideoTagResponse(BaseModel):
    """Schema for video tag association."""
    tag: TagResponse
    confidence: Optional[float] = Field(None, description="Confidence score")
    source: str = Field(..., description="Source of the tag assignment")
    created_at: datetime

    model_config = {
        "from_attributes": True
    }


class VideoTagsResponse(BaseModel):
    """Schema for video's tags response."""
    video_id: int = Field(..., description="Video ID (BIGINT in database)")
    tags: List[VideoTagResponse]


class TagCountItem(BaseModel):
    """Schema for tag count item."""
    id: str
    name: str
    color: Optional[str] = "#6366f1"
    icon: Optional[str] = None
    type: str = "system"
    count: int


class TagStatisticsResponse(BaseModel):
    """Schema for tag statistics response."""
    success: bool = True
    top_tags: List[TagCountItem]
    total_tagged_videos: int = 0
