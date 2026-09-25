# app/schemas/shares.py

"""
Shares system validation schemas.

Pydantic models for creating and accessing shared content
(links, reviews, presentations, deliveries).
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# ─── Create ─────────────────────────────────────


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


# ─── Public Access ────────────────────────────────────────


class ShareAccessRequest(BaseModel):
    """Request body for accessing a share by code (password verification)."""

    password: Optional[str] = Field(
        None, description="Password if the share is protected"
    )


# Response shapes live in app/schemas/share_responses.py.
