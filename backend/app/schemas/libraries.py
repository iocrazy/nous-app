# app/schemas/libraries.py

"""
Library validation schemas.

Pydantic models for team library CRUD operations.
"""

from typing import Optional

from pydantic import BaseModel, Field


class LibraryCreate(BaseModel):
    """Request body for creating a library."""

    name: str = Field(..., min_length=1, max_length=200)
    scope_type: str = Field(default="team", pattern="^team$")
    scope_id: str = Field(..., description="Team ID")
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)


class LibraryUpdate(BaseModel):
    """Request body for updating a library."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)
    sort_order: Optional[int] = None
    visibility: Optional[str] = Field(None, pattern="^(inherited|restricted)$")
