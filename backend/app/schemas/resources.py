# app/schemas/resources.py

"""
Resource library validation schemas.

Pydantic models for resource upload, CRUD, version management,
folder operations, and tag binding.
"""

from typing import Optional

from pydantic import BaseModel, Field


# ─── Resources ────────────────────────────────────────────

class ResourceUpdate(BaseModel):
    """Request body for updating resource metadata."""

    filename: Optional[str] = Field(None, min_length=1, max_length=500)
    is_trashed: Optional[bool] = None


class ResourceMoveRequest(BaseModel):
    """Request body for moving a resource to a folder."""

    folder_id: Optional[str] = Field(None, description="Target folder ID, null for root")
    scope_type: str = Field(..., pattern="^(personal|team)$")
    scope_id: str = Field(..., description="User ID or team ID")


# ─── Folders ──────────────────────────────────────────────

class FolderCreate(BaseModel):
    """Request body for creating a folder."""

    name: str = Field(..., min_length=1, max_length=200)
    parent_id: Optional[str] = None
    scope_type: str = Field(..., pattern="^(personal|team)$")
    scope_id: str = Field(..., description="User ID or team ID")
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)


class FolderUpdate(BaseModel):
    """Request body for updating a folder."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)
    sort_order: Optional[int] = None
    is_trashed: Optional[bool] = None


# ─── Tags ─────────────────────────────────────────────────

class ResourceTagRequest(BaseModel):
    """Request body for tagging a resource."""

    tag_id: str = Field(..., description="Tag ID to associate")
