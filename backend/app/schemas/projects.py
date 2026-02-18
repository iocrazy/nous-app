# app/schemas/projects.py

"""
MediaTrack project and file validation schema module

Defines Pydantic 2.0 validation schemas for the project-based file
management system, including project CRUD, file metadata, and
video linking operations.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """Request body for creating a new project"""

    name: str = Field(..., min_length=1, max_length=30)
    description: Optional[str] = None
    team_id: Optional[str] = None
    project_type: str = Field(
        default="personal", pattern="^(internal|external|personal)$"
    )
    project_group: Optional[str] = Field(None, max_length=100)
    announcement: Optional[str] = Field(None, max_length=100)


class ProjectUpdate(BaseModel):
    """Request body for updating an existing project"""

    name: Optional[str] = Field(None, min_length=1, max_length=30)
    description: Optional[str] = None
    project_type: Optional[str] = Field(None, pattern="^(internal|external|personal)$")
    project_group: Optional[str] = Field(None, max_length=100)
    is_starred: Optional[bool] = None
    announcement: Optional[str] = Field(None, max_length=100)
    color_label: Optional[str] = Field(None, max_length=20)


class ProjectResponse(BaseModel):
    """API response for a single project"""

    id: str
    name: str
    description: Optional[str] = None
    owner_id: str
    team_id: Optional[str] = None
    project_type: str
    project_group: Optional[str] = None
    announcement: Optional[str] = None
    is_starred: bool = False
    color_label: Optional[str] = None
    file_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectFileCreate(BaseModel):
    """Request body for creating a file record"""

    filename: str = Field(..., min_length=1, max_length=500)
    notes: Optional[str] = None


class ProjectFileUpdate(BaseModel):
    """Request body for updating an existing file"""

    filename: Optional[str] = Field(None, min_length=1, max_length=500)
    notes: Optional[str] = None
    is_trashed: Optional[bool] = None


class ProjectFileResponse(BaseModel):
    """API response for a single project file"""

    id: str
    project_id: str
    filename: str
    file_type: Optional[str] = None
    mime_type: Optional[str] = None
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    media_id: Optional[str] = None
    duration_seconds: Optional[int] = None
    resolution: Optional[str] = None
    fps: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    video_bitrate_kbps: Optional[int] = None
    audio_bitrate_kbps: Optional[int] = None
    audio_channels: Optional[int] = None
    audio_sample_rate: Optional[int] = None
    review_status: Optional[str] = None
    current_version: int = 1
    thumbnail_path: Optional[str] = None
    cover_image_path: Optional[str] = None
    uploaded_by: Optional[str] = None
    notes: Optional[str] = None
    is_trashed: bool = False
    trashed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LinkMediaRequest(BaseModel):
    """Request body for linking a media item to a project"""

    media_id: str = Field(..., description="ID of the media to link")


class FileVersionResponse(BaseModel):
    """API response for a file version"""

    id: str
    file_id: str
    version_number: int
    filename: Optional[str] = None
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    duration_seconds: Optional[int] = None
    resolution: Optional[str] = None
    fps: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    video_bitrate_kbps: Optional[int] = None
    audio_bitrate_kbps: Optional[int] = None
    audio_channels: Optional[int] = None
    audio_sample_rate: Optional[int] = None
    thumbnail_path: Optional[str] = None
    cover_image_path: Optional[str] = None
    uploaded_by: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CreateCommentRequest(BaseModel):
    """Request body for creating a review comment"""

    content: str = Field(..., min_length=1)
    timestamp_seconds: Optional[float] = None
    version_id: Optional[str] = None
    drawing_data: Optional[dict] = None


class CommentResponse(BaseModel):
    """API response for a review comment"""

    id: str
    file_id: str
    version_id: Optional[str] = None
    author_id: str
    author_email: Optional[str] = None
    content: str
    timestamp_seconds: Optional[float] = None
    drawing_data: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReviewStatusUpdate(BaseModel):
    """Request body for updating review status"""

    review_status: Optional[str] = Field(
        None,
        pattern="^(pending_review|in_review|feedback_collected|approved)$",
        description="Set to null to remove status",
    )


class AddMemberRequest(BaseModel):
    """Request body for adding a project member"""

    user_id: str
    role: str = Field(default="viewer", pattern="^(admin|editor|viewer)$")


class UpdateMemberRoleRequest(BaseModel):
    """Request body for updating a member's role"""

    role: str = Field(..., pattern="^(admin|editor|viewer)$")
