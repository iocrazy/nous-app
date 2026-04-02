"""Skill request/response Pydantic schemas."""

from typing import List, Optional

from pydantic import BaseModel, Field


class SkillCreate(BaseModel):
    """Request body for creating a skill."""

    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    content_md: str = Field(..., min_length=1, max_length=10000)
    category: Optional[str] = Field(None, max_length=100)
    icon: str = Field(default="✨", max_length=20)
    output_format: Optional[str] = Field(None, max_length=5000)
    trigger_keywords: List[str] = Field(default_factory=list)
    project_id: Optional[str] = None
    is_public: bool = False


class SkillUpdate(BaseModel):
    """Request body for updating a skill."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    content_md: Optional[str] = Field(None, min_length=1, max_length=10000)
    category: Optional[str] = Field(None, max_length=100)
    icon: Optional[str] = Field(None, max_length=20)
    output_format: Optional[str] = Field(None, max_length=5000)
    trigger_keywords: Optional[List[str]] = None
    project_id: Optional[str] = None
    is_public: Optional[bool] = None
    status: Optional[str] = Field(None, pattern=r"^(active|archived)$")
