"""Script Editor request/response Pydantic schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Script Project schemas
# ---------------------------------------------------------------------------


class ScriptProjectCreate(BaseModel):
    """Request body for creating a new script project."""

    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    project_id: int


class ScriptProjectUpdate(BaseModel):
    """Request body for updating an existing script project."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[str] = Field(None, pattern="^(active|archived)$")
    settings_json: Optional[Dict[str, Any]] = None


class ScriptProjectResponse(BaseModel):
    """API response for a single script project."""

    id: str
    project_id: str
    team_id: str
    created_by: str
    name: str
    description: Optional[str] = None
    display_code: Optional[str] = None
    status: str
    settings_json: Optional[Dict[str, Any]] = None
    viewport_json: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Script Chapter schemas
# ---------------------------------------------------------------------------


class ScriptChapterCreate(BaseModel):
    """Request body for creating a chapter node."""

    title: Optional[str] = Field(None, max_length=200)
    summary: Optional[str] = Field(None, max_length=5000)
    content: Optional[str] = None
    chapter_number: Optional[int] = None
    parent_chapter_id: Optional[str] = None
    branch_label: Optional[str] = Field(None, max_length=100)
    branch_type: Optional[str] = Field(None, pattern="^(condition|choice)$")
    position_x: float = Field(default=0.0)
    position_y: float = Field(default=0.0)
    width: Optional[float] = None
    height: Optional[float] = None
    data_json: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = Field(default=0)


class ScriptChapterUpdate(BaseModel):
    """Request body for updating a chapter node."""

    title: Optional[str] = Field(None, max_length=200)
    summary: Optional[str] = Field(None, max_length=5000)
    content: Optional[str] = None
    chapter_number: Optional[int] = None
    parent_chapter_id: Optional[str] = None
    branch_label: Optional[str] = Field(None, max_length=100)
    branch_type: Optional[str] = Field(None, pattern="^(condition|choice)$")
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    data_json: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None


# ---------------------------------------------------------------------------
# Canvas sync schema
# ---------------------------------------------------------------------------


class ScriptCanvasSyncRequest(BaseModel):
    """Request body for a full script canvas sync operation."""

    added_chapters: List[ScriptChapterCreate] = Field(default_factory=list)
    updated_chapters: List[Dict[str, Any]] = Field(default_factory=list)
    deleted_chapter_ids: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# AI outline generation
# ---------------------------------------------------------------------------


class GenerateOutlineRequest(BaseModel):
    """Request body for AI-generated story outline."""

    script_id: str
    premise: str = Field(..., min_length=10, max_length=10000)
    chapter_count: int = Field(default=5, ge=2, le=20)
    style_guide: Optional[str] = Field(None, max_length=2000)
