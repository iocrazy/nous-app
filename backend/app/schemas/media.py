# app/schemas/media.py

"""
Parsed media data validation schema module

Defines Pydantic 2.0 validation schemas for parsed media data used in API request
and response validation. Includes schemas for create, update, query, and
other operations.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.core.enums import DownloadStatus


class MediaBase(BaseModel):
    """Base parsed media schema"""

    # Unique media identifier
    platform_id: str = Field(..., description="Unique media identifier from platform")

    # User association
    user_id: Optional[str] = Field(None, description="User ID")

    # Engagement metrics
    like_count: Optional[int] = Field(None, description="Like count")
    comment_count: Optional[int] = Field(None, description="Comment count")
    share_count: Optional[int] = Field(None, description="Share count")
    favorite_count: Optional[int] = Field(None, description="Favorite/bookmark count")

    # Media metadata
    original_url: str = Field(..., description="Original media URL")
    duration: Optional[str] = Field(None, description="Media duration (seconds)")
    resolution: Optional[str] = Field(None, description="Media resolution")
    datasize: Optional[str] = Field(
        None, description="Media file size (human readable)"
    )
    datasize_bytes: Optional[int] = Field(None, description="Media file size in bytes")
    hashtags: Optional[str] = Field(None, description="Hashtag names")
    published_at: Optional[datetime] = Field(None, description="Media publish time")
    author: Optional[str] = Field(None, description="Author name")
    title: Optional[str] = Field(None, description="Media title")
    media_type: Optional[str] = Field(None, description="Media type")
    description: Optional[str] = Field(None, description="Media description")

    # Platform identification
    source_platform: Optional[str] = Field(
        default="douyin", description="Source platform"
    )

    # HLS streaming
    hls_path: Optional[str] = Field(None, description="HLS playlist path")
    media_format: Optional[str] = Field(
        default="mp4", description="Media format: mp4 or hls"
    )

    # Video download info
    need_download_video: Optional[bool] = Field(
        None, description="Whether to download video"
    )
    video_download_urls: Optional[list] = Field(None, description="Video download URLs")
    image_download_urls: Optional[list] = Field(None, description="Image download URLs")

    # Audio info
    music_name: Optional[str] = Field(None, description="Audio name")
    music_play_urls: Optional[list] = Field(
        None, description="Standalone music play URLs (carousel/image-text types)"
    )

    # Cover info
    cover_urls: Optional[list] = Field(None, description="Cover URL list")
    dynamic_cover_url: Optional[str] = Field(None, description="Dynamic cover URL")
    need_download_cover: Optional[bool] = Field(
        None, description="Whether to download cover"
    )
    cover_download_status: Optional[DownloadStatus] = Field(
        None, description="Cover download status"
    )
    cover_download_path: Optional[str] = Field(None, description="Cover download path")

    # Download status tracking
    video_download_status: Optional[DownloadStatus] = Field(
        None, description="Video download status"
    )
    music_download_status: Optional[DownloadStatus] = Field(
        None, description="Audio download status"
    )
    download_duration: Optional[float] = Field(
        None, description="Download duration (seconds)"
    )
    download_path: Optional[str] = Field(None, description="Download path")

    # Image download info (for image carousel content)
    image_download_status: Optional[DownloadStatus] = Field(
        None, description="Image download status"
    )
    image_download_path: Optional[str] = Field(None, description="Image download path")

    error_message: Optional[str] = Field(None, description="Error message")
    download_time: Optional[datetime] = Field(None, description="Download time")

    # Rich source-specific metadata (jsonb column, migration 248)
    metadata: Optional[dict] = Field(
        None, description="Rich source-specific metadata (jsonb)"
    )


class MediaCreate(MediaBase):
    """Schema for creating a parsed media record"""

    # Set default download statuses
    video_download_status: Optional[DownloadStatus] = Field(
        default=DownloadStatus.PENDING, description="Video download status"
    )

    music_download_status: Optional[DownloadStatus] = Field(
        default=DownloadStatus.SKIPPED, description="Audio download status"
    )

    # todo: validate video_download_urls, image_download_urls
    @model_validator(mode="after")
    def validate_urls(self):
        """Validate URL format"""
        # Validate original_url
        if self.original_url and not (
            self.original_url.startswith("http://")
            or self.original_url.startswith("https://")
        ):
            raise ValueError("original_url must be a valid HTTP or HTTPS URL")

        return self


class MediaUpdate(MediaBase):
    """Schema for updating a parsed media record"""

    # Override required fields from base to make them optional
    platform_id: Optional[str] = Field(
        None, description="Unique media identifier from platform"
    )
    original_url: Optional[str] = Field(None, description="Original media URL")


class MediaInDB(MediaBase):
    """Schema for parsed media data as stored in the database"""

    id: str = Field(..., description="Record ID (UUID)")
    video_download_status: DownloadStatus = Field(
        ..., description="Video download status"
    )
    music_download_status: DownloadStatus = Field(
        ..., description="Audio download status"
    )
    download_time: Optional[datetime] = Field(None, description="Download time")
    created_at: datetime = Field(..., description="Created at timestamp")
    updated_at: datetime = Field(..., description="Updated at timestamp")

    model_config = {"from_attributes": True}


class MediaSearchParams(BaseModel):
    """Parsed media search parameters"""

    keyword: Optional[str] = Field(None, min_length=1, description="Search keyword")
    author: Optional[str] = Field(None, description="Author name")
    download_status: Optional[DownloadStatus] = Field(
        None, description="Download status"
    )
    is_downloaded: Optional[bool] = Field(None, description="Whether downloaded")
    created_after: Optional[datetime] = Field(None, description="Created after")
    created_before: Optional[datetime] = Field(None, description="Created before")
    skip: int = Field(0, ge=0, description="Number of records to skip")
    limit: int = Field(
        100, ge=1, le=1000, description="Maximum number of records to return"
    )


class DownloadVideoResult(BaseModel):
    """Video download result model"""

    video_download_status: DownloadStatus = Field(
        default=DownloadStatus.PENDING, description="Download status"
    )
    video_path: Optional[str] = Field(None, description="Video file path")
    download_duration: Optional[float] = Field(
        None, description="Download duration (seconds)"
    )
    error: Optional[str] = Field(None, description="Error message")
    warning: Optional[str] = Field(None, description="Warning message")

    @property
    def is_successful(self) -> bool:
        """Check if download was successful"""
        return (
            self.video_download_status == DownloadStatus.COMPLETED
            and self.error is None
        )


class DownloadMusicResult(BaseModel):
    """Audio download result model"""

    music_download_status: DownloadStatus = Field(
        default=DownloadStatus.PENDING, description="Audio download status"
    )
    music_path: Optional[str] = Field(None, description="Audio file path")
    music_downloaded: Optional[bool] = Field(
        default=False, description="Whether audio was downloaded"
    )
    error: Optional[str] = Field(None, description="Error message")
    warning: Optional[str] = Field(None, description="Warning message")

    @property
    def is_successful(self) -> bool:
        """Check if download was successful"""
        return (
            self.music_download_status == DownloadStatus.COMPLETED
            and self.error is None
        )


class DownloadImagesResult(BaseModel):
    """Image download result model"""

    image_urls_list: list[str] = Field([], description="Image file path list")
    video_download_status: DownloadStatus = Field(
        default=DownloadStatus.PENDING, description="Video download status"
    )
    error: Optional[str] = Field(None, description="Error message")
    warning: Optional[str] = Field(None, description="Warning message")


class DownloadCoverResult(BaseModel):
    """Cover download result model"""

    cover_download_status: DownloadStatus = Field(
        default=DownloadStatus.PENDING, description="Cover download status"
    )
    cover_path: Optional[str] = Field(None, description="Cover file path")
    error: Optional[str] = Field(None, description="Error message")

    @property
    def is_successful(self) -> bool:
        """Check if download was successful"""
        return (
            self.cover_download_status == DownloadStatus.COMPLETED
            and self.error is None
        )


class MediaFetchRequest(BaseModel):
    """Media fetch request model"""

    url: str = Field(..., description="Text containing a media URL")
    video_bool: bool = Field(default=True, description="Whether to download video")
    cover_bool: bool = Field(default=True, description="Whether to download cover")

    @model_validator(mode="after")
    def validate_url(self):
        """Validate URL format - check if text contains a valid URL"""
        if not self.url:
            raise ValueError("url field cannot be empty")

        # URL format validation is delegated to Utils.extract_valid_url
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "Video description https://v.douyin.com/example/ copy this link...",
                    "video_bool": True,
                    "transcript_bool": True,
                    "summary_bool": True,
                }
            ]
        }
    }


class MediaTypeFetchRequest(BaseModel):
    """Request body for subsequent per-type fetch (POST /videos/{platform_id}/fetch)."""

    types: list[str] = Field(
        ...,
        description="Media types to fetch: 'video', 'cover', 'image'",
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_types(self):
        valid = {"video", "cover", "image"}
        invalid = set(self.types) - valid
        if invalid:
            raise ValueError(f"Invalid types: {invalid}. Must be one of {valid}")
        return self
