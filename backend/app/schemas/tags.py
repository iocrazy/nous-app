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


def _digits_only(v):
    """Reject a non-numeric BIGINT id at validation instead of letting it reach
    asyncpg, where it surfaces as an opaque 500. ``None`` passes through — for
    ``group_id`` a null is a real instruction ("move to Uncategorized")."""
    if v is None:
        return None
    text = str(v).strip()
    if not text.isdigit():
        raise ValueError("must be a numeric tag group id")
    return text


# A Snowflake id arriving FROM a client (vs SnowflakeId, which normalises one
# on the way out).
GroupIdIn = Annotated[str, BeforeValidator(_digits_only)]


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

    prompt_trigger: bool = Field(
        False,
        description="Assets with this tag surface the Prompt panel/badge",
    )
    group_id: Optional[GroupIdIn] = Field(
        None,
        description="Create the tag straight into this tag group. Omit for "
        "uncategorized. The repo already supported it; wiring it here removes "
        "the chrome extension's POST-then-PUT dance.",
    )


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
    group_id: Optional[GroupIdIn] = Field(
        None,
        description="Reassign to a tag group. Explicit null moves the tag to "
        "the uncategorized bucket; omit the field to leave the group alone.",
    )
    origin: Optional[Literal["curated"]] = Field(
        None,
        description="Promote a shadow (origin='note') tag into the curated pool. "
        "Only 'curated' is settable — 'note' is never accepted (422).",
    )
    prompt_trigger: Optional[bool] = Field(
        None, description="Toggle the Prompt-panel trigger for this tag"
    )


class TagResponse(TagBase):
    """Schema for tag response."""

    id: SnowflakeId
    type: Literal["system", "user", "time"]
    user_id: Optional[str] = None
    group_id: Optional[SnowflakeId] = None
    group_name: Optional[str] = Field(None, description="Tag group name")
    enabled: bool = Field(True, description="Whether visible in frontend API")
    origin: str = Field(
        "curated",
        description="'curated' (real tag) or 'note' (shadow tag auto-created "
        "from an inspiration note word).",
    )
    created_at: datetime
    media_count: Optional[int] = Field(
        0, description="Number of resources using this tag"
    )
    prompt_trigger: bool = Field(
        False, description="Whether this tag triggers the Prompt panel"
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
    notes: int = Field(0, description="Live inspiration-note usage count")
    hotspots: int = Field(0, description="Hotspot word hits in the recent window")


class TagStatisticsResponse(BaseModel):
    """Schema for tag statistics response."""

    success: bool = True
    top_tags: List[TagCountItem]
    total_tagged_videos: int = 0
