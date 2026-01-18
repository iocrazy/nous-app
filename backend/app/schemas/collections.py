"""Pydantic schemas for Smart Collections API."""
from datetime import datetime
from typing import Optional, List, Literal, Any
from uuid import UUID

from pydantic import BaseModel, Field


class CollectionCondition(BaseModel):
    """A single condition in a collection rule."""
    field: str = Field(..., description="Field to match: tag, author, date, title, description")
    operator: str = Field(..., description="Operator: equals, contains, starts_with, in, gt, lt, gte, lte")
    value: Any = Field(..., description="Value to compare against")


class CollectionRules(BaseModel):
    """Rules definition for smart collection."""
    match: Literal["all", "any"] = Field("all", description="Match all or any conditions")
    conditions: List[CollectionCondition] = Field(default_factory=list)


class CollectionCreate(BaseModel):
    """Schema for creating a smart collection."""
    name: str = Field(..., min_length=1, max_length=100)
    icon: str = Field("📁", max_length=50)
    description: Optional[str] = Field(None, max_length=500)
    rules: CollectionRules
    sort_by: str = Field("created_at", description="Sort field")
    sort_order: Literal["asc", "desc"] = Field("desc")


class CollectionUpdate(BaseModel):
    """Schema for updating a smart collection."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    icon: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None
    rules: Optional[CollectionRules] = None
    sort_by: Optional[str] = None
    sort_order: Optional[Literal["asc", "desc"]] = None


class CollectionResponse(BaseModel):
    """Response schema for a smart collection."""
    id: UUID
    user_id: UUID
    name: str
    icon: str
    description: Optional[str]
    rules: CollectionRules
    cached_count: int
    cached_at: Optional[datetime]
    is_preset: bool
    sort_by: str
    sort_order: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CollectionListResponse(BaseModel):
    """Response schema for list of collections."""
    collections: List[CollectionResponse]
    total: int


class CollectionVideosResponse(BaseModel):
    """Response schema for videos in a collection."""
    collection_id: UUID
    collection_name: str
    videos: List[dict]
    total: int
    page: int
    page_size: int
