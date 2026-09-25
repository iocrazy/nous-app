"""Pydantic schemas for Smart Collections API."""

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class CollectionCondition(BaseModel):
    """A single condition in a collection rule."""

    field: str = Field(
        ..., description="Field to match: tag, author, date, title, description"
    )
    operator: str = Field(
        ..., description="Operator: equals, contains, starts_with, in, gt, lt, gte, lte"
    )
    value: Any = Field(..., description="Value to compare against")


class CollectionRules(BaseModel):
    """Rules definition for smart collection."""

    match: Literal["all", "any"] = Field(
        "all", description="Match all or any conditions"
    )
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
    """One ``smart_collections`` row as the collections routes return it.

    ``id`` is the Snowflake BIGINT primary key: a JSON **number** (it was
    declared ``UUID``, so every route returning a real row answered 500).
    The repository already turns ``user_id`` and the timestamps into strings,
    so they are declared ``str``. Nullable columns fall back to the documented
    defaults in ``app/api/collections_router.py::_collection_out`` before they
    reach this model, so a legacy row with NULLs still validates.
    """

    id: int
    user_id: str
    name: str
    icon: str
    color: Optional[str] = None
    description: Optional[str]
    rules: CollectionRules
    media_count: int = Field(description="Number of media matching this collection")
    cached_at: Optional[str]
    is_preset: bool
    is_active: bool = Field(description="Whether the collection is active")
    sort_by: str
    sort_order: str
    created_at: Optional[str]
    updated_at: Optional[str]


class CollectionListResponse(BaseModel):
    """Response schema for list of collections."""

    collections: List[CollectionResponse]
    total: int


class CollectionMediaResponse(BaseModel):
    """Response schema for media in a collection."""

    collection_id: int
    collection_name: str
    media: List[dict]
    total: int
    page: int
    page_size: int


class CollectionRefreshResult(BaseModel):
    """``POST /collections/{id}/refresh``.

    ``collection_id`` echoes the path segment (a string); ``media_count`` is
    the number of media now matching the rules (the cache keeps the first
    1000 ids).
    """

    message: str
    collection_id: str
    media_count: int


class CollectionPresetRef(BaseModel):
    """One preset created by ``POST /collections/init-presets``.

    ``id`` is the Snowflake BIGINT as the repository returns it: a JSON
    **number**.
    """

    id: int
    name: str


class CollectionInitPresetsResult(BaseModel):
    """``POST /collections/init-presets``.

    ``presets`` is only present when this call created them; when the user
    already had presets the body is ``{message, count}`` (the route declares
    ``response_model_exclude_unset`` so the key stays absent).
    """

    message: str
    count: int
    presets: Optional[List[CollectionPresetRef]] = None
