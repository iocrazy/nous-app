"""Style Template request/response Pydantic schemas."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class StyleTemplateCreate(BaseModel):
    """Request body for creating a style template."""

    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    prompt_content: str = Field(..., min_length=1, max_length=50000)
    category: Optional[str] = Field(None, max_length=100)
    is_public: bool = False


class StyleTemplateUpdate(BaseModel):
    """Request body for updating a style template."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    prompt_content: Optional[str] = Field(None, min_length=1, max_length=50000)
    category: Optional[str] = Field(None, max_length=100)
    is_public: Optional[bool] = None
