# app/schemas/shares.py

"""
Shares system validation schemas.

Pydantic models for creating, updating, and accessing shared content
(links, reviews, presentations, deliveries).
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# ─── Create / Update ─────────────────────────────────────


class ShareCreate(BaseModel):
    """Request body for creating a new share."""

    resource_id: Optional[str] = Field(None, description="Resource ID to share")
    project_file_id: Optional[str] = Field(None, description="Project file ID to share")
    folder_id: Optional[str] = Field(None, description="Folder ID to share")
    version_id: Optional[str] = Field(None, description="Specific version ID to share")
    share_type: str = Field(
        ...,
        pattern="^(link|review|presentation|delivery)$",
        description="Type of share: link, review, presentation, or delivery",
    )
    share_name: str = Field(
        ..., min_length=1, max_length=200, description="Display name for the share"
    )
    password: Optional[str] = Field(
        None, max_length=100, description="Optional access password"
    )
    allow_download: bool = Field(
        True, description="Whether viewers can download the content"
    )
    expires_at: Optional[datetime] = Field(
        None, description="Expiration timestamp (UTC)"
    )
    max_views: Optional[int] = Field(
        None, ge=1, description="Maximum number of views allowed"
    )
    watermark: bool = Field(
        False, description="Whether to apply watermark on shared content"
    )
    team_id: Optional[str] = Field(None, description="Team ID for team-scoped shares")


class ShareUpdate(BaseModel):
    """Request body for updating share settings."""

    share_name: Optional[str] = Field(
        None, min_length=1, max_length=200, description="Display name"
    )
    password: Optional[str] = Field(
        None, max_length=100, description="Access password (empty string to remove)"
    )
    allow_download: Optional[bool] = Field(
        None, description="Whether viewers can download"
    )
    expires_at: Optional[datetime] = Field(
        None, description="Expiration timestamp (UTC)"
    )
    max_views: Optional[int] = Field(None, ge=1, description="Maximum views allowed")
    watermark: Optional[bool] = Field(None, description="Whether to apply watermark")


# ─── Public Access ────────────────────────────────────────


class ShareAccessRequest(BaseModel):
    """Request body for accessing a share by code (password verification)."""

    password: Optional[str] = Field(
        None, description="Password if the share is protected"
    )


# ─── Responses ────────────────────────────────────────────


class ShareResponse(BaseModel):
    """Share record returned from the API."""

    id: str
    resource_id: Optional[str] = None
    project_file_id: Optional[str] = None
    folder_id: Optional[str] = None
    version_id: Optional[str] = None
    share_type: str
    shared_by: str
    share_name: str
    share_code: str
    password: Optional[str] = None
    allow_download: bool
    expires_at: Optional[datetime] = None
    max_views: Optional[int] = None
    view_count: int = 0
    watermark: bool = False
    status: str
    created_at: datetime
    share_url: Optional[str] = None


class ShareViewRecord(BaseModel):
    """A single view record for a share."""

    id: str
    share_id: str
    viewer_id: Optional[str] = None
    is_favorited: bool = False
    last_viewed_at: datetime
    view_count: int = 1
    created_at: datetime
