# app/schemas/tags.py

"""
Pydantic schemas for Tags API.

NOTE: tags.id is BIGINT (Snowflake), NOT UUID. Migrated in 051.
All Snowflake IDs are serialized as strings to avoid JS precision loss.
"""

from datetime import datetime
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, BeforeValidator, Field

# Snowflake BIGINT IDs: Supabase returns int, JS needs string to avoid precision loss
SnowflakeId = Annotated[str, BeforeValidator(lambda v: str(v) if v is not None else v)]


class TagBase(BaseModel):
    """Base tag schema with common fields."""

    name: str = Field(
        ..., min_length=1, max_length=50, description="Tag name (English)"
    )
    name_zh: Optional[str] = Field(
        None, max_length=50, description="Tag name in Chinese"
    )
    color: Optional[str] = Field("#6366f1", description="Hex color code")
    icon: Optional[str] = Field(None, description="Emoji or icon identifier")


class TagCreate(TagBase):
    """Schema for creating a user tag."""


class TagUpdate(BaseModel):
    """Schema for updating a tag."""

    name: Optional[str] = Field(
        None, min_length=1, max_length=50, description="Tag name"
    )
    name_zh: Optional[str] = Field(
        None,
        max_length=50,
        description="Chinese alias. Empty string clears it.",
    )
    color: Optional[str] = Field(None, description="Hex color code")
    icon: Optional[str] = Field(None, description="Emoji or icon identifier")
    enabled: Optional[bool] = Field(None, description="Whether visible in frontend API")
    group_id: Optional[str] = Field(
        None,
        description="Reassign to a tag group (or null to leave uncategorized)",
    )


class TagResponse(TagBase):
    """Schema for tag response."""

    id: SnowflakeId
    type: Literal["system", "user", "time"]
    user_id: Optional[str] = None
    group_id: Optional[SnowflakeId] = None
    group_name: Optional[str] = Field(None, description="Tag group name")
    enabled: bool = Field(True, description="Whether visible in frontend API")
    created_at: datetime
    media_count: Optional[int] = Field(
        0, description="Number of resources using this tag"
    )

    model_config = {"from_attributes": True}


class TagListResponse(BaseModel):
    """Schema for list of tags response."""

    tags: List[TagResponse]
    total: int


class MediaTagCreate(BaseModel):
    """Schema for adding tag to media/resource."""

    tag_id: SnowflakeId
    confidence: Optional[float] = Field(
        None, ge=0, le=1, description="Confidence score for auto/AI tags"
    )
    source: Literal["auto", "manual", "ai"] = Field(
        "manual", description="Source of the tag assignment"
    )


class MediaTagResponse(BaseModel):
    """Schema for media tag association."""

    tag: TagResponse
    confidence: Optional[float] = Field(None, description="Confidence score")
    source: str = Field("manual", description="Source of the tag assignment")
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class MediaTagsResponse(BaseModel):
    """Schema for media's tags response."""

    media_id: str = Field(..., description="Media/Resource ID")
    tags: List[MediaTagResponse]


class TagCountItem(BaseModel):
    """Schema for tag count item."""

    id: SnowflakeId
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
