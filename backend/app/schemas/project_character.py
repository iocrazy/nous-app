"""Pydantic schemas for the project character library (mig 357).

The authored character rows the bible cards and the character canvas bind to.
``tags`` is grouped chips ({"personality": [...], "conflict": [...], ...}) —
free-form groups, client-rendered, treated as opaque JSONB here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

CharacterRoleTag = Literal["", "lead", "support", "antagonist"]
CharacterSource = Literal["manual", "script"]


class ProjectCharacterResponse(BaseModel):
    id: str = Field(..., description="Snowflake bigint, serialized as string")
    project_id: str = Field(..., description="Snowflake bigint, serialized as string")
    name: str
    role_tag: CharacterRoleTag = ""
    description: str = ""
    tags: Dict[str, Any] = Field(default_factory=dict)
    portrait_url: Optional[str] = None
    source: CharacterSource = "manual"
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectCharacterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    role_tag: CharacterRoleTag = ""
    description: str = ""
    tags: Dict[str, Any] = Field(default_factory=dict)
    portrait_url: Optional[str] = None


class ProjectCharacterUpdate(BaseModel):
    """PATCH payload — None means "leave unchanged"."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    role_tag: Optional[CharacterRoleTag] = None
    description: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None
    portrait_url: Optional[str] = None
    sort_order: Optional[int] = None
