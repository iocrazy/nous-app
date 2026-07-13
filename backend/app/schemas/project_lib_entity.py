"""Pydantic schemas for the generalized project library (mig 358).

One shape serves locations and props; ``entity_type`` rides in the path,
``badge_tag`` carries the per-type badge (location → interior/exterior,
prop → hero/set/costume — free text, client-rendered).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

LibEntityType = Literal["location", "prop"]
LibEntitySource = Literal["manual", "script"]


class LibEntityResponse(BaseModel):
    id: str = Field(..., description="Snowflake bigint, serialized as string")
    project_id: str = Field(..., description="Snowflake bigint, serialized as string")
    entity_type: LibEntityType
    name: str
    badge_tag: str = ""
    description: str = ""
    tags: Dict[str, Any] = Field(default_factory=dict)
    cover_url: Optional[str] = None
    source: LibEntitySource = "manual"
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime


class LibEntityCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    badge_tag: str = ""
    description: str = ""
    tags: Dict[str, Any] = Field(default_factory=dict)
    cover_url: Optional[str] = None


class LibEntityUpdate(BaseModel):
    """PATCH payload — None means "leave unchanged"."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    badge_tag: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None
    cover_url: Optional[str] = None
    sort_order: Optional[int] = None
